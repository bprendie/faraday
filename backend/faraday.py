#!/usr/bin/python3
"""Faraday's privileged controller. Standard library only; never shells out.

Installed with a root-owned, isolated Python launcher. All changes are journaled
before execution. The original journal survives failures and mode transitions.
"""

import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

VERSION = "0.1.2"
STATE = Path("/var/lib/faraday")
PUBLIC = Path("/run/faraday/status.json")
ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"}
WIFI_TYPES = {"802-11-wireless", "wifi"}


def radio_identity(kind, path):
    # Controller numbers (phy0, hci1, nfc0) can change after reboot. The bus
    # location identifies the physical slot; never restore by an rfkill index.
    return kind + ":" + re.sub(r"/(?:ieee80211/phy\d+|bluetooth/hci\d+|nfc/nfc\d+)$", "", str(path))


class Failure(RuntimeError):
    pass


def run(*args, input=None, ok=False, timeout=30):
    result = subprocess.run(args, input=input, text=True, capture_output=True,
                            timeout=timeout, env=ENV, cwd="/")
    if result.returncode and not ok:
        raise Failure(f"{args[0]}: {result.stderr.strip() or result.stdout.strip()}")
    return result


def atomic_json(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".faraday-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def idle_worker(home, request):
    """Only called AFTER dropping privileges to the session owner.

    Legacy recovery only: restore fields recorded by 0.1.0/0.1.1 snapshots,
    preserving unrelated edits. New activations never call this worker.
    """
    config = Path(home) / ".config/omarchy/shell.json"
    indicator = Path(home) / ".local/state/omarchy/indicators/stay-awake"
    current = read_json(config)
    if not isinstance(current, dict) or current.get("version") != 1:
        raise Failure("A valid Omarchy shell.json (version 1) is required")
    idle = current.get("idle", {})
    if not isinstance(idle, dict):
        raise Failure("shell.json idle must be an object")
    action = request["action"]
    if action == "capture":
        return {"idle_present": "idle" in current, "lock_present": "lock" in idle,
                "lock": idle.get("lock"), "awake_present": indicator.exists(),
                "awake": base64.b64encode(indicator.read_bytes()).decode()
                if indicator.exists() else ""}
    if action == "restore":
        saved = request["snapshot"]
        if saved["lock_present"]:
            current.setdefault("idle", {})["lock"] = saved["lock"]
        else:
            current.get("idle", {}).pop("lock", None)
            if not saved["idle_present"] and not current.get("idle"):
                current.pop("idle", None)
        atomic_json(config, current, config.stat().st_mode & 0o777)
        if saved["awake_present"]:
            indicator.parent.mkdir(parents=True, exist_ok=True)
            indicator.write_bytes(base64.b64decode(saved["awake"]))
        else:
            indicator.unlink(missing_ok=True)
    else:
        raise Failure("Unknown idle operation")
    return True


def firewall_rules(mode, wifi_interfaces=()):
    """Owned tables only. No flush ruleset and no changes to UFW/firewalld.

    Conntrack reply direction prevents existing inbound sessions bypassing the
    lockdown. Manual Wi-Fi allows DHCP and IPv6 link maintenance, not listeners.
    """
    incoming = 'iifname "lo" accept;'
    outgoing = 'oifname "lo" accept;'
    if mode == "manual-wifi":
        if not wifi_interfaces:
            raise Failure("No managed Wi-Fi adapter is available")
        names = ", ".join(json.dumps(name) for name in sorted(wifi_interfaces))
        incoming += f"""
        iifname {{ {names} }} ct direction reply ct state established,related accept;
        iifname {{ {names} }} udp sport 67 udp dport 68 accept;
        iifname {{ {names} }} ip6 saddr fe80::/10 udp sport 547 udp dport 546 accept;
        iifname {{ {names} }} ip6 hoplimit 255 icmpv6 type {{ nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert }} accept;
        """
        outgoing += f"oifname {{ {names} }} accept;"
    return f"""table inet faraday {{
      chain input {{ type filter hook input priority -310; policy drop; {incoming} }}
      chain output {{ type filter hook output priority -310; policy drop; {outgoing} }}
      chain forward {{ type filter hook forward priority -310; policy drop; }}
    }}
    table bridge faraday {{
      chain forward {{ type filter hook forward priority -310; policy drop; }}
    }}
    """


class Linux:
    def __init__(self, uid):
        self.uid = uid

    def idle(self, action, snapshot=None):
        account = pwd.getpwuid(self.uid)
        request = {"action": action, "snapshot": snapshot}
        result = subprocess.run(
            ["/usr/bin/python3", "-I", str(Path(__file__).resolve()), "idle-worker"],
            input=json.dumps(request), text=True, capture_output=True,
            user=self.uid, group=account.pw_gid, extra_groups=[], cwd="/",
            env={**ENV, "HOME": account.pw_dir}, timeout=15)
        if result.returncode:
            raise Failure("Idle settings: " + result.stderr.strip())
        return json.loads(result.stdout)

    def preflight(self):
        missing = [name for name in ("nft", "nmcli", "rfkill", "systemctl", "busctl")
                   if not shutil.which(name, path=ENV["PATH"])]
        if missing:
            raise Failure("Missing required tools: " + ", ".join(missing))
        if run("nmcli", "-t", "-f", "RUNNING", "general").stdout.strip() != "running":
            raise Failure("NetworkManager must be running")
        rules = json.loads(run("nft", "-j", "list", "ruleset").stdout)
        if any("flowtable" in entry for entry in rules["nftables"]):
            raise Failure("Flowtable offload is not supported; disable it before activation")
        # Probe both families before committing the original snapshot.
        run("nft", "--check", "-f", "-", input=firewall_rules("sealed"))

    def radios(self):
        rows = json.loads(run("rfkill", "--json", "--output", "ID,TYPE,DEVICE,SOFT,HARD").stdout)["rfkilldevices"]
        result = {}
        for row in rows:
            # rfkill numeric IDs are transient; bind restore to the sysfs device.
            identity = str((Path("/sys/class/rfkill") / f"rfkill{row['id']}" / "device").resolve())
            key = radio_identity(row["type"], identity)
            if key in result:
                raise Failure("Ambiguous radio identity; cannot guarantee restoration")
            result[key] = {**row, "soft": row["soft"] in (True, "blocked"),
                           "hard": row["hard"] in (True, "blocked")}
        return result

    def profiles(self):
        result = {}
        rows = run("nmcli", "-t", "-f", "UUID,TYPE", "connection", "show").stdout
        for line in rows.splitlines():
            uuid, kind = line.split(":", 1)
            if kind not in WIFI_TYPES:
                continue
            value = run("nmcli", "-g", "connection.autoconnect", "connection", "show", "uuid", uuid).stdout.strip()
            result[uuid] = value == "yes"
        return result

    def bluetooth(self):
        # Never D-Bus-activate a radio service during discovery.
        owner = json.loads(run("busctl", "--auto-start=no", "--json=short", "call",
                               "org.freedesktop.DBus", "/org/freedesktop/DBus",
                               "org.freedesktop.DBus", "NameHasOwner", "s", "org.bluez").stdout)
        if not owner["data"][0]:
            return {}
        objects = json.loads(run("busctl", "--auto-start=no", "--json=short", "call",
                                 "org.bluez", "/", "org.freedesktop.DBus.ObjectManager",
                                 "GetManagedObjects").stdout)["data"][0]
        adapters = {}
        for path, interfaces in objects.items():
            adapter = interfaces.get("org.bluez.Adapter1")
            if adapter is not None:
                address = adapter["Address"]["data"]
                adapters[address] = {"path": path, "powered": adapter["Powered"]["data"]}
        return adapters

    def set_bluetooth(self, adapter, powered):
        # Unblocking rfkill can itself power the adapter on, or leave BlueZ
        # briefly unready. Read before writing and allow the transition to settle.
        deadline = time.monotonic() + 30
        last_error = None
        while True:
            try:
                value = json.loads(run("busctl", "--auto-start=no", "--json=short", "get-property",
                                       "org.bluez", adapter["path"], "org.bluez.Adapter1", "Powered",
                                       timeout=10).stdout)["data"]
                if value == powered:
                    return
                run("busctl", "--auto-start=no", "set-property", "org.bluez", adapter["path"],
                    "org.bluez.Adapter1", "Powered", "b", "true" if powered else "false", timeout=10)
            except (Failure, subprocess.TimeoutExpired) as exc:
                last_error = exc
                if any(marker in str(exc).lower() for marker in ("accessdenied", "access denied", "not authorized", "permission denied")):
                    raise
            if time.monotonic() >= deadline:
                raise Failure("Bluetooth did not reach its requested power state after 30 seconds"
                              + (f": {last_error}" if last_error else ""))
            time.sleep(1)

    def devices(self):
        result = {}
        names = run("nmcli", "-g", "DEVICE", "device", "status").stdout.splitlines()
        for name in names:
            kind = run("nmcli", "-g", "GENERAL.TYPE", "device", "show", name).stdout.strip()
            if kind != "wifi":
                continue
            managed = run("nmcli", "-g", "GENERAL.NM-MANAGED", "device", "show", name).stdout.strip()
            if managed != "yes":
                raise Failure(f"Wi-Fi device {name} is not managed by NetworkManager")
            auto = run("nmcli", "-g", "GENERAL.AUTOCONNECT", "device", "show", name).stdout.strip()
            identity = str((Path("/sys/class/net") / name / "device").resolve())
            result[identity] = {"name": name, "autoconnect": auto == "yes"}
        return result

    def manual_supported(self):
        # IWD can independently autojoin, ignoring NetworkManager's per-profile
        # policy. Never claim deliberate-only Wi-Fi on an unverified backend.
        if run("systemctl", "is-active", "--quiet", "iwd.service", ok=True).returncode == 0:
            raise Failure("Manual Wi-Fi currently requires NetworkManager with wpa_supplicant; IWD has an independent autojoin policy")

    def nm_radios(self):
        return {kind: run("nmcli", "radio", kind).stdout.strip() == "enabled"
                for kind in ("wifi", "wwan")}

    def active_connections(self):
        # UUIDs, not SSIDs, so names containing delimiters cannot affect parsing.
        rows = run("nmcli", "-t", "-f", "UUID,TYPE", "connection", "show", "--active").stdout
        return [line.split(":", 1)[0] for line in rows.splitlines()
                if line.split(":", 1)[1] in WIFI_TYPES | {"gsm", "cdma"}]

    def set_radio(self, radio, blocked):
        run("rfkill", "block" if blocked else "unblock", str(radio["id"]))

    def set_nm_radio(self, kind, on):
        run("nmcli", "radio", kind, "on" if on else "off")

    def set_profile(self, uuid, on):
        run("nmcli", "connection", "modify", "uuid", uuid, "connection.autoconnect", "yes" if on else "no")

    def set_device(self, name, on):
        run("nmcli", "device", "set", name, "autoconnect", "yes" if on else "no")

    def disconnect(self, uuid):
        run("nmcli", "--wait", "10", "connection", "down", "uuid", uuid)

    def reconnect(self, uuid):
        def state():
            return run("nmcli", "-g", "GENERAL.STATE", "connection", "show", "uuid", uuid).stdout.strip()

        current = state()
        # Restoring radio flags can already trigger NetworkManager activation.
        # Reissuing 'connection up' restarts a healthy connection, including a
        # cellular registration that can take much longer than a Wi-Fi join.
        deadline = time.monotonic() + 90
        while current == "activating" and time.monotonic() < deadline:
            time.sleep(1)
            current = state()
        if current == "activated":
            return
        if current == "activating":
            raise Failure("Connection is still activating; retry restore once it has connected")
        try:
            run("nmcli", "--wait", "90", "connection", "up", "uuid", uuid, timeout=95)
        except (Failure, subprocess.TimeoutExpired):
            # A CLI timeout is not proof the asynchronous activation failed.
            if state() == "activated":
                return
            raise
        if state() != "activated":
            raise Failure("NetworkManager has not confirmed the connection is activated")

    def table_state(self):
        result = {}
        for family in ("inet", "bridge"):
            # Listing tables must succeed; permission errors are not absence.
            tables = json.loads(run("nft", "-j", "list", "tables", family).stdout)
            exists = any(e.get("table", {}).get("name") == "faraday" for e in tables["nftables"])
            if exists:
                value = run("nft", "list", "table", family, "faraday").stdout
                result[family] = hashlib.sha256(value.encode()).hexdigest()
        return result

    def firewall(self, mode, devices):
        present = self.table_state()
        prefix = "".join(f"delete table {family} faraday\n" for family in present)
        rules = prefix + firewall_rules(mode, [d["name"] for d in devices.values()])
        run("nft", "--check", "-f", "-", input=rules)
        run("nft", "-f", "-", input=rules)
        return self.table_state()

    def remove_firewall(self):
        present = self.table_state()
        if present:
            run("nft", "-f", "-", input="".join(f"delete table {f} faraday\n" for f in present))


class Controller:
    def __init__(self, system, directory=STATE, public=PUBLIC):
        self.system = system
        self.directory = Path(directory)
        self.path = self.directory / "snapshot.json"
        self.public = Path(public)
        self.state = read_json(self.path)
        self.working = False
        self.operation = ""
        self.progress = ""
        self.started = None
        self.last_errors = list(self.state.get("errors", [])) if self.state else []
        self.publish_lock = threading.RLock()

    def save(self):
        atomic_json(self.path, self.state)

    def publish(self, errors=None):
        with self.publish_lock:
            if errors is not None:
                self.last_errors = list(errors)
            s = self.state
            status = {"version": VERSION, "updated": time.time(), "mode": s["mode"] if s else "off",
                      "phase": s["phase"] if s else "off", "owner": s["uid"] if s else None,
                      "errors": self.last_errors[:], "snapshot": bool(s),
                      "working": self.working, "operation": self.operation,
                      "progress": self.progress, "started": self.started,
                      "healthy": not self.last_errors and (not s or s["phase"] == "active")}
            atomic_json(self.public, status, 0o644)
            return status

    @contextlib.contextmanager
    def activity(self, operation, interval=2):
        """Keep a heartbeat even while nmcli/BlueZ holds the controller lock."""
        self.working = True
        self.operation = operation
        self.started = time.time()
        self.progress = "Restoring your original settings…" if operation == "restoring" else "Applying protection…"
        self.publish([])
        stopped = threading.Event()

        def heartbeat():
            while not stopped.wait(interval):
                try:
                    self.publish()
                except OSError:
                    # A failed status write must not stop restoration itself.
                    # The UI will correctly become stale if writes keep failing.
                    pass

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            yield
        except Exception as exc:
            self.publish([str(exc)])
            raise
        finally:
            stopped.set()
            thread.join()
            self.working = False
            self.operation = ""
            self.progress = ""
            self.started = None
            self.publish()

    def report_step(self, key):
        messages = {"restore-barrier": "Preparing restore…", "disconnect": "Releasing current connections…",
                    "idle": "Restoring your screen-lock settings…", "nm": "Restoring wireless radios…",
                    "radio": "Restoring radio settings…", "bluetooth": "Restoring Bluetooth…",
                    "profile": "Restoring Wi-Fi preferences…", "device": "Restoring Wi-Fi adapters…",
                    "firewall": "Restoring your firewall…", "connection": "Reconnecting your original networks…"}
        self.progress = messages.get(key.split(":", 1)[0], "Restoring your original settings…")
        self.publish()

    def capture_new(self):
        """Journal hotplugged devices and newly saved profiles BEFORE touching them."""
        s = self.state
        radios, profiles, devices = self.system.radios(), self.system.profiles(), self.system.devices()
        for field, values in (("radios", radios), ("profiles", profiles), ("devices", devices),
                              ("bluetooth", self.system.bluetooth())):
            s.setdefault(field, {})
            for key, value in values.items():
                if key not in s[field]:
                    s[field][key] = value
        self.save()
        return radios, profiles, devices

    def activate(self, mode):
        with self.activity("applying"):
            self._activate(mode)

    def _activate(self, mode):
        if mode not in ("sealed", "manual-wifi"):
            raise Failure("Unknown mode")
        if mode == "manual-wifi":
            self.system.manual_supported()
            if not self.system.devices():
                raise Failure("No managed Wi-Fi adapter is available")
        if self.state and self.state["phase"] == "restoring":
            raise Failure("Finish restoring the original state before activating")
        if not self.state:
            if self.system.table_state():
                raise Failure("An existing faraday firewall table has no snapshot; refusing to take ownership")
            self.system.preflight()
            self.state = {"schema": 1, "uid": self.system.uid, "mode": mode, "phase": "applying",
                          "created": time.time(), "radios": self.system.radios(),
                          "nm_radios": self.system.nm_radios(), "profiles": self.system.profiles(),
                          "devices": self.system.devices(), "active": self.system.active_connections(),
                          "bluetooth": self.system.bluetooth(),
                          "firewall": {}, "restored": []}
            self.save()
        self.state["mode"] = mode
        self.state["phase"] = "applying"
        self.state["needs_disconnect"] = True
        self.save()
        self.publish([])
        try:
            self.enforce()
        except Exception as exc:
            self.state["phase"] = "error"
            self.save()
            self.publish([str(exc)])
            raise

    def enforce(self):
        s = self.state
        if s and s["phase"] == "restored":
            os.replace(self.path, self.directory / "last-restored.json")
            self.state = None
            return self.publish()
        if not s or s["phase"] == "restoring":
            return self.publish(s.get("errors", []) if s else [])
        # This intent is persisted, so a process crash cannot skip disconnecting
        # an existing Wi-Fi session before entering deliberate-connection mode.
        if s.get("needs_disconnect"):
            s["firewall"] = self.system.firewall("sealed", {})
            s.pop("signature", None)
            self.save()
        radios, profiles, devices = self.capture_new()
        mode = s["mode"]
        if mode == "manual-wifi":
            try:
                self.system.manual_supported()
            except Exception:
                s["firewall"] = self.system.firewall("sealed", {})
                self.system.set_nm_radio("wifi", False)
                self.save()
                raise
        # Device autoconnect blocks new profiles too; profile policy persists on disk.
        for device in devices.values():
            if device["autoconnect"]:
                self.system.set_device(device["name"], False)
        for uuid, autoconnect in profiles.items():
            if autoconnect:
                self.system.set_profile(uuid, False)
        if s.get("needs_disconnect"):
            for uuid in self.system.active_connections():
                self.system.disconnect(uuid)
            s["needs_disconnect"] = False
            self.save()
        for adapter in self.system.bluetooth().values():
            if adapter["powered"]:
                self.system.set_bluetooth(adapter, False)
        for kind, value in self.system.nm_radios().items():
            wanted = kind == "wifi" and mode == "manual-wifi"
            if wanted != value:
                self.system.set_nm_radio(kind, wanted)
        for radio in radios.values():
            blocked = not (mode == "manual-wifi" and radio["type"] == "wlan")
            if radio["soft"] != blocked:
                self.system.set_radio(radio, blocked)
        signature = [mode, sorted(d["name"] for d in devices.values())]
        if self.system.table_state() != s["firewall"] or s.get("signature") != signature:
            s["firewall"] = self.system.firewall(mode, devices)
            s["signature"] = signature
            self.save()
        errors = self.verify()
        s["phase"] = "error" if errors else "active"
        self.save()
        self.publish(errors)
        if errors:
            raise Failure("; ".join(errors))

    def verify(self):
        errors = []
        mode = self.state["mode"]
        if self.system.table_state() != self.state["firewall"] or len(self.state["firewall"]) != 2:
            errors.append("Firewall verification failed")
        for radio in self.system.radios().values():
            blocked = not (mode == "manual-wifi" and radio["type"] == "wlan")
            if radio["soft"] != blocked:
                errors.append(f"Radio policy failed: {radio['type']}")
        for kind, value in self.system.nm_radios().items():
            if value != (kind == "wifi" and mode == "manual-wifi"):
                errors.append(f"NetworkManager {kind} policy failed")
        if any(self.system.profiles().values()) or any(d["autoconnect"] for d in self.system.devices().values()):
            errors.append("Wi-Fi autoconnect is enabled")
        if any(adapter["powered"] for adapter in self.system.bluetooth().values()):
            errors.append("Bluetooth adapter is still powered on")
        return errors

    def boot(self):
        """Run before network services. A reboot always returns active mode to Sealed."""
        if not self.state:
            return
        if self.state["phase"] == "restored":
            self.enforce()
            return
        s = self.state
        # A retry after reboot must replay all restoration operations, including
        # removing the new boot-time barrier.
        if s["phase"] == "restoring":
            s["restored"] = []
        else:
            s["phase"] = "applying"
            s["mode"] = "sealed"
        self.save()
        s["firewall"] = self.system.firewall("sealed", {})
        self.save()
        radios = self.system.radios()
        for key, radio in radios.items():
            if key not in s["radios"]:
                s["radios"][key] = radio
        self.save()
        for radio in radios.values():
            self.system.set_radio(radio, True)
        self.publish()

    def restore(self):
        with self.activity("restoring"):
            self._restore()
        return self.publish()

    def _restore(self):
        if not self.state:
            return self.publish([])
        s = self.state
        s["phase"] = "restoring"
        s["errors"] = []
        self.save()
        self.publish()
        errors = []

        def step(key, operation):
            if key in s["restored"]:
                return
            self.report_step(key)
            try:
                operation()
                s["restored"].append(key)
                self.save()
            except Exception as exc:
                errors.append(f"{key}: {exc}")

        # Retry only unfinished steps, so a reconnect failure doesn't replay every change.
        step("restore-barrier", lambda: self.system.firewall("sealed", {}))
        def disconnect_current():
            for uuid in self.system.active_connections():
                self.system.disconnect(uuid)
        step("disconnect", disconnect_current)
        # Compatibility: return settings changed by 0.1.0/0.1.1, but never
        # capture or modify idle settings for new activations.
        if "idle" in s:
            step("idle", lambda: self.system.idle("restore", s["idle"]))
        try:
            radios, profiles, devices = self.system.radios(), self.system.profiles(), self.system.devices()
        except Exception as exc:
            s["errors"] = [str(exc)]
            self.save()
            return self.publish(s["errors"])
        for kind, enabled in s["nm_radios"].items():
            step("nm:" + kind, lambda k=kind, v=enabled: self.system.set_nm_radio(k, v))
        for key, saved in s["radios"].items():
            if key not in radios:
                if "radio:" + key not in s["restored"]:
                    errors.append(f"Radio missing; reconnect it to restore: {saved['device']}")
                continue
            step("radio:" + key, lambda k=key, v=saved: self.system.set_radio(radios[k], v["soft"]))
        try:
            bluetooth = self.system.bluetooth()
            for address, saved in s.get("bluetooth", {}).items():
                if address not in bluetooth:
                    errors.append("Bluetooth adapter missing; reconnect it to restore its power state")
                    continue
                step("bluetooth:" + address,
                     lambda a=address, v=saved: self.system.set_bluetooth(bluetooth[a], v["powered"]))
        except Exception as exc:
            errors.append("Bluetooth restore: " + str(exc))
        for uuid, value in s["profiles"].items():
            # A deliberately deleted profile has no setting left to restore; never recreate secrets.
            if uuid in profiles:
                step("profile:" + uuid, lambda u=uuid, v=value: self.system.set_profile(u, v))
        for key, saved in s["devices"].items():
            if key not in devices:
                if "device:" + key not in s["restored"]:
                    errors.append("Wi-Fi adapter missing; reconnect it to restore device settings")
                continue
            step("device:" + key, lambda k=key, v=saved: self.system.set_device(devices[k]["name"], v["autoconnect"]))
        # Leave the firewall barrier in place until the configuration has been restored.
        if not errors:
            step("firewall", self.system.remove_firewall)
        if not errors:
            for uuid in s["active"]:
                step("connection:" + uuid, lambda u=uuid: self.system.reconnect(u))
        if not errors:
            self.progress = "Checking your restored settings…"
            self.publish()
            errors = self.verify_restored()
            if errors:
                # Readback found drift despite command success; replay restoration
                # on retry rather than trusting the completed-step journal.
                s["restored"] = []
        if errors:
            s["errors"] = errors
            self.save()
            return self.publish(errors)
        s["phase"] = "restored"
        self.save()
        # Keep the latest completed journal for diagnosis; don't expose its contents.
        os.replace(self.path, self.directory / "last-restored.json")
        self.state = None
        return self.publish([])

    def verify_restored(self):
        s = self.state
        errors = []
        if self.system.table_state():
            errors.append("Faraday firewall rules are still present")
        if self.system.nm_radios() != s["nm_radios"]:
            errors.append("Original NetworkManager radio state did not restore")
        radios, profiles, devices = self.system.radios(), self.system.profiles(), self.system.devices()
        bluetooth = self.system.bluetooth()
        for address, saved in s.get("bluetooth", {}).items():
            if address not in bluetooth or bluetooth[address]["powered"] != saved["powered"]:
                errors.append("Original Bluetooth power state did not restore")
        for key, saved in s["radios"].items():
            if key not in radios or radios[key]["soft"] != saved["soft"]:
                errors.append("Original radio state did not restore: " + saved["device"])
        for uuid, auto in s["profiles"].items():
            if uuid in profiles and profiles[uuid] != auto:
                errors.append("Original Wi-Fi autoconnect setting did not restore")
        for key, saved in s["devices"].items():
            if key not in devices or devices[key]["autoconnect"] != saved["autoconnect"]:
                errors.append("Original Wi-Fi adapter policy did not restore")
        if "idle" in s:
            idle = self.system.idle("capture")
            for key in ("lock_present", "lock", "awake_present", "awake"):
                if idle.get(key) != s["idle"].get(key):
                    errors.append("Original idle settings did not restore")
                    break
        return errors


@contextlib.contextmanager
def controller_lock():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    with (STATE / "controller.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def doctor():
    dependencies = {name: shutil.which(name, path=ENV["PATH"]) is not None
                    for name in ("python3", "nft", "nmcli", "rfkill", "pkexec", "systemctl", "busctl", "omarchy-shell")}
    return {"version": VERSION, "dependencies": dependencies,
            "supported": all(dependencies.values()),
            "note": "Activation also checks privileges, NetworkManager, and firewall support."}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Faraday laptop isolation controller")
    parser.add_argument("action", choices=["sealed", "manual-wifi", "off", "status", "doctor", "watch", "boot", "idle-worker"])
    args = parser.parse_args(argv)
    if args.action == "idle-worker":
        if os.geteuid() == 0:
            raise Failure("Idle file operations must run as the desktop user")
        print(json.dumps(idle_worker(pwd.getpwuid(os.geteuid()).pw_dir, json.load(sys.stdin))))
        return 0
    if args.action == "doctor":
        print(json.dumps(doctor()))
        return 0
    if args.action == "status":
        status = read_json(PUBLIC, {"mode": "off", "phase": "unavailable", "healthy": False,
                                    "errors": ["Install and start the Faraday helper"], "snapshot": False})
        if time.time() - status.get("updated", 0) > 15:
            status.update(healthy=False, phase="unavailable", errors=["Faraday monitor is not responding"])
        print(json.dumps(status))
        return 0
    if os.geteuid() != 0:
        raise Failure("Use the widget, or sudo faraday-helper <mode>")
    if args.action == "boot":
        with controller_lock():
            state = read_json(STATE / "snapshot.json")
            Controller(Linux(state["uid"] if state else 0)).boot()
        return 0
    if args.action == "watch":
        while True:
            with controller_lock():
                state = read_json(STATE / "snapshot.json")
                ctl = Controller(Linux(state["uid"] if state else 0))
                try:
                    ctl.enforce()
                except Exception as exc:
                    if ctl.state:
                        ctl.state["phase"] = "error"
                        ctl.save()
                    ctl.publish([str(exc)])
            time.sleep(2)
    uid = int(os.environ.get("PKEXEC_UID", os.environ.get("SUDO_UID", "0")))
    with controller_lock():
        state = read_json(STATE / "snapshot.json")
        if state and uid not in (0, state["uid"]):
            raise Failure("Faraday is owned by another user's session")
        owner = state["uid"] if state else uid
        if not owner:
            raise Failure("Activate from the desktop account using pkexec or sudo")
        ctl = Controller(Linux(owner))
        if args.action == "off":
            result = ctl.restore()
            print(json.dumps(result))
            return 0 if result["healthy"] else 1
        ctl.activate(args.action)
        print(json.dumps(read_json(PUBLIC)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Failure, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
