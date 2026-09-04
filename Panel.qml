import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons
import "Model.js" as Model

Panel {
    id: root
    moduleName: "io.github.bprendie.faraday"
    ipcTarget: "faraday"

    property var info: ({ mode: "off", phase: "loading", healthy: false, errors: [], snapshot: false })
    property bool busy: false
    property string requestedOperation: ""
    property string message: ""
    property double now: Date.now()
    readonly property string helper: "/usr/local/libexec/faraday-helper"
    readonly property string visualState: Model.visualState(info, now, busy)
    readonly property bool inProgress: Model.inProgress(info, now, busy)
    readonly property string operation: busy ? requestedOperation : (info.operation || "")
    readonly property string stateTitle: Model.title(visualState, info.phase, operation)
    readonly property color stateColor: visualState === "sealed" ? "#a9cf86"
        : visualState === "error" ? "#ef8b87"
        : visualState === "manual-wifi" ? "#eac078" : root.barForeground

    function refresh() {
        if (!statusProc.running) statusProc.running = true;
    }

    function applyMode(mode) {
        if (inProgress) return;
        busy = true;
        requestedOperation = mode === "off" ? "restoring" : "applying";
        message = mode === "off" ? "Restoring your original settings…" : "Applying protection…";
        actionProc.command = ["pkexec", helper, mode];
        actionProc.running = true;
    }

    function parseStatus(text) {
        try {
            const value = JSON.parse(text);
            info = value;
        } catch (e) {
            message = "Cannot read Faraday status";
        }
    }

    Component.onCompleted: refresh()
    onOpenedChanged: if (opened) refresh()

    Timer {
        interval: 2000
        running: true
        repeat: true
        onTriggered: { root.now = Date.now(); root.refresh(); }
    }

    Process {
        id: statusProc
        command: [root.helper, "status"]
        stdout: StdioCollector {
            waitForEnd: true
            onStreamFinished: root.parseStatus(text)
        }
        onExited: function(code) {
            if (code !== 0) root.message = "Faraday helper unavailable. Run the project installer first.";
        }
    }

    Process {
        id: actionProc
        stdout: StdioCollector { id: actionOutput; waitForEnd: true }
        stderr: StdioCollector { id: actionErrors; waitForEnd: true }
        onExited: function(code) {
            root.busy = false;
            root.requestedOperation = "";
            if (actionOutput.text.trim().startsWith("{")) {
                root.parseStatus(actionOutput.text);
                root.message = "";
            } else {
                root.message = code === 0 ? "" : (actionErrors.text.trim() || "Action cancelled or failed. Your restore snapshot is retained.");
            }
            root.refresh();
        }
    }

    BarIconButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        tooltipText: "Faraday · " + root.stateTitle
        iconComponent: Component {
            CageIcon { foreground: root.stateColor; stateName: root.visualState }
        }
        onPressed: function(mouseButton) { root.toggle(); }
    }

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    KeyboardPanel {
        id: panel
        anchorItem: button
        owner: root
        bar: root.bar
        open: root.opened
        focusTarget: sealedButton
        contentWidth: panel.fittedContentWidth(Style.space(390))
        contentHeight: panel.fittedContentHeight(content.implicitHeight)

        Column {
            id: content
            width: parent.width
            spacing: Style.space(12)
            Keys.onEscapePressed: root.close()

            Row {
                spacing: Style.space(12)
                CageIcon {
                    width: Style.space(44); height: width
                    foreground: root.stateColor; stateName: root.visualState
                }
                Column {
                    Text {
                        text: "faraday"; color: Color.foreground
                        font.family: Style.font.family; font.pixelSize: Style.font.display
                    }
                    Text {
                        text: root.stateTitle; color: root.stateColor
                        font.family: Style.font.family; font.pixelSize: Style.font.body
                    }
                }
            }

            Text {
                width: parent.width
                text: "Choose how your laptop connects. Disabling Faraday restores the settings saved before activation."
                wrapMode: Text.WordWrap; color: Color.foreground
                font.family: Style.font.family; font.pixelSize: Style.font.body
            }

            Text {
                width: parent.width
                visible: root.inProgress
                text: (root.info.progress || root.message || root.stateTitle)
                    + (root.info.started ? " · " + Math.max(0, Math.floor(root.now / 1000 - root.info.started)) + "s" : "")
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: Color.foreground
                font.family: Style.font.family; font.pixelSize: Style.font.bodySmall
            }

            Button {
                id: sealedButton
                width: parent.width; focusable: true; bordered: true
                text: "Caged - Faraday Mode.\nAll radios off."
                selected: root.info.mode === "sealed" && root.info.snapshot
                enabled: !root.inProgress && root.info.phase !== "restoring"
                onClicked: root.applyMode("sealed")
            }
            Button {
                width: parent.width; focusable: true; bordered: true
                text: "Selective - Wi-Fi by choice.\nIncoming connections blocked."
                selected: root.info.mode === "manual-wifi" && root.info.snapshot
                enabled: !root.inProgress && root.info.phase !== "restoring"
                onClicked: root.applyMode("manual-wifi")
            }
            Button {
                width: parent.width; focusable: true; bordered: true
                text: root.inProgress && root.operation === "restoring" ? "Restoring…"
                    : root.info.phase === "restoring" ? "Retry restore" : "Normal - Restore normal operations."
                enabled: !root.inProgress && root.info.snapshot === true
                onClicked: root.applyMode("off")
            }

            Text {
                visible: root.info.phase === "active"
                width: parent.width
                text: root.info.mode === "manual-wifi"
                    ? "Incoming connections blocked · Wi-Fi autoconnect off\nBluetooth, cellular, NFC and other radios off"
                    : "Network traffic blocked · all radios off"
                wrapMode: Text.WordWrap; color: Color.foreground
                font.family: Style.font.family; font.pixelSize: Style.font.bodySmall
            }
            Button {
                width: parent.width; focusable: true
                visible: root.info.mode === "manual-wifi" && root.info.phase === "active"
                text: "Choose a Wi-Fi network…"
                onClicked: {
                    root.close();
                    Quickshell.execDetached(["omarchy-shell", "shell", "toggle", "omarchy.network", "{}"]);
                }
            }
            Text {
                width: parent.width
                visible: !root.inProgress && text.length > 0
                text: [root.message].concat(root.info.errors || []).filter(function(v) { return !!v; }).join("\n")
                textFormat: Text.PlainText
                wrapMode: Text.WrapAnywhere; color: root.stateColor
                font.family: Style.font.family; font.pixelSize: Style.font.bodySmall
            }
        }
    }
}
