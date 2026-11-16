"""Exercise the delivered entry point rather than only its version string."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import PySide6
from prompt_calculus_studio.releases import CURRENT, write_launchers


class Release089Tests(unittest.TestCase):
    def test_current_release_launches_workspace_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base/'files'
            root.mkdir()
            note = root/'note.md'
            original = b'# Example\r\n\r\nKeep this text.\r\n\r\n$x^2$\r\n\r\n```mermaid\r\ngraph LR\r\nA-->B\r\n```\r\n'
            note.write_bytes(original)
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen',
                       QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu',
                       PROMPT_STUDIO_DATA=str(base/'wrong-data'))
            # An isolated dev Python can receive its vendor path in-process;
            # neither -I nor embedded Python forwards that path to a child.
            # Keep the actual run.py entry point and argv, supplying only the
            # same dependency directory that this test imported successfully.
            vendor = str(Path(PySide6.__file__).resolve().parent.parent)
            stacks = Path(os.environ.get('PROMPT_STUDIO_DATA', base)).parent / ('entrypoint-' + base.name + '-stacks.txt')
            bootstrap = ('import runpy,sys;sys.path[:0]=[sys.argv.pop(1),sys.argv.pop(1),sys.argv.pop(1)];'
                         'from _test_runtime import install_crash_diagnostics;'
                         'install_crash_diagnostics(sys.argv.pop(1),30);'
                         "sys.argv[0]='run.py';runpy.run_path('run.py',run_name='__main__')")
            run = subprocess.run([sys.executable, '-I', '-c', bootstrap,
                                  vendor, str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent),
                                  str(stacks), CURRENT.argument,
                                  '--root', str(root), '--data', str(base/'data'),
                                  '--open-left', str(note), '--smoke-test'], env=env, capture_output=True,
                                 text=True, timeout=40)
            result_path = base/'data/smoke-result.json'
            details = run.stdout+run.stderr
            if stacks.exists():details += '\n'+stacks.read_text(encoding='utf-8')
            if result_path.exists():details += '\n'+result_path.read_text(encoding='utf-8')
            self.assertEqual(run.returncode, 0, details)
            result = json.loads(result_path.read_text(encoding='utf-8'))
            self.assertEqual(result['entry'], 'WorkspaceShell')
            self.assertIn('v0.8.10 Pre-alpha2', result['title'])
            self.assertTrue(result['ok'])
            self.assertEqual(result['reading'], 'ready')
            self.assertEqual(result['blocked_urls'], [])
            self.assertEqual(note.read_bytes(), original)
            self.assertFalse((base/'wrong-data').exists())

    def test_launchers_and_shared_identity_are_current(self):
        self.assertEqual(CURRENT.flag, '--v0.8.10-pre-alpha.2')
        with tempfile.TemporaryDirectory() as directory:
            write_launchers(directory, CURRENT)
            for suffix in ('.cmd', '.vbs'):
                text = (Path(directory)/(CURRENT.launcher+suffix)).read_text()
                self.assertIn('--v0.8.10-pre-alpha.2', text)
                self.assertNotIn('--v0.8.8-alpha', text)
