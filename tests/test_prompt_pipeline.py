"""Prompt owns round composition; only bound inputs enter new schedulers."""
import copy
import unittest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from prompt_calculus_studio import multi_output as model, prompt_sources as supply, stage_model
from prompt_calculus_studio import scheduler_wiring as wiring
from prompt_calculus_studio.flow_data import add_scheduler
from prompt_calculus_studio.module_contracts import scheduler_port_specs
import test_bound_scheduler as bound
from test_prompt_supply import definition


class PromptPipelineTests(unittest.TestCase):
    setUp=bound.BoundSchedulerTests.setUp
    tearDown=bound.BoundSchedulerTests.tearDown
    stages=bound.BoundSchedulerTests.stages
    click=bound.BoundSchedulerTests.click
    workflows=bound.BoundSchedulerTests.workflows
    submitted_texts=bound.BoundSchedulerTests.submitted_texts
    group_source=bound.BoundSchedulerTests.group_source

    def pipeline(self,scheduled=True,through_canvas=False):
        stage=self.stages()[0];keys=dict(stage=stage)
        def setup(state):
            state['uses']['root']['prompt']='1'
            root=definition((1,)*6)
            for group,text in zip(root['sequence']['groups'],'abcdef'):group.update(text=text,name=text)
            reader=supply.add(state,root=root);keys['reader']=reader
            wiring.connect(state,reader,self.cid if through_canvas else self.out,'content' if through_canvas else 'clip')
            if scheduled:
                q=add_scheduler(state);keys['scheduler']=q
                edge=next(e for e in state['multi_output']['connections'] if e['source']==self.clip and e['destination']==stage)
                model.disconnect(state,edge['id']);wiring.connect(state,self.clip,q+'::bindings','control')
                wiring.connect(state,q+'::bindings',stage,'control')
        self.assertTrue(self.c.commit(setup),self.notices)
        return keys

    def text(self):return model.compile_output(self.w.state,self.out)['final_prompt']

    def test_six_flow_rounds_repeat_fixed_canvas_once_and_match_visible_prompt(self):
        config=self.pipeline();self.assertEqual(self.text(),'a\n1')
        self.assertEqual(supply.definitions(self.w.state)[config['reader']]['supply'],'batch')
        self.click();entries=self.runner.store.rows('entry')
        expected=[letter+'\n1' for letter in 'abcdef']
        self.assertEqual(len(entries),6,self.notices)
        self.assertEqual([e['saved']['values'][self.clip]['value'] for e in entries],expected)
        for index,text in enumerate(expected):
            self.assertEqual(self.text(),text)
            self.assertEqual(self.submitted_texts()[-1],text)
            root=supply.definitions(self.w.state)[config['reader']]['root']
            self.assertEqual(root['sequence']['selected'],root['sequence']['groups'][index]['id'])
            self.executor.finish()
        self.assertEqual(self.submitted_texts(),expected)
        self.assertIsNone(self.runner.current())

    def test_reader_through_canvas_still_expands_six_rounds(self):
        self.pipeline(through_canvas=True);self.click()
        self.assertEqual(len(self.runner.store.rows('entry')),6,self.notices)
        for _ in range(6):self.executor.finish()
        self.assertEqual(self.submitted_texts(),[letter+',\n\n1' for letter in 'abcdef'])

    def test_real_reader_text_port_connects_to_prompt_and_disconnect_undo_restores_order(self):
        config=self.pipeline(scheduled=False);reader=config['reader']
        def remove(state):
            edge=next(e for e in state['multi_output']['connections'] if e['source']==reader and e['destination']==self.out)
            model.disconnect(state,edge['id'])
            state['text_positions'].update({reader:[100,0],self.out:[650,0],self.clip:[1100,0],config['stage']:[1600,0]})
        self.assertTrue(self.c.commit(remove))
        view=self.c.view;view.resetTransform();view.scale(.5,.5);view.centerOn(900,250);QTest.qWait(25)
        first=self.c.ports[(reader,'clip',True)];last=self.c.ports[(self.out,'text',False)]
        self.assertTrue(first.compatible(last))
        a=view.mapFromScene(first.scenePos());b=view.mapFromScene(last.scenePos())
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=a);QTest.mouseMove(view.viewport(),b,30)
        QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=b);QTest.qWait(25)
        self.assertEqual(self.text(),'a\n1',self.notices)
        self.assertEqual(self.c.data()['outputs'][self.out]['text_sources'],[reader,self.cid])
        edge=next(e for e in self.c.data()['connections'] if e['source']==reader and e['destination']==self.out)
        self.assertIs(self.c.line_port(edge,False),self.c.ports[(self.out,'clip',False,reader)])
        self.assertTrue(self.c.commit(lambda s:model.disconnect(s,edge['id'])))
        self.assertEqual(self.text(),'1');self.c.undo();self.assertEqual(self.text(),'a\n1')

    def test_saved_six_values_survive_edits_without_resubmitting_new_text(self):
        config=self.pipeline();self.click()
        saved=copy.deepcopy([e['saved'] for e in self.runner.store.rows('entry')])
        def edit(state):
            state['uses']['root']['prompt']='changed fixed text'
            for group in supply.definitions(state)[config['reader']]['root']['sequence']['groups']:group['text']='changed flow'
        self.assertTrue(self.c.commit(edit))
        self.w.persist()
        self.assertEqual([e['saved'] for e in self.runner.store.rows('entry')],saved)
        for _ in range(6):self.executor.finish()
        self.assertEqual(self.submitted_texts(),[letter+'\n1' for letter in 'abcdef'])

    def test_clip_can_bypass_scheduler_with_full_flow_or_explicit_step(self):
        config=self.pipeline(scheduled=False);self.click()
        self.assertEqual(self.submitted_texts(),[letter+'\n1' for letter in 'abcdef'],self.notices)
        for _ in range(6):self.executor.finish()
        self.assertEqual(self.runner.store.rows('entry'),[])
        def step(state):
            source=supply.definitions(state)[config['reader']];source['supply']='step'
            root=source['root'];root['sequence']['selected']=root['sequence']['start'];root.pop('supply_cursor',None)
        self.assertTrue(self.c.commit(step));self.click()
        self.assertEqual(len(self.submitted_texts()),7);self.assertEqual(self.submitted_texts()[-1],'a\n1')

    def test_manual_empty_prompt_is_retained_for_every_round(self):
        self.pipeline()
        def blank(state):
            state['draft']='';state['multi_output']['outputs'][self.out]['draft']=''
        self.assertTrue(self.c.commit(blank));self.click()
        self.assertEqual(len(self.runner.store.rows('entry')),6,self.notices)
        for _ in range(6):self.executor.finish()
        self.assertEqual(self.submitted_texts(),['']*6)

    def test_only_bound_inputs_accept_new_wires_and_add_next_input(self):
        config=self.pipeline();q=config['scheduler'];state=self.w.state
        specs=scheduler_port_specs(state,q)
        self.assertEqual([(p[1],p[2],p[4]) for p in specs],[('control',False,0),('control',False,1),('control',True,0)])
        self.assertEqual(self.c.ports[(q+'::bindings','control',False,self.clip)].caption.text(),'輸入1')
        self.assertEqual(self.c.ports[(q+'::bindings','control',False)].caption.text(),'輸入2')
        for source,kind in ((config['reader'],'clip'),(config['reader'],'content'),(self.cid,'image'),(self.out,'clip')):
            before=copy.deepcopy(state)
            with self.assertRaisesRegex(ValueError,'輸入模塊'):wiring.connect(state,source,q+'::'+kind+'1',kind)
            self.assertEqual(state,before)
        panel=self.c.flow_cards[q].panel
        self.assertFalse(hasattr(panel,'add_channel'))

    def test_convert_legacy_data_route_keeps_target_and_rounds_and_undo(self):
        config=self.group_source((1,)*6,batch=True);q=config['scheduler']
        before=copy.deepcopy(self.c.history_state())
        self.assertTrue(wiring.legacy_edges(self.w.state,q))
        targets=[b['target'] for s in stage_model.compile_plan(self.w.state)['stages'].values() for b in s['bindings']]
        panel=self.c.flow_cards[q].panel
        self.assertTrue(panel.legacy_button.isVisible())
        QTest.mouseClick(panel.legacy_button,Qt.MouseButton.LeftButton);QTest.qWait(20)
        self.assertEqual(wiring.legacy_edges(self.w.state,q),[])
        self.assertEqual([b['target'] for s in stage_model.compile_plan(self.w.state)['stages'].values() for b in s['bindings']],targets)
        after=self.c.history_state();self.c.undo();self.assertEqual(self.c.history_state(),before)
        self.c.redo();self.assertEqual(self.c.history_state(),after)
        self.w.persist();self.assertEqual(self.c.history_projection(self.w.store.load()),self.c.history_state())
        self.click();self.assertEqual(len(self.runner.store.rows('entry')),6,self.notices)

    def test_completed_flow_can_start_six_rounds_again(self):
        self.pipeline();self.click()
        for _ in range(6):self.executor.finish()
        self.click();self.assertEqual(len(self.runner.store.rows('entry')),12,self.notices)
        for _ in range(6):self.executor.finish()
        self.assertEqual(self.submitted_texts(),[letter+'\n1' for letter in 'abcdef']*2)

    def test_legacy_side_flow_is_moved_to_prompt_not_hidden_in_scheduler(self):
        config=self.pipeline();q=config['scheduler'];reader=config['reader']
        def legacy(state):
            edge=next(e for e in state['multi_output']['connections'] if e['source']==reader and e['destination']==self.out)
            model.disconnect(state,edge['id']);model.connect(state,reader,q+'::content1','content')
        self.assertTrue(self.c.commit(legacy));self.assertEqual(self.text(),'1')
        with self.assertRaisesRegex(ValueError,'Prompt 輸出'):stage_model.compile_plan(self.w.state)
        self.assertTrue(self.c.commit(lambda s:wiring.convert(s,q)))
        self.assertEqual(self.text(),'a\n1');self.assertFalse(wiring.legacy_edges(self.w.state,q))
        self.click();self.assertEqual(len(self.runner.store.rows('entry')),6,self.notices)
        for _ in range(6):self.executor.finish()
        self.assertEqual(self.submitted_texts(),[letter+'\n1' for letter in 'abcdef'])


if __name__=='__main__':unittest.main()
