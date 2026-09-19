import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from astra_luna import platform as p


class PlatformTests(unittest.TestCase):
    def test_json_rejects_ambiguous_input(self):
        for value in ('{"a":1,"a":2}', '{"a":NaN}', '[Infinity]'):
            with self.assertRaises(ValueError):
                p.loads(value)

    def test_unicode_atomic_and_link_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'space 中文' / 'config'
            p.atomic(target, b'first')
            p.atomic(target, b'second')
            self.assertEqual(p.read(target), b'second')
            if os.name != 'nt':
                link = root / 'link'
                link.symlink_to(target)
                with self.assertRaises(ValueError):
                    p.atomic(link, b'changed')
                self.assertEqual(target.read_bytes(), b'second')

    def test_lock_exclusion_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'lock'
            with p.file_lock(path):
                with self.assertRaises(RuntimeError):
                    with p.file_lock(path, timeout=0.05):
                        pass
            with p.file_lock(path, timeout=0.05):
                pass

    def test_process_io_bounded_output_timeout(self):
        result = p.run([sys.executable, '-c', 'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'], input=b'input')
        self.assertEqual(result.stdout, b'input')
        with self.assertRaisesRegex(RuntimeError, 'output'):
            p.run([sys.executable, '-c', 'print("x"*1000000)'], limit=1024)
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            p.run([sys.executable, '-c', 'import time;time.sleep(20)'], timeout=0.1)
        self.assertLess(time.monotonic() - start, 5)

    def test_quoted_command_and_clean_environment(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-never-print', 'PYTHONPATH': 'untrusted'}):
            env = p.clean_env()
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('PYTHONPATH', env)
        self.assertEqual(env['PYTHONDONTWRITEBYTECODE'], '1')
        text = p.command_text(['path with space', "a'b"])
        self.assertIn('path with space', text)

    def test_timeout_stops_descendant_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / 'unexpected'
            child = 'import time,pathlib;time.sleep(1);pathlib.Path(' + repr(str(target)) + ').write_text("alive")'
            parent = 'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",' + repr(child) + ']);time.sleep(20)'
            with self.assertRaises(RuntimeError):
                p.run([sys.executable, '-c', parent], timeout=0.2)
            time.sleep(1.1)
            self.assertFalse(target.exists())

    def test_parent_exit_still_stops_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / 'unexpected'
            child = 'import time,pathlib;time.sleep(1);pathlib.Path(' + repr(str(target)) + ').write_text("alive")'
            parent = ('import subprocess,sys;subprocess.Popen([sys.executable,"-c",' + repr(child)
                      + '],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)')
            p.run([sys.executable, '-c', parent], timeout=3)
            time.sleep(1.1)
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
