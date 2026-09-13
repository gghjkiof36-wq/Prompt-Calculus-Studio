import copy,os,tempfile,unittest
from unittest.mock import patch
from PySide6.QtCore import Qt,QTimer,QPoint,QPointF
from PySide6.QtGui import QContextMenuEvent,QImage,QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QCheckBox
from prompt_studio.window import Window
from prompt_studio import multi_output as model
from prompt_studio.widgets import RoundMenu
from prompt_studio.workflow_catalog import workflow_path
from prompt_studio.core import validate_state
from test_multi_output import workflow,workspace
APP=QApplication.instance() or QApplication([])

class Alpha3Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1'}); self.env.start()
        self.w=Window(self.temp.name); self.w.state['settings'].update(online=False,material='solid',reduce_motion=True); self.w.show(); self.w.set_interface_mode('canvas'); QTest.qWait(20)
        self.c=self.w.canvas; self.cid=next(iter(self.c.data()['canvases'])); self.oid=self.c.data()['current_output']
    def tearDown(self):
        for widget in APP.topLevelWidgets():
            if isinstance(widget,QDialog): widget.reject()
        self.w.close(); APP.processEvents(); self.env.stop(); self.temp.cleanup()
    def modal(self,callback,title,accept=True,check=False):
        seen=[]; timer=QTimer(); timer.setSingleShot(True)
        def close():
            dialog=APP.activeModalWidget()
            if dialog:
                seen.append(dialog.windowTitle())
                if check: dialog.findChild(QCheckBox).setChecked(True)
                dialog.accept() if accept else dialog.reject()
        timer.timeout.connect(close); timer.start(40)
        try: result=callback()
        finally: timer.stop()
        self.assertEqual(seen,[title]); return result
    def editor(self): self.c.open_editor(self.cid); QTest.qWait(20); return self.c.editor_page
    def test_actual_first_tool_dimensions_crop_preview_and_binding_dialogs(self):
        e=self.editor()
        self.modal(lambda:QTest.mouseClick(e.tool_buttons['rect'],Qt.MouseButton.LeftButton),'底圖尺寸')
        self.assertEqual(e.image()['width'],1024); self.assertEqual(e.tool,'rect')
        view=e.view; a=view.mapFromScene(QPointF(100,100)); b=view.mapFromScene(QPointF(300,300))
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=a); QTest.mouseMove(view.viewport(),b,20); QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=b); QTest.qWait(20)
        self.assertEqual(e.image()['layers'][-1]['type'],'rect')
        self.modal(e.preview,'輸出預覽',False)
        path=self.w.store.directory/'crop.png'; image=QImage(32,24,QImage.Format.Format_RGB32); image.fill(QColor('red')); image.save(str(path))
        e.import_images(paths=[str(path)]); self.modal(e.crop,'裁切圖片')
        e.close_editor(); self.w.generation_panel.save_profile(workflow()); self.modal(lambda:self.c.bind_dialog(self.oid),'綁定工作流文字欄')
    def test_editor_pan_text_creation_ownership_resize_and_right_menu(self):
        e=self.editor(); view=e.view; a=QPoint(70,75); b=a+QPoint(90,45)
        for button in [Qt.MouseButton.LeftButton,Qt.MouseButton.MiddleButton]:
            before=view.mapToScene(a)
            QTest.mousePress(view.viewport(),button,pos=a); QTest.mouseMove(view.viewport(),b,20); QTest.mouseRelease(view.viewport(),button,pos=b)
            self.assertGreater((view.mapToScene(a)-before).manhattanLength(),20)
        before=self.c.data()['canvases'][self.cid]['members'][:]
        e.text_canvas.add_tag('test composition',QPointF(141,152)); QTest.qWait(20)
        data=self.c.data()['canvases'][self.cid]; key=next(k for k in data['members'] if k not in before)
        self.assertEqual(data['edit_positions'][key],[141,152]); self.assertEqual(model.owner(self.w.state,key),self.cid)
        outer=copy.deepcopy(self.w.state['text_positions'][key]); card=e.text_canvas.cards[key]
        e.text_canvas.resize_card(key,card.width+20,card.height+20,[120,130]); QTest.qWait(20)
        self.assertEqual(self.c.data()['canvases'][self.cid]['edit_positions'][key],[120,130]); self.assertEqual(self.w.state['text_positions'][key],outer)
        captured=[]
        with patch.object(RoundMenu,'open_at',lambda menu,pos:captured.extend(a.text() for a in menu.actions())):
            view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse,QPoint(8,8),view.mapToGlobal(QPoint(8,8))))
        self.assertIn('加入 Tag 與素材',captured); self.assertIn('匯入圖片…',captured)
        validate_state(self.w.state)
    def test_all_port_labels_are_inside_and_do_not_cover_controls(self):
        self.c.add_output(); self.c.functions.add_image(); self.c.refresh()
        for port in self.c.ports.values():
            parent=port.parentItem(); rect=port.caption.mapRectToParent(port.caption.boundingRect()).translated(port.pos())
            self.assertGreaterEqual(rect.left(),0); self.assertLessEqual(rect.right(),parent.width)
            if hasattr(parent,'proxy'): self.assertLess(rect.bottom(),parent.proxy.y())
    def test_names_migrate_preserving_custom_names_drafts_and_text(self):
        state,cid,oid=workspace(); data=state['multi_output']; keys=list(data['outputs'])
        data['outputs'][keys[0]]['name']='最終 Prompt'; data['outputs'][keys[1]]['name']='最終 Prompt 2'; data['outputs'][keys[2]]['name']='自訂文字'
        original=model.compiled_outputs(state); changed=model.migrate(state)
        self.assertEqual([v['name'] for v in changed['multi_output']['outputs'].values()],['prompt輸出1','prompt輸出2','自訂文字'])
        text=lambda values:{k:(v['generated_prompt'],v['final_prompt']) for k,v in values.items()}
        self.assertEqual(text(original),text(model.compiled_outputs(changed))); self.assertEqual(model.migrate(changed),changed)
        added=self.c.add_output(); self.assertEqual(self.c.data()['outputs'][added]['name'],'prompt輸出2')
    def test_catalog_filter_layout_and_persistent_delete_confirmation(self):
        self.w.generation_panel.save_profile(workflow()); m=self.w.settings_page.workflow_manager
        m.catalog.files=[f'folder-{n%10}/workflow-{n}.json' for n in range(500)]; m.catalog.loaded=True; m.rebuild()
        self.assertEqual(m.list.count(),501); self.assertLess(m.list.height(),400)
        m.query.setText('workflow-499'); self.assertEqual(m.list.count(),1)
        m.query.clear(); m.source.setCurrentIndex(m.source.findData('desktop')); self.assertEqual(m.list.count(),1)
        before=copy.deepcopy(self.w.state['generation'])
        self.modal(m.remove,'刪除工作流',False,True); self.assertEqual(before,self.w.state['generation']); self.assertFalse(self.w.state['settings'].get('skip_workflow_delete_confirmation',False))
        self.modal(m.remove,'刪除工作流',True,True); self.assertEqual(m.list.count(),0); self.assertTrue(self.w.state['settings']['skip_workflow_delete_confirmation'])
        m.undo_remove(); self.assertEqual(m.list.count(),1); self.assertEqual(self.w.state['generation']['profiles'][0]['id'],'flow')
        m.remove(); self.w.store.save(self.w.state)
        self.assertEqual(self.w.store.db.execute('SELECT COUNT(*) FROM workflow_deletions').fetchone()[0],1)
        self.assertEqual(self.w.settings_page.comfy_tabs.count(),3)
    def test_remote_workflow_links_stable_identity_without_mass_import(self):
        m=self.w.settings_page.workflow_manager; path='folder/saved.json'; m.catalog.files=[path]; m.catalog.loaded=True; m.rebuild()
        m.source.setCurrentIndex(m.source.findData('comfy')); entry=m.entry()
        self.assertEqual(len(self.w.state.get('generation',{}).get('profiles',[])),0)
        with patch.object(m.catalog,'read',lambda p,done,failed:done(workflow()['graph'])): m.activate()
        self.assertEqual(len(self.w.state['generation']['profiles']),1); p=self.w.state['generation']['profiles'][0]
        self.assertEqual(p['id'],entry['id']); self.assertEqual(p['origin']['path'],path)
        with patch.object(m.catalog,'read',lambda p,done,failed:done(workflow()['graph'])): m.activate()
        self.assertEqual(len(self.w.state['generation']['profiles']),1)
        for bad in ['/outside.json','../outside.json','foo/../../outside.json','C:/x.json','bad\\file.json']:
            with self.assertRaises(ValueError): workflow_path(bad)
    def test_catalog_empty_directory_and_reconnection_release_stale_operation(self):
        from prompt_studio.comfy_client import RequestFailure
        m=self.w.settings_page.workflow_manager
        with patch.object(self.w.comfy,'request',lambda route,done,failed:failed(RequestFailure('missing',True,404))): m.catalog.refresh()
        self.assertTrue(m.catalog.loaded); self.assertFalse(m.catalog.busy); self.assertEqual(m.catalog.files,[])
        pending=[]
        with patch.object(self.w.comfy,'request',lambda route,done,failed:pending.append(done)): m.catalog.refresh()
        m.loading=True; self.w.comfy.epoch+=1; self.w.comfy.stateChanged.emit(); pending[0](['stale.json'])
        self.assertFalse(m.loading); self.assertFalse(m.catalog.busy); self.assertEqual(m.catalog.files,[])
