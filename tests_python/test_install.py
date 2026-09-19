import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from astra_luna import install as i
from astra_luna.platform import encode, read


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home space 中文'
        self.home.mkdir()
        self.source = self.base / 'source'
        for name in i.RUNTIME_FILES:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(read(i.ROOT / name) or b'# fixture\n')
        self.mock = patch.object(i, 'ROOT', self.source)
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.config = b'# keep\nmodel="gpt-5.6-sol" # chosen\nnotify=["a", "b"]\n[agents]\nargs=["x"]\n[agents.implementer]\nconfig_file="custom.toml"\n[mcp_servers.example]\ncommand="existing"\n'
        (self.home / 'config.toml').write_bytes(self.config)
        (self.home / 'AGENTS.md').write_bytes(b'User instructions\n')

    def apply(self, uninstall=False):
        _, _, plan = i.build(self.home, uninstall)
        return i.apply(self.home, plan['plan_id'], uninstall)

    def test_install_reinstall_uninstall_preserves_later_unrelated_changes(self):
        self.apply()
        receipt = i.validate(self.home)
        self.assertEqual(receipt['schema'], 4)
        self.assertEqual(set(receipt['owned_files']), i.legacy(4))
        self.assertEqual(i.build(self.home)[2]['config_changes'], [])
        undo = i.build(self.home, True)[2]['config_changes']
        self.assertEqual(next(row for row in undo if row['key'] == 'model')['after'], '"gpt-5.6-sol"')
        self.assertEqual(self.apply()['status'], 'IDEMPOTENT_PASS')
        with (self.home / 'config.toml').open('ab') as stream:
            stream.write(b'\n[projects.example]\ntrust_level="trusted"\n')
        with (self.home / 'AGENTS.md').open('ab') as stream:
            stream.write(b'Later instructions\n')
        self.apply(True)
        result = tomllib.loads((self.home / 'config.toml').read_text())
        self.assertEqual(result['model'], 'gpt-5.6-sol')
        self.assertEqual(result['notify'], ['a', 'b'])
        self.assertEqual(result['mcp_servers']['example']['command'], 'existing')
        self.assertIn('example', result['projects'])
        self.assertEqual((self.home / 'AGENTS.md').read_bytes(), b'User instructions\nLater instructions\n')
        self.assertFalse((self.home / i.ENTRY).exists())

    def test_stale_plan_and_managed_conflict(self):
        _, _, plan = i.build(self.home)
        with (self.home / 'AGENTS.md').open('ab') as stream:
            stream.write(b'concurrent\n')
        with self.assertRaisesRegex(ValueError, 'stale'):
            i.apply(self.home, plan['plan_id'])
        self.apply()
        path = self.home / 'config.toml'
        path.write_text(path.read_text().replace('gpt-6-astra', 'gpt-5.6-sol'))
        with self.assertRaisesRegex(ValueError, 'changed'):
            i.build(self.home, True)

    def test_failure_rolls_back_and_recovery_rejects_later_edit(self):
        _, _, plan = i.build(self.home)
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            i.apply(self.home, plan['plan_id'], _fail_after=4)
        self.assertEqual((self.home / 'config.toml').read_bytes(), self.config)
        self.assertEqual((self.home / 'AGENTS.md').read_bytes(), b'User instructions\n')
        result = self.apply()
        with (self.home / 'config.toml').open('ab') as stream:
            stream.write(b'# changed later\n')
        with self.assertRaisesRegex(ValueError, 'preserved'):
            i.recover(self.home, result['backup'])

    def test_upgrade_schema3_preserves_legacy_runtime(self):
        self.apply()
        receipt_path = self.home / i.RESOURCE / 'install-manifest.json'
        receipt = json.loads(receipt_path.read_bytes())
        # Synthetic prior receipt uses exact valid Go schema3 paths.
        old = self.home / i.RESOURCE / 'runtime/astra-luna'
        old.write_bytes(b'old-runtime-preserved')
        receipt.update(schema=3, version='0.3.0')
        receipt['owned_files'][i.RESOURCE + '/runtime/astra-luna'] = i.sha(old.read_bytes())
        receipt_path.write_bytes(encode(receipt))
        self.apply()
        self.assertEqual(i.validate(self.home)['schema'], 4)
        self.assertEqual(old.read_bytes(), b'old-runtime-preserved')

    def test_override_and_unowned_files_and_provider_fail_closed(self):
        override = self.home / 'AGENTS.override.md'
        override.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'override'):
            i.build(self.home)
        override.unlink()
        (self.home / 'config.toml').write_text('model_provider="custom"\n')
        with self.assertRaisesRegex(ValueError, 'provider'):
            i.build(self.home)

    def test_inline_table_and_comments_survive(self):
        raw = b'# header\nagents={enabled=false, args=["x"], max_threads=2}\n'
        merged, changes = i.transform(raw, 'merge', desired=i.desired(raw))
        restored, _ = i.transform(merged, 'revert', changes=changes)
        obj = tomllib.loads(restored.decode())
        self.assertEqual(obj['agents'], dict(enabled=False, args=['x'], max_threads=2))
        self.assertTrue(restored.startswith(b'# header'))

    def test_recovery_exact(self):
        result = self.apply()
        self.assertEqual(i.recover(self.home, result['backup'])['status'], 'ROLLBACK_EXACT_PASS')
        self.assertEqual((self.home / 'config.toml').read_bytes(), self.config)
        self.assertEqual((self.home / 'AGENTS.md').read_bytes(), b'User instructions\n')

    def test_implicit_tables_and_wrong_types(self):
        for raw in (b'agents.enabled=false\nagents.args=["x"]\n',
                    b'[agents.implementer]\nconfig_file="custom.toml"\n'):
            changed, changes = i.transform(raw, 'merge', desired=i.desired(raw))
            restored, _ = i.transform(changed, 'revert', changes=changes)
            self.assertEqual(tomllib.loads(raw.decode()), tomllib.loads(restored.decode()))
        with self.assertRaises(ValueError):
            raw=b'agents.enabled=1\n'
            i.transform(raw, 'merge', desired=i.desired(raw))


if __name__ == '__main__':
    unittest.main()
