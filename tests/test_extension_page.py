import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QProcess
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout
from package_documents import package_manifest
from prompt_studio.extension_install import comfy_root, update_state, build_info, InstallPreferences
from prompt_studio.extension_page import ExtensionPage
from prompt_studio.releases import CURRENT, EXTENSION_FOLDER
from test_manager_page import Window

ROOT = Path(__file__).resolve().parents[1]


class ExtensionPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'qa')
        self.root = Path(self.temp.name)
        self.comfy = self.root/'ComfyUI'; (self.comfy/'custom_nodes').mkdir(parents=True)
        (self.comfy/'main.py').write_text('# inert fixture')
        self.base = self.root/'PCS'; self.source = self.base/'extensions'/EXTENSION_FOLDER
        self.source.mkdir(parents=True)
        self.info = dict(version=CURRENT.version, release=CURRENT.flag, git_head='a'*40, working_changes='')
        for name, content in {'__init__.py':'# inert', 'web/prompt_studio.js':'// inert', 'SOURCE_MANIFEST.json':'[]', 'PromptCalculusStudio-source.zip':'inert'}.items():
            target=self.source/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content)
        self.info['source_sha256'] = hashlib.sha256((self.source/'PromptCalculusStudio-source.zip').read_bytes()).hexdigest()
        (self.source/'BUILD_INFO.json').write_text(json.dumps(self.info), encoding='utf-8')
        package_manifest(self.source, 'comfyui')
        self.data=self.root/'data'; self.data.mkdir(); (self.data/'studio.sqlite3').write_bytes(b'inert database')
        self.window=Window(self.data)
        # Execute the real product script with only its process inventory isolated.
        quoted=str(ROOT/'install_comfyui.ps1').replace("'", "''")
        (self.base/'install_comfyui.ps1').write_text("$ErrorActionPreference='Stop'\nfunction Get-CimInstance { @() }\n& '"+quoted+"' @args\nexit $LASTEXITCODE", encoding='utf-8-sig')
        self.page=self.make_page()

    def make_page(self):
        with patch('prompt_studio.extension_page.available_package', return_value=(self.base,self.source)):
            return ExtensionPage(self.window)

    def tearDown(self):
        if self.page.busy:
            self.wait_install()
        self.page.shutdown(); self.page.close(); self.page.deleteLater(); self.window.close()
        self.app.processEvents(); self.temp.cleanup()

    def wait_install(self):
        for _ in range(300):
            self.app.processEvents()
            if not self.page.busy: return
            QTest.qWait(40)
        self.fail('Synthetic installer did not finish')

    def select(self, path=None):
        with patch('prompt_studio.extension_page.QFileDialog.getExistingDirectory',return_value=str(path or self.comfy)):
            self.page.choose.click()

    def test_choose_folder_installs_without_connection_workflow_or_manager(self):
        self.assertFalse(self.window.comfy.enabled)
        self.select(self.comfy/'custom_nodes'); self.wait_install()
        target=self.comfy/'custom_nodes'/EXTENSION_FOLDER
        self.assertTrue((target/'web/prompt_studio.js').is_file())
        self.assertEqual(json.loads((target/'local_library.json').read_text())['library'],str(self.data/'studio.sqlite3'))
        self.assertIn('已安裝',self.page.status.text())
        self.assertFalse(self.page.update.isEnabled())
        self.assertEqual(InstallPreferences(self.data).value['root'],str(self.comfy))
        self.assertTrue(self.page.log.isEnabled())

    def test_auto_update_preserves_original_library_and_old_program_backup(self):
        old=self.comfy/'custom_nodes'/'comfyui_prompt_studio'; old.mkdir()
        (old/'BUILD_INFO.json').write_text(json.dumps(dict(self.info,release='--v086-revision8',git_head='b'*40)))
        (old/'obsolete.py').write_text('# previous')
        config=json.dumps(dict(library=str(self.root/'original/studio.sqlite3'),extra='keep')).encode()
        (old/'local_library.json').write_bytes(config)
        self.page.preferences.value['root']=str(self.comfy); self.page.preferences.save()
        self.page.startup(); self.wait_install()
        target=old.with_name(EXTENSION_FOLDER)
        self.assertEqual((target/'local_library.json').read_bytes(),config)
        self.assertFalse((target/'obsolete.py').exists())
        backup=next((self.comfy/'.pcs-program-backups').glob('previous-*'))
        self.assertEqual((backup/'local_library.json').read_bytes(),config)
        self.assertTrue((backup/'obsolete.py').exists())

    def test_corrupt_candidate_fails_without_mutation_or_automatic_retry(self):
        (self.source/'__init__.py').write_text('# corrupt')
        self.select(); self.wait_install()
        self.assertEqual(list((self.comfy/'custom_nodes').iterdir()),[])
        self.assertIn('未完成',self.page.status.text())
        self.assertFalse(self.page.waiting); self.assertFalse(self.page.retry_timer.isActive())

    def test_auto_opt_out_keeps_installation_untouched(self):
        self.page.preferences.value.update(root=str(self.comfy),automatic=False)
        with patch.object(self.page.process,'start') as start:
            self.page.startup(); start.assert_not_called()
        self.assertEqual(list((self.comfy/'custom_nodes').iterdir()),[])

    def test_wait_for_stop_only_retries_explicit_preflight_deferral(self):
        self.page.finished(20,QProcess.ExitStatus.NormalExit)
        self.assertTrue(self.page.waiting); self.assertTrue(self.page.retry_timer.isActive())
        self.page.automatic.setChecked(False)
        self.assertFalse(self.page.waiting); self.assertFalse(self.page.retry_timer.isActive())
        self.assertNotIn('會自動接續',self.page.status.text())
        self.page.finished(1,QProcess.ExitStatus.NormalExit)
        self.assertFalse(self.page.retry_timer.isActive())

    def test_loaded_status_requires_same_folder_and_fresh_process_identity(self):
        self.select(); self.wait_install()
        self.window.comfy.connected=True
        self.window.comfy.extension_info=dict(self.info,root=str(self.comfy/'custom_nodes'/EXTENSION_FOLDER),git_head='b'*40)
        self.window.comfy.stateChanged.emit(); self.assertNotIn('已安裝並載入',self.page.status.text())
        self.window.comfy.extension_info['git_head']='a'*40
        self.window.comfy.stateChanged.emit(); self.assertIn('已安裝並載入',self.page.status.text())
        self.window.comfy.connected=False
        self.window.comfy.stateChanged.emit(); self.assertNotIn('已安裝並載入',self.page.status.text())

    def test_future_and_unrecognized_versions_are_not_silently_replaced(self):
        self.assertEqual(update_state(dict(self.info,release='--v086-revision8'),self.info),'newer')
        self.assertEqual(update_state(self.info,dict(self.info,release='--future',git_head='b'*40)),'unknown')
        self.assertEqual(update_state(self.info,dict(self.info,git_head='b'*40)),'unknown')
        target=self.comfy/'custom_nodes'/EXTENSION_FOLDER; target.mkdir()
        (target/'unrecognized.py').write_text('keep')
        self.select(); self.assertFalse(self.page.busy)
        self.assertEqual((target/'unrecognized.py').read_text(),'keep')
        self.assertIn('無法確認',self.page.status.text())

    def test_path_selection_cancellation_and_invalid_folder_write_nothing(self):
        with patch('prompt_studio.extension_page.QFileDialog.getExistingDirectory',return_value=''):
            self.page.choose.click()
        self.assertFalse(self.page.preferences.path.exists())
        with self.assertRaises(ValueError):comfy_root(self.data)
        self.assertEqual(comfy_root(self.comfy/'custom_nodes'),self.comfy)

    def test_narrow_page_buttons_stack_and_keep_install_available(self):
        self.page.resize(510,650); self.page.show(); QTest.qWait(20)
        self.assertEqual(self.page.actions.direction(),QBoxLayout.Direction.TopToBottom)
        self.assertTrue(self.page.choose.isEnabled())
        self.assertTrue(self.page.choose.isVisible())

    def test_main_window_cannot_close_halfway_through_replacement(self):
        from prompt_studio.window import Window as MainWindow
        from unittest.mock import Mock
        fake=SimpleNamespace(settings_page=SimpleNamespace(manager=SimpleNamespace(extension=SimpleNamespace(busy=True))),notice=Mock())
        event=Mock(); MainWindow.closeEvent(fake,event)
        event.ignore.assert_called_once(); fake.notice.assert_called_once()

    def test_loaded_build_identity_does_not_change_with_files_on_disk(self):
        module_path=self.root/'build_identity.py'
        shutil.copyfile(ROOT/'comfyui_prompt_studio/build_identity.py',module_path)
        info=self.root/'BUILD_INFO.json'; info.write_text(json.dumps(self.info))
        spec=importlib.util.spec_from_file_location('isolated_build_identity',module_path)
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        info.write_text(json.dumps(dict(self.info,git_head='b'*40)))
        self.assertEqual(module.LOADED_BUILD['git_head'],'a'*40)
        self.assertEqual(module.read_identity()['git_head'],'b'*40)
