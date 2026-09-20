import json
import os
from pathlib import Path
import shutil
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

    def test_prior_schema4_receipt_upgrades_new_runtime_and_uninstalls(self):
        for prior_runtime in (i.PREVIOUS_SCHEMA4_RUNTIME,
                              i.PREVIOUS_SCHEMA4_RUNTIME + ('astra_luna/process_tree.py',)):
            with self.subTest(prior_runtime=prior_runtime):
                self._prior_schema4_upgrade(prior_runtime)

    def _prior_schema4_upgrade(self, prior_runtime):
        home = self.base / ('prior-schema4-home-' + str(len(prior_runtime)))
        home.mkdir()
        (home / 'config.toml').write_bytes(b'model="old"\n')
        (home / 'AGENTS.md').write_bytes(b'user instructions\n')
        with patch.object(i, 'ROOT', self.source), \
                patch.object(i, 'RUNTIME_FILES', prior_runtime):
            _, _, plan = i.build(home)
            installed = i.apply(home, plan['plan_id'])
        self.assertEqual(installed['status'], 'INSTALLED')
        process_tree = home / i.RESOURCE / 'runtime/astra_luna/process_tree.py'
        registry = home / i.RESOURCE / 'runtime/astra_luna/process_registry.py'
        self.assertEqual(process_tree.exists(), 'astra_luna/process_tree.py' in prior_runtime)
        self.assertFalse(registry.exists())
        with patch.object(i, 'ROOT', self.source):
            _, _, upgrade = i.build(home)
            self.assertIn(i.RESOURCE + '/runtime/astra_luna/process_registry.py',
                          [row['path'] for row in upgrade['files']])
            self.assertEqual(i.apply(home, upgrade['plan_id'])['status'], 'INSTALLED')
            self.assertTrue(process_tree.is_file())
            self.assertTrue(registry.is_file())
            self.assertEqual(i.build(home)[2]['files'], [])
            _, _, uninstall = i.build(home, uninstall=True)
            self.assertEqual(i.apply(home, uninstall['plan_id'], uninstall=True)['status'], 'UNINSTALLED')
        self.assertFalse(process_tree.exists())
        self.assertFalse(registry.exists())

    def test_prior_schema4_receipt_rejects_missing_or_unknown_runtime_paths(self):
        home = self.base / 'invalid-prior-schema4-home'
        home.mkdir()
        (home / 'config.toml').write_bytes(b'model="old"\n')
        (home / 'AGENTS.md').write_bytes(b'user instructions\n')
        with patch.object(i, 'ROOT', self.source), \
                patch.object(i, 'RUNTIME_FILES', i.PREVIOUS_SCHEMA4_RUNTIME):
            _, _, plan = i.build(home)
            i.apply(home, plan['plan_id'])
        receipt_path = home / i.RESOURCE / 'install-manifest.json'
        original = json.loads(receipt_path.read_bytes())
        missing = json.loads(json.dumps(original))
        missing['owned_files'].pop(i.RESOURCE + '/runtime/astra_luna/transport.py')
        receipt_path.write_bytes(encode(missing))
        with self.assertRaisesRegex(ValueError, 'invalid receipt paths'):
            i.load(home)
        unknown = json.loads(json.dumps(original))
        unknown['owned_files'][i.RESOURCE + '/runtime/not-a-runtime.py'] = i.sha(b'unknown')
        receipt_path.write_bytes(encode(unknown))
        with self.assertRaisesRegex(ValueError, 'invalid receipt paths'):
            i.load(home)

    def test_case_variant_alias_supports_validation_upgrade_uninstall_and_recovery(self):
        home = self.base / 'CaseHome'
        home.mkdir()
        (home / 'config.toml').write_bytes(b'model="old"\n')
        (home / 'AGENTS.md').write_bytes(b'user instructions\n')
        alias = self.base / 'casehome'
        with patch.object(i, 'ROOT', self.source):
            _, _, plan = i.build(home)
            installed = i.apply(home, plan['plan_id'])
            if not alias.exists() or not os.path.samefile(home, alias):
                self.skipTest('case-sensitive filesystem; no case alias exists')
            self.assertEqual(i.validate(alias)['codex_home'], str(home))
            self.assertEqual(i.build(alias)[2]['files'], [])
            rollback = i.recover(alias, installed['backup'])
            self.assertEqual(rollback['status'], 'ROLLBACK_EXACT_PASS')
            _, _, reinstall = i.build(alias)
            i.apply(alias, reinstall['plan_id'])
            receipt_path = home / i.RESOURCE / 'install-manifest.json'
            receipt = json.loads(receipt_path.read_bytes())
            old = home / i.RESOURCE / 'runtime/astra-luna'
            old.write_bytes(b'old-runtime-preserved')
            receipt.update(schema=3, version='0.3.0', codex_home=str(home))
            receipt['owned_files'][i.RESOURCE + '/runtime/astra-luna'] = i.sha(old.read_bytes())
            receipt_path.write_bytes(encode(receipt))
            _, _, upgrade = i.build(alias)
            i.apply(alias, upgrade['plan_id'])
            self.assertEqual(i.validate(alias)['schema'], 4)
            _, _, uninstall = i.build(alias, uninstall=True)
            result = i.apply(alias, uninstall['plan_id'], uninstall=True)
        self.assertEqual(result['status'], 'UNINSTALLED')
        self.assertEqual(tomllib.loads((home / 'config.toml').read_text())['model'], 'old')

    def test_receipt_rejects_credentials_copied_to_different_home(self):
        home = self.base / 'OriginalHome'
        home.mkdir()
        (home / 'config.toml').write_bytes(b'model="old"\n')
        (home / 'AGENTS.md').write_bytes(b'user instructions\n')
        with patch.object(i, 'ROOT', self.source):
            _, _, plan = i.build(home)
            i.apply(home, plan['plan_id'])
        copied = self.base / 'CopiedHome'
        shutil.copytree(home, copied)
        with self.assertRaisesRegex(ValueError, 'invalid installation receipt'):
            i.load(copied)

    def test_receipt_rejects_symbolic_link_home_alias(self):
        home = self.base / 'RealHome'
        home.mkdir()
        (home / 'config.toml').write_bytes(b'model="old"\n')
        (home / 'AGENTS.md').write_bytes(b'user instructions\n')
        with patch.object(i, 'ROOT', self.source):
            _, _, plan = i.build(home)
            i.apply(home, plan['plan_id'])
        alias = self.base / 'LinkHome'
        try:
            alias.symlink_to(home, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links unavailable on this runner')
        with self.assertRaisesRegex(ValueError, 'symlink or reparse point rejected'):
            i.load(alias)

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
