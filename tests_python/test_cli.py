"""Real isolated Python entry lifecycle with fake public CLI/source data."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from astra_luna import install
from astra_luna.platform import clean_env, run


class CLITests(unittest.TestCase):
    def test_portable_moved_download_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source = base / 'download space 中文'
            for name in install.RUNTIME_FILES:
                dest = source / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(install.ROOT / name, dest)
            home = base / 'codex home 中文'
            home.mkdir()
            (home / 'config.toml').write_text('# keep\nnotify=["x"]\n', encoding='utf-8')
            fakepy = base / 'fake_codex.py'
            fakepy.write_text('''import json, sys
args=sys.argv[1:]
if args == ['--version']: print('codex-cli 0.147.0')
elif args == ['debug','models']:
 print(json.dumps({'models': [
  {'slug':'gpt-6-astra','supported_reasoning_levels':[{'effort':'max'}]},
  {'slug':'gpt-5.6-luna','supported_reasoning_levels':[{'effort':e} for e in ('low','medium','high','xhigh','max')]}
 ]}))
else: sys.exit(2)
''', encoding='utf-8')
            launcher = base / ('codex.cmd' if os.name == 'nt' else 'codex')
            if os.name == 'nt':
                launcher.write_text('@echo off\r\n"' + sys.executable + '" "' + str(fakepy) + '" %*\r\n', encoding='utf-8')
            else:
                launcher.write_text('#!' + sys.executable + '\n' + fakepy.read_text(encoding='utf-8'), encoding='utf-8')
                launcher.chmod(0o755)
            # Only external data is mocked. Execute the actual entry file and
            # moved installed module tree in separate Python processes.
            runner = base / 'offline_runner.py'
            runner.write_text('''import runpy, sys
from pathlib import Path
entry=sys.argv.pop(1)
sys.path.insert(0,str(Path(entry).parent))
from astra_luna import policy
policy.fetch_sources=lambda *a,**k: []
policy.FetchSources=policy.fetch_sources
sys.argv[0]=entry
runpy.run_path(entry,run_name='__main__')
''', encoding='utf-8')
            env = clean_env()
            # No Go in PATH. Python and the fake Codex are resolved absolutely.
            env['PATH'] = os.environ.get('SystemRoot', 'C:\\Windows') + '\\System32' if os.name == 'nt' else '/usr/bin:/bin'
            probe = run([str(launcher), '--version'], env=env, timeout=10)
            self.assertEqual(probe.returncode, 0, probe.stderr.decode('utf-8', errors='replace'))
            self.assertEqual(probe.stdout.strip(), b'codex-cli 0.147.0')
            def invoke(entry, *args, expect=0):
                result = run([sys.executable, '-B', str(runner), str(entry), '--codex-home', str(home),
                              '--codex', str(launcher), *args], env=env, timeout=30)
                self.assertEqual(result.returncode, expect, result.stdout.decode('utf-8') + result.stderr.decode('utf-8'))
                return json.loads(result.stdout)
            entry = source / 'astra-luna.py'
            initial = invoke(entry, 'doctor', expect=1)
            self.assertEqual(initial['status'], 'NOT_READY')
            plan = invoke(entry, 'install', '--dry-run')
            self.assertTrue(plan['plan_id'])
            self.assertFalse((home / install.ENTRY).exists())
            installed = invoke(entry, 'install', '--yes')
            self.assertEqual(installed['status'], 'INSTALLED')
            source.rename(base / 'download moved')
            entry = home / install.ENTRY
            ready = invoke(entry, 'doctor')
            self.assertEqual(ready['status'], 'READY_FALLBACK')
            project = base / 'project space'
            project.mkdir()
            selected = invoke(entry, 'select', '--project', str(project), '--explain')
            self.assertEqual(selected['role'], 'adaptive_luna_max')
            self.assertFalse(invoke(entry, 'select', '--project', str(project), '--explain')['refreshed'])
            self.assertEqual(invoke(entry, 'refresh')['role'], 'adaptive_luna_max')
            self.assertEqual(invoke(entry, 'install', '--yes')['status'], 'IDEMPOTENT_PASS')
            self.assertEqual(invoke(entry, 'uninstall', '--yes')['status'], 'UNINSTALLED')
            self.assertFalse(entry.exists())
            self.assertIn('# keep', (home / 'config.toml').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
