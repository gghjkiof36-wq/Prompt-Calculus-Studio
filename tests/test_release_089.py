"""Exercise the delivered entry point rather than only its version string."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import PySide6
from prompt_calculus_studio.releases import CURRENT, RELEASES, write_launchers


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
            (base/'tmp').mkdir()
            env.update({key: str(base/'tmp') for key in ('TEMP', 'TMP', 'TMPDIR')})
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
            self.assertIn('v0.8.10 Pre-alpha3', result['title'])
            self.assertTrue(result['ok'])
            self.assertEqual(result['reading'], 'ready')
            self.assertEqual(result['blocked_urls'], [])
            self.assertEqual(note.read_bytes(), original)
            self.assertFalse((base/'wrong-data').exists())

    def test_launchers_and_shared_identity_are_current(self):
        self.assertEqual(CURRENT.flag, '--v0.8.10-pre-alpha.3')
        self.assertEqual(CURRENT.folder, 'v0.8.10-pre-alpha.3')
        self.assertEqual(CURRENT.launcher, 'Start-PCS-v0.8.10-Pre-alpha3')
        self.assertEqual(CURRENT.argument, '--v0.8.10-pre-alpha.3')
        with tempfile.TemporaryDirectory() as directory:
            write_launchers(directory, CURRENT)
            for suffix in ('.cmd', '.vbs'):
                text = (Path(directory)/(CURRENT.launcher+suffix)).read_text()
                self.assertIn('--v0.8.10-pre-alpha.3', text)
                self.assertNotIn('--v0.8.8-alpha', text)

    def test_pre_alpha2_extension_is_an_update_even_at_the_same_commit(self):
        from prompt_calculus_studio.extension_install import update_state
        old = '--v0.8.10-pre-alpha.2'
        new = '--v0.8.10-pre-alpha.3'
        self.assertGreater(list(RELEASES).index(new), list(RELEASES).index(old))
        installed = dict(release=old, git_head='2d5fc3f895cb3a12a0666adc4cb1221dbaa1275b')
        for head in (installed['git_head'], 'a'*40):
            with self.subTest(head=head):
                candidate = dict(release=new, git_head=head)
                self.assertEqual(update_state(candidate, installed), 'update')
                self.assertEqual(update_state(installed, candidate), 'newer')
                self.assertEqual(update_state(candidate, candidate), 'current')

    def test_old_and_new_flags_keep_workspace_locations_and_preferences(self):
        import contextlib
        import io
        import run
        import run_workspace
        from prompt_calculus_studio import workspace_profile
        from prompt_calculus_studio.extension_install import InstallPreferences
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            program = base/'program'
            program.mkdir()
            (program/'files').mkdir()
            root, data = base/'files', base/'data'
            root.mkdir(); data.mkdir()
            (root/'note.md').write_text('# Retained note', encoding='utf-8')
            preferences = InstallPreferences(data)
            preferences.value = dict(root=str(base/'ComfyUI'), automatic=False)
            preferences.save()
            workspace_profile.remember(program, root, data)
            profile_before = (program/'PCS-WORKSPACE.json').read_bytes()
            preferences_before = (data/'extension-install.json').read_bytes()
            # Exercise both public entry points and the real CLI parser/check.
            # A synthetic frozen program directory isolates the saved profile.
            for entry in (run.main, run_workspace.main):
                for flag in ('--v0.8.10-pre-alpha.2', '--v0.8.10-pre-alpha.3'):
                    for mode in ('profile', 'environment', 'explicit'):
                        with self.subTest(entry=entry.__module__, flag=flag, mode=mode):
                            argv = ['run.py', flag, '--check']
                            env = dict(os.environ)
                            env.pop('PROMPT_STUDIO_DATA', None)
                            expected_root = root
                            if mode == 'environment':
                                env['PROMPT_STUDIO_DATA'] = str(data)
                                expected_root = program/'files'
                            elif mode == 'explicit':
                                env['PROMPT_STUDIO_DATA'] = str(base/'wrong-data')
                                argv += ['--root', str(root), '--data', str(data)]
                            output = io.StringIO()
                            with patch.object(sys, 'argv', argv), patch.dict(os.environ, env, clear=True), \
                                    patch.object(sys, 'frozen', True, create=True), \
                                    patch.object(sys, 'executable', str(program/'PromptCalculusStudio.exe')), \
                                    contextlib.redirect_stdout(output):
                                self.assertEqual(entry(), 0)
                            checked = json.loads(output.getvalue())
                            self.assertEqual(Path(checked['root']), expected_root)
                            self.assertEqual(Path(checked['data']), data)
                            self.assertEqual(checked['check'], 'passed')
            self.assertEqual((program/'PCS-WORKSPACE.json').read_bytes(), profile_before)
            self.assertEqual((data/'extension-install.json').read_bytes(), preferences_before)
            self.assertFalse((base/'wrong-data').exists())
            # Both generated launchers keep the same portable data expression.
            for release in (RELEASES['--v0.8.10-pre-alpha.2'], CURRENT):
                write_launchers(program, release)
                cmd = (program/(release.launcher+'.cmd')).read_text(encoding='utf-8')
                vbs = (program/(release.launcher+'.vbs')).read_text(encoding='utf-8')
                self.assertIn('PROMPT_STUDIO_DATA=%~dp0data', cmd)
                self.assertIn('files.BuildPath(folder, "data")', vbs)
                self.assertIn(release.argument, cmd)
                self.assertIn(release.argument, vbs)
