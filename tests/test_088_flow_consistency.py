"""Visible supply position must agree with the next queued and native payload."""
import copy
import unittest
from PySide6.QtCore import QEvent, Qt, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio import multi_output as model, composition as comp
from prompt_calculus_studio import prompt_sources as supply
from prompt_calculus_studio.flow_data import add_scheduler
import test_prompt_supply as supply_tests


class FlowConsistencyTests(unittest.TestCase):
    setUp=supply_tests.SupplyTests.setUp
    tearDown=supply_tests.SupplyTests.tearDown
    stages=supply_tests.SupplyTests.stages
    click=supply_tests.SupplyTests.click
    workflows=supply_tests.SupplyTests.workflows
    submitted_texts=supply_tests.SupplyTests.submitted_texts
    group_source=supply_tests.SupplyTests.group_source

    def canvas_sequence(self, bound=False):
        self.stage=self.stages()[0];result=[]
        def setup(state):
            state['uses']['root']=supply_tests.definition((1,)*6)
            state['uses']['root']['collapsed']=True
            q=add_scheduler(state,(700,0));result.append(q)
            if bound:
                for edge in list(state['multi_output']['connections']):
                    if edge['source']==self.clip and edge['destination']==self.stage:model.disconnect(state,edge['id'])
                model.connect(state,self.clip,q+'::bindings','control')
                model.connect(state,q+'::bindings',self.stage,'control')
            else:
                model.connect(state,self.out,q+'::clip1','clip')
                model.connect(state,q+'::clip1',self.clip,'clip')
        self.assertTrue(self.c.commit(setup),self.notices)
        self.w.comfy.snapshot_versions=[1,2,3,4,5,6]
        return result[0]

    def test_bound_run_reaches_connected_image_preview(self):
        self.canvas_sequence(bound=True)
        self.assertTrue(self.c.commit(lambda state:model.connect(state,self.stage,model.PREVIEW,'image')))
        self.click();self.executor.finish();QTest.qWait(40)
        preview=self.c.preview_cards[model.PREVIEW].panel
        self.assertEqual(len(preview.input_records),1,self.notices)
        self.assertEqual(self.submitted_texts(),['prompt 0'])
        self.assert_visible(1)

    def assert_visible(self, index):
        root=self.w.state['uses']['root'];card=self.c.cards['root']
        self.assertEqual(root['sequence']['selected'],root['sequence']['groups'][index]['id'])
        self.assertEqual(card.value['sequence']['selected'],root['sequence']['selected'])
        self.assertEqual(card.own,comp.render(root))
        self.assertEqual(self.c.outputs[self.out].panel.editor.toPlainText(),model.compile_output(self.w.state,self.out)['final_prompt'])

    def test_six_manual_clicks_refresh_visible_round_and_freeze_each_payload(self):
        self.canvas_sequence()
        expected=[]
        for index in range(6):
            self.assert_visible(index)
            expected.append(self.c.outputs[self.out].panel.editor.toPlainText())
            self.click()
            self.assert_visible(min(index+1,5))
        for _ in range(6):self.executor.finish()
        self.assertEqual(self.submitted_texts(),expected,self.notices)

    def test_bound_scheduler_keeps_prompt_output_and_manual_draft_authoritative(self):
        from prompt_calculus_studio.drafts import edit
        self.canvas_sequence(bound=True)
        self.click();self.assert_visible(1)
        self.assertTrue(self.c.commit(lambda state:edit(state,'manual prompt',self.out)))
        self.click()
        self.assertEqual(self.c.outputs[self.out].panel.editor.toPlainText(),'manual prompt')
        for _ in range(2):self.executor.finish()
        self.assertEqual(self.submitted_texts(),['prompt 0','manual prompt'],self.notices)
        self.assertTrue(self.c.commit(lambda state:edit(state,'',self.out)))
        self.c.update_output()
        self.assertEqual(self.c.outputs[self.out].panel.editor.toPlainText(),'')

    def test_binding_input_is_numbered_and_has_only_one_control_row_family(self):
        q=self.canvas_sequence(bound=True)
        inputs=[p for p in self.c.ports.values() if p.key.startswith(q+'::') and not p.output and p.kind in ('control','flow')]
        self.assertEqual([p.caption.text() for p in inputs],['輸入1','輸入2'])
        outputs=[p for p in self.c.ports.values() if p.key.startswith(q+'::') and p.output and p.kind in ('control','flow')]
        self.assertEqual([p.caption.text() for p in outputs],['輸出'])

    def test_interrupted_rewire_restores_original_and_clears_ghost(self):
        q=self.canvas_sequence(bound=True)
        port=self.c.ports[(q+'::bindings','control',False,self.clip)]
        edge=next(e for e in self.c.data()['connections'] if e['destination']==q+'::bindings')
        gesture=self.c.connection_gesture;before=copy.deepcopy(self.c.data()['connections'])
        gesture.begin(port,port.scenePos());gesture.move(port.scenePos()+QPointF(-40,40))
        self.assertIsNotNone(gesture.preview)
        QApplication.sendEvent(self.w,QEvent(QEvent.Type.WindowDeactivate))
        self.assertIsNone(gesture.anchor)
        self.assertIsNone(gesture.preview)
        self.assertEqual(self.c.lines[edge['id']].opacity(),1)
        self.assertEqual(self.c.data()['connections'],before)

    def test_automatic_supply_progress_refreshes_both_source_and_output(self):
        config=self.group_source((1,1,1),batch=True)
        self.click()
        self.assertEqual(self.c.outputs[self.out].panel.editor.toPlainText(),'prompt 0')
        self.executor.finish();self.executor.finish();QTest.qWait(30)
        root=supply.definitions(self.w.state)[config['source']]['root']
        self.assertEqual(root['sequence']['selected'],root['sequence']['groups'][2]['id'])
        self.assertEqual(self.c.outputs[self.out].panel.editor.toPlainText(),comp.render(root))
        self.executor.finish()
        self.assertEqual(self.submitted_texts(),['prompt 0','prompt 1','prompt 2'])

    def test_stage_can_connect_to_the_same_visible_input_and_disconnect_with_undo(self):
        stage=self.stages()[0];keys=[]
        self.assertTrue(self.c.commit(lambda state:keys.append(add_scheduler(state,(1600,0)))))
        q=keys[0];view=self.c.view
        view.resetTransform();view.scale(.6,.6);view.centerOn(1500,140);QTest.qWait(20)
        initial=self.c.ports[(q+'::bindings','control',False)]
        self.assertEqual(initial.caption.text(),'輸入')
        start=view.mapFromScene(self.c.ports[(stage,'flow',True)].scenePos())
        end=view.mapFromScene(initial.scenePos())
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=start)
        QTest.mouseMove(view.viewport(),end,25);QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=end);QTest.qWait(30)
        edge=next(e for e in self.c.data()['connections'] if e['destination']==q+'::flow')
        self.assertEqual(edge['source'],stage)
        port=self.c.line_port(edge,False)
        self.assertEqual(port.caption.text(),'輸入1')
        self.assertEqual(self.c.ports[(q+'::flow','flow',False)].caption.text(),'輸入2')
        start=view.mapFromScene(port.scenePos());end=start+QPointF(-20,-110).toPoint()
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=start)
        QTest.mouseMove(view.viewport(),end,25);QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=end);QTest.qWait(30)
        self.assertNotIn(edge,self.c.data()['connections'])
        self.assertIsNone(self.c.connection_gesture.preview)
        self.c.undo();self.assertIn(edge,self.c.data()['connections'])
        self.assertIsNotNone(self.c.line_port(edge,False))

    def test_repaint_does_not_replace_card_or_enter_undo_history(self):
        self.canvas_sequence();card=self.c.cards['root']
        history=copy.deepcopy(self.c.history_state())
        self.click()
        self.assertIs(self.c.cards['root'],card)
        self.assert_visible(1)
        # Ordinary editor refresh remains read-only for the source and drafts.
        state=copy.deepcopy(self.w.state)
        self.c.update_output();self.c.update_output()
        self.assertEqual(self.w.state,state)
        self.assertEqual(history['text_positions'],self.c.history_state()['text_positions'])


if __name__=='__main__':unittest.main()
