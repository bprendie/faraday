"""Release lifecycle guards, without root or changes to the host installation."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


helper = module('install_helper')
plugin = module('install_plugin')


class InstallationTests(unittest.TestCase):
    def test_snapshot_refuses_before_any_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            (state / 'snapshot.json').write_text('{}')
            run = Mock()
            with self.assertRaisesRegex(RuntimeError, 'Normal'):
                helper.require_normal(state, run)
            run.assert_not_called()

    def test_orphan_tables_refuse_and_unrelated_tables_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            for family in ('inet', 'bridge'):
                run = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({
                    'nftables': [{'table': {'family': family, 'name': 'faraday'}}]})))
                with self.assertRaisesRegex(RuntimeError, 'tables remain'):
                    helper.require_normal(Path(temporary), run)
            run.return_value.stdout = '{"nftables":[{"table":{"family":"inet","name":"ufw"}}]}'
            helper.require_normal(Path(temporary), run)

    def test_failed_firewall_inspection_is_not_assumed_normal(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Mock(side_effect=subprocess.CalledProcessError(1, 'nft'))
            with self.assertRaises(subprocess.CalledProcessError):
                helper.require_normal(Path(temporary), run)

    def test_release_copy_and_upgrade_preserve_source_and_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            dest = Path(temporary) / 'plugins' / 'faraday'
            dest.parent.mkdir()
            dest.symlink_to(ROOT, target_is_directory=True)
            plugin.copy_plugin(ROOT, dest)
            self.assertFalse(dest.is_symlink())
            self.assertTrue((dest / 'uninstall.sh').is_file())
            self.assertFalse((dest / 'output').exists())
            self.assertFalse((dest / '.git').exists())
            self.assertFalse(any(p.is_symlink() for p in dest.rglob('*')))
            backup = dest.parent.parent / 'faraday-backups' / 'faraday.previous'
            self.assertEqual(backup.resolve(), ROOT)
            with self.assertRaisesRegex(RuntimeError, 'backup'):
                plugin.copy_plugin(ROOT, dest)
            self.assertTrue((dest / 'Panel.qml').is_file())
            subprocess.run(['omarchy', 'plugin', 'validate', str(dest)], check=True,
                           capture_output=True)

    def test_unmanaged_checkout_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            dest = Path(temporary) / 'plugins' / 'faraday'
            dest.mkdir(parents=True)
            (dest / 'custom').write_text('keep')
            with self.assertRaisesRegex(RuntimeError, 'unmanaged'):
                plugin.copy_plugin(ROOT, dest)
            self.assertEqual((dest / 'custom').read_text(), 'keep')

    def test_linked_source_rejected_before_replacing_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source'
            source.mkdir()
            (source / 'manifest.json').symlink_to(ROOT / 'manifest.json')
            dest = Path(temporary) / 'plugins' / 'faraday'
            with self.assertRaisesRegex(RuntimeError, 'symlinks'):
                plugin.copy_plugin(source, dest)
            self.assertFalse(dest.exists())


if __name__ == '__main__':
    unittest.main()
