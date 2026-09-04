import QtQuick

// Native, themeable rendering of the shared 24px SVG geometry.
Canvas {
    id: root
    property color foreground: "white"
    property string stateName: "off"
    property real spinnerAngle: 0
    onSpinnerAngleChanged: requestPaint()
    NumberAnimation on spinnerAngle {
        from: 0
        to: 360
        duration: 1000
        loops: Animation.Infinite
        running: root.stateName === "applying" && root.visible
    }
    implicitWidth: 24
    implicitHeight: 24
    onForegroundChanged: requestPaint()
    onStateNameChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        const c = getContext("2d");
        c.reset();
        c.scale(width / 24, height / 24);
        c.strokeStyle = foreground;
        c.fillStyle = foreground;
        c.lineWidth = 1.5;
        c.lineCap = "round";
        c.lineJoin = "round";
        c.beginPath();
        c.arc(12, 2.6, 1.4, 0, Math.PI * 2);
        c.stroke();
        c.beginPath();
        c.moveTo(4, 21.5);
        c.lineTo(4, 12);
        c.bezierCurveTo(4, 1.5, 20, 1.5, 20, 12);
        c.lineTo(20, 21.5);
        c.closePath();
        c.moveTo(4, 18.5);
        c.lineTo(20, 18.5);
        // Leave the bird's silhouette unobscured by cage bars.
        c.moveTo(12, 4.2);
        c.bezierCurveTo(9, 6, 8.5, 8, 8.5, 10.5);
        c.moveTo(12, 4.2);
        c.bezierCurveTo(15, 6, 15.5, 8, 15.5, 10.5);
        c.stroke();
        c.beginPath();
        c.moveTo(7, 17.3);
        c.lineTo(9.7, 12.5);
        c.bezierCurveTo(10.5, 9.8, 14, 10.2, 14.6, 12);
        c.lineTo(16.5, 12.8);
        c.lineTo(14.5, 13.5);
        c.bezierCurveTo(14.2, 16.8, 11.6, 17.8, 9.5, 16);
        c.closePath();
        c.fill();
        c.beginPath();
        c.moveTo(11, 16.5); c.lineTo(11.5, 18.5);
        c.stroke();
        if (stateName === "off") {
            c.beginPath();
            c.moveTo(4, 11); c.lineTo(1, 10); c.lineTo(1, 19); c.lineTo(4, 20);
            c.stroke();
        } else if (stateName === "manual-wifi") {
            c.lineWidth = 1.3;
            c.beginPath(); c.arc(21, 6.5, 2.6, Math.PI * 1.2, Math.PI * 1.8); c.stroke();
            c.beginPath(); c.arc(21, 6.5, 1.2, Math.PI * 1.2, Math.PI * 1.8); c.stroke();
            c.beginPath(); c.arc(21, 6.5, 0.45, 0, Math.PI * 2); c.fill();
        } else if (stateName === "error") {
            c.beginPath(); c.moveTo(22.3, 9); c.lineTo(22.3, 13); c.stroke();
            c.beginPath(); c.arc(22.3, 15.3, 0.7, 0, Math.PI * 2); c.fill();
        } else if (stateName === "applying") {
            const angle = spinnerAngle * Math.PI / 180;
            c.beginPath(); c.arc(21, 8, 2, angle, angle + Math.PI * 1.5); c.stroke();
        }
    }
}
