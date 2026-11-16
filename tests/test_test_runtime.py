"""Exercise the process boundary: a native fault must leave evidence, not a dialog."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class TestRuntimeTests(unittest.TestCase):
    def child(self, directory, action):
        code = '''
import ctypes, faulthandler, json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from _test_runtime import install_crash_diagnostics, use_local_temp
root = Path(sys.argv[2])
use_local_temp(root / 'tmp')
install_crash_diagnostics(root / 'stacks.txt', timeout=1 if sys.argv[3] == 'shutdown' else None)
mode = ctypes.windll.kernel32.GetErrorMode() if sys.platform == 'win32' else None
(root / 'runtime.json').write_text(json.dumps(dict(mode=mode, temp=tempfile.gettempdir(),
    environment={k: os.environ[k] for k in ('TEMP', 'TMP', 'TMPDIR')})))
if sys.argv[3] == 'crash':
    faulthandler._sigsegv()
elif sys.argv[3] == 'shutdown':
    import atexit, time
    atexit.register(lambda: time.sleep(2))
else:
    with tempfile.TemporaryDirectory() as value:
        assert Path(value).parent == root / 'tmp'
'''
        return subprocess.run([sys.executable, '-I', '-c', code,
                               str(Path(__file__).resolve().parent), str(directory), action],
                              capture_output=True, timeout=15)

    def test_native_crash_keeps_stack_and_exit_code_without_error_box(self):
        # The canonical runner supplies this output-local root. The fallback
        # also stays in the checkout when unittest is invoked directly.
        root = Path(os.environ.get('PROMPT_STUDIO_DATA',
                                   Path(__file__).resolve().parents[1] / 'qa/test-runtime'))
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as value:
            directory = Path(value)
            result = self.child(directory, 'crash')
            self.assertNotEqual(result.returncode, 0)
            stack = (directory / 'stacks.txt').read_text(encoding='utf-8')
            self.assertIn('<module>', stack)
            self.assertTrue('access violation' in stack.lower() or 'segmentation fault' in stack.lower(), stack)
            record = json.loads((directory / 'runtime.json').read_text())
            if sys.platform == 'win32':
                self.assertTrue(record['mode'] & 0x0002)
            self.assertEqual(Path(record['temp']), directory / 'tmp')
            self.assertTrue(all(Path(p) == directory / 'tmp' for p in record['environment'].values()))
            evidence = root / ('crash-evidence-' + directory.name)
            evidence.mkdir()
            (evidence / 'stacks.txt').write_text(stack, encoding='utf-8')
            (evidence / 'result.json').write_text(json.dumps(dict(
                exit_code=result.returncode, runtime=record), indent=2), encoding='utf-8')

    def test_normal_child_cleans_only_its_own_local_temp(self):
        root = Path(os.environ.get('PROMPT_STUDIO_DATA',
                                   Path(__file__).resolve().parents[1] / 'qa/test-runtime'))
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as value:
            directory = Path(value)
            result = self.child(directory, 'normal')
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            self.assertEqual(list((directory / 'tmp').iterdir()), [])
            self.assertEqual((directory / 'stacks.txt').read_text(), '')

    def test_watchdog_stays_active_during_interpreter_shutdown(self):
        root = Path(os.environ.get('PROMPT_STUDIO_DATA',
                                   Path(__file__).resolve().parents[1] / 'qa/test-runtime'))
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as value:
            directory = Path(value)
            result = self.child(directory, 'shutdown')
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            stack = (directory / 'stacks.txt').read_text(encoding='utf-8')
            self.assertIn('Timeout', stack)
            self.assertIn('<lambda>', stack)


if __name__ == '__main__':
    unittest.main()
