"""Picker contracts through the offscreen fallback; native Windows visuals need manual QA."""
import copy,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QFileDialog
from PySide6.QtTest import QTest
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.local_files import choose_file
from prompt_calculus_studio.source_controls import choose_many
from test_comfy_integration import png

APP=QApplication.instance() or QApplication([])
IMAGE_FILTER='圖片 (*.png *.jpg *.jpeg *.webp *.bmp)'


class NativeFileDialogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_DATA':str(self.root/'data')})
        self.env.start();self.w=Window(self.root/'data')
        self.w.state['settings'].update(online=False,material='solid')
        self.w.show();self.w.set_interface_mode('canvas');QTest.qWait(30)
        self.folder=self.root/'圖片 sources';self.folder.mkdir()
        self.files=[self.folder/name for name in ('sample 10.png','sample 2.png')]
        for path in self.files:png(path,{})
        self.key=self.w.canvas.functions.add_image()
        self.w.persist()

    def tearDown(self):
        self.w.close();APP.processEvents();self.env.stop();self.tmp.cleanup()

    def panel(self):return self.w.canvas.functions.cards[self.key].panel

    def picker(self,action,mode,*,selection=None,filters=None):
        observed=[]
        def respond():
            dialog=APP.activeModalWidget()
            if not isinstance(dialog,QFileDialog):
                observed.append(None)
                if dialog:dialog.reject()
                return
            observed.append(dict(parent=dialog.parentWidget(),mode=dialog.fileMode(),
                non_native=dialog.testOption(QFileDialog.Option.DontUseNativeDialog),
                filters=dialog.nameFilters(),directory=dialog.directory().absolutePath(),
                directories_only=dialog.testOption(QFileDialog.Option.ShowDirsOnly)))
            if selection is None:dialog.reject()
            else:
                dialog.setDirectory(str(selection.parent));dialog.selectFile(str(selection))
                QTimer.singleShot(30,dialog.accept)
        QTimer.singleShot(60,respond)
        result=action();APP.processEvents()
        self.assertEqual(len(observed),1);self.assertIsNotNone(observed[0])
        info=observed[0];self.assertIs(info['parent'],self.w);self.assertEqual(info['mode'],mode)
        self.assertFalse(info['non_native'])
        if mode==QFileDialog.FileMode.Directory:self.assertTrue(info['directories_only'])
        if filters:self.assertEqual(info['filters'],[filters])
        return result,info

    def test_single_selection_remembers_folder_and_cancel_keeps_it(self):
        self.w.state['settings']['import_directory']=str(self.folder)
        selected,info=self.picker(lambda:choose_file(self.w,'選擇圖片',IMAGE_FILTER),
            QFileDialog.FileMode.ExistingFile,selection=self.files[0],filters=IMAGE_FILTER)
        self.assertEqual(Path(selected),self.files[0]);self.assertEqual(Path(info['directory']),self.folder)
        self.assertEqual(Path(self.w.state['settings']['import_directory']),self.folder)
        before=copy.deepcopy(self.w.state)
        cancelled,_=self.picker(lambda:choose_file(self.w,'選擇圖片',IMAGE_FILTER),QFileDialog.FileMode.ExistingFile)
        self.assertEqual(cancelled,'');self.assertEqual(self.w.state,before)

    def test_missing_remembered_folder_does_not_block_cancel(self):
        self.w.state['settings']['import_directory']=str(self.root/'missing')
        before=copy.deepcopy(self.w.state)
        result,_=self.picker(lambda:choose_file(self.w,'匯入工作流','工作流 (*.json)'),
            QFileDialog.FileMode.ExistingFile,filters='工作流 (*.json)')
        self.assertEqual(result,'');self.assertEqual(self.w.state,before)

    def test_image_workflow_and_embed_entries_keep_main_owner_and_cancel_state(self):
        canvas=next(iter(self.w.canvas.data()['canvases']))
        entries=[(self.panel().choose_file,IMAGE_FILTER),(self.w.generation_panel.choose_source,IMAGE_FILTER),
            (self.w.generation_panel.import_workflow,'工作流 (*.json)'),
            (lambda:self.w.canvas.import_canvas_image(canvas),IMAGE_FILTER)]
        for action,filters in entries:
            with self.subTest(entry=action):
                before=copy.deepcopy(self.w.state);undo=len(self.w.canvas.undo_stack)
                self.picker(action,QFileDialog.FileMode.ExistingFile,filters=filters)
                self.assertEqual(self.w.state,before);self.assertEqual(len(self.w.canvas.undo_stack),undo)

    def test_multiple_and_folder_entries_use_native_policy_and_cancel_without_edits(self):
        entries=[(lambda:choose_many(self.panel()),QFileDialog.FileMode.ExistingFiles),
            (lambda:choose_many(self.panel(),True),QFileDialog.FileMode.Directory),
            (self.w.recent.choose_directory,QFileDialog.FileMode.Directory)]
        for action,mode in entries:
            with self.subTest(mode=mode,entry=action):
                before=copy.deepcopy(self.w.state);undo=len(self.w.canvas.undo_stack)
                with patch.object(self.w.comfy.input_flow,'source_active',return_value=True),patch.object(self.w,'notice') as notice:
                    self.picker(action,mode)
                notice.assert_not_called();self.assertEqual(self.w.state,before)
                self.assertEqual(len(self.w.canvas.undo_stack),undo)

    def test_image_import_from_picker_can_undo_redo_and_reopen(self):
        self.picker(self.panel().choose_file,QFileDialog.FileMode.ExistingFile,selection=self.files[0],filters=IMAGE_FILTER)
        image=copy.deepcopy(self.w.state['canvas_functions']['images'][self.key]['source'])
        self.assertEqual(image['name'],self.files[0].name)
        self.assertTrue((self.w.store.directory/image['relative']).is_file())
        self.w.canvas.undo();self.assertIsNone(self.w.state['canvas_functions']['images'][self.key]['source'])
        self.w.canvas.redo();self.assertEqual(self.w.state['canvas_functions']['images'][self.key]['source'],image)
        self.w.persist();self.w.close();APP.processEvents();self.w=Window(self.root/'data')
        self.assertEqual(self.w.state['canvas_functions']['images'][self.key]['source'],image)

    def test_multiple_and_folder_import_preserve_natural_order(self):
        (self.folder/'ignore.txt').write_text('not an image',encoding='utf-8')
        for folder in (False,True):
            with self.subTest(folder=folder),patch('prompt_calculus_studio.source_controls.edit_list') as edit:
                if folder:
                    with patch.object(QFileDialog,'getExistingDirectory',return_value=str(self.folder)):
                        choose_many(self.panel(),True)
                else:
                    with patch.object(QFileDialog,'getOpenFileNames',return_value=([str(p) for p in self.files],IMAGE_FILTER)):
                        choose_many(self.panel())
                items=self.w.state['canvas_functions']['images'][self.key]['items']
                self.assertEqual([item['name'] for item in items],['sample 2.png','sample 10.png'])
                self.assertTrue(all((self.w.store.directory/item['relative']).is_file() for item in items))
                edit.assert_called_once();self.w.canvas.undo()

    def test_recent_folder_selection_persists_and_cancel_does_not_replace_it(self):
        self.picker(self.w.recent.choose_directory,QFileDialog.FileMode.Directory,selection=self.folder)
        self.assertEqual(self.w.recent.destination(),(str(self.folder),''))
        self.w.persist();before=copy.deepcopy(self.w.state)
        self.picker(self.w.recent.choose_directory,QFileDialog.FileMode.Directory)
        self.assertEqual(self.w.state,before)
        self.w.close();APP.processEvents();self.w=Window(self.root/'data')
        self.assertEqual(self.w.recent.destination(),(str(self.folder),''))


if __name__=='__main__':unittest.main()
