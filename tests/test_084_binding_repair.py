"""Regression for retained Anima #11 vs selected illustrious #9 destinations."""
import copy
import unittest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton
from PySide6.QtTest import QTest
import stage_fixture as fixture
import test_084_stages as stages
from prompt_studio import clip_flow,multi_output as model,stage_model
from prompt_studio.flow_data import resolve,image_list
from prompt_studio.result_data import add_text_reader


class BindingRepairTests(unittest.TestCase):
    setUp=stages.StageTests.setUp
    tearDown=fixture.StageFixture.tearDown
    click=fixture.StageFixture.click
    workflows=fixture.StageFixture.workflows
    stages=stages.StageTests.stages

    def historical_binding(self):
        profile=copy.deepcopy(self.w.state['generation']['profiles'][0]);profile.update(id='previous',name='Previous',frontend_id='native-previous')
        profile['graph']['11']=copy.deepcopy(profile['graph']['6'])
        self.w.generation_panel.save_profile(profile)
        def edit(s):
            b=dict(workflow='previous',clip=self.clip,node='11',field='text')
            s['multi_output']['bindings'].insert(0,b)
        self.assertTrue(self.c.commit(edit),self.notices)
        # Only the selected workflow is open; the historical one is unavailable.
        self.executor.live['workflows']=[self.executor.identities['flow']]
        self.executor.live['identity']=self.executor.identities['flow']

    def test_retained_other_workflow_binding_never_enters_apply_or_stage_submission(self):
        self.historical_binding();self.click()
        self.assertEqual([v['workflow'] for v in self.executor.applied],['flow'],self.notices)
        self.assertEqual(self.executor.applied[0]['snapshot']['state']['multi_output']['bindings'][0]['node'],'6')
        self.assertEqual(self.executor.submissions,[])
        key=self.stages()[0]
        self.assertEqual(stage_model.compile_plan(self.w.state)['stages'][key]['bindings'][0]['target']['workflow'],'flow')
        for text in ('first','second','third'):
            self.c.commit(lambda s,t=text:s['uses']['root'].update(prompt=t));self.click()
        self.assertEqual(self.workflows(),['flow']*3,self.notices)
        self.assertEqual([v['prompt']['6']['inputs']['text'] for v in self.executor.submissions],['first','second','third'])
        for _ in range(3):self.executor.finish()
        self.assertTrue(all(r['status']=='complete' for r in self.runner.runs()),self.notices)

    def test_edit_does_not_apply_until_click_even_with_old_web_preference(self):
        from integration_test.workflow_state import live_state
        self.c.commit(lambda s:s['multi_output']['clip_inputs'][self.clip].update(text_source='web'))
        self.c.commit(lambda s:s['uses']['root'].update(prompt='changed at PCS'))
        QTest.qWait(420);self.assertEqual(self.executor.applied,[])
        library=dict(state=self.w.state,library_id='test',connection=dict(enabled=True,server=self.w.comfy.url))
        self.assertIsNone(live_state(self.executor.identities['flow'],library,self.w.comfy.url))
        before=copy.deepcopy(self.executor.graphs['flow']['3']['inputs'])
        self.click();self.assertEqual(self.executor.graphs['flow']['6']['inputs']['text'],'changed at PCS')
        self.assertEqual(self.executor.graphs['flow']['3']['inputs'],before)
        self.assertFalse(self.executor.submissions)

    def test_stage_card_has_only_workflow_and_uniform_purple_ports(self):
        key=self.stages()[0]
        for card in (self.c.flow_cards[key],self.c.clips[self.clip]):
            buttons=[b.text() for b in card.panel.findChildren(QPushButton) if not b.isHidden()]
            self.assertEqual(len(buttons),1,buttons)
        self.assertEqual([b.text() for b in self.c.flow_cards[key].panel.findChildren(QPushButton)],['選擇工作流'])
        for port in self.c.ports.values():
            self.assertNotEqual(port.kind,'done')
            if port.kind in ('control','flow'):self.assertEqual(port.caption.text(),'輸出' if port.output else '輸入')
        from prompt_studio.stage_widgets import StageDialog
        self.w.settings_page.workflow_manager.catalog.loaded=True
        dialog=StageDialog(self.c,key)
        try:
            dialog.refresh_workflows();self.assertEqual(dialog.workflow.itemText(0),'選擇工作流')
            self.assertTrue(dialog.target.isHidden());self.assertTrue(dialog.hint.isHidden())
        finally:dialog.reject()

    def test_apply_failure_remains_visible_instead_of_success_guidance(self):
        self.executor.live.update(identity=dict(path='different.json',frontend_id='different',workflow=''),workflows=[])
        self.click()
        self.assertFalse(self.executor.applied);self.assertFalse(self.executor.submissions)
        self.assertIn('同步未完成',self.notices[-1])

    def test_stage_order_drag_uses_one_purple_output_and_undo_preserves_edge(self):
        a,b=self.stages(('flow','B'))
        self.c.commit(lambda s:s['multi_output'].update(connections=[c for c in s['multi_output']['connections'] if not(c['source']==a and c['destination']==b)]))
        view=self.c.view;view.resetTransform();view.scale(.7,.7);view.centerOn(1450,220);QTest.qWait(10)
        source=self.c.ports[(a,'flow',True)];target=self.c.ports[(b,'control',False)]
        p,q=view.mapFromScene(source.scenePos()),view.mapFromScene(target.scenePos())
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=p);QTest.mouseMove(view.viewport(),q,20);QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=q);QTest.qWait(20)
        edge=next(c for c in self.c.data()['connections'] if c['source']==a and c['destination']==b)
        self.assertEqual(edge['kind'],'done');self.assertIsNotNone(self.c.line_port(edge,False))
        view.setFocus();QTest.keyClick(view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        self.assertFalse(any(c['source']==a and c['destination']==b for c in self.c.data()['connections']))

    def test_text_reader_selects_actual_field_without_regenerating(self):
        key=self.stages()[0];readers=[]
        def setup(s):
            s['multi_output']['stages'][key]['text_output']=None
            readers.append(add_text_reader(s,(1400,500)));model.connect(s,key,readers[0],'clip')
            s['canvas_functions']['images'][readers[0]]['text_field']=['6','text']
        self.assertTrue(self.c.commit(setup),self.notices);self.click();self.executor.finish()
        self.assertEqual(resolve(self.runner.context(self.runner.results),readers[0],'clip')['value'],'white shirt')
        selector=self.c.functions.cards[readers[0]].panel.selector
        selector.setFocus();QTest.keyClick(selector,Qt.Key.Key_Down);QTest.qWait(20)
        self.assertEqual(self.w.state['canvas_functions']['images'][readers[0]]['text_field'],['7','text'])
        self.assertEqual(resolve(self.runner.context(self.runner.results),readers[0],'clip')['value'],self.executor.submissions[0]['prompt']['7']['inputs']['text'])
        self.assertEqual(len(self.executor.submissions),1)

    def test_reader_filter_keeps_node_and_batch_order_and_missing_does_not_fallback(self):
        key=self.stages()[0];source=self.c.functions.add_image(enhanced=False)
        self.c.commit(lambda s:model.connect(s,key,source,'image'));self.executor.graphs['flow']['5']['inputs']['batch_size']=2
        self.click();self.executor.finish(2)
        context=self.runner.context(self.runner.results)
        self.assertIn(key,context['_stage_results'],(self.notices,self.runner.store.rows('attempt')))
        first=copy.deepcopy(context['_stage_results'][key]['images'][0]);first['reference']['node']='12'
        context['_stage_results'][key]['images'].append(first)
        context['canvas_functions']['images'][source]['output_node']='9'
        self.assertEqual([v['reference']['index'] for v in image_list(context,source)],[0,1])
        context['canvas_functions']['images'][source]['output_node']='12'
        self.assertEqual([v['reference']['node'] for v in image_list(context,source)],['12'])
        context['canvas_functions']['images'][source]['output_node']='999'
        with self.assertRaisesRegex(ValueError,'沒有圖片'):image_list(context,source)

    def test_three_output_nodes_collected_then_loader_selects_one_for_next_stage(self):
        a,b=self.stages(('flow','B'));source=self.c.functions.add_image(enhanced=False)
        def setup(s):
            profile=next(p for p in s['generation']['profiles'] if p['id']=='flow')
            for key in ('12','13'):profile['graph'][key]=copy.deepcopy(profile['graph']['9'])
            s['multi_output']['stages'][a].update(output=None,text_output=None)
            model.connect(s,a,source,'image');s['canvas_functions']['images'][source]['output_node']='12'
            target=next(k for k,v in s['multi_output']['image_inputs'].items() if v['workflow']=='B')
            model.connect(s,source,target,'image')
        self.c.commit(setup)
        self.executor.graphs['flow']=copy.deepcopy(next(p for p in self.w.state['generation']['profiles'] if p['id']=='flow')['graph'])
        self.executor.graphs['flow']['5']['inputs']['batch_size']=2
        self.click();self.executor.finish(2,nodes=['9','12','13'])
        self.assertIn(a,self.runner.results,(self.notices,[(v['status'],v.get('error')) for v in self.runner.store.rows('attempt')]))
        result=self.runner.results[a]
        self.assertEqual([(v['reference']['node'],v['reference']['index']) for v in result['images']],[('9',0),('9',1),('12',0),('12',1),('13',0),('13',1)])
        for index in range(2):
            self.assertIn(result['images'][2+index]['sha256'],self.executor.submissions[-1]['prompt']['10']['inputs']['image'])
            self.executor.finish()
        self.assertEqual(self.workflows(),['flow','B','B']);self.assertIsNone(self.runner.current(),self.notices)

    def test_queued_future_result_retains_reader_selection_while_ui_changes(self):
        from prompt_studio.flow_data import add_scheduler
        a,b=self.stages(('flow','B'));source=self.c.functions.add_image(enhanced=False)
        def setup(s):
            profile=next(p for p in s['generation']['profiles'] if p['id']=='flow')
            profile['graph']['12']=copy.deepcopy(profile['graph']['9'])
            model.connect(s,a,source,'image');s['canvas_functions']['images'][source]['output_node']='9'
            target=next(k for k,v in s['multi_output']['image_inputs'].items() if v['workflow']=='B')
            model.connect(s,source,target,'image')
            scheduler=add_scheduler(s)
            for stage in (a,b):model.connect(s,stage,scheduler+'::flow','flow')
        self.assertTrue(self.c.commit(setup),self.notices)
        self.executor.graphs['flow']=copy.deepcopy(next(p for p in self.w.state['generation']['profiles'] if p['id']=='flow')['graph'])
        self.click();self.click()
        selector=self.c.functions.cards[source].panel.result_node
        selector.setFocus();QTest.keyClick(selector,Qt.Key.Key_Down);QTest.qWait(20)
        self.assertEqual(self.w.state['canvas_functions']['images'][source]['output_node'],'12')
        for _ in range(2):
            self.executor.finish(nodes=['9','12'])
            result=self.runner.results[a]['images']
            image=next(v for v in reversed(result) if v['reference']['node']=='9')
            self.assertIn(image['sha256'],self.executor.submissions[-1]['prompt']['10']['inputs']['image'])
            self.executor.finish()
        self.assertEqual(self.workflows(),['flow','B','flow','B']);self.assertIsNone(self.runner.current(),self.notices)


if __name__=='__main__':unittest.main()
