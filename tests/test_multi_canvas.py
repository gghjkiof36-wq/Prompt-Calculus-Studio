import copy,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF,Qt
from PySide6.QtTest import QTest
from prompt_studio.window import Window
from prompt_studio import multi_output as model
from prompt_studio import clip_flow
from prompt_studio.core import build_prompt
from test_multi_output import workflow
APP=QApplication.instance() or QApplication([])


class MultiCanvasTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1'}); self.env.start()
        self.window=Window(self.tmp.name); self.w=self.window; self.w.state['settings'].update(online=False,material='solid',separate_selections=True)
        self.w.show(); self.w.set_interface_mode('canvas'); QTest.qWait(30); self.canvas=self.w.canvas
        self.cid=next(iter(self.canvas.containers)); self.oid=self.canvas.data()['current_output']
    def tearDown(self): self.w.close(); APP.processEvents(); self.env.stop(); self.tmp.cleanup()
    def add(self,text='test',cid=None):
        old=set(self.w.state.get('uses',{})); p=self.canvas.containers[cid or self.cid].pos()+QPointF(35,85)
        self.assertTrue(self.canvas.add_tag(text,p)); return (set(self.w.state['uses'])-old).pop()
    def test_function_sizes_survive_first_move_refresh_resize_and_undo(self):
        canvas=self.canvas
        canvas.functions.add_image(position=QPointF(-700,100))
        QTest.qWait(20)
        def sizes():
            cards=[*canvas.outputs.values(),*canvas.clips.values(),*canvas.functions.cards.values(),canvas.preview_card]
            return {c.key:(c.width,c.height) for c in cards}
        initial=sizes(); source=next(iter(canvas.functions.cards))
        canvas.move_cards({source:[-650,110]}); QTest.qWait(20)
        self.assertEqual(sizes(),initial)
        canvas.refresh(); QTest.qWait(20); self.assertEqual(sizes(),initial)
        for key,(width,height) in initial.items():
            canvas.resize_card(key,width+170,height+120)
            expected=sizes()
            canvas.move_cards({source:[-620,115]}); QTest.qWait(20)
            self.assertEqual(sizes(),expected)
            canvas.refresh(); QTest.qWait(20); self.assertEqual(sizes(),expected)
        enlarged=sizes(); canvas.reset_size(canvas.preview_card.key)
        self.assertEqual(sizes()[canvas.preview_card.key],initial[canvas.preview_card.key])
        canvas.undo(); self.assertEqual(sizes(),enlarged)
        self.w.persist(); self.w.close(); APP.processEvents()
        self.w=Window(self.tmp.name); self.w.show(); self.w.set_interface_mode('canvas'); QTest.qWait(50)
        canvas=self.w.canvas; self.assertEqual(sizes(),enlarged)
    def test_inside_and_outside_addition_and_undo(self):
        root=self.add(); before=build_prompt(self.w.state)
        self.assertTrue(self.canvas.add_tag('outside',QPointF(-2200,-1200)))
        outside=next(k for k in self.w.state['uses'] if k!=root)
        self.assertIsNone(model.owner(self.w.state,outside)); self.assertEqual(build_prompt(self.w.state),before)
        self.canvas.undo(); self.assertNotIn(outside,self.w.state['uses']); self.canvas.redo(); self.assertIn(outside,self.w.state['uses'])
    def test_expansion_clamp_does_not_resize_image_or_adopt_outside(self):
        root=self.add(); before=list(self.canvas.data()['canvases'][self.cid]['display_size'])
        self.canvas.resize_card(root,1200,1100); c=self.canvas.containers[self.cid]; card=self.canvas.cards[root]
        self.assertGreater(c.width,before[0]); self.assertTrue(c.sceneBoundingRect().contains(card.sceneBoundingRect()))
        self.canvas.move_cards({root:[c.x()+5000,c.y()+5000]}); c=self.canvas.containers[self.cid]; card=self.canvas.cards[root]
        self.assertTrue(c.sceneBoundingRect().contains(card.sceneBoundingRect())); self.assertIsNone(self.canvas.data()['canvases'][self.cid]['image'])
        self.canvas.undo(); self.assertIn(root,self.w.state['uses'])
    def test_drop_between_containers_preserves_content_and_order(self):
        root=self.add('first'); other=self.canvas.add_canvas(QPointF(1300,-400)); card=self.canvas.cards[root]
        card.setSelected(True); position=self.canvas.containers[other].pos()+QPointF(45,70); card.setPos(position)
        self.canvas.finish_node_drop(card,position); QTest.qWait(15)
        self.assertEqual(model.owner(self.w.state,root),other); self.assertEqual(build_prompt(self.w.state),'')
        self.canvas.undo(); self.assertEqual(model.owner(self.w.state,root),self.cid)
    def test_output_edit_binding_and_typed_replacement_undo(self):
        self.add('positive'); c2=self.canvas.add_canvas(QPointF(1200,-600)); o2=self.canvas.add_output(QPointF(2100,-600)); self.add('negative',c2)
        self.canvas.commit(lambda s:model.connect(s,c2,o2,'text'))
        self.w.generation_panel.save_profile(workflow())
        clip=next(k for k in self.canvas.data()['clip_inputs'] if clip_flow.source(self.w.state,k)==self.oid)
        clip2=self.canvas.add_clip(QPointF(2700,-600),o2)
        self.canvas.commit(lambda s:clip_flow.set_binding(s,'flow',clip,('6','text')))
        self.canvas.commit(lambda s:clip_flow.set_binding(s,'flow',clip2,('7','text')))
        self.canvas.outputs[o2].panel.editor.setPlainText('manual negative')
        self.assertEqual(model.compile_output(self.w.state,o2)['final_prompt'],'manual negative')
        self.assertIn('有效綁定',self.canvas.clips[clip2].panel.status.text())
        self.assertFalse(hasattr(self.canvas.outputs[o2].panel,'binding'))
        line=next(c for c in self.canvas.data()['connections'] if c['destination']==o2)
        self.canvas.commit(lambda s:model.disconnect(s,line['id'])); self.assertIn('有效綁定',self.canvas.clips[clip2].panel.status.text())
        self.assertEqual(model.compile_output(self.w.state,o2)['final_prompt'],'manual negative')
        self.canvas.undo(); self.assertEqual(model.compile_output(self.w.state,o2)['final_prompt'],'manual negative')
    def test_canvas_drag_carries_members_and_ports_connect_by_gesture(self):
        root=self.add(); canvas=self.canvas; c=canvas.containers[self.cid]; card=canvas.cards[root]
        start=QPointF(card.pos()); canvas.view.resetTransform(); canvas.view.centerOn(c)
        a=canvas.view.mapFromScene(c.pos()+QPointF(45,25)); b=a+QPointF(60,35).toPoint()
        QTest.mousePress(canvas.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,a)
        QTest.mouseMove(canvas.view.viewport(),b,30); QTest.mouseRelease(canvas.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,b); QTest.qWait(20)
        card=canvas.cards[root]; self.assertAlmostEqual(card.x()-start.x(),60,delta=3); self.assertAlmostEqual(card.y()-start.y(),35,delta=3)
    def test_reopen_keeps_lines_text_and_manual_drafts(self):
        self.add('saved'); self.canvas.outputs[self.oid].panel.editor.setPlainText('kept draft'); self.w.persist()
        expected=copy.deepcopy(self.canvas.data()); self.w.close(); APP.processEvents(); self.w=Window(self.tmp.name); self.window=self.w
        self.assertEqual(self.w.state['multi_output'],expected); self.assertEqual(self.w.state['draft'],'kept draft')

    def test_default_canvas_and_later_list_preference_keep_controls_and_drafts(self):
        self.assertEqual(self.w.state['selection_view'],'canvas')
        self.canvas.outputs[self.oid].panel.editor.setPlainText('canvas manual')
        self.w.set_interface_mode('list'); self.w.final.setPlainText('list manual'); self.w.persist()
        self.w.close(); APP.processEvents(); self.w=Window(self.tmp.name); self.window=self.w; self.w.show(); QTest.qWait(20)
        self.assertEqual(self.w.state['selection_view'],'list'); self.assertEqual(self.w.state['draft'],'list manual')
        self.assertTrue(self.w.run_controls.isVisible())
        self.w.set_interface_mode('canvas'); self.assertEqual(self.w.state['draft'],'canvas manual')

    def test_clip_picker_selects_workflow_then_node_without_name_input(self):
        from prompt_studio.clip_widgets import ClipBindingDialog
        first=workflow(); second=copy.deepcopy(first); second.update(id='other',name='Other workflow')
        self.w.generation_panel.save_profile(first); self.w.generation_panel.save_profile(second)
        key=next(iter(self.canvas.clips)); other=self.canvas.add_clip(QPointF(1400,300),self.oid)
        self.canvas.commit(lambda s:clip_flow.set_binding(s,'other',other,('6','text')))
        dialog=ClipBindingDialog(self.canvas,key)
        dialog.workflow.setCurrentIndex(dialog.workflow.findData('other'))
        taken=dialog.target_index(('6','text')); self.assertFalse(dialog.target.model().item(taken).isEnabled())
        dialog.target.setCurrentIndex(dialog.target_index(('7','text'))); dialog.apply()
        self.assertIn(dict(workflow='other',clip=key,node='7',field='text'),self.canvas.data()['bindings'])
        self.assertFalse(hasattr(self.canvas.clips[key].panel,'name'))
        self.assertFalse(hasattr(self.canvas.outputs[self.oid].panel,'name'))
        before=copy.deepcopy(self.canvas.data()); self.w.state['generation']['profiles'][1]['graph']['7']['inputs']['text']='changed'
        dialog.target.setCurrentIndex(0); dialog.apply(); self.assertEqual(self.canvas.data(),before)
        dialog.close()

    def test_clip_rewire_drag_geometry_undo_and_execution_bar_stays_in_viewport(self):
        self.add('one'); canvas=self.canvas; clip=next(iter(canvas.clips)); bar=canvas.execution_bar
        self.assertEqual(self.w.state['selection_view'],'canvas'); self.assertTrue(bar.isVisible())
        self.assertFalse(any(getattr(item,'key',None)==model.GENERATOR for item in canvas.view.scene().items()))
        before=bar.geometry(); canvas.view.scale(.7,.7); canvas.view.centerOn(2000,2000); APP.processEvents()
        self.assertEqual(bar.geometry(),before)
        card=canvas.clips[clip]; old=QPointF(card.pos()); canvas.move_cards({clip:[old.x()+60,old.y()+80]})
        canvas.undo(); self.assertEqual(canvas.clips[clip].pos(),old); canvas.redo()
        c2=canvas.add_canvas(QPointF(1300,-600)); o2=canvas.add_output(QPointF(2200,-600)); self.add('two',c2)
        canvas.commit(lambda s:model.connect(s,c2,o2,'text'))
        canvas.commit(lambda s:model.connect(s,o2,clip,'clip'))
        self.assertEqual(clip_flow.source(self.w.state,clip),o2); canvas.undo()
        self.assertEqual(clip_flow.source(self.w.state,clip),self.oid)
        canvas.commit(lambda s:clip_flow.remove(s,clip)); self.assertNotIn(clip,canvas.clips)
        canvas.undo(); self.assertIn(clip,canvas.clips)

    def test_real_port_drag_replacement_reject_type_and_undo(self):
        canvas=self.canvas; c2=canvas.add_canvas(QPointF(-500,-600))
        canvas.view.resetTransform(); canvas.view.scale(.5,.5)
        def drag(source,destination):
            a=canvas.ports[source].scenePos(); b=canvas.ports[destination].scenePos()
            canvas.view.centerOn((a+b)/2); QTest.qWait(5)
            start=canvas.view.mapFromScene(a); end=canvas.view.mapFromScene(b)
            QTest.mousePress(canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=start)
            QTest.mouseMove(canvas.view.viewport(),end,30)
            QTest.mouseRelease(canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=end); QTest.qWait(20)
        drag((c2,'image',True),(self.oid,'text',False))
        self.assertEqual(canvas.data()['outputs'][self.oid]['canvas'],self.cid)
        drag((c2,'text',True),(self.oid,'text',False))
        self.assertEqual(canvas.data()['outputs'][self.oid]['canvases'],[self.cid,c2])
        canvas.undo(); self.assertEqual(canvas.data()['outputs'][self.oid]['canvas'],self.cid)

    def test_import_legacy_json_keeps_multi_canvas_and_original_text(self):
        import json
        from prompt_studio.core import initial_state
        from prompt_studio.composition import node
        old=initial_state(); old.update(version=3,uses={'old':node('old','original text')},selection_view='canvas',draft='old manual')
        path=Path(self.tmp.name)/'legacy.json'; path.write_text(json.dumps(old),encoding='utf-8')
        with patch('prompt_studio.window.QFileDialog.getOpenFileName',return_value=(str(path),'')),patch('prompt_studio.window.ask',return_value=True): self.w.import_json()
        self.assertEqual(self.w.state['version'],4); self.assertEqual(self.w.state['draft'],'old manual')
        self.assertEqual(model.compile_output(self.w.state,self.w.state['multi_output']['current_output'])['final_prompt'],'old manual')
        self.w.canvas.refresh(); self.assertTrue(self.w.canvas.containers)
