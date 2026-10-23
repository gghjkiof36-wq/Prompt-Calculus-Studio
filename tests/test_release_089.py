"""Exercise the delivered entry point rather than only its version string."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from prompt_calculus_studio.releases import CURRENT, SHARED_MODULES, write_launchers


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
                       PROMPT_STUDIO_DATA=str(base/'wrong-data'))
            run = subprocess.run([sys.executable, 'run.py', CURRENT.argument,
                                  '--root', str(root), '--data', str(base/'data'),
                                  '--open-left', str(note), '--smoke-test'], env=env, capture_output=True,
                                 text=True, timeout=40)
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
            result = json.loads((base/'data/smoke-result.json').read_text())
            self.assertEqual(result['entry'], 'WorkspaceShell')
            self.assertIn('v0.8.9 Beta3', result['title'])
            self.assertTrue(result['ok'])
            self.assertEqual(result['reading'], 'ready')
            self.assertEqual(result['blocked_urls'], [])
            self.assertEqual(note.read_bytes(), original)
            self.assertFalse((base/'wrong-data').exists())

    def test_launchers_and_shared_identity_are_current(self):
        self.assertEqual(CURRENT.flag, '--v0.8.9-beta.3')
        self.assertEqual(len(SHARED_MODULES), 34)
        with tempfile.TemporaryDirectory() as directory:
            write_launchers(directory, CURRENT)
            for suffix in ('.cmd', '.vbs'):
                text = (Path(directory)/(CURRENT.launcher+suffix)).read_text()
                self.assertIn('--v0.8.9-beta.3', text)
                self.assertNotIn('--v0.8.8-alpha', text)
