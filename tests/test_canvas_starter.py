import copy,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from prompt_studio.core import Storage,initial_state,validate_state
from prompt_studio.state_loading import prepare_state
from prompt_studio.canvas_starter import initialize,guide
from prompt_studio.multi_output import PREVIEW,compile_output
from prompt_studio.flow_data import incoming
from prompt_studio.stage_model import compile_plan


def fresh():return prepare_state(initial_state(),multi=True)


class CanvasStarterDataTests(unittest.TestCase):
    def test_new_layout_has_ordered_typed_path_but_no_workflow_or_private_data(self):
        original=fresh();before=copy.deepcopy(original);state=initialize(original,fresh_install=True)
        self.assertEqual(original,before);validate_state(state)
        refs=guide(state);data=state['multi_output'];channel=refs['scheduler']+'::clip1'
        self.assertEqual(incoming(state,refs['output'],'text'),refs['canvas'])
        self.assertEqual(incoming(state,channel,'clip'),refs['output'])
        self.assertEqual(incoming(state,refs['clip'],'clip'),channel)
        self.assertEqual(incoming(state,refs['stage'],'control'),refs['clip'])
        self.assertEqual(incoming(state,PREVIEW,'image'),refs['stage'])
        keys=[refs['output'],refs['scheduler'],refs['clip'],refs['stage'],PREVIEW]
        xs=[data['canvases'][refs['canvas']]['position'][0]]+[state['text_positions'][key][0] for key in keys]
        self.assertEqual(xs,sorted(xs));self.assertEqual(len(set(xs)),6)
        self.assertFalse(state.get('generation',{}).get('profiles'));self.assertFalse(data['bindings'])
        self.assertIsNone(data['stages'][refs['stage']]['workflow'])
        self.assertFalse(state.get('uses'));self.assertEqual(compile_output(state,refs['output'])['final_prompt'],'')
        with self.assertRaisesRegex(ValueError,'請選擇工作流'):compile_plan(state)

    def test_existing_layout_manual_empty_and_repeat_initialization_are_unchanged(self):
        state=fresh();state['text_positions']['custom']=[241,432];state['draft']=''
        before=copy.deepcopy(state)
        self.assertIs(initialize(state),state);self.assertIs(initialize(state,fresh_install=True),state)
        self.assertEqual(state,before)
        seeded=initialize(fresh(),fresh_install=True)
        self.assertIs(initialize(seeded,fresh_install=True),seeded)
        moved=fresh();next(iter(moved['multi_output']['canvases'].values()))['position']=[11,22]
        self.assertIs(initialize(moved,fresh_install=True),moved)
        disconnected=fresh();disconnected['multi_output']['connections']=[]
        self.assertIs(initialize(disconnected,fresh_install=True),disconnected)

    def test_save_reopen_scene_and_dismissal_preserve_graph_and_layout(self):
        from prompt_studio.workspace_scene import capture,switch,create
        state=initialize(fresh(),fresh_install=True);refs=guide(state)
        state['text_positions'][refs['stage']]=[2800,110]
        state['multi_output']['outputs'][refs['output']]['draft']=''
        state['draft']='';refs['dismissed']=True;capture(state)
        old=state['workspace'];new=create(state,'第二個工作區');switch(state,new)
        self.assertEqual(guide(state)['workspace'],new);self.assertFalse(guide(state)['dismissed']);switch(state,old)
        self.assertEqual(state['text_positions'][refs['stage']],[2800,110])
        with tempfile.TemporaryDirectory() as folder:
            store=Storage(folder);store.save(state);restored=store.load_current(multi=True);store.db.close()
        self.assertEqual(restored,state)
        self.assertTrue(guide(restored)['dismissed']);self.assertEqual(restored['draft'],'')

    def test_new_workspace_has_six_nodes_without_changing_existing_edits_or_bindings(self):
        from prompt_studio.workspace_scene import capture,switch,create,scene
        from prompt_studio.generation import store_profile
        from prompt_studio.clip_flow import set_binding
        from prompt_studio.stage_model import choose
        from prompt_studio.drafts import edit
        from test_multi_output import workflow
        state=initialize(fresh(),fresh_install=True);refs=guide(state);old=state['workspace']
        store_profile(state,workflow());set_binding(state,'flow',refs['clip'],('6','text'))
        choose(state,refs['stage'],'flow');edit(state,'',refs['output'])
        state['text_positions'][refs['stage']]=[2900,145];state['canvas_view']=[.67,480,620]
        refs['dismissed']=True;capture(state)
        original=scene(state);profiles=copy.deepcopy(state['generation']['profiles']);settings=copy.deepcopy(state['settings'])
        created=create(state,'新的預設流程')
        self.assertEqual(state['workspace'],old);self.assertEqual(scene(state),original)
        self.assertEqual(state['settings'],settings);self.assertEqual(state['generation']['profiles'],profiles)
        switch(state,created);validate_state(state);new_refs=guide(state)
        self.assertEqual(new_refs['workspace'],created);self.assertFalse(new_refs['dismissed'])
        channel=new_refs['scheduler']+'::clip1';data=state['multi_output']
        for destination,kind,source in ((new_refs['output'],'text',new_refs['canvas']),
                (channel,'clip',new_refs['output']),(new_refs['clip'],'clip',channel),
                (new_refs['stage'],'control',new_refs['clip']),(PREVIEW,'image',new_refs['stage'])):
            self.assertEqual(incoming(state,destination,kind),source)
        self.assertEqual(len(data['connections']),5);self.assertFalse(data['bindings'])
        self.assertIsNone(data['stages'][new_refs['stage']]['workflow'])
        self.assertEqual(state['generation']['profiles'],profiles);self.assertFalse(state['uses'])
        new_refs['dismissed']=True;capture(state)
        with tempfile.TemporaryDirectory() as folder:
            store=Storage(folder);store.save(state);restored=store.load_current(multi=True);store.db.close()
        self.assertTrue(guide(restored)['dismissed']);switch(restored,old)
        self.assertEqual(scene(restored),original);self.assertEqual(guide(restored),refs)

    def test_legacy_document_and_duplicate_keep_their_original_layout(self):
        from prompt_studio.workspace_scene import capture,switch,create,scene
        legacy=fresh();legacy['generation']=dict(mode='txt2img',profiles=[],chosen={},source=None)
        capture(legacy);original=scene(legacy)
        self.assertIs(initialize(legacy,fresh_install=False),legacy)
        duplicate=create(legacy,'保留既有布局',duplicate=True);switch(legacy,duplicate)
        self.assertEqual(scene(legacy),original);self.assertIsNone(guide(legacy))
        self.assertFalse(legacy['multi_output']['schedulers']);self.assertFalse(legacy['multi_output']['stages'])


class CanvasStarterUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
        if cls.app.platformName()=='offscreen':
            from PySide6.QtGui import QFontDatabase
            for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
                QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)

    def setUp(self):
        from prompt_studio.window import Window
        self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_DATA':self.tmp.name})
        self.env.start();state=initialize(fresh(),fresh_install=True);state['settings'].update(online=False,material='solid')
        store=Storage(self.tmp.name);store.save(state);store.db.close()
        self.window=Window(self.tmp.name);self.window.show();self.window.set_interface_mode('canvas')
        self.wait();self.canvas=self.window.canvas;self.refs=guide(self.window.state)
        self.canvas.restore_view()

    def wait(self):
        from PySide6.QtTest import QTest
        QTest.qWait(40)

    def tearDown(self):
        self.window.close();self.app.processEvents();self.env.stop();self.tmp.cleanup()

    def test_readable_start_and_responsive_guide_do_not_rearrange_nodes(self):
        before=copy.deepcopy(self.window.state['text_positions']);guidebar=self.canvas.onboarding
        self.assertTrue(guidebar.isVisible());self.assertGreaterEqual(self.canvas.view.transform().m11(),.8)
        for width,height in [(1440,900),(960,620),(1200,760)]:
            self.window.resize(width,height);self.wait()
            self.assertEqual(self.window.state['text_positions'],before)
            for action in guidebar.actions:
                self.assertGreater(action.width(),100)
                self.assertTrue(guidebar.card.rect().contains(action.geometry().center()))
            card=guidebar.card.geometry()
            self.assertGreaterEqual(card.left(),12);self.assertGreaterEqual(card.top(),12)
            self.assertGreaterEqual(guidebar.width()-card.right()-1,12)
            self.assertGreaterEqual(guidebar.height()-card.bottom()-1,12)
            self.assertLessEqual(guidebar.card.height(),guidebar.card.sizeHint().height()+2)

    def test_compact_guide_emphasizes_only_the_current_step(self):
        from PySide6.QtCore import QPointF
        bar=self.canvas.onboarding
        for width in (1440,800,640):
            self.window.resize(width,740);self.wait()
            self.assertLessEqual(bar.card.height(),96)
            self.assertEqual([action.property('currentStep') for action in bar.actions],[True,False,False,False])
            self.assertEqual(bar.actions[0].objectName(),'Primary')
            self.assertTrue(all(action.isVisible() and action.isEnabled() for action in bar.actions))
        point=self.canvas.containers[self.refs['canvas']].pos()+QPointF(35,100)
        self.canvas.add_tag('synthetic guidance text',point);self.wait()
        self.assertEqual([action.property('currentStep') for action in bar.actions],[False,True,False,False])
        self.assertEqual(bar.actions[1].accessibleDescription(),'目前步驟')

    def test_first_canvas_after_settings_overrides_unpresented_view_saved_by_mode_change(self):
        from prompt_studio.window import Window
        with tempfile.TemporaryDirectory() as folder:
            window=Window(folder)
            window.state['settings'].update(online=False,material='solid')
            window.settings('appearance');window.resize(640,640);window.show();self.wait()
            window.enter_canvas();window.persist();self.wait()
            refs=guide(window.state);card=window.canvas.containers[refs['canvas']]
            corner=window.canvas.view.mapFromScene(card.scenePos())
            self.assertGreaterEqual(corner.x(),0);self.assertLess(corner.x(),80)
            self.assertGreaterEqual(corner.y(),0);self.assertLess(corner.y(),80)
            self.assertGreaterEqual(window.canvas.view.transform().m11(),.8)
            window.close();self.app.processEvents()

    def test_database_without_a_saved_document_still_gets_the_six_node_starter(self):
        from prompt_studio.window import Window
        with tempfile.TemporaryDirectory() as folder:
            store=Storage(folder);store.db.close()
            window=Window(folder)
            try:
                self.assertTrue(window.fresh_install)
                refs=guide(window.state);self.assertIsNotNone(refs)
                self.assertEqual(incoming(window.state,refs['stage'],'control'),refs['clip'])
                self.assertEqual(incoming(window.state,PREVIEW,'image'),refs['stage'])
            finally:window.close();self.app.processEvents()

    def test_saved_legacy_default_is_preserved_until_user_creates_a_new_workspace(self):
        from prompt_studio.window import Window
        from prompt_studio.workspace_scene import scene
        with tempfile.TemporaryDirectory() as folder:
            state=fresh();state['generation']=dict(mode='txt2img',profiles=[],chosen={},source=None);state['output_order']=[]
            store=Storage(folder);store.save(state);before=scene(state);store.db.close()
            window=Window(folder)
            try:
                self.assertFalse(window.fresh_install);self.assertIsNone(guide(window.state))
                self.assertEqual(scene(window.state),before)
                self.assertFalse(window.state['multi_output']['stages'])
                self.assertFalse(window.state['multi_output']['schedulers'])
            finally:window.close();self.app.processEvents()

    def test_large_font_guide_card_keeps_actions_inside_and_natural_height(self):
        from PySide6.QtCore import QPoint,QRect,QPointF
        bar=self.canvas.onboarding
        self.window.state['settings']['ui_size']=18;self.window.apply_theme(preserve_layout=True)
        for width in (1440,640):
            self.window.resize(width,640);self.wait();self.canvas.onboarding.first_view();self.wait()
            rectangles=[QRect(action.mapTo(bar.card,QPoint()),action.size()) for action in bar.actions]
            self.assertTrue(all(bar.card.rect().contains(rectangle) for rectangle in rectangles))
            self.assertFalse(any(a.intersects(b) for index,a in enumerate(rectangles) for b in rectangles[index+1:]))
            self.assertLessEqual(bar.card.height(),bar.card.sizeHint().height()+2)
            node=self.canvas.containers[self.refs['canvas']]
            bottom=self.canvas.view.mapFromScene(node.scenePos()+QPointF(0,node.height)).y()
            self.assertLess(bottom,self.canvas.execution_bar.y())

    def test_guide_actions_open_real_entries_without_submitting(self):
        bar=self.canvas.onboarding
        with patch.object(self.window,'settings') as settings:
            bar.connection();settings.assert_called_once_with('comfy')
        with patch.object(self.canvas,'bind_dialog') as bind:
            bar.bind_clip();bind.assert_called_once_with(self.refs['clip'])
        with patch('prompt_studio.stage_parameter_panel.open_parameters') as stage:
            bar.stage();stage.assert_called_once_with(self.canvas,self.refs['stage'])
        with patch.object(self.canvas,'palette') as palette:
            bar.add_text();self.assertEqual(palette.call_count,1)

    def test_resize_and_return_keep_starting_edge_without_rearranging_nodes(self):
        self.window.resize(1440,900);self.wait();self.canvas.onboarding.first_view()
        view=self.canvas.view;origin=view.mapToScene(0,0);scale=view.transform().m11()
        positions=copy.deepcopy(self.window.state['text_positions'])
        for width,height in ((800,740),(1440,900),(640,700)):
            self.window.resize(width,height);self.wait();self.window.set_interface_mode('canvas');self.wait()
            actual=view.mapToScene(0,0)
            self.assertLess((actual-origin).manhattanLength(),3)
            corner=view.mapFromScene(self.canvas.containers[self.refs['canvas']].scenePos())
            self.assertGreaterEqual(corner.x(),0);self.assertGreaterEqual(corner.y(),0)
            self.assertEqual(view.transform().m11(),scale)
        self.window.settings('appearance');self.window.resize(960,760);self.wait()
        self.window.return_to_prompt();self.wait()
        self.assertLess((view.mapToScene(0,0)-origin).manhattanLength(),3)
        self.assertEqual(self.window.state['text_positions'],positions)

    def test_resizing_and_returning_preserve_manual_pan_and_zoom(self):
        self.canvas.onboarding.focus(self.refs['stage']);view=self.canvas.view
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value()+137)
        view.verticalScrollBar().setValue(view.verticalScrollBar().value()+83)
        origin=view.mapToScene(0,0);scale=view.transform().m11()
        self.window.settings('appearance');self.window.resize(800,740);self.wait()
        self.window.return_to_prompt();self.wait()
        self.assertLess((view.mapToScene(0,0)-origin).manhattanLength(),3)
        self.assertEqual(view.transform().m11(),scale)
        self.window.resize(1200,900);self.wait()
        self.assertLess((view.mapToScene(0,0)-origin).manhattanLength(),3)

    def test_short_windows_keep_first_node_above_integrated_execution_bar(self):
        from PySide6.QtCore import QPointF
        view=self.canvas.view;bar=self.canvas.execution_bar
        self.window.resize(1440,900);self.wait();self.canvas.onboarding.first_view()
        original=copy.deepcopy(self.canvas.data()['canvases'][self.refs['canvas']]['display_size'])
        for width,height in ((800,640),(640,540)):
            self.window.resize(width,height);self.wait();self.window.set_interface_mode('canvas');self.wait()
            card=self.canvas.containers[self.refs['canvas']]
            top=view.mapFromScene(card.scenePos()).y()
            bottom=view.mapFromScene(card.scenePos()+QPointF(0,card.height)).y()
            self.assertGreaterEqual(top,0)
            self.assertGreaterEqual(view.viewport().height()-bottom,72)
            self.assertLess(bottom,bar.y())
            self.assertFalse(hasattr(self.canvas,'recent_corner'))
            self.assertTrue(bar.more.isVisible())
            menu=bar.tools_menu();self.assertNotIn('最近生成',[action.text() for action in menu.actions()]);menu.deleteLater()
            self.assertTrue(view.viewport().rect().contains(bar.geometry()))
            self.assertLessEqual(bar.width(),view.viewport().width()-24)
            self.assertLessEqual(bar.height(),96)
            self.assertEqual(bar.controls.count.suffix(),' 次')
            self.assertEqual(bar.controls.stop.text(),'取消');self.assertFalse(bar.controls.stop.icon().isNull())
            self.assertTrue(all(port.caption.font().pixelSize()*view.transform().m11()>=12 for port in self.canvas.ports.values()))
        self.assertEqual(self.canvas.data()['canvases'][self.refs['canvas']]['display_size'],original)

    def test_run_controls_keep_count_and_cancel_scope_with_clear_labels(self):
        from prompt_studio.stage_model import add as add_stage
        controls=self.canvas.execution_bar.controls;client=self.window.comfy
        self.assertEqual(controls.count.suffix(),' 次');self.assertEqual(controls.count.accessibleName(),'執行次數')
        controls.count.setValue(3);self.assertEqual(self.window.state['settings']['comfy_count'],3)
        self.canvas.commit(lambda state:add_stage(state,(2600,600)));self.wait();controls.refresh()
        self.assertEqual(controls.count.suffix(),' 輪');self.assertEqual(controls.count.accessibleName(),'流程輪數')
        self.assertEqual(controls.count.value(),3);self.assertEqual(controls.stop.accessibleName(),'取消執行')
        self.assertEqual(controls.stop.fontMetrics().height(),controls.run_button.fontMetrics().height())
        client.connected=True
        with patch.object(client.input_flow,'has_work',return_value=True),patch.object(client,'interrupt') as cancel:
            controls.refresh();self.assertTrue(controls.stop.isEnabled());controls.stop.click()
            cancel.assert_called_once_with()
        self.assertIn('取消本次流程',controls.stop.toolTip())

    def test_edit_undo_and_reopen_keep_manual_empty_and_start_connections(self):
        from PySide6.QtCore import QPointF
        original=copy.deepcopy(self.canvas.data()['connections'])
        point=self.canvas.containers[self.refs['canvas']].pos()+QPointF(35,100)
        self.assertTrue(self.canvas.add_tag('synthetic test text',point));self.wait()
        self.assertIn('synthetic test text',compile_output(self.window.state,self.refs['output'])['final_prompt'])
        self.canvas.undo();self.wait();self.assertFalse(self.window.state['uses'])
        self.assertEqual(self.canvas.data()['connections'],original)
        self.canvas.redo();self.wait();self.assertTrue(self.window.state['uses'])
        from prompt_studio.drafts import edit
        self.canvas.commit(lambda state:edit(state,'',self.refs['output']));self.wait()
        self.assertEqual(self.canvas.data()['outputs'][self.refs['output']]['draft'],'')
        self.canvas.onboarding.dismiss();self.window.persist()
        saved=self.window.store.load_current(multi=True)
        self.assertEqual(saved['multi_output']['outputs'][self.refs['output']]['draft'],'')
        self.assertEqual(saved['multi_output']['connections'],original)
        self.assertTrue(guide(saved)['dismissed'])


if __name__=='__main__':unittest.main()
