"""Execute clicks, native receipts and real queue/history reconciliation for Stage routing."""
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from test_0831_stages import StageTests
from prompt_studio import multi_output as model,stage_model
from prompt_studio.flow_data import add_scheduler

class StageLifecycleTests(StageTests):
    # Reuse the formal native fixture, but do not rerun the inherited suite here.
    def queue(self,stage=None,scope=False):
        stage=stage or self.stages()[0];keys=[]
        def setup(state):
            key=add_scheduler(state);keys.append(key)
            if scope:model.connect(state,stage,key+'::flow','flow')
            else:
                model.connect(state,self.out,key+'::clip1','clip')
                model.connect(state,key+'::clip1',self.clip,'clip')
        self.assertTrue(self.c.commit(setup),self.notices)
        return stage,keys[0]

    def test_orphan_stage_does_not_generate_or_consume_queue(self):
        stage,key=self.queue()
        self.c.commit(lambda s:[model.disconnect(s,c['id']) for c in list(s['multi_output']['connections']) if c['destination']==stage and c['kind']=='control'])
        self.click()
        self.assertEqual(len(self.executor.applied),1,self.notices)
        self.assertFalse(self.executor.submissions)
        self.assertFalse(self.runner.store.rows('entry'))
        self.assertEqual(stage_model.compile_plan(self.w.state)['order'],[])

    def test_unconfigured_orphan_and_empty_scope_do_not_block_sync(self):
        stage=[]
        def setup(s):
            stage.append(stage_model.add(s));key=add_scheduler(s);model.connect(s,stage[0],key+'::flow','flow')
        self.c.commit(setup);self.click()
        self.assertEqual(len(self.executor.applied),1,self.notices)
        self.assertFalse(self.executor.submissions)

    def test_readers_and_renaming_do_not_pause_execution(self):
        stage,key=self.queue();self.click();self.click()
        reader=self.c.functions.add_image(enhanced=False)
        self.c.commit(lambda s:(model.connect(s,stage,reader,'image'),s['multi_output']['stages'][stage].update(name='Renamed')))
        self.assertEqual(self.runner.current()['status'],'running',self.notices)
        self.executor.finish();self.assertEqual(len(self.executor.submissions),2,self.notices)
        self.executor.finish();self.assertIsNone(self.runner.current())

    def test_image_input_rename_during_native_submission_keeps_flow_running(self):
        self.stages(('flow','B'));self.click();run=self.runner.current()['id']
        self.executor.finish();self.assertEqual(self.workflows(),['flow','B'],self.notices)
        key=next(iter(self.c.data()['image_inputs']))
        self.assertTrue(self.c.commit(lambda s:s['multi_output']['image_inputs'][key].update(name='另一個名稱')))
        self.assertEqual(self.runner.store.read(run)['status'],'running',self.notices)
        self.assertEqual(self.runner.current()['id'],run)
        self.assertFalse(self.executor.cancelled)
        self.executor.finish()
        self.assertEqual(self.runner.store.read(run)['status'],'complete',self.notices)
        self.assertEqual(self.workflows(),['flow','B'])

    def test_removing_waiting_free_input_keeps_stage_foreign_apply_owned(self):
        import copy
        from prompt_studio.clip_flow import add,set_binding
        from prompt_studio.flow_data import add_image_input
        stage=self.stages()[0];reader=self.c.functions.add_image(enhanced=False);targets=[]
        def setup(state):
            data=state['multi_output']
            profile=copy.deepcopy(next(p for p in state['generation']['profiles'] if p['id']=='flow'))
            profile.update(id='D',name='D',frontend_id='native-D');state['generation']['profiles'].append(profile)
            clip=add(state);model.connect(state,self.out,clip,'clip');set_binding(state,'D',clip,('7','text'))
            model.connect(state,clip,stage,'control')
            target=add_image_input(state);targets.append(target);data['image_inputs'][target].update(workflow='B',node='10')
            model.connect(state,stage,reader,'image');model.connect(state,reader,target,'image')
            state['canvas_functions']['images'][reader]['output_node']='9'
        self.assertTrue(self.c.commit(setup),self.notices)
        profile=next(p for p in self.w.state['generation']['profiles'] if p['id']=='D')
        self.executor.graphs['D']=copy.deepcopy(profile['graph'])
        self.executor.identities['D']=dict(workflow='D',path='',frontend_id='native-D')
        self.executor.live['workflows']=list(self.executor.identities.values())
        request=self.w.comfy.request;pending=[]
        def delay(route,data=None,done=None,failed=None,**kwargs):
            if route=='workflow/native/apply' and data['workflow']=='D':
                self.executor.native.poll(self.executor.live)
                self.executor.native.start(data,action='apply')
                def acknowledge():
                    native=self.executor.native;live=self.executor.live
                    command=native.poll(live)['commands'][0];target=self.executor.identities['D']
                    native.activate(dict(command,session=live['session'],client_id=live['client_id'],opened_identity=target,opened_epoch=live['epoch']+1))
                    live.update(identity=target,epoch=live['epoch']+1)
                    for binding in command['texts']:
                        self.executor.graphs['D'][binding['node']]['inputs'][binding['field']]=binding['text']
                    result=native.reply(dict(command,session=live['session'],applied=True))
                    self.executor.applied.append(data);done(result)
                pending.append(acknowledge)
            else:return request(route,data,done,failed,**kwargs)
        self.w.comfy.request=delay;self.click();run=self.runner.current()['id']
        self.assertEqual(len(pending),1);self.assertFalse(self.executor.submissions)
        def remove(state):
            data=state['multi_output'];data['image_inputs'].pop(targets[0])
            data['connections']=[c for c in data['connections'] if c['destination']!=targets[0]]
        self.assertTrue(self.c.commit(remove),self.notices)
        current=self.runner.store.read(run)
        self.assertEqual((current['status'],current['free_state']),('running','retained'),self.notices)
        self.assertFalse(self.executor.cancelled)
        pending.pop()();QTest.qWait(30)
        self.assertEqual([r['workflow'] for r in self.executor.applied],['D'])
        self.assertEqual(self.workflows(),['flow'],self.notices)
        self.executor.finish();self.assertEqual(self.runner.store.read(run)['status'],'complete',self.notices)
        self.assertEqual([r['workflow'] for r in self.executor.applied],['D'])

    def test_png_restore_undo_redo_cancels_pending_free_apply_without_regenerating(self):
        import copy
        from prompt_studio.flow_data import add_image_input
        from prompt_studio.snapshot_history import capture
        from prompt_studio.snapshots import make_snapshot,restore_snapshot
        stage=self.stages()[0];original=copy.deepcopy(self.w.state);before=capture(self.c)
        targets=[]
        def connect(state):
            target=add_image_input(state);targets.append(target)
            state['multi_output']['image_inputs'][target].update(workflow='B',node='10')
            model.connect(state,stage,target,'image')
        self.assertTrue(self.c.commit(connect),self.notices)
        restored=restore_snapshot(original,make_snapshot(self.w.state))
        self.runner.workspace_changed(original['workspace']);self.w.state=restored
        self.c.last_state=self.c.history_state();self.w.refresh_workspaces();self.w.refresh_builder()
        QTest.qWait(40);self.w.remember_canvas_view()
        after=capture(self.c);imported=self.w.state['workspace']
        self.assertNotEqual(imported,original['workspace'])
        request=self.w.comfy.request;held=[];cancelled=[]
        def delay(route,data=None,done=None,failed=None,**kwargs):
            if route=='workflow/native/apply':
                self.executor.native.poll(self.executor.live)
                self.executor.native.start(data,action='apply');held.append((data,done));return
            if route=='workflow/native/cancel':
                cancelled.append(data['id']);result=self.executor.native.cancel(data['id'],None,None)
                if done:done(result)
                return
            return request(route,data,done,failed,**kwargs)
        self.w.comfy.request=delay;self.click();self.executor.finish()
        run=self.runner.runs()[0];self.assertEqual(run['free_state'],'applying')
        attempts=copy.deepcopy(self.runner.store.rows('attempt',owner=run['id']))
        self.assertEqual([r['status'] for r in attempts],['complete'])
        operation=held[0][0]['id'];self.assertEqual(self.executor.native.status(operation)['state'],'pending')
        self.c.undo_stack=[(before,after)];self.c.redo_stack=[]
        self.c.view.setFocus();self.c.view.scene().clearFocus()
        QTest.keyClick(self.c.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(30)
        self.assertEqual(self.w.state['workspace'],original['workspace'],self.notices)
        self.assertNotIn(targets[0],self.c.data()['image_inputs'])
        self.assertEqual(cancelled,[operation])
        self.assertEqual(self.executor.native.poll(self.executor.live)['commands'],[])
        for data,done in held:done(self.executor.native.status(data['id']))
        QTest.keyClick(self.c.view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier);QTest.qWait(80)
        self.assertEqual(self.w.state['workspace'],imported,self.notices)
        self.assertIn(targets[0],self.c.data()['image_inputs'])
        self.assertEqual(self.runner.store.rows('attempt',owner=run['id']),attempts)
        self.assertFalse(self.executor.applied);self.assertEqual(self.workflows(),['flow'])
        self.assertEqual(len(held),1);self.assertEqual(self.executor.native.status(operation)['state'],'failed')

    def test_insert_queue_during_direct_run_cannot_leave_future_waiting(self):
        stage=self.stages()[0];self.click();old=self.runner.current()['id']
        stage,key=self.queue(stage);self.executor.finish()
        self.assertEqual(self.runner.store.read(old)['status'],'complete',self.notices)
        self.click();self.assertEqual(len(self.executor.submissions),2,self.notices)
        self.executor.finish();self.click();self.assertEqual(len(self.executor.submissions),3,self.notices)
        self.executor.finish();self.assertIsNone(self.runner.current())

    def test_replace_queue_keeps_old_waiting_without_owning_new_run(self):
        stage,old_queue=self.queue();self.click();self.click();old=self.runner.current()['id']
        self.c.remove_flow_node('schedulers',old_queue);stage,new_queue=self.queue(stage)
        self.executor.finish()
        retained=[e for e in self.runner.store.rows('entry') if e['run']==old and e['status']=='retained']
        self.assertEqual(len(retained),1)
        self.click();new=self.runner.current()
        self.assertNotEqual(old,new['id']);self.assertEqual(len(self.executor.submissions),2,self.notices)
        self.executor.finish();self.executor.tick()
        self.assertEqual(len(self.executor.submissions),2)
        self.assertEqual(self.runner.store.read(old)['status'],'paused')
        self.assertEqual(self.runner.store.read(new['id'])['status'],'complete')

    def test_explicit_pause_survives_empty_queue_without_leaving_completed_run_open(self):
        stage,key=self.queue();self.click();old=self.runner.current()['id'];self.runner.pause(old)
        self.executor.finish();self.assertEqual(self.runner.store.read(old)['status'],'complete',self.notices)
        self.click();self.assertEqual(len(self.executor.submissions),1)
        self.assertEqual(self.runner.current()['status'],'paused')
        panel=self.c.flow_cards[key].panel
        QTest.mouseClick(next(b for b in panel.findChildren(QPushButton) if b.text()=='繼續'),Qt.MouseButton.LeftButton);QTest.qWait(40)
        self.assertEqual(len(self.executor.submissions),2,self.notices);self.executor.finish()
        self.click();self.assertEqual(len(self.executor.submissions),3,self.notices);self.executor.finish()

    def test_policy_only_changes_new_items_and_does_not_pause(self):
        stage,key=self.queue(scope=True);self.click()
        self.c.commit(lambda s:s['uses']['root'].update(prompt='saved second'));self.click()
        self.c.commit(lambda s:s['multi_output']['schedulers'][key].update(policies={self.clip:'live'}))
        self.c.commit(lambda s:s['uses']['root'].update(prompt='third initially'));self.click()
        self.c.commit(lambda s:s['uses']['root'].update(prompt='latest live'))
        self.executor.finish();self.assertEqual(self.executor.submissions[-1]['prompt']['6']['inputs']['text'],'saved second')
        self.executor.finish();self.assertEqual(self.executor.submissions[-1]['prompt']['6']['inputs']['text'],'latest live')
        self.executor.finish();self.assertIsNone(self.runner.current(),self.notices)

    def test_reopen_old_completed_tail_closes_without_resubmitting(self):
        from prompt_studio.stage_runner import StageRunner
        stage,key=self.queue();self.click();old=self.runner.current()['id']
        self.runner.pause(old,reason='restart')
        # Reproduce the previous release's missing frame cleanup while the
        # formal native history still establishes actual completion.
        with patch.object(self.runner,'settle'):
            self.executor.finish()
        tail=self.runner.store.read(old)
        self.assertEqual(tail['stack'][-1]['index'],1)
        self.assertEqual(self.runner.store.rows('attempt')[0]['status'],'complete')
        self.runner.close();self.w.comfy.input_flow.chain=self.runner=StageRunner(self.w.comfy.input_flow)
        self.click();self.assertEqual(self.runner.store.read(old)['status'],'complete')
        self.assertEqual(len(self.executor.submissions),2,self.notices)
        self.executor.finish();self.assertIsNone(self.runner.current())

    def test_native_only_stage_runs_only_after_reached_predecessor(self):
        first=self.stages()[0];following=[]
        self.c.commit(lambda s:(following.append(stage_model.add(s,workflow='B')),model.connect(s,first,following[0],'done')))
        self.click();self.assertEqual(self.workflows(),['flow'])
        self.executor.finish();self.assertEqual(self.workflows(),['flow','B'],self.notices)
        self.executor.finish();self.assertIsNone(self.runner.current())

    def test_data_queue_hides_stage_input_policy_button(self):
        stage,key=self.queue();panel=self.c.flow_cards[key].panel;panel.refresh()
        self.assertTrue(panel.policy_button.isHidden())
        self.c.remove_flow_node('schedulers',key);stage,key=self.queue(stage,scope=True)
        panel=self.c.flow_cards[key].panel;panel.refresh();self.assertFalse(panel.policy_button.isHidden())

# unittest discovery otherwise repeats every inherited test from StageTests.
def load_tests(loader,tests,pattern):
    return unittest.TestSuite(StageLifecycleTests(name) for name in StageLifecycleTests.__dict__ if name.startswith('test_'))
