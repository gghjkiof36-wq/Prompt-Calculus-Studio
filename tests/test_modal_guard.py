"""A stuck real modal loop must fail a child test, not silently cancel/pass."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest


class ModalGuardTests(unittest.TestCase):
    def run_child(self, body):
        paths=json.dumps(sys.path)
        script=('import sys;sys.path[:0]='+paths+'\n'
                'import unittest\n'
                'from PySide6.QtWidgets import QApplication,QDialog,QMessageBox,QMenu\n'
                'from PySide6.QtCore import QTimer\n'
                'from _modal_guard import install\n'
                'install()\napp=QApplication([])\n'+body)
        started=time.monotonic()
        result=subprocess.run([sys.executable,'-I','-c',script],capture_output=True,text=True,
                              encoding='utf-8',errors='replace',timeout=20,
                              env=dict(os.environ,PCS_TEST_MODAL_GUARD='1'))
        result.seconds=time.monotonic()-started
        return result

    def test_stuck_exec_fails_with_identity_and_call_stack(self):
        result=self.run_child('''class StuckDialog(QDialog):pass
class StuckCase(unittest.TestCase):
    def test_stuck(self):
        dialog=StuckDialog();dialog.setObjectName('guard-self-test');dialog.setWindowTitle('never closed')
        dialog.exec()
unittest.main(verbosity=2)
''')
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertGreaterEqual(result.seconds,5)
        self.assertLess(result.seconds,10)
        for text in ('AssertionError','StuckDialog',"objectName='guard-self-test'",
                     "title='never closed'",'StuckCase.test_stuck','Modal timeout after 5s'):
            self.assertIn(text,result.stderr)
        print(json.dumps(dict(expected_child_failure=True, code=result.returncode,
                              seconds=result.seconds, stderr=result.stderr)))

    def test_single_shot_dialog_message_box_menu_and_product_dialog_return_normally(self):
        result=self.run_child('''from prompt_calculus_studio.widgets import StudioDialog as Dialog
class NormalCase(unittest.TestCase):
    def test_normal(self):
        for kind in (QDialog,QMessageBox,Dialog):
            dialog=kind();QTimer.singleShot(30,dialog.accept)
            self.assertEqual(dialog.exec(),QDialog.Accepted)
        menu=QMenu();menu.addAction('sample');QTimer.singleShot(30,menu.close)
        self.assertIsNone(menu.exec())
unittest.main(verbosity=2)
''')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('Modal timeout',result.stderr)
