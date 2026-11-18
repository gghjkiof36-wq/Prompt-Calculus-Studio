import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from prompt_calculus_studio.releases import CURRENT

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('pcs_source_startup', str(ROOT/'launch_source.pyw'))
spec = importlib.util.spec_from_loader(loader.name, loader)
startup = importlib.util.module_from_spec(spec)
loader.exec_module(startup)


class SourceStartupTests(unittest.TestCase):
    def make_home(self, root):
        home = root/'PCS source with spaces'
        (home/'.venv/Scripts').mkdir(parents=True)
        (home/'.venv/Scripts/python.exe').touch()
        (home/'run.py').touch()
        return home

    def test_hidden_child_keeps_arguments_and_uses_this_release(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(Path(directory))
            process = Mock(pid=21); process.wait.return_value = 0
            errors = []
            with patch.object(startup.subprocess, 'Popen', return_value=process) as spawn:
                status = startup.launch(home, ['--root', 'D:/notes with spaces/筆記'], error_dialog=errors.append)
            self.assertEqual(status, 0); self.assertEqual(errors, [])
            args, kwargs = spawn.call_args
            self.assertEqual(args[0][-3:], [CURRENT.argument, '--root', 'D:/notes with spaces/筆記'])
            self.assertEqual(args[0][0], str(home/'.venv/Scripts/python.exe'))
            self.assertEqual(kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)
            self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            self.assertEqual(kwargs['cwd'], home.resolve())
            self.assertIn('exited with status: 0', next((home/'logs').glob('*.log')).read_text())

    def test_failed_child_preserves_log_and_reports_it(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(Path(directory)); errors = []
            process = Mock(pid=22); process.wait.return_value = 3
            with patch.object(startup.subprocess, 'Popen', return_value=process):
                self.assertEqual(startup.launch(home, error_dialog=errors.append), 3)
            log = next((home/'logs').glob('*.log'))
            self.assertIn('status: 3', log.read_text()); self.assertIn(str(log), errors[0])

    def test_missing_python_does_not_start_a_child(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(Path(directory)); (home/'.venv/Scripts/python.exe').unlink(); errors = []
            with patch.object(startup.subprocess, 'Popen') as spawn:
                self.assertEqual(startup.launch(home, error_dialog=errors.append), 1)
            spawn.assert_not_called()
            self.assertIn('FileNotFoundError', next((home/'logs').glob('*.log')).read_text())
            self.assertEqual(len(errors), 1)

    def test_missing_entry_does_not_start_a_child(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(Path(directory)); (home/'run.py').unlink(); errors = []
            with patch.object(startup.subprocess, 'Popen') as spawn:
                self.assertEqual(startup.launch(home, error_dialog=errors.append), 1)
            spawn.assert_not_called(); self.assertEqual(len(errors), 1)

    def test_process_start_failure_is_logged(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(Path(directory)); errors = []
            with patch.object(startup.subprocess, 'Popen', side_effect=OSError('start denied')):
                self.assertEqual(startup.launch(home, error_dialog=errors.append), 1)
            self.assertIn('start denied', next((home/'logs').glob('*.log')).read_text())

    def test_log_creation_failure_does_not_start_a_child(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(Path(directory)); (home/'logs').write_text('keep this file'); errors = []
            with patch.object(startup.subprocess, 'Popen') as spawn:
                self.assertEqual(startup.launch(home, error_dialog=errors.append), 1)
            spawn.assert_not_called(); self.assertEqual((home/'logs').read_text(), 'keep this file')
            self.assertEqual(len(errors), 1)

    def test_repeated_startup_preserves_each_log(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self.make_home(Path(directory)); process = Mock(pid=23); process.wait.return_value = 0
            with patch.object(startup.subprocess, 'Popen', return_value=process):
                self.assertEqual(startup.launch(home, error_dialog=self.fail), 0)
                first = next((home/'logs').glob('*.log')); original = first.read_bytes()
                self.assertEqual(startup.launch(home, error_dialog=self.fail), 0)
            self.assertEqual(len(list((home/'logs').glob('*.log'))), 2)
            self.assertEqual(first.read_bytes(), original)
