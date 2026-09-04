import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

SPEC = importlib.util.spec_from_file_location("faraday", Path(__file__).resolve().parents[1] / "backend/faraday.py")
faraday = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(faraday)


class FakeLinux:
    uid = 1000

    def __init__(self):
        self.radio_data = {
            "wifi": {"id": 4, "device": "phy3", "type": "wlan", "soft": False, "hard": False},
            "bt": {"id": 7, "device": "hci1", "type": "bluetooth", "soft": True, "hard": False},
            "nfc": {"id": 9, "device": "nfc2", "type": "nfc", "soft": False, "hard": False}}
        self.profile_data = {"uuid-a": True, "uuid-b": False}
        self.bluetooth_data = {"adapter-a": {"path": "/org/bluez/hci1", "powered": False},
                               "adapter-b": {"path": "/org/bluez/hci2", "powered": True}}
        self.device_data = {"pci-path": {"name": "wlp42s0", "autoconnect": True}}
        self.nm_data = {"wifi": True, "wwan": False}
        self.idle_data = {"lock": 317, "awake": True}
        self.connections = ["uuid-a"]
        self.tables = {}
        self.fail = set()
        self.events = []
        self.iwd = False

    def event(self, action):
        self.events.append(action)
        if action in self.fail:
            raise faraday.Failure("injected " + action)

    def preflight(self): self.event("preflight")
    def manual_supported(self):
        if self.iwd: raise faraday.Failure("IWD unsupported")
    def radios(self): return copy.deepcopy(self.radio_data)
    def profiles(self): return self.profile_data.copy()
    def devices(self): return copy.deepcopy(self.device_data)
    def nm_radios(self): return self.nm_data.copy()
    def active_connections(self): return self.connections[:]
    def table_state(self): return self.tables.copy()
    def bluetooth(self): return copy.deepcopy(self.bluetooth_data)

    def set_bluetooth(self, adapter, powered):
        self.event("bluetooth:" + adapter["path"])
        for row in self.bluetooth_data.values():
            if row["path"] == adapter["path"]:
                row["powered"] = powered

    def idle(self, action, snapshot=None):
        self.event("idle:" + action)
        if action == "capture": return self.idle_data.copy()
        if action == "restore": self.idle_data = snapshot.copy()

    def set_radio(self, radio, blocked):
        self.event("radio:" + radio["type"])
        for row in self.radio_data.values():
            if row["id"] == radio["id"]: row["soft"] = blocked

    def set_nm_radio(self, kind, on):
        self.event("nm:" + kind)
        self.nm_data[kind] = on

    def set_profile(self, uuid, on):
        self.event("profile:" + uuid)
        self.profile_data[uuid] = on

    def set_device(self, name, on):
        self.event("device:" + name)
        for row in self.device_data.values():
            if row["name"] == name: row["autoconnect"] = on

    def disconnect(self, uuid):
        self.event("disconnect:" + uuid)
        self.connections.remove(uuid)

    def reconnect(self, uuid):
        self.event("reconnect:" + uuid)
        if uuid not in self.connections: self.connections.append(uuid)

    def firewall(self, mode, devices):
        self.event("firewall:" + mode)
        self.tables = {"inet": mode, "bridge": "drop"}
        return self.tables.copy()

    def remove_firewall(self):
        self.event("firewall:remove")
        self.tables = {}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.system = FakeLinux()
        self.ctl = self.controller()

    def controller(self):
        return faraday.Controller(self.system, self.base / "private", self.base / "public/status.json")

    def test_round_trip_mixed_original_settings_and_modes(self):
        original = copy.deepcopy(self.system.__dict__)
        self.ctl.activate("sealed")
        snapshot = copy.deepcopy(self.ctl.state)
        self.assertTrue(all(v["soft"] for v in self.system.radios().values()))
        self.ctl.activate("manual-wifi")
        self.assertEqual(self.ctl.state["radios"], snapshot["radios"])
        self.assertNotIn("idle", self.ctl.state)
        self.assertFalse(self.system.radio_data["wifi"]["soft"])
        self.assertFalse(any(self.system.profiles().values()))
        self.assertFalse(self.system.connections)
        self.assertEqual(self.system.tables["inet"], "manual-wifi")
        self.assertTrue(self.ctl.restore()["healthy"])
        for field in ("radio_data", "bluetooth_data", "profile_data", "device_data", "nm_data", "idle_data", "connections", "tables"):
            self.assertEqual(getattr(self.system, field), original[field], field)
        self.assertIsNone(self.controller().state)

    def test_snapshot_exists_before_first_mutation(self):
        firewall = self.system.firewall
        def assert_snapshot(mode, devices):
            snapshot = json.loads(self.ctl.path.read_text())
            self.assertEqual(snapshot["nm_radios"], {"wifi": True, "wwan": False})
            return firewall(mode, devices)
        self.system.firewall = assert_snapshot
        self.ctl.activate("sealed")

    def test_partial_activation_retains_snapshot_and_can_restore(self):
        self.system.fail.add("radio:nfc")
        with self.assertRaises(faraday.Failure): self.ctl.activate("sealed")
        self.assertEqual(self.controller().state["phase"], "error")
        self.system.fail.clear()
        self.assertTrue(self.controller().restore()["healthy"])
        self.assertEqual(self.system.idle_data["lock"], 317)

    def test_restore_failure_retries_after_process_restart(self):
        self.ctl.activate("sealed")
        self.system.fail.add("profile:uuid-a")
        result = self.ctl.restore()
        self.assertFalse(result["healthy"])
        self.assertEqual(result["phase"], "restoring")
        self.assertTrue(self.system.tables)
        self.system.fail.clear()
        new = self.controller()
        self.system.events.clear()
        self.assertTrue(new.restore()["healthy"])
        self.assertNotIn("idle:restore", self.system.events)

    def test_radio_id_changes_restore_using_stable_identity(self):
        self.ctl.activate("sealed")
        self.system.radio_data["wifi"]["id"] = 91
        self.assertTrue(self.ctl.restore()["healthy"])
        self.assertFalse(self.system.radio_data["wifi"]["soft"])

    def test_hotplug_snapshot_captured_before_changes(self):
        self.ctl.activate("sealed")
        self.system.radio_data["new"] = {"id": 28, "type": "wwan", "device": "modem", "soft": False, "hard": False}
        self.system.profile_data["new-profile"] = True
        self.ctl.enforce()
        self.assertFalse(self.ctl.state["radios"]["new"]["soft"])
        self.assertTrue(self.ctl.state["profiles"]["new-profile"])
        self.assertTrue(self.system.radio_data["new"]["soft"])
        self.ctl.restore()
        self.assertFalse(self.system.radio_data["new"]["soft"])
        self.assertTrue(self.system.profile_data["new-profile"])

    def test_missing_radio_does_not_report_success(self):
        self.ctl.activate("sealed")
        radio = self.system.radio_data.pop("bt")
        self.assertFalse(self.ctl.restore()["healthy"])
        self.system.radio_data["bt"] = radio
        self.assertTrue(self.controller().restore()["healthy"])

    def test_firewall_drift_is_repaired(self):
        self.ctl.activate("sealed")
        self.system.tables = {}
        self.ctl.enforce()
        self.assertEqual(self.system.tables["inet"], "sealed")

    def test_no_wifi_manual_refuses_before_mutating(self):
        self.system.device_data.clear()
        with self.assertRaises(faraday.Failure): self.ctl.activate("manual-wifi")
        self.assertIsNone(self.controller().state)

    def test_existing_owned_name_is_not_overwritten(self):
        self.system.tables = {"inet": "somebody else's rules"}
        with self.assertRaises(faraday.Failure): self.ctl.activate("sealed")
        self.assertFalse(self.ctl.path.exists())

    def test_iwd_manual_refuses_before_mutating(self):
        self.system.iwd = True
        with self.assertRaises(faraday.Failure): self.ctl.activate("manual-wifi")
        self.assertIsNone(self.controller().state)

    def test_restore_disconnects_wifi_selected_while_active(self):
        self.system.connections = []
        self.ctl.activate("manual-wifi")
        self.system.connections = ["uuid-b"]
        self.ctl.restore()
        self.assertEqual(self.system.connections, [])

    def test_reconnect_failure_keeps_recovery_available(self):
        self.ctl.activate("sealed")
        self.system.fail.add("reconnect:uuid-a")
        result = self.ctl.restore()
        self.assertFalse(result["healthy"])
        self.assertEqual(self.system.tables, {})
        self.system.fail.clear()
        self.assertTrue(self.controller().restore()["healthy"])

    def test_no_radios_sealed_works(self):
        self.system.radio_data = {}
        self.system.device_data = {}
        self.ctl.activate("sealed")
        self.assertTrue(self.ctl.publish()["healthy"])
        self.assertTrue(self.ctl.restore()["healthy"])

    def test_public_status_has_no_profile_or_device_identifiers(self):
        self.ctl.activate("sealed")
        public = self.ctl.public.read_text()
        self.assertNotIn("uuid-a", public)
        self.assertNotIn("wlp42s0", public)
        self.assertEqual(self.ctl.path.stat().st_mode & 0o777, 0o600)

    def test_boot_seals_manual_mode_without_replacing_original(self):
        self.ctl.activate("manual-wifi")
        original = copy.deepcopy(self.ctl.state["nm_radios"])
        self.controller().boot()
        new = self.controller()
        self.assertEqual(new.state["mode"], "sealed")
        self.assertEqual(new.state["nm_radios"], original)
        self.assertEqual(self.system.tables["inet"], "sealed")
        self.assertTrue(new.restore()["healthy"])

    def test_boot_during_failed_reconnect_replays_restore(self):
        self.ctl.activate("sealed")
        self.system.fail.add("reconnect:uuid-a")
        self.ctl.restore()
        self.system.fail.clear()
        self.controller().boot()
        new = self.controller()
        self.assertEqual(new.state["phase"], "restoring")
        self.assertEqual(new.state["restored"], [])
        self.assertTrue(new.restore()["healthy"])
        self.assertEqual(self.system.tables, {})

    def test_completed_restore_journal_is_finalized_after_crash(self):
        self.ctl.activate("sealed")
        self.ctl.state["phase"] = "restored"
        self.ctl.save()
        self.system.events.clear()
        self.controller().enforce()
        self.assertIsNone(self.controller().state)
        self.assertEqual(self.system.events, [])

    def test_restore_checks_readback_instead_of_command_exit_only(self):
        self.ctl.activate("sealed")
        self.system.set_profile = lambda uuid, on: None
        self.assertFalse(self.ctl.restore()["healthy"])
        self.assertEqual(self.controller().state["restored"], [])

    def test_radio_identity_ignores_enumeration_numbers(self):
        self.assertEqual(faraday.radio_identity("wlan", "/sys/devices/pci0/ieee80211/phy0"),
                         faraday.radio_identity("wlan", "/sys/devices/pci0/ieee80211/phy9"))
        self.assertNotEqual(faraday.radio_identity("wlan", "/sys/devices/pci0/ieee80211/phy0"),
                            faraday.radio_identity("wlan", "/sys/devices/pci1/ieee80211/phy0"))

    def test_reentering_manual_reopens_only_after_disconnect(self):
        self.ctl.activate("manual-wifi")
        self.system.connections = ["uuid-b"]
        self.ctl.activate("manual-wifi")
        self.assertEqual(self.system.connections, [])
        self.assertEqual(self.system.tables["inet"], "manual-wifi")

    def test_interrupted_transition_replays_persisted_disconnect(self):
        self.ctl.activate("sealed")
        self.ctl.state.update(mode="manual-wifi", phase="applying", needs_disconnect=True)
        self.ctl.save()
        self.system.connections = ["uuid-b"]
        self.controller().enforce()
        self.assertEqual(self.system.connections, [])
        self.assertEqual(self.system.tables["inet"], "manual-wifi")

    def test_bluetooth_is_off_in_both_modes_and_original_power_returns(self):
        original = self.system.bluetooth()
        for mode in ("sealed", "manual-wifi"):
            self.ctl.activate(mode)
            self.assertFalse(any(a["powered"] for a in self.system.bluetooth().values()))
        self.ctl.restore()
        self.assertEqual(self.system.bluetooth(), original)

    def test_bluetooth_power_drift_is_repaired(self):
        self.ctl.activate("manual-wifi")
        self.system.bluetooth_data["adapter-b"]["powered"] = True
        self.assertIn("Bluetooth adapter is still powered on", self.ctl.verify())
        self.ctl.enforce()
        self.assertFalse(self.system.bluetooth_data["adapter-b"]["powered"])

    def test_bluetooth_adapter_renumbering_preserves_original_power(self):
        self.ctl.activate("sealed")
        self.system.bluetooth_data["adapter-b"]["path"] = "/org/bluez/hci9"
        self.assertTrue(self.ctl.restore()["healthy"])
        self.assertTrue(self.system.bluetooth_data["adapter-b"]["powered"])

    def test_bluetooth_power_restore_failure_is_retryable(self):
        self.ctl.activate("sealed")
        self.system.fail.add("bluetooth:/org/bluez/hci2")
        self.assertFalse(self.ctl.restore()["healthy"])
        self.system.fail.clear()
        self.assertTrue(self.controller().restore()["healthy"])

    def test_no_bluez_service_is_supported(self):
        self.system.bluetooth_data.clear()
        self.ctl.activate("sealed")
        self.assertTrue(self.ctl.restore()["healthy"])

    def test_bluetooth_hotplug_preserves_first_seen_power(self):
        self.ctl.activate("sealed")
        self.system.bluetooth_data["new-adapter"] = {"path": "/org/bluez/hci4", "powered": True}
        self.ctl.enforce()
        self.assertFalse(self.system.bluetooth_data["new-adapter"]["powered"])
        self.assertTrue(self.ctl.state["bluetooth"]["new-adapter"]["powered"])
        self.assertTrue(self.ctl.restore()["healthy"])
        self.assertTrue(self.system.bluetooth_data["new-adapter"]["powered"])

    def test_screen_lock_and_stay_awake_are_untouched_in_every_mode(self):
        original = self.system.idle_data.copy()
        self.ctl.activate("manual-wifi")
        self.ctl.activate("sealed")
        self.ctl.restore()
        self.assertEqual(self.system.idle_data, original)
        self.assertFalse(any(event.startswith("idle:") for event in self.system.events))

    def test_legacy_snapshot_still_restores_previous_idle_settings(self):
        self.ctl.activate("sealed")
        self.ctl.state["idle"] = {"lock": 317, "awake": True}
        self.ctl.save()
        self.system.idle_data = {"lock": 20, "awake": False}
        self.assertTrue(self.controller().restore()["healthy"])
        self.assertEqual(self.system.idle_data, {"lock": 317, "awake": True})


class ActivityTests(unittest.TestCase):
    setUp = ControllerTests.setUp
    controller = ControllerTests.controller

    def test_background_heartbeat_continues_during_blocking_step(self):
        self.ctl.activate("sealed")
        observed = threading.Event()
        published = []
        original_publish = self.ctl.publish
        def publish(errors=None):
            result = original_publish(errors)
            if threading.current_thread() is not threading.main_thread():
                published.append(result)
                observed.set()
            return result
        self.ctl.publish = publish
        with self.ctl.activity("restoring", interval=0.01):
            self.ctl.report_step("connection:uuid-a")
            self.assertTrue(observed.wait(2), "heartbeat must not depend on the blocked restore loop")
            self.assertTrue(published[-1]["working"])
            self.assertEqual(published[-1]["operation"], "restoring")
        self.assertFalse(json.loads(self.ctl.public.read_text())["working"])

    def test_retry_clears_old_errors_while_work_is_running(self):
        self.ctl.activate("sealed")
        self.system.fail.add("profile:uuid-a")
        self.ctl.restore()
        self.system.fail.clear()
        new = self.controller()
        seen = []
        original_set = self.system.set_profile
        def set_profile(uuid, on):
            seen.append(json.loads(new.public.read_text()))
            original_set(uuid, on)
        self.system.set_profile = set_profile
        result = new.restore()
        self.assertTrue(result["healthy"])
        self.assertTrue(seen[0]["working"])
        self.assertEqual(seen[0]["errors"], [])
        self.assertFalse(result["working"])

    def test_real_failure_exits_working_state_and_retains_snapshot(self):
        self.ctl.activate("sealed")
        self.system.fail.add("profile:uuid-a")
        result = self.ctl.restore()
        self.assertFalse(result["working"])
        self.assertTrue(result["errors"])
        self.assertTrue(result["snapshot"])


class BluetoothTransitionTests(unittest.TestCase):
    adapter = {"path": "/org/bluez/hci0", "powered": False}

    def test_already_correct_power_state_is_not_written(self):
        with patch.object(faraday, "run", return_value=SimpleNamespace(stdout='{"data":true}')) as run:
            faraday.Linux(1000).set_bluetooth(self.adapter, True)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("set-property", run.call_args.args)

    def test_temporary_bluez_failure_waits_for_power_readback(self):
        with patch.object(faraday, "run", side_effect=[SimpleNamespace(stdout='{"data":false}'), faraday.Failure("busctl: Failed to set property Powered"), SimpleNamespace(stdout='{"data":true}')]) as run:
            with patch.object(faraday.time, "sleep"):
                faraday.Linux(1000).set_bluetooth(self.adapter, True)
        self.assertEqual(run.call_count, 3)

    def test_successful_write_requires_readback(self):
        with patch.object(faraday, "run", side_effect=[SimpleNamespace(stdout='{"data":false}'), SimpleNamespace(stdout=""), SimpleNamespace(stdout='{"data":true}')]) as run:
            with patch.object(faraday.time, "sleep"):
                faraday.Linux(1000).set_bluetooth(self.adapter, True)
        self.assertEqual(run.call_count, 3)

    def test_actual_permission_failure_is_not_suppressed(self):
        with patch.object(faraday, "run", side_effect=faraday.Failure("Access denied")):
            with self.assertRaisesRegex(faraday.Failure, "Access denied"):
                faraday.Linux(1000).set_bluetooth(self.adapter, True)

    def test_transition_that_never_finishes_reports_failure(self):
        with patch.object(faraday, "run", side_effect=[SimpleNamespace(stdout='{"data":false}'), faraday.Failure("not ready")]):
            with patch.object(faraday.time, "monotonic", side_effect=[0, 31]):
                with self.assertRaisesRegex(faraday.Failure, "after 30 seconds"):
                    faraday.Linux(1000).set_bluetooth(self.adapter, True)


class ReconnectTests(unittest.TestCase):
    def test_already_connected_profile_is_not_restarted(self):
        with patch.object(faraday, "run", return_value=SimpleNamespace(stdout="activated\n")) as run:
            faraday.Linux(1000).reconnect("profile-uuid")
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("up", run.call_args.args)

    def test_in_progress_connection_is_allowed_to_finish(self):
        with patch.object(faraday, "run", side_effect=[SimpleNamespace(stdout="activating\n"), SimpleNamespace(stdout="activated\n")]) as run:
            with patch.object(faraday.time, "sleep"):
                faraday.Linux(1000).reconnect("profile-uuid")
        self.assertTrue(all("up" not in call.args for call in run.call_args_list))

    def test_disconnected_profile_gets_longer_activation_and_readback(self):
        with patch.object(faraday, "run", side_effect=[SimpleNamespace(stdout=""), SimpleNamespace(stdout=""), SimpleNamespace(stdout="activated\n")]) as run:
            faraday.Linux(1000).reconnect("profile-uuid")
        self.assertEqual(run.call_args_list[1].args, ("nmcli", "--wait", "90", "connection", "up", "uuid", "profile-uuid"))

    def test_timeout_with_successful_readback_is_success(self):
        with patch.object(faraday, "run", side_effect=[SimpleNamespace(stdout=""), faraday.Failure("timeout"), SimpleNamespace(stdout="activated\n")]):
            faraday.Linux(1000).reconnect("profile-uuid")

    def test_real_connection_failure_still_fails_restore(self):
        with patch.object(faraday, "run", side_effect=[SimpleNamespace(stdout=""), faraday.Failure("timeout"), SimpleNamespace(stdout="")]):
            with self.assertRaises(faraday.Failure):
                faraday.Linux(1000).reconnect("profile-uuid")

    def test_successful_command_without_activation_is_not_success(self):
        with patch.object(faraday, "run", side_effect=[SimpleNamespace(stdout=""), SimpleNamespace(stdout=""), SimpleNamespace(stdout="activating\n")]):
            with self.assertRaises(faraday.Failure):
                faraday.Linux(1000).reconnect("profile-uuid")


class LegacyIdleTests(unittest.TestCase):
    def test_restores_missing_lock_without_losing_unrelated_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".config/omarchy/shell.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"version":1,"bar":{"position":"top"}}')
            snapshot = faraday.idle_worker(directory, {"action": "capture"})
            current = json.loads(path.read_text())
            current["bar"]["position"] = "bottom"
            current["idle"] = {"lock": 20, "screensaver": 99}
            path.write_text(json.dumps(current))
            faraday.idle_worker(directory, {"action": "restore", "snapshot": snapshot})
            self.assertEqual(json.loads(path.read_text()), {"version": 1, "bar": {"position": "bottom"}, "idle": {"screensaver": 99}})

    def test_preserves_custom_lock_and_stay_awake(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".config/omarchy/shell.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"version":1,"idle":{"lock":42}}')
            awake = Path(directory) / ".local/state/omarchy/indicators/stay-awake"
            awake.parent.mkdir(parents=True)
            awake.write_bytes(b"original\x00")
            snapshot = faraday.idle_worker(directory, {"action": "capture"})
            path.write_text('{"version":1,"idle":{"lock":20}}')
            awake.unlink()
            faraday.idle_worker(directory, {"action": "restore", "snapshot": snapshot})
            self.assertEqual(json.loads(path.read_text())["idle"]["lock"], 42)
            self.assertEqual(awake.read_bytes(), b"original\x00")


if __name__ == "__main__":
    unittest.main()
