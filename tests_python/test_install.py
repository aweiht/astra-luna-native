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

    def test_040_to_050_upgrade_accepts_unchanged_defaults(self):
        home = self.base / 'upgrade-040-home'
        home.mkdir()
        (home / 'config.toml').write_bytes(
            b'model = "gpt-6-astra"\n[agents]\n'
            b'default_subagent_model = "gpt-5.6-luna"\nmax_threads = 5\n')
        (home / 'AGENTS.md').write_bytes(b'old instructions\n')
        with patch.object(i, '__version__', '0.4.0'):
            _, _, plan = i.build(home)
            i.apply(home, plan['plan_id'])
        old_receipt = json.loads((home / i.RESOURCE / 'install-manifest.json').read_bytes())
        self.assertEqual(old_receipt['version'], '0.4.0')
        self.assertNotIn('model', {row['key'] for row in old_receipt['config_changes']})

        with patch.object(i, '__version__', '0.5.0'):
            _, _, upgrade = i.build(home)
            self.assertEqual(upgrade['config_changes'], [])
            self.assertEqual(i.apply(home, upgrade['plan_id'])['status'], 'INSTALLED')
            receipt = i.validate(home)
        self.assertEqual(receipt['version'], '0.5.0')
        self.assertNotIn('model', {row['key'] for row in receipt['config_changes']})

    def test_unknown_illegal_version_and_downgrade_refuse(self):
        with patch.object(i, '__version__', '0.5.0'):
            self.apply()
        manifest = self.home / i.RESOURCE / 'install-manifest.json'
        original = json.loads(manifest.read_bytes())
        for version in ('0.6.0', 'not-a-version'):
            with self.subTest(version=version):
                mutated = dict(original, version=version)
                manifest.write_bytes(encode(mutated))
                with self.assertRaisesRegex(ValueError, 'unsupported installation version'):
                    i.load(self.home)
        # A version on a schema-1/2 receipt is itself unsupported; old schemas
        # are accepted only through their known version-less source profile.
        manifest.write_bytes(encode(dict(original, schema=2, version='0.3.0')))
        with self.assertRaisesRegex(ValueError, 'unsupported installation version'):
            i.load(self.home)
        manifest.write_bytes(encode(original))
        with patch.object(i, '__version__', '0.4.0'):
            with self.assertRaisesRegex(ValueError, 'downgrade'):
                i.build(self.home)

    def test_incomplete_managed_keys_reject_before_upgrade_writes(self):
        self.apply()
        manifest = self.home / i.RESOURCE / 'install-manifest.json'
        receipt = json.loads(manifest.read_bytes())
        receipt['managed_keys'].remove('model')
        manifest.write_bytes(encode(receipt))
        config = self.home / 'config.toml'
        before = config.read_bytes()
        with self.assertRaisesRegex(ValueError, 'invalid managed configuration receipt'):
            i.build(self.home)
        self.assertEqual(config.read_bytes(), before)

    def test_schema1_and_schema2_receipts_without_version_upgrade(self):
        for schema in (1, 2):
            with self.subTest(schema=schema):
                home = self.base / f'legacy-schema-{schema}'
                home.mkdir()
                (home / 'config.toml').write_bytes(b'model = "gpt-6-astra"\n')
                (home / 'AGENTS.md').write_bytes(b'old instructions\n')
                with patch.object(i, '__version__', '0.4.0'):
                    _, _, plan = i.build(home)
                    i.apply(home, plan['plan_id'])
                manifest = home / i.RESOURCE / 'install-manifest.json'
                receipt = json.loads(manifest.read_bytes())
                owned = {}
                for relative in i.legacy(schema):
                    path = home / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if not path.exists():
                        path.write_bytes(b'legacy receipt fixture\n')
                    owned[relative] = i.sha(path.read_bytes())
                # A real schema-1/2 installation predates these Python
                # runtime destinations.  Remove the newer fixture payloads
                # so the upgrade can create them as owned files.
                for relative in i.RUNTIME_FILES:
                    path = home / i.RESOURCE / 'runtime' / relative
                    if path.exists() and i.RESOURCE + '/runtime/' + relative not in owned:
                        path.unlink()
                receipt.update(schema=schema, owned_files=owned)
                receipt.pop('version', None)
                receipt.pop('managed_keys', None)
                for key in i.RECEIPT_METADATA:
                    receipt.pop(key, None)
                manifest.write_bytes(encode(receipt))
                with patch.object(i, '__version__', '0.5.0'):
                    _, _, upgrade = i.build(home)
                    self.assertEqual(i.apply(home, upgrade['plan_id'])['status'], 'INSTALLED')
                    self.assertEqual(i.validate(home)['schema'], 4)

    def test_approved_future_profile_composes_original_values(self):
        home = self.base / 'future-profile-home'
        home.mkdir()
        original = (b'model = "gpt-6-astra"\n[agents]\n'
                    b'default_subagent_model = "gpt-5.6-sol"\nmax_threads = 5\n')
        (home / 'config.toml').write_bytes(original)
        (home / 'AGENTS.md').write_bytes(b'old instructions\n')
        with patch.object(i, '__version__', '0.5.0'):
            _, _, plan = i.build(home)
            i.apply(home, plan['plan_id'])
        future = dict(i.VERSION_PROFILES['0.5.0'])
        future['model'] = '"synthetic-future-root"'
        future['agents.default_subagent_model'] = '"synthetic-future-leaf"'
        with patch.dict(i.VERSION_PROFILES, {'0.6.0': future}), \
                patch.object(i, '__version__', '0.6.0'):
            _, _, upgrade = i.build(home)
            delta = {row['key']: row for row in upgrade['config_changes']}
            self.assertEqual(delta['model']['before'], '"gpt-6-astra"')
            self.assertEqual(delta['model']['after'], '"synthetic-future-root"')
            self.assertEqual(delta['agents.default_subagent_model']['before'],
                             '"gpt-5.6-luna"')
            self.assertEqual(i.apply(home, upgrade['plan_id'])['status'], 'INSTALLED')
            receipt = i.validate(home)
            rows = {row['key']: row for row in receipt['config_changes']}
            self.assertEqual(rows['model']['before'], '"gpt-6-astra"')
            self.assertEqual(rows['agents.default_subagent_model']['before'],
                             '"gpt-5.6-sol"')
            self.assertEqual(i.build(home)[2]['files'], [])
            _, _, uninstall = i.build(home, uninstall=True)
            self.assertEqual(i.apply(home, uninstall['plan_id'], uninstall=True)['status'],
                             'UNINSTALLED')
        self.assertEqual((home / 'config.toml').read_bytes(), original)

    def test_managed_conflict_does_not_write_during_upgrade(self):
        self.apply()
        manifest = self.home / i.RESOURCE / 'install-manifest.json'
        before_manifest = manifest.read_bytes()
        config = self.home / 'config.toml'
        config.write_bytes(config.read_bytes().replace(b'gpt-6-astra', b'user-model'))
        before_config = config.read_bytes()
        with self.assertRaisesRegex(ValueError, 'changed'):
            i.build(self.home)
        self.assertEqual(config.read_bytes(), before_config)
        self.assertEqual(manifest.read_bytes(), before_manifest)

    def test_upgrade_rollback_restores_immediate_previous_receipt_and_repeats(self):
        home = self.base / 'rollback-upgrade-home'
        home.mkdir()
        (home / 'config.toml').write_bytes(b'model = "gpt-6-astra"\n')
        (home / 'AGENTS.md').write_bytes(b'old instructions\n')
        with patch.object(i, '__version__', '0.5.0'):
            _, _, plan = i.build(home)
            i.apply(home, plan['plan_id'])
        future = dict(i.VERSION_PROFILES['0.5.0'])
        future['model'] = '"synthetic-rollback-root"'
        with patch.dict(i.VERSION_PROFILES, {'0.6.0': future}), \
                patch.object(i, '__version__', '0.6.0'):
            _, _, upgrade = i.build(home)
            result = i.apply(home, upgrade['plan_id'])
            self.assertEqual(i.build(home)[2]['files'], [])
            self.assertEqual(i.apply(home, i.build(home)[2]['plan_id'])['status'],
                             'IDEMPOTENT_PASS')
            self.assertEqual(i.recover(home, result['backup'])['status'], 'ROLLBACK_EXACT_PASS')
            self.assertEqual(i.validate(home)['version'], '0.5.0')
            _, _, repeat = i.build(home)
            self.assertEqual(repeat['config_changes'][0]['after'], '"synthetic-rollback-root"')
            self.assertEqual(i.apply(home, repeat['plan_id'])['status'], 'INSTALLED')
            self.assertEqual(i.validate(home)['version'], '0.6.0')

    def test_old_receipt_without_ownership_metadata_upgrades_conservatively(self):
        home = self.base / 'old-metadata-upgrade-home'
        home.mkdir()
        original = b'model = "gpt-6-astra"\n'
        (home / 'config.toml').write_bytes(original)
        (home / 'AGENTS.md').write_bytes(b'old instructions\n')
        with patch.object(i, '__version__', '0.4.0'):
            _, _, plan = i.build(home)
            i.apply(home, plan['plan_id'])
        manifest = home / i.RESOURCE / 'install-manifest.json'
        receipt = json.loads(manifest.read_bytes())
        for key in i.RECEIPT_METADATA:
            receipt.pop(key, None)
        manifest.write_bytes(encode(receipt))
        with patch.object(i, '__version__', '0.5.0'):
            _, _, upgrade = i.build(home)
            i.apply(home, upgrade['plan_id'])
            self.assertFalse(any(key in i.validate(home) for key in i.RECEIPT_METADATA))
            _, _, uninstall = i.build(home, uninstall=True)
            i.apply(home, uninstall['plan_id'], uninstall=True)
        self.assertEqual((home / 'config.toml').read_bytes(), original + b'\n[agents]\n')

    def test_legacy_receipt_uninstall_preserves_later_provider_edit(self):
        home = self.base / 'legacy-provider-edit-home'
        home.mkdir()
        (home / 'config.toml').write_bytes(b'model = "gpt-5.6-sol"\n')
        (home / 'AGENTS.md').write_bytes(b'old instructions\n')
        with patch.object(i, '__version__', '0.4.0'):
            _, _, plan = i.build(home)
            i.apply(home, plan['plan_id'])
        manifest = home / i.RESOURCE / 'install-manifest.json'
        receipt = json.loads(manifest.read_bytes())
        receipt.pop('managed_keys', None)
        for key in i.RECEIPT_METADATA:
            receipt.pop(key, None)
        manifest.write_bytes(encode(receipt))
        config = home / 'config.toml'
        config.write_bytes(b'model_provider = "custom"\n' + config.read_bytes())
        with patch.object(i, '__version__', '0.5.0'):
            _, _, uninstall = i.build(home, uninstall=True)
            self.assertEqual(i.apply(home, uninstall['plan_id'], uninstall=True)['status'],
                             'UNINSTALLED')
        self.assertIn(b'model_provider = "custom"\n', config.read_bytes())

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

    def test_fresh_uninstall_removes_only_empty_installer_structures(self):
        home = self.base / 'fresh-home'
        home.mkdir()
        _, _, plan = i.build(home)
        self.assertEqual(i.apply(home, plan['plan_id'])['status'], 'INSTALLED')
        receipt = i.validate(home)
        self.assertFalse(receipt['config_existed'])
        self.assertFalse(receipt['agents_existed'])
        self.assertEqual(receipt['created_config_tables'], [{'name': 'agents', 'separator': 1}])

        _, _, uninstall = i.build(home, uninstall=True)
        self.assertEqual(i.apply(home, uninstall['plan_id'], uninstall=True)['status'], 'UNINSTALLED')
        self.assertFalse((home / 'config.toml').exists())
        self.assertFalse((home / 'AGENTS.md').exists())

    def test_uninstall_preserves_user_content_in_created_table_and_agents_file(self):
        home = self.base / 'preserve-home'
        home.mkdir()
        (home / 'config.toml').write_bytes(b'model = "old"\n')
        _, _, plan = i.build(home)
        i.apply(home, plan['plan_id'])
        with (home / 'config.toml').open('ab') as stream:
            stream.write(b'# user note\ncustom = true\n')
        with (home / 'AGENTS.md').open('ab') as stream:
            stream.write(b'User addition\n')

        _, _, uninstall = i.build(home, uninstall=True)
        i.apply(home, uninstall['plan_id'], uninstall=True)
        config = (home / 'config.toml').read_bytes()
        self.assertIn(b'model = "old"\n', config)
        self.assertIn(b'[agents]\n', config)
        self.assertIn(b'# user note\ncustom = true\n', config)
        self.assertEqual((home / 'AGENTS.md').read_bytes(), b'User addition\n')

    def test_old_receipt_never_guesses_file_or_table_ownership(self):
        home = self.base / 'old-receipt-home'
        home.mkdir()
        _, _, plan = i.build(home)
        i.apply(home, plan['plan_id'])
        manifest = home / i.RESOURCE / 'install-manifest.json'
        receipt = json.loads(manifest.read_bytes())
        for key in i.RECEIPT_METADATA:
            receipt.pop(key, None)
        manifest.write_bytes(encode(receipt))

        _, _, uninstall = i.build(home, uninstall=True)
        i.apply(home, uninstall['plan_id'], uninstall=True)
        self.assertTrue((home / 'config.toml').exists())
        self.assertEqual((home / 'config.toml').read_bytes(), b'\n[agents]\n')
        self.assertTrue((home / 'AGENTS.md').exists())
        self.assertEqual((home / 'AGENTS.md').read_bytes(), b'')

    def test_committed_recover_without_backup_is_read_only_but_explicit_rollback_works(self):
        result = self.apply()
        transaction = self.home / i.RESOURCE / 'transaction.json'
        before_transaction = transaction.read_bytes()
        before_config = (self.home / 'config.toml').read_bytes()
        with self.assertRaisesRegex(ValueError, 'explicit backup'):
            i.recover(self.home, '')
        self.assertEqual(transaction.read_bytes(), before_transaction)
        self.assertEqual((self.home / 'config.toml').read_bytes(), before_config)
        with self.assertRaisesRegex(ValueError, 'committed'):
            i.recover(self.home)
        self.assertEqual(transaction.read_bytes(), before_transaction)
        self.assertEqual((self.home / 'config.toml').read_bytes(), before_config)
        self.assertEqual(i.recover(self.home, result['backup'])['status'], 'ROLLBACK_EXACT_PASS')
        self.assertEqual((self.home / 'config.toml').read_bytes(), self.config)

    def test_backup_symlink_is_rejected_before_outside_creation(self):
        resource = self.home / i.RESOURCE
        resource.mkdir()
        outside = self.base / 'outside-backups'
        outside.mkdir()
        link = resource / 'backups'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links unavailable on this runner')
        _, _, plan = i.build(self.home)
        with self.assertRaisesRegex(ValueError, 'symlink or reparse point rejected'):
            i.apply(self.home, plan['plan_id'])
        self.assertEqual(list(outside.iterdir()), [])

    def test_malformed_receipt_shapes_are_stable_value_errors(self):
        self.apply()
        manifest = self.home / i.RESOURCE / 'install-manifest.json'
        original = json.loads(manifest.read_bytes())
        cases = [
            ('config_changes', {'config_changes': {'key': 'model'}}),
            ('config_change_row', {'config_changes': ['model']}),
            ('block', {'block': []}),
            ('metadata_name', {'created_config_tables': [{'name': [], 'separator': 1}]}),
            ('managed_keys', {'managed_keys': []}),
        ]
        for label, replacement in cases:
            with self.subTest(label=label):
                mutated = dict(original)
                mutated.update(replacement)
                manifest.write_bytes(encode(mutated))
                with self.assertRaisesRegex(ValueError, 'invalid'):
                    i.load(self.home)
                manifest.write_bytes(encode(original))
        manifest.write_bytes(encode([]))
        with self.assertRaisesRegex(ValueError, 'invalid installation receipt'):
            i.load(self.home)


if __name__ == '__main__':
    unittest.main()
