"""Qt clicks through native prepare -> server receipt -> queue/history -> assets.
Only the ComfyUI executor/GPU is replaced; jobs never become complete by fiat.
"""
import copy
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QPointF,QPoint,QTimer,QMimeData
from PySide6.QtGui import QDragEnterEvent,QDragMoveEvent,QDropEvent,QFontDatabase
from PySide6.QtWidgets import QApplication,QPushButton,QPlainTextEdit
from PySide6.QtTest import QTest
import stage_fixture as fixtures
from prompt_studio import multi_output as model,stage_model
from prompt_studio.flow_data import add_image_input,add_scheduler

if not QFontDatabase.families():
    for filename in ('msjh.ttc','consola.ttf','segoeui.ttf'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+filename)


class Executor(fixtures.ChainExecutor):
    def __init__(self,window,root):super().__init__(window,root);self.applied=[]
    def request(self,route,data=None,done=None,failed=None,**kw):
        if route=='desktop/results':
            if done:done([])
            return
        if route!='workflow/native/apply':return super().request(route,data,done,failed,**kw)
        try:
            self.native.poll(self.live);self.native.start(data,action='apply')
            command=self.native.poll(self.live)['commands'][0];target=self.identities[data['workflow']]
            if self.live['identity']!=target:
                self.native.activate(dict(command,session=self.live['session'],client_id=self.live['client_id'],opened_identity=target,opened_epoch=self.live['epoch']+1))
                self.live.update(identity=target,epoch=self.live['epoch']+1)
            for b in command.get('texts',[])+command.get('images',[]):
                if b.get('text_source')!='web':self.graphs[data['workflow']][b['node']]['inputs'][b['field']]=b['text']
            result=self.native.reply(dict(command,session=self.live['session'],applied=True));self.applied.append(data)
            if done:done(result)
        except ValueError as exc:
            if failed:failed(str(exc))
            else:raise


class StageTests(unittest.TestCase):
    def test_paused_schedule_projects_attempt_failure_without_rewriting_scope_ownership(self):
        keys=self.stages(('flow','B'));schedule=[]
        def setup(s):
            key=add_scheduler(s);schedule.append(key)
            model.connect(s,keys[0],key+'::flow','flow')
        self.assertTrue(self.c.commit(setup),self.notices);self.click()
        entry=self.runner.store.rows('entry')[0];attempt=self.runner.store.rows('attempt')[0]
        self.assertEqual(entry['status'],'running');self.assertEqual(self.runner.entry_status(entry),'submitted')
        self.runner.pause(entry['run'])
        self.assertEqual(self.runner.entry_status(entry),'submitted','submitted work continues while the range is paused')
        self.runner.store.update(attempt['id'],status='unconfirmed',error='fixture lost reply')
        self.assertEqual(self.runner.entry_status(entry),'unconfirmed')
        self.assertEqual(self.runner.store.read(entry['id'])['status'],'running')
        from prompt_studio.stage_widgets import SchedulePanel
        panel=SchedulePanel(self.c,schedule[0]);panel.refresh()
        self.assertIn('提交未確認',panel.list.item(0).text());self.assertNotIn('執行中',panel.list.item(0).text())
        panel.deleteLater()

    def setUp(self):
        fixtures.StageFixture.setUp(self)
        self.executor=Executor(self.w,Path(self.tmp.name)/'stages-executor');self.runner=self.w.comfy.input_flow.chain
    tearDown=fixtures.StageFixture.tearDown
    click=fixtures.StageFixture.click
    workflows=fixtures.StageFixture.workflows

    def stages(self,names=('flow',)):
        keys=[]
        def setup(s):
            previous=None
            for name in names:
                key=stage_model.add(s,(1000+len(keys)*550,100),name);keys.append(key)
                s['multi_output']['stages'][key].update(name=name,output='9',text_output=['6','text'] if name=='flow' else None)
                if name=='flow':model.connect(s,self.clip,key,'control')
                if previous:
                    model.connect(s,previous,key,'done')
                    if name!='flow':
                        image=add_image_input(s);s['multi_output']['image_inputs'][image].update(workflow=name,node='10')
                        model.connect(s,previous,image,'image');model.connect(s,image,key,'control')
                previous=key
        self.assertTrue(self.c.commit(setup),self.notices);return keys

    def test_no_stage_applies_only_then_single_stage_three_clicks(self):
        unused=[]
        self.c.commit(lambda s:(unused.append(add_scheduler(s)),s['multi_output']['schedulers'][unused[0]].update(mode='stage')))
        self.click();self.assertEqual(len(self.executor.submissions),0);self.assertEqual(len(self.executor.applied),1,self.notices)
        self.c.commit(lambda s:s['multi_output']['schedulers'].pop(unused[0]))
        self.stages()
        for text in ('one','two','three'):
            self.c.commit(lambda s,t=text:s['uses']['root'].update(prompt=t));self.click()
        self.assertEqual(len(self.executor.submissions),3,self.notices)
        self.assertEqual([p['prompt']['6']['inputs']['text'] for p in self.executor.submissions],['one','two','three'])
        for _ in range(3):self.executor.finish()
        self.assertTrue(all(r['status']=='complete' for r in self.runner.runs()),self.notices)

    def test_three_stage_results_and_barrier(self):
        keys=self.stages(('flow','B','C'));self.executor.graphs['flow']['5']['inputs']['batch_size']=3
        self.click();self.assertEqual(self.workflows(),['flow'],self.notices)
        self.executor.finish(3)
        for _ in range(3):self.executor.finish(2)
        self.assertEqual(self.workflows(),['flow']+['B']*3+['C'],self.notices)
        for _ in range(6):self.executor.finish()
        self.assertEqual(self.workflows(),['flow']+['B']*3+['C']*6)
        self.assertIsNone(self.runner.current(),self.notices)
        self.executor.tick();self.assertEqual(len(self.executor.submissions),10)

    def test_control_mixed_workflows_applies_b_but_runs_only_a(self):
        key=self.stages()[0]
        from prompt_studio.clip_flow import add,set_binding
        other=[]
        def setup(s):
            # Use another native text workflow, independently bound.
            profile=copy.deepcopy(next(p for p in s['generation']['profiles'] if p['id']=='flow'));profile.update(id='D',name='D',frontend_id='native-D')
            s['generation']['profiles'].append(profile)
            cid=add(s);other.append(cid);model.connect(s,self.out,cid,'clip');set_binding(s,'D',cid,('7','text'));model.connect(s,cid,key,'control')
        self.assertTrue(self.c.commit(setup),self.notices)
        p=next(p for p in self.w.state['generation']['profiles'] if p['id']=='D');self.executor.graphs['D']=copy.deepcopy(p['graph']);self.executor.identities['D']=dict(workflow='D',path='',frontend_id='native-D');self.executor.live['workflows']=list(self.executor.identities.values())
        self.click();self.assertEqual(self.workflows(),['flow'],self.notices);self.assertEqual(len(self.executor.applied),1)
        self.assertEqual(self.executor.graphs['D']['7']['inputs']['text'],'white shirt')

    def test_scope_abc_runs_whole_group_in_order(self):
        keys=self.stages(('flow','B','C'));scopes=[]
        def setup(s):
            key=add_scheduler(s);scopes.append(key)
            for stage in keys:model.connect(s,stage,key+'::flow','flow')
        self.assertTrue(self.c.commit(setup),self.notices);self.click();self.click()
        for _ in range(6):
            self.assertTrue(self.executor.pending,self.notices);self.executor.finish()
        self.assertEqual(self.workflows(),['flow','B','C']*2,self.notices)

    def test_disconnected_stage_applies_free_input_without_generation(self):
        key=self.stages()[0]
        def setup(s):
            for c in list(s['multi_output']['connections']):
                if c['kind']=='control':model.disconnect(s,c['id'])
            s['multi_output']['stages'][key]['output']=None
        self.assertTrue(self.c.commit(setup),self.notices);self.click();self.assertFalse(self.workflows(),self.notices)
        self.assertEqual(len(self.executor.applied),1);self.assertIsNone(self.runner.current())

    def image_batch(self,count=3,auto=True,outer=False):
        from test_comfy_integration import png
        from prompt_studio.image_source import set_items
        from prompt_studio.image_bindings import import_source
        from test_multi_output import workflow
        items=[]
        for i in range(count):
            path=Path(self.tmp.name)/f'input{i}.png';graph=workflow()['graph'];graph['6']['inputs']['text']=f'image prompt {i}'
            png(path,dict(prompt=graph));items.append(import_source(path,self.w.store.directory))
        source=self.c.functions.add_image(source=items[0]);result={}
        def setup(s):
            set_items(s,source,items,self.w.store.directory,True)
            stage=stage_model.add(s,workflow='B');s['multi_output']['stages'][stage]['output']='9';result['stage']=stage
            image=add_image_input(s);s['multi_output']['image_inputs'][image].update(workflow='B',node='10');model.connect(s,image,stage,'control')
            if auto:
                scheduler=add_scheduler(s);result['inner']=scheduler
                model.connect(s,source,scheduler+'::image1','image');model.connect(s,source,scheduler+'::clip1','clip')
                model.connect(s,scheduler+'::image1',image,'image')
            else:model.connect(s,source,image,'image')
            if outer:
                scheduler=add_scheduler(s);result['outer']=scheduler;model.connect(s,stage,scheduler+'::flow','flow')
        self.assertTrue(self.c.commit(setup),self.notices);return source,items,result

    def test_source_no_scheduler_repeated_click_current_then_advance_once(self):
        source,items,_=self.image_batch(auto=False)
        self.click();self.click();self.assertEqual(len(self.executor.submissions),2,self.notices)
        values=[p['prompt']['10']['inputs']['image'] for p in self.executor.submissions];self.assertEqual(values[0],values[1])
        self.executor.finish();self.assertEqual(self.w.state['canvas_functions']['images'][source]['index'],1)
        self.executor.finish();self.assertEqual(self.w.state['canvas_functions']['images'][source]['index'],1)
        self.assertEqual(len(self.executor.submissions),2)

    def test_auto_batch_capacity_twelve_and_nested_three_times_two(self):
        source,items,config=self.image_batch(count=12)
        self.click()
        panel=self.c.flow_cards[config['inner']].panel;panel.refresh()
        self.assertIn('1 張',panel.list.item(0).text());self.assertNotIn('12 張',panel.list.item(0).text())
        self.assertTrue(all(not entry['saved']['batches'] for entry in self.runner.store.rows('entry',active=True)))
        for i in range(12):
            rows=self.runner.store.rows('entry',active=True);self.assertLessEqual(len(rows),10)
            self.assertTrue(self.executor.pending,self.notices);self.executor.finish()
        self.assertEqual(len(self.executor.submissions),12,self.notices);self.assertIsNone(self.runner.current())

    def test_nested_three_images_two_manual_outer_items(self):
        self.image_batch(outer=True);self.click();self.click()
        for i in range(6):
            self.assertTrue(self.executor.pending,(i,self.notices));self.executor.finish()
        self.assertEqual(self.workflows(),['B']*6,self.notices);self.assertIsNone(self.runner.current())

    def test_cancel_releases_capacity_and_stop_feed_preserves_saved_items(self):
        source,items,config=self.image_batch(count=12)
        self.click();old=self.runner.current()
        self.assertEqual(len(self.runner.store.rows('entry',active=True)),10)
        self.runner.cancel(old['id']);self.executor.tick()
        self.assertFalse(self.runner.store.rows('entry',active=True))
        self.click();current=self.runner.current();self.assertNotEqual(old['id'],current['id'])
        self.assertEqual(len(self.runner.store.rows('entry',active=True)),10)
        self.runner.stop_feed(current['id'],source)
        self.executor.finish();self.assertEqual(len(self.executor.submissions),2)
        self.runner.resume(current['id']);QTest.qWait(35)
        for _ in range(9):self.executor.finish()
        self.assertEqual(len(self.executor.submissions),11)
        self.assertEqual(self.runner.store.read(current['id'])['status'],'complete')
        self.assertFalse(self.runner.store.rows('entry',active=True))

    def test_data_only_freezes_connected_text_but_negative_live(self):
        from prompt_studio import clip_flow
        key=self.stages()[0];refs={}
        def setup(s):
            sched=add_scheduler(s);refs['s']=sched;model.connect(s,self.out,sched+'::clip1','clip');model.connect(s,sched+'::clip1',self.clip,'clip')
            out=model.ident('out_');s['multi_output']['outputs'][out]=dict(model.new_output('negative'),canvases=[],text_sources=[],draft='neg one')
            clip=clip_flow.add(s);clip_flow.set_binding(s,'flow',clip,('7','text'));model.connect(s,out,clip,'clip');model.connect(s,clip,key,'control');refs['o']=out
        self.assertTrue(self.c.commit(setup),self.notices);self.click()
        self.c.commit(lambda s:s['uses']['root'].update(prompt='positive two'));self.click()
        self.c.commit(lambda s:(s['uses']['root'].update(prompt='positive later'),s['multi_output']['outputs'][refs['o']].update(draft='neg later')))
        self.executor.finish();self.assertEqual(len(self.executor.submissions),2,self.notices)
        graph=self.executor.submissions[-1]['prompt'];self.assertEqual(graph['6']['inputs']['text'],'positive two');self.assertEqual(graph['7']['inputs']['text'],'neg later')

    def test_a_then_scope_bc_preserves_upstream_each_iteration(self):
        keys=self.stages(('flow','B','C'))
        def setup(s):
            scheduler=add_scheduler(s)
            for key in keys[1:]:model.connect(s,key,scheduler+'::flow','flow')
        self.c.commit(setup);self.controls.count.setValue(3);self.click()
        for _ in range(7):self.executor.finish()
        self.assertEqual(self.workflows(),['flow','B','C','B','C','B','C'],self.notices)
        self.assertEqual(len(self.runner.results[keys[-1]]['images']),3)

    def test_equal_scope_nesting_prompt_only_counts_one_click_once(self):
        stage=self.stages()[0]
        def setup(s):
            data=add_scheduler(s);model.connect(s,self.out,data+'::clip1','clip');model.connect(s,data+'::clip1',self.clip,'clip')
            outer=add_scheduler(s);inner=add_scheduler(s)
            model.connect(s,stage,inner+'::flow','flow');model.connect(s,inner+'::flow',outer+'::flow','flow')
        self.assertTrue(self.c.commit(setup),self.notices);self.click();self.click()
        self.executor.finish();self.executor.finish()
        self.assertEqual(len(self.executor.submissions),2,self.notices);self.assertIsNone(self.runner.current())

    def test_stage_group_fixed_and_live_policies(self):
        from prompt_studio import clip_flow
        stage=self.stages()[0];refs={}
        def setup(s):
            out=model.ident('out_');s['multi_output']['outputs'][out]=dict(model.new_output('negative'),canvases=[],text_sources=[],draft='negative first');refs['out']=out
            clip=clip_flow.add(s);clip_flow.set_binding(s,'flow',clip,('7','text'));model.connect(s,out,clip,'clip');model.connect(s,clip,stage,'control')
            scheduler=add_scheduler(s);model.connect(s,stage,scheduler+'::flow','flow');s['multi_output']['schedulers'][scheduler]['policies']={clip:'live'}
        self.assertTrue(self.c.commit(setup),self.notices);self.click()
        self.c.commit(lambda s:s['uses']['root'].update(prompt='saved second'));self.click()
        self.c.commit(lambda s:(s['uses']['root'].update(prompt='third'),s['multi_output']['outputs'][refs['out']].update(draft='negative live')))
        self.executor.finish();payload=self.executor.submissions[-1]
        self.assertEqual(payload['prompt']['6']['inputs']['text'],'saved second');self.assertEqual(payload['prompt']['7']['inputs']['text'],'negative live')
        snap=payload['extra_data']['extra_pnginfo']['prompt_studio']['bindings'][0]['snapshot']
        self.assertIn('saved second',[r['prompt'] for r in snap['state']['uses'].values()])

    def test_results_failure_refetch_without_generation_and_pause_resume(self):
        self.stages(('flow','B'));self.click();run=self.runner.current();self.executor.result_failure=True;self.executor.finish()
        item=self.runner.store.rows('attempt')[0];self.assertEqual(item['status'],'result_error');self.assertEqual(self.runner.current()['status'],'paused')
        self.executor.result_failure=False;self.runner.retry(item['id']);QTest.qWait(20)
        self.assertEqual(self.workflows(),['flow']);self.runner.resume(run['id']);QTest.qWait(30)
        self.assertEqual(self.workflows(),['flow','B']);self.executor.finish();self.assertIsNone(self.runner.current())

    def test_reopen_and_workspace_results_do_not_restart(self):
        from prompt_studio.stage_runner import StageRunner
        self.stages(('flow','B'));self.click();run=self.runner.current();first=self.w.state['workspace']
        self.runner.close();self.w.comfy.input_flow.chain=self.runner=StageRunner(self.w.comfy.input_flow)
        self.executor.finish();self.assertEqual(self.runner.current()['status'],'paused');self.assertEqual(len(self.executor.submissions),1)
        with patch('prompt_studio.window.QInputDialog.getText',return_value=('other',True)):self.w.new_workspace()
        self.assertFalse(self.runner.results);self.w.workspace.setCurrentIndex(self.w.workspace.findData(first));QTest.qWait(20)
        self.assertTrue(self.runner.results);self.runner.resume(run['id']);QTest.qWait(30);self.executor.finish()
        self.assertEqual(self.workflows(),['flow','B'])

    def test_cancel_preserves_foreign_tasks_and_late_completion_does_not_advance(self):
        source,items,_=self.image_batch(auto=False);self.click();run=self.runner.current();attempt=self.runner.store.rows('attempt')[0]
        self.executor.pending['foreign']={'prompt_id':'foreign'}
        # Leave the original submission in flight to deliver a successful late receipt.
        original=self.executor.request
        def delayed(route,data=None,done=None,failed=None,**kwargs):
            if route=='workflow/native/cancel':
                self.executor.cancelled.append(data['id'])
                if done:done(dict(state='cancelling'))
            else:original(route,data,done,failed,**kwargs)
        self.w.comfy.request=delayed;self.runner.cancel(run['id']);self.executor.finish(ident=attempt['prompt_id'])
        self.assertIn('foreign',self.executor.pending);self.assertEqual(self.executor.cancelled,[attempt['id']])
        self.assertEqual(self.w.state['canvas_functions']['images'][source]['index'],0);self.assertEqual(len(self.executor.submissions),1)
        self.assertEqual(self.runner.store.read(run['id'])['status'],'cancelled')

    def test_formal_failure_retry_reuses_recorded_seed_and_text(self):
        self.stages();self.click();original=copy.deepcopy(self.executor.submissions[0]);self.executor.finish(error=True)
        item=self.runner.store.rows('attempt')[0];self.assertEqual(item['status'],'failed')
        self.c.commit(lambda s:s['uses']['root'].update(prompt='later'));self.executor.graphs['flow']['3']['inputs']['seed']=999
        self.runner.retry(item['id']);QTest.qWait(30)
        self.assertEqual(len(self.executor.submissions),2,self.notices)
        self.assertEqual(self.executor.submissions[-1]['prompt']['6']['inputs']['text'],original['prompt']['6']['inputs']['text'])
        self.assertEqual(self.executor.submissions[-1]['prompt']['3']['inputs']['seed'],original['prompt']['3']['inputs']['seed'])

    def test_selected_result_missing_pauses_only_its_flow(self):
        keys=self.stages(('flow','B'));source=self.c.functions.add_image(enhanced=False)
        def wire(s):
            s['canvas_functions']['images'][source].update(stage_reference=keys[0],input_index=4)
            image=next(k for k,v in s['multi_output']['image_inputs'].items() if v['workflow']=='B')
            model.connect(s,source,image,'image')
        self.c.commit(wire)
        self.click();self.executor.finish();self.assertEqual(self.workflows(),['flow']);self.assertIn('不存在',self.runner.current()['message'])

    def test_result_source_text_matches_each_image(self):
        from prompt_studio import clip_flow
        source,items,refs=self.image_batch(count=3);down={}
        def setup(s):
            for name in ('B','C'):
                p=next(p for p in s['generation']['profiles'] if p['id']==name);p['graph']['6']=dict(class_type='CLIPTextEncode',inputs=dict(text='web'))
            first=clip_flow.add(s);clip_flow.set_binding(s,'B',first,('6','text'))
            model.connect(s,refs['inner']+'::clip1',first,'clip');model.connect(s,first,refs['stage'],'control')
            downstream=stage_model.add(s,workflow='C');s['multi_output']['stages'][downstream]['output']='9';down['stage']=downstream
            model.connect(s,refs['stage'],downstream,'done')
        self.assertTrue(self.c.commit(setup),self.notices)
        second_source=self.c.functions.add_image(source=items[0])
        def connect(s):
            model.connect(s,refs['stage'],second_source,'image')
            image=add_image_input(s);s['multi_output']['image_inputs'][image].update(workflow='C',node='10');model.connect(s,second_source,image,'image');model.connect(s,image,down['stage'],'control')
            clip=clip_flow.add(s);clip_flow.set_binding(s,'C',clip,('6','text'));model.connect(s,second_source,clip,'clip');model.connect(s,clip,down['stage'],'control')
        self.assertTrue(self.c.commit(connect),self.notices)
        for name in ('B','C'):self.executor.graphs[name]=copy.deepcopy(next(p for p in self.w.state['generation']['profiles'] if p['id']==name)['graph'])
        self.click()
        for i in range(6):self.assertTrue(self.executor.pending,(i,self.notices));self.executor.finish()
        self.assertEqual([p['prompt']['6']['inputs']['text'] for p in self.executor.submissions],['image prompt 0','image prompt 1','image prompt 2']*2)
        self.assertEqual(self.w.state['canvas_functions']['images'][source]['index'],2)


    def test_repeated_a_stages_and_two_complete_rounds(self):
        keys=self.stages(('flow','B','flow'));self.controls.count.setValue(2);self.click()
        self.click();self.assertEqual(len(self.executor.submissions),1)
        for _ in range(6):self.executor.finish()
        self.assertEqual(self.workflows(),['flow','B','flow']*2,self.notices)
        markers=[p['extra_data']['extra_pnginfo']['prompt_studio']['generation']['chain'] for p in self.executor.submissions]
        self.assertEqual([m['stage'] for m in markers],keys*2)
        self.assertEqual([m['round'] for m in markers],[1,1,1,2,2,2])
        self.assertEqual(len({m['attempt'] for m in markers}),6)

    def test_stage_reference_has_dependency_without_long_wire(self):
        from test_comfy_integration import png
        path=Path(self.tmp.name)/'old.png';png(path,{})
        source=self.c.functions.add_image(path);keys=self.stages(('flow','B'))
        def wire(s):
            s['canvas_functions']['images'][source]['stage_reference']=keys[0]
            s['canvas_functions']['images'][source]['input_index']=1
            image=next(k for k,v in s['multi_output']['image_inputs'].items() if v['workflow']=='B')
            model.connect(s,source,image,'image')
            for line in list(s['multi_output']['connections']):
                if line['kind']=='done':model.disconnect(s,line['id'])
        self.assertTrue(self.c.commit(wire),self.notices)
        self.executor.graphs['flow']['5']['inputs']['batch_size']=3
        self.click();self.assertEqual(self.workflows(),['flow']);self.executor.finish(3)
        reference=self.runner.results[keys[0]]['images'][1]
        self.assertIn(reference['sha256'],self.executor.submissions[-1]['prompt']['10']['inputs']['image'])
        self.executor.finish();self.assertEqual(self.workflows(),['flow','B'])

    def test_pause_stops_supply_and_removed_waiting_item_is_not_submitted(self):
        self.image_batch(count=12);self.click();run=self.runner.current()
        self.runner.pause(run['id']);self.executor.finish()
        self.assertEqual(len(self.executor.submissions),1)
        pending=self.runner.store.rows('entry',active=True);self.assertEqual(len(pending),9)
        self.assertEqual(sum(e['status']=='complete' for e in self.runner.store.rows('entry')),1)
        victim=next(i for i in pending if i['status']=='waiting');self.runner.store.remove(victim['id'])
        removed=next(v['value']['sha256'] for v in victim['saved']['inputs'].values() if v['type']=='image')
        self.runner.resume(run['id']);QTest.qWait(30)
        for _ in range(10):self.executor.finish()
        self.assertEqual(len(self.executor.submissions),11,self.notices)
        self.assertFalse(any(removed in p['prompt']['10']['inputs']['image'] for p in self.executor.submissions))
        self.assertIsNone(self.runner.current())

    def test_outer_items_visible_before_stage_and_capacity_is_per_module(self):
        keys=self.stages(('flow','B','C'));refs=[]
        def setup(s):
            q=add_scheduler(s);refs.append(q);model.connect(s,keys[1],q+'::flow','flow')
        self.c.commit(setup);self.controls.count.setValue(10);self.click()
        rows=self.runner.store.rows('entry',active=True);self.assertEqual(len(rows),10)
        self.assertTrue(all(i['status']=='waiting' for i in rows));self.controls.count.setValue(1);self.click()
        self.assertEqual(len(self.runner.store.rows('entry',active=True)),10)
        self.assertEqual(len(self.runner.runs(True)),1);self.assertTrue(any('十項' in n for n in self.notices))

    def test_generated_metadata_restores_modules_and_manual_blank(self):
        from prompt_studio.image_source import read_content
        key=self.stages()[0];self.click();self.executor.finish()
        source=self.runner.results[key]['images'][0];content=read_content(self.w.store.directory/source['relative'])
        self.assertEqual(content['kind'],'modules');self.assertEqual(content['roots'][0]['prompt'],'white shirt')
        self.assertIsNone(content['manual_text'])
        self.c.outputs[self.out].panel.editor.setPlainText('');self.click();self.executor.finish()
        source=self.runner.results[key]['images'][0];content=read_content(self.w.store.directory/source['relative'])
        self.assertEqual(content['text'],'');self.assertEqual(content.get('manual_text'),'')

    def test_cyclic_stage_and_crossed_scope_rejected(self):
        keys=self.stages(('flow','B','C'));refs=[]
        def setup(s):
            for group in (keys[:2],keys[1:]):
                q=add_scheduler(s);refs.append(q)
                for key in group:model.connect(s,key,q+'::flow','flow')
        self.c.commit(setup)
        with self.assertRaisesRegex(ValueError,'交錯'):stage_model.compile_plan(self.w.state)
        state=copy.deepcopy(self.w.state);state['multi_output']['connections']=[c for c in state['multi_output']['connections'] if c['kind']!='flow']
        for q in refs:state['multi_output']['schedulers'][q]['mode']='data'
        state['multi_output']['connections'].append(dict(id='cycle',source=keys[-1],destination=keys[0],kind='done'))
        with self.assertRaisesRegex(ValueError,'循環'):stage_model.compile_plan(state)


class StageInterfaceTests(unittest.TestCase):
    setUp=StageTests.setUp
    tearDown=StageTests.tearDown
    click=StageTests.click
    workflows=StageTests.workflows
    stages=StageTests.stages
    image_batch=StageTests.image_batch

    def test_waiting_outer_item_lists_and_replaces_one_saved_batch_image(self):
        from prompt_studio.widgets import ComboBox
        source,images,config=self.image_batch(outer=True);self.click();self.click()
        panel=self.c.flow_cards[config['outer']].panel;panel.refresh();panel.list.setCurrentRow(1)
        item=panel.selected();seen=[]
        def edit():
            dialog=QApplication.activeModalWidget();picker=dialog.findChild(ComboBox);seen.append(picker.count())
            self.assertIn('input0.png',picker.itemText(0))
            with patch('PySide6.QtWidgets.QFileDialog.getOpenFileName',return_value=(str(self.w.store.directory/images[1]['relative']),'')):
                QTest.mouseClick(next(b for b in dialog.findChildren(QPushButton) if b.text()=='替換此項圖片'),Qt.MouseButton.LeftButton)
            QTest.mouseClick(next(b for b in dialog.findChildren(QPushButton) if b.text()=='保存此項'),Qt.MouseButton.LeftButton)
        QTimer.singleShot(30,edit);panel.detail()
        self.assertEqual(seen,[3]);saved=self.runner.store.read(item['id'])['saved']['batches'][source]
        self.assertEqual(saved[0]['sha256'],images[1]['sha256']);self.assertEqual(saved[1:],images[1:])
        self.assertEqual(self.w.state['canvas_functions']['images'][source]['items'],images)
        for _ in range(6):self.executor.finish()
        submitted=[p['prompt']['10']['inputs']['image'] for p in self.executor.submissions]
        self.assertEqual(submitted[3],submitted[1]);self.assertNotEqual(submitted[3],submitted[0])
        self.assertIsNone(self.runner.current(),self.notices)

    def test_visible_ports_follow_shared_module_contracts(self):
        self.image_batch(outer=True)
        from prompt_studio.module_contracts import port_types
        for port in self.c.ports.values():
            self.assertIn(port.kind,port_types(self.w.state,port.key,port.output),(port.key,port.kind,port.output))

    def test_simple_loader_reads_metadata_and_selects_stage_batch_input(self):
        from test_comfy_integration import png
        from prompt_studio.flow_data import resolve,image_list
        path=Path(self.tmp.name)/'simple.png';graph=copy.deepcopy(self.executor.graphs['flow']);graph['6']['inputs']['text']='raw original text';png(path,{'prompt':graph})
        source=self.c.functions.add_image(path,enhanced=False)
        self.assertEqual(resolve(self.w.state,source,'clip')['value'],'raw original text')
        stage=self.stages()[0];self.c.commit(lambda s:model.connect(s,stage,source,'image'))
        self.executor.graphs['flow']['5']['inputs']['batch_size']=3;self.click();self.executor.finish(3)
        collection=self.w.comfy.images.collection(source);self.assertEqual(len(collection['items']),3)
        self.w.comfy.images.choose(source,collection['collection'],2)
        self.assertEqual(self.w.comfy.images.source(source),collection['items'][2])
        self.assertEqual(len(image_list(self.runner.context(self.runner.results),source)),1)
        self.assertEqual(len(self.w.comfy.images.collection(source)['items']),3)
        self.assertEqual(len(self.executor.submissions),1)

    def test_control_drag_auto_choice_stable_manual_choice_undo(self):
        stages=[]
        self.c.commit(lambda s:(stages.append(stage_model.add(s,(1000,0))),s['text_positions'].update({self.clip:[400,0]})))
        stage=stages[0];view=self.c.view;view.resetTransform();view.scale(.7,.7);view.centerOn(850,200);QTest.qWait(10)
        a=view.mapFromScene(self.c.ports[(self.clip,'control',True)].scenePos());b=view.mapFromScene(self.c.ports[(stage,'control',False)].scenePos())
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=a);QTest.mouseMove(view.viewport(),b,20);QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=b);QTest.qWait(20)
        self.assertEqual(self.c.data()['stages'][stage]['workflow'],'flow',self.notices)
        self.w.settings_page.workflow_manager.catalog.loaded=True
        def edit():
            dialog=QApplication.activeModalWidget();dialog.workflow.setCurrentIndex(dialog.workflow.findData('B'));dialog.target.setCurrentIndex(dialog.target.findData('9'))
            QTest.mouseClick(next(b for b in dialog.findChildren(QPushButton) if b.text()=='儲存'),Qt.MouseButton.LeftButton)
        QTimer.singleShot(20,edit);self.c.flow_cards[stage].panel.edit();self.assertEqual(self.c.data()['stages'][stage]['workflow'],'B',self.notices)
        from prompt_studio.stage_widgets import StageDialog
        self.c.commit(lambda s:s['multi_output']['stages'][stage].update(output=None))
        dialog=StageDialog(self.c,stage);self.assertIsNone(dialog.target.currentData());dialog.deleteLater()
        self.c.commit(lambda s:s['multi_output']['stages'][stage].update(output='9'))
        self.click();self.assertEqual(self.workflows(),['B'],self.notices)
        view.setFocus();b=view.mapFromScene(self.c.ports[(stage,'control',False,self.clip)].scenePos());blank=b+QPoint(0,-110)
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=b);QTest.mouseMove(view.viewport(),blank,20);QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=blank)
        self.executor.finish();QTest.qWait(150);self.assertIsNone(self.runner.current())
        self.assertEqual(self.runner.runs()[-1]['status'],'complete')
        QTest.keyClick(view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        self.assertIn(self.clip,stage_model.controls(self.w.state,stage));self.assertEqual(len(self.executor.submissions),1)

    def test_pending_drag_full_editor_and_runtime_readonly(self):
        stage=self.stages()[0];refs=[]
        def setup(s):
            q=add_scheduler(s);refs.append(q);model.connect(s,stage,q+'::flow','flow')
        self.c.commit(setup)
        for text in ('first','second full text','third full text'):
            self.c.commit(lambda s,t=text:s['uses']['root'].update(prompt=t));self.click()
        panel=self.c.flow_cards[refs[0]].panel;listing=panel.list;self.c.fit();panel.refresh();QApplication.processEvents()
        ids=[i['id'] for i in panel.entries()];listing.setCurrentRow(2);selected=listing.currentItem();panel.refresh();self.assertIs(listing.currentItem(),selected)
        self.assertNotIn('full text',selected.text())
        listing._source=ids[2];listing._drag_token=b'stage-queue-drag';panel.dragging=True
        mime=QMimeData();mime.setData(listing.mime,listing._drag_token);point=listing.visualItemRect(listing.item(1)).topLeft()+QPoint(12,2)
        for event in (QDragEnterEvent(point,Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier),QDragMoveEvent(point,Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier),QDropEvent(QPointF(point),Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)):
            event.ignore();QApplication.sendEvent(listing.viewport(),event);self.assertTrue(event.isAccepted())
        panel.dragging=False;listing._source=None;listing._drag_token=b'';panel.refresh()
        self.assertEqual([i['id'] for i in panel.entries()],[ids[0],ids[2],ids[1]])
        seen=[]
        def edit():
            dialog=QApplication.activeModalWidget();editor=dialog.findChild(QPlainTextEdit);seen.append(editor.toPlainText());editor.setPlainText('')
            QTest.mouseClick(next(b for b in dialog.findChildren(QPushButton) if b.text()=='保存此項'),Qt.MouseButton.LeftButton)
        QTimer.singleShot(30,edit)
        QTest.mouseClick(listing.viewport(),Qt.MouseButton.LeftButton,pos=listing.visualItemRect(listing.currentItem()).center())
        QTest.mouseDClick(listing.viewport(),Qt.MouseButton.LeftButton,pos=listing.visualItemRect(listing.currentItem()).center())
        self.assertEqual(seen,['third full text']);self.assertEqual(self.runner.store.read(ids[2])['saved']['values'][self.clip]['value'],'')
        self.executor.finish();self.assertEqual(self.executor.submissions[-1]['prompt']['6']['inputs']['text'],'')
        panel.refresh();listing.setCurrentRow(0)
        def inspect():
            dialog=QApplication.activeModalWidget();self.assertTrue(dialog.findChild(QPlainTextEdit).isReadOnly());dialog.accept()
        QTimer.singleShot(30,inspect);panel.detail()

    def test_preview_terminal_recent_drawer_and_layout(self):
        stage=self.stages()[0];self.c.commit(lambda s:model.connect(s,stage,model.PREVIEW,'image'))
        self.executor.graphs['flow']['5']['inputs']['batch_size']=3;self.click();self.executor.finish(3)
        self.assertEqual(len(self.c.results.input_records),3);self.assertFalse(self.c.results.recent_button.isVisible())
        self.assertFalse(any(k[0]==model.PREVIEW and k[2] for k in self.c.ports))
        before=copy.deepcopy(self.w.state)
        with patch.object(self.c.results,'show_image') as shown:self.c.results.select(type('Item',(),{'data':lambda self,role:dict(index=2)})())
        self.assertEqual(self.w.state,before)
        self.w.resize(1280,800);QTest.qWait(20);corner=self.c.recent_corner.button
        QTest.mouseClick(corner,Qt.MouseButton.LeftButton);QTest.qWait(20)
        self.assertTrue(self.w.recent_dock.isVisible());self.assertTrue(self.c.view.isVisible())
        self.w.recent_dock.close();QTest.qWait(15);self.assertGreaterEqual(self.w.tabs.indexOf(self.w.recent),0)
        buttons=[b.text() for b in self.controls.findChildren(QPushButton) if b.isVisible()]
        self.assertEqual(len(buttons),3,buttons)
        for width,height in ((1280,800),(1024,720)):
            self.w.resize(width,height);self.c.fit();QTest.qWait(20)
            bar=self.controls;self.assertLessEqual(bar.width(),self.w.width())
            self.assertTrue(self.c.view.viewport().rect().contains(corner.geometry()))
        target=Path(__import__('os').environ.get('PCS_TEST_ARTIFACT_DIR',self.tmp.name));target.mkdir(parents=True,exist_ok=True)
        self.w.resize(1280,800);self.c.fit();QTest.qWait(25);self.w.grab().save(str(target/'stage-layout-1280.png'))
