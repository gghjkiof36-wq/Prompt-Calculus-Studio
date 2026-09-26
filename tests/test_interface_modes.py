import copy,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import QApplication,QFileDialog
from PySide6.QtTest import QTest
from prompt_studio.window import Window
from prompt_studio.core import Storage,initial_state,build_prompt
from prompt_studio.composition import node
from prompt_studio.generation_panel import WorkflowDialog
from prompt_studio.media import import_image
from test_comfy_integration import png
from test_generation import workflow
APP=QApplication.instance() or QApplication([])


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        # Keep these legacy snapshot/collection fixtures independent of the
        # caller's mode. New CLIP flows have dedicated multi-Canvas tests.
        mode=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'0'})
        mode.start(); self.addCleanup(mode.stop)
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'qa'); self.root=Path(self.tmp.name)
        self.w=Window(self.root/'data'); self.w.state['settings'].update(online=False,material='solid'); self.w.apply_theme(); self.w.show(); QTest.qWait(40)
    def tearDown(self): self.w.close(); APP.processEvents(); self.tmp.cleanup()

    def test_first_choice_and_full_settings_preserve_both_drafts(self):
        w=self.w; w.start_interface(); self.assertIs(w.surface_stack.currentWidget(),w.welcome)
        self.assertEqual(w.welcome.mode(),'canvas')
        self.assertEqual([c.property('mode') for c in w.welcome.choices.buttons()],['canvas','list'])
        w.set_interface_mode('list'); w.final.setPlainText('list draft'); w.settings()
        self.assertIs(w.surface_stack.currentWidget(),w.settings_page)
        self.assertFalse(w.settings_page.preferences.isVisible())
        for choice in w.settings_page.interface.choices.buttons(): choice.setChecked(choice.property('mode')=='canvas')
        w.settings_page.save(); QTest.qWait(60)
        self.assertTrue(w.canvas.isVisible()); self.assertTrue(w.prompt_modes.tabBar().isHidden())
        self.assertEqual(w.state['settings']['interface_mode'],'canvas')
        w.final.setPlainText('canvas draft'); w.settings('appearance'); w.settings_page.cancel(); QTest.qWait(20)
        self.assertEqual(w.final.toPlainText(),'canvas draft'); w.set_interface_mode('list')
        self.assertEqual(w.final.toPlainText(),'list draft'); w.set_interface_mode('canvas'); self.assertEqual(w.final.toPlainText(),'canvas draft')

    def test_saved_canvas_populates_on_first_show_without_toggling(self):
        w=self.w; w.set_interface_mode('canvas'); w.canvas.add_tag('landscape'); expected=build_prompt(w.state)
        w.close(); APP.processEvents(); self.w=Window(self.root/'data'); self.w.show(); QTest.qWait(120)
        self.assertTrue(self.w.canvas.isVisible()); self.assertEqual(build_prompt(self.w.state),expected)
        self.assertTrue(self.w.canvas.cards); self.assertIsNotNone(self.w.canvas.output.proxy.widget())
        self.assertGreater(self.w.canvas.view.viewport().width(),800)
        self.assertTrue(self.w.canvas.view.mapToScene(self.w.canvas.view.viewport().rect()).boundingRect().intersects(self.w.canvas.output.sceneBoundingRect()))

    def test_import_dialog_uses_host_window_and_cancel_preserves_canvas(self):
        w=self.w; w.set_interface_mode('canvas'); w.canvas.add_tag('white background'); before=copy.deepcopy(w.state)
        observed=[]
        def cancel():
            dialog=APP.activeModalWidget(); observed.append(isinstance(dialog,QFileDialog) and dialog.parentWidget() is w and dialog.testOption(QFileDialog.Option.DontUseNativeDialog)); dialog.reject()
        QTimer.singleShot(80,cancel); w.generation_panel.import_workflow(); QTest.qWait(40)
        self.assertEqual(observed,[True]); self.assertTrue(w.isVisible()); self.assertTrue(w.canvas.isVisible())
        self.assertEqual(w.state,before)

    def test_import_mapping_from_settings_keeps_current_prompt(self):
        w=self.w; w.set_interface_mode('canvas'); w.final.setPlainText('keep prompt'); w.generation_panel.mode.setCurrentIndex(1); w.settings('workflows')
        path=self.root/'workflow.json'; path.write_text(json.dumps(workflow()['graph']),encoding='utf-8')
        w.generation_panel.import_workflow(path)
        self.assertTrue(w.isVisible()); self.assertIs(w.surface_stack.currentWidget(),w.settings_page)
        self.assertEqual(w.final.toPlainText(),'keep prompt'); self.assertEqual(len(w.state['generation']['profiles']),1)

    def test_canvas_media_uses_compact_shell_and_returns(self):
        w=self.w; w.set_interface_mode('canvas'); w.show_page(w.gallery)
        self.assertIs(w.surface_stack.currentWidget(),w.canvas_shell); self.assertIs(w.canvas_content.currentWidget(),w.gallery)
        w.return_to_prompt(); QTest.qWait(40); self.assertIs(w.canvas_content.currentWidget(),w.canvas)
        self.assertLessEqual(w.canvas_header.height(),50); self.assertEqual(w.tabs.indexOf(w.models),-1)
        w.settings('models'); self.assertIs(w.settings_page.pages.currentWidget(),w.settings_page.comfy_content)
        self.assertIs(w.settings_page.comfy_tabs.currentWidget(),w.models)
        self.assertTrue(w.settings_page.comfy_content.isAncestorOf(w.models))
        self.assertEqual([w.settings_page.navigation.item(i).text() for i in range(w.settings_page.navigation.count())].count('ComfyUI'),1)
        for section in ('workflows','models','nodes'):
            w.settings(section); self.assertIs(w.settings_page.pages.currentWidget(),w.settings_page.comfy_content)

    def test_preview_collection_freezes_selected_result_when_new_one_arrives(self):
        w=self.w; w.set_interface_mode('canvas'); w.final.setPlainText('unchanged')
        def add(ident):
            path=self.root/(ident+'.png'); png(path,{})
            record=import_image(path,w.store.directory,''); record.update(id=ident,source={'prompt_id':ident,'image':{'filename':path.name,'subfolder':'','type':'output'}},collected={})
            w.catalog.put('recent',record); w.canvas.results.refresh(); return record
        first=add('first'); result=w.canvas.results; w.comfy.connected=True; calls=[]
        destination=self.root/'saved'; destination.mkdir(); w.state['settings'].update(recent_external=str(destination),recent_destination='external'); w.recent.refresh_destinations(); result.refresh_destination()
        with patch.object(w.comfy,'request',side_effect=lambda *args,**kwargs:calls.append((args,kwargs))):
            result.save(); add('second')
        self.assertEqual(calls[0][0][1]['prompt_id'],'first'); self.assertEqual(w.final.toPlainText(),'unchanged')
        self.assertIs(w.surface_stack.currentWidget(),w.canvas_shell)
        calls[0][0][3]('test request finished')

    def test_older_backend_cannot_accept_new_canvas_snapshot(self):
        w=self.w; w.set_interface_mode('canvas'); w.canvas.add_tag('landscape'); w.comfy.ready=True; w.comfy.connected=True; w.comfy.snapshot_versions=[1]
        self.assertFalse(w.comfy.can_run)
        with patch.object(w.comfy,'request') as request: w.comfy.run(); w.comfy.sync(); request.assert_not_called()
        w.comfy.snapshot_versions=[1,2,3]; self.assertTrue(w.comfy.can_run)

    def test_canvas_notice_fits_text_and_wraps_only_when_needed(self):
        w=self.w; w.set_interface_mode('canvas'); w.notice('已加入'); QTest.qWait(20)
        short=w.canvas_status.size(); self.assertLess(short.width(),180)
        w.notice('已將這份素材加入目前的模組'); medium=w.canvas_status.size()
        self.assertGreater(medium.width(),short.width()); self.assertEqual(medium.height(),short.height())
        w.notice('這是一段較長的提示訊息，需要在畫布內完整顯示。'*20)
        self.assertLessEqual(w.canvas_status.width(),min(720,w.canvas.view.viewport().width()-16))
        self.assertGreater(w.canvas_status.height(),short.height())
        w.notice('完成'); self.assertLess(w.canvas_status.width(),medium.width()); self.assertEqual(w.canvas_status.height(),short.height())

if __name__=='__main__': unittest.main()
