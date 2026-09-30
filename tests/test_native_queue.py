"""Native command/payload evidence. All backend history and images are fixtures."""
import copy
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from test_comfy_integration import Service
from test_multi_output import workspace
from prompt_studio.snapshots import make_snapshot
from prompt_studio.job_details import describe


class NativeQueueTests(unittest.TestCase):
    def test_diagnostics_are_owned_bounded_and_do_not_replace_first_error(self):
        self.queue.start(self.request);self.queue.poll(self.live)
        issued=self.queue.status(self.request['id'])
        self.assertIn('issued_at',issued);self.assertEqual(issued['diagnostics'],{})
        value=dict(self.live,id=self.request['id'],event=dict(seq=1,phase='received',state='started',elapsed_ms=0))
        with self.assertRaises(ValueError):self.queue.event(dict(value,session='other'))
        with self.assertRaises(ValueError):self.queue.event(dict(value,client_id='other'))
        with self.assertRaises(ValueError):self.queue.event(dict(value,event=dict(value['event'],prompt='secret')))
        for i in reversed(range(1,65)):
            self.queue.event(dict(value,event=dict(seq=i,phase='select',state='timeout' if i==64 else 'started',elapsed_ms=i)))
        self.queue.event(value)
        events=self.queue.status(self.request['id'])['diagnostics']['events']
        self.assertEqual(len(events),64);self.assertEqual(events[-1]['seq'],64)
        self.assertTrue(all(set(e)=={'seq','phase','state','elapsed_ms','received_at'} for e in events))
        self.queue.reply(dict(self.live,id=self.request['id'],error='original load timeout'))
        operation=self.queue.read(self.request['id']);operation.update(error='cancelled later',terminal_action='cancel');self.queue.save(operation)
        receipt=self.queue.status(self.request['id'])
        self.assertEqual(receipt['first_error'],'original load timeout')
        self.assertEqual(receipt['terminal_action'],'cancel')
        text=describe(dict(receipt,requested_workflow='test'))
        self.assertIn('切換工作流 · 逾時',text);self.assertIn('最初錯誤：original load timeout',text)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.service=Service(self.root,self.root,self.root);self.queue=self.service.native_queue
        state,_,_=workspace();profile=state['generation']['profiles'][0]
        profile.update(origin=dict(server='http://127.0.0.1:8188',path='folder/A.json'),frontend_id='native-A')
        profile['graph']['5']['inputs'].update(width=832,height=1216,batch_size=1);profile['values']={'width':832,'height':1216}
        self.snapshot=make_snapshot(state)
        self.live=dict(session='tab',identity=dict(workflow='',path='folder/A.json',frontend_id='native-A'),epoch=0,client_id='real-web-sid',ready=True)
        self.request=dict(id='operation-1',snapshot=self.snapshot,workflow='flow')
        self.queue.poll(self.live)

    def prepared(self):
        self.queue.start(self.request);command=self.queue.poll(self.live)['commands'][0]
        actual=copy.deepcopy(self.snapshot['state']['generation']['profiles'][0]['graph'])
        actual['5']['inputs'].update(width=1216,height=832,batch_size=2)
        actual['6']['inputs']['text']='';actual['7']['inputs']['text']='manual negative'
        visual=dict(id='native-A',nodes=[dict(id=5,type='EmptyLatentImage',widgets_values=[1216,832,2])])
        value=dict(command,session='tab',client_id='real-web-sid',output=actual,workflow=visual)
        self.queue.prepare(value)
        visual['extra']={'pcs_native_operation':'operation-1'}
        return dict(prompt=actual,client_id='real-web-sid',extra_data=dict(extra_pnginfo=dict(workflow=visual)))

    def test_exact_native_payload_keeps_two_images_manual_text_and_original_snapshot(self):
        before=copy.deepcopy(self.snapshot);payload=self.prepared();graph=copy.deepcopy(payload['prompt'])
        self.service.prepare_prompt(payload)
        self.assertEqual(payload['prompt'],graph)
        self.assertEqual(payload['prompt']['5']['inputs'],dict(width=1216,height=832,batch_size=2))
        envelope=payload['extra_data']['extra_pnginfo']['prompt_studio']
        self.assertEqual([t['text'] for t in envelope['texts']],['','manual negative'])
        self.assertEqual(envelope['bindings'][0]['snapshot'],before)
        self.assertNotIn('pcs_native_operation',payload['extra_data']['extra_pnginfo']['workflow']['extra'])
        self.queue.reply(dict(self.live,id='operation-1',prompt_id=payload['prompt_id']))
        receipt=self.queue.status('operation-1');self.assertEqual(receipt['state'],'queued')
        self.assertEqual(receipt['payload']['prompt'],graph);self.assertNotIn('client_id',receipt['payload'])
        entry=dict(prompt=(0,payload['prompt_id'],graph,payload['extra_data']),outputs={'9':{'images':[
            dict(filename='one.png',type='temp',subfolder=''),dict(filename='two.png',type='temp',subfolder='')]}})
        self.assertEqual(len(self.service.results({payload['prompt_id']:entry})),2)
        text=describe(dict(id='operation-1',state='queued',created=0,server='local',prompt_id=payload['prompt_id'],payload=receipt['payload']))
        self.assertIn('1216',text);self.assertIn('manual negative',text)

    def test_wrong_identity_duplicate_tabs_and_server_refused(self):
        self.queue.sessions.clear();self.queue.poll(dict(self.live,identity=dict(self.live['identity'],frontend_id='A-copy')))
        with self.assertRaisesRegex(ValueError,'同一份'):self.queue.start(self.request)
        self.queue.poll(self.live);self.queue.poll(dict(self.live,session='second'))
        with self.assertRaisesRegex(ValueError,'唯一'):self.queue.start(self.request)
        self.queue.sessions.pop('second')
        with self.assertRaisesRegex(ValueError,'另一個'):self.queue.start(self.request,'http://127.0.0.1:8288')

    def test_unsaved_workflow_requires_native_id_as_well_as_embedded_id(self):
        profile=self.snapshot['state']['generation']['profiles'][0];profile.pop('origin')
        for native in ('native-A-copy',''):
            with self.subTest(native=native):
                self.queue.sessions.clear()
                self.queue.poll(dict(self.live,identity=dict(workflow='flow',path='',frontend_id=native)))
                with self.assertRaisesRegex(ValueError,'同一份'):self.queue.start(self.request)
        self.queue.sessions.clear()
        live=dict(self.live,identity=dict(workflow='flow',path='',frontend_id='native-A'))
        self.queue.poll(live);self.assertEqual(self.queue.start(self.request)['state'],'pending')
        command=self.queue.poll(live)['commands'][0]
        self.queue.prepare(dict(command,session='tab',client_id='real-web-sid',output=profile['graph'],workflow=dict(id='native-A',nodes=[])))
        payload=dict(prompt=copy.deepcopy(profile['graph']),client_id='real-web-sid',
            extra_data=dict(extra_pnginfo=dict(workflow=dict(extra=dict(pcs_native_operation=command['id'])))))
        self.service.prepare_prompt(payload)
        self.assertEqual(self.queue.reply(dict(live,id=command['id'],prompt_id=payload['prompt_id']))['state'],'queued')

    def test_inactive_target_requires_switch_receipt_then_records_target_not_source(self):
        target=copy.deepcopy(self.live['identity'])
        live=dict(self.live,identity=dict(workflow='',path='B.json',frontend_id='native-B'),workflows=[target],navigation_protocol=1)
        self.queue.poll(live);self.queue.start(self.request)
        command=self.queue.poll(live)['commands'][0]
        profile=self.snapshot['state']['generation']['profiles'][0]
        value=dict(command,session='tab',client_id='real-web-sid',output=profile['graph'],workflow=dict(id='native-A',nodes=[]))
        with self.assertRaisesRegex(ValueError,'切換回執'):self.queue.prepare(value)
        for opened,epoch in ((target,0),(dict(target,frontend_id='copy'),1)):
            with self.assertRaisesRegex(ValueError,'切換回執'):
                self.queue.activate(dict(value,opened_identity=opened,opened_epoch=epoch))
        self.queue.activate(dict(value,opened_identity=target,opened_epoch=1))
        self.queue.prepare(value)
        operation=self.queue.read(command['id'])
        self.assertEqual(operation['identity']['frontend_id'],'native-B')
        self.assertEqual(operation['marker']['generation']['native_identity']['frontend_id'],'native-A')
        self.assertEqual(operation['state'],'prepared')

    def test_second_browser_with_inactive_same_target_still_blocks_and_stale_switch_never_prepares(self):
        target=self.live['identity']
        live=dict(self.live,identity=dict(workflow='',path='B.json',frontend_id='native-B'),workflows=[target],navigation_protocol=1)
        self.queue.poll(live);self.queue.poll(dict(live,session='second'))
        with self.assertRaisesRegex(ValueError,'同一份'):self.queue.start(self.request)
        self.queue.sessions.pop('second');self.queue.start(self.request)
        command=self.queue.poll(live)['commands'][0]
        self.queue.cancel_pending()
        with self.assertRaisesRegex(ValueError,'切換回執'):
            self.queue.activate(dict(command,session='tab',client_id='real-web-sid',opened_identity=target,opened_epoch=1))

    def test_unsaved_workflow_identity_changed_before_prepare_is_refused(self):
        profile=self.snapshot['state']['generation']['profiles'][0];profile.pop('origin')
        self.queue.sessions.clear()
        live=dict(self.live,identity=dict(workflow='flow',path='',frontend_id='native-A'))
        self.queue.poll(live);self.queue.start(self.request);command=self.queue.poll(live)['commands'][0]
        self.queue.poll(dict(live,identity=dict(live['identity'],frontend_id='native-A-copy')))
        with self.assertRaisesRegex(ValueError,'身分'):
            self.queue.prepare(dict(command,session='tab',client_id='real-web-sid',output=profile['graph'],workflow=dict(id='native-A',nodes=[])))

    def test_either_journal_write_failure_rolls_back_both_and_blocks_submission(self):
        payload=self.prepared()
        for table in ('native_operations','jobs'):
            with self.subTest(table=table):
                with self.service.connect() as db:
                    db.execute(f"CREATE TRIGGER reject_receipt BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT, 'receipt failed'); END")
                rejected=copy.deepcopy(payload)
                with self.assertRaises(sqlite3.Error):self.service.prepare_prompt(rejected)
                self.assertEqual(rejected['prompt'],{})
                self.assertNotIn('prompt_studio',rejected['extra_data']['extra_pnginfo'])
                self.assertEqual(self.queue.read('operation-1')['state'],'prepared')
                with self.service.connect() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)
                    db.execute('DROP TRIGGER reject_receipt')

    def test_commit_failure_blocks_submission_and_leaves_no_partial_receipt(self):
        payload=self.prepared();connect=self.service.connect
        with connect() as db:
            db.execute('CREATE TABLE receipt_parent (id INTEGER PRIMARY KEY)')
            db.execute('CREATE TABLE receipt_child (id INTEGER REFERENCES receipt_parent(id) DEFERRABLE INITIALLY DEFERRED)')
            db.execute('CREATE TRIGGER reject_commit AFTER INSERT ON jobs BEGIN INSERT INTO receipt_child VALUES (1); END')
        @contextmanager
        def checked_connect():
            with connect() as db:
                db.execute('PRAGMA foreign_keys=ON');yield db
        with patch.object(self.service,'connect',checked_connect):
            with self.assertRaises(sqlite3.IntegrityError):self.service.prepare_prompt(payload)
        self.assertEqual(payload['prompt'],{})
        self.assertEqual(self.queue.read('operation-1')['state'],'prepared')
        with connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM receipt_child').fetchone()[0],0)

    def test_command_delivered_once_restart_and_lost_ack_do_not_requeue(self):
        payload=self.prepared();self.service.prepare_prompt(payload)
        self.assertEqual(self.queue.poll(self.live)['commands'],[])
        self.assertEqual(self.queue.start(self.request)['state'],'submitted')
        restarted=Service(self.root,self.root,self.root).native_queue
        self.assertEqual(restarted.status('operation-1')['state'],'unconfirmed')
        self.assertEqual(restarted.poll(self.live)['commands'],[])
        self.assertEqual(restarted.reconcile('operation-1',{payload['prompt_id']})['state'],'queued')
        self.assertEqual(restarted.start(self.request)['prompt_id'],payload['prompt_id'])

    def test_cancellation_or_tampered_actual_graph_fails_backend_validation(self):
        payload=self.prepared();self.queue.cancel_pending();self.service.prepare_prompt(payload)
        self.assertEqual(payload['prompt'],{})
        self.request['id']='operation-2'
        self.queue.start(self.request);command=self.queue.poll(self.live)['commands'][0]
        graph=copy.deepcopy(self.snapshot['state']['generation']['profiles'][0]['graph'])
        self.queue.prepare(dict(command,session='tab',client_id='real-web-sid',output=graph,workflow=dict(id='native-A',nodes=[])))
        changed=copy.deepcopy(graph);changed['5']['inputs']['batch_size']=3
        data=dict(client_id='real-web-sid',prompt=changed,extra_data=dict(extra_pnginfo=dict(workflow=dict(extra=dict(pcs_native_operation='operation-2')))))
        self.service.prepare_prompt(data);self.assertEqual(data['prompt'],{})

    def test_same_operation_id_cannot_change_content_and_diagnostics_work_before_payload(self):
        self.queue.start(self.request)
        with self.assertRaisesRegex(ValueError,'內容不同'):self.queue.start(dict(self.request,unexpected=True))
        self.assertIn('尚未收到原生提交內容',describe(dict(id='operation-1',state='submitting',requested_workflow='flow')))

    def test_open_uses_loaded_native_catalog_and_cannot_be_prepared_as_generation(self):
        before=copy.deepcopy(self.snapshot)
        target=self.live['identity'];live=dict(self.live,identity=dict(path='B.json',workflow='',frontend_id='native-B'),workflows=[target],navigation_protocol=1)
        self.queue.poll(live)
        self.assertEqual(self.queue.start(self.request,action='open')['state'],'pending')
        command=self.queue.poll(live)['commands'][0]
        self.assertEqual(command['action'],'open');self.assertEqual(command['target']['frontend_id'],'native-A')
        with self.assertRaises(ValueError):self.queue.prepare(dict(command,session='tab',client_id=live['client_id']))
        with self.assertRaisesRegex(ValueError,'不符'):
            self.queue.reply(dict(command,session='tab',opened_identity=dict(target,frontend_id='native-A-copy')))
        result=self.queue.reply(dict(command,session='tab',opened_identity=target))
        self.assertEqual(result['state'],'opened');self.assertNotIn('prompt_id',result)
        self.assertEqual(self.queue.poll(live)['commands'],[]);self.assertEqual(self.snapshot,before)
        with self.service.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)

    def test_open_missing_duplicate_or_closed_target_does_not_switch_by_name(self):
        with self.assertRaises(ValueError):self.queue.start(self.request,action='open')
        target=self.live['identity']
        live=dict(self.live,workflows=[target,target]);self.queue.poll(live)
        with self.assertRaises(ValueError):self.queue.start(self.request,action='open')
        live=dict(self.live,workflows=[target]);self.queue.poll(live)
        self.queue.poll(dict(live,session='second'))
        with self.assertRaises(ValueError):self.queue.start(self.request,action='open')
        self.queue.sessions.pop('second');self.queue.start(self.request,action='open')
        self.assertEqual(self.queue.poll(dict(live,workflows=[]))['commands'],[])
        self.assertEqual(self.queue.status(self.request['id'])['state'],'failed')

    def test_multi_user_or_unknown_mode_refuses_native_operations_and_old_transport_markers(self):
        payload=self.prepared();before=self.queue.read(self.request['id']);sessions=copy.deepcopy(self.queue.sessions)
        for mode in (True,None,'false'):
            self.queue.multi_user=lambda:mode
            self.assertFalse(self.queue.available())
            for action in (lambda:self.queue.poll(dict(self.live,user='default')),
                           lambda:self.queue.start(self.request),lambda:self.queue.start(self.request,action='open'),
                           lambda:self.queue.prepare({}),lambda:self.queue.reply({}),
                           lambda:self.queue.status(self.request['id']),lambda:self.queue.reconcile(self.request['id'],set())):
                with self.assertRaisesRegex(ValueError,'單一使用者'):action()
            self.assertEqual(self.queue.read(self.request['id']),before);self.assertEqual(self.queue.sessions,sessions)
            rejected=copy.deepcopy(payload);self.service.prepare_prompt(rejected)
            self.assertEqual(rejected['prompt'],{})
            self.assertNotIn('prompt_studio',rejected['extra_data']['extra_pnginfo'])
            ordinary=dict(prompt=copy.deepcopy(payload['prompt']))
            self.service.prepare_prompt(ordinary);self.assertEqual(ordinary['prompt'],payload['prompt'])
        self.queue.multi_user=lambda:False;self.assertTrue(self.queue.available())
        self.assertEqual(before['user_scope'],'default')
        self.assertEqual(before['marker']['generation']['user_scope'],'default')


class NativeDesktopTests(unittest.TestCase):
    from test_multi_canvas import MultiCanvasTests as _Fixture
    setUp=_Fixture.setUp
    tearDown=_Fixture.tearDown
    add=_Fixture.add

    def test_delayed_native_error_does_not_reopen_terminal_job(self):
        runner=self.w.comfy.generation;callbacks=[]
        with patch.object(self.w.comfy,'request',lambda *args,**kwargs:callbacks.append(args)):
            ident=runner.submit_native(dict(state=dict(workspace=self.w.state['workspace'],multi_output=dict(version=5))),'flow',lambda job:None,lambda error:None,lambda:False)
            job=runner.jobs[ident];job.update(state='complete',prompt_id='confirmed');runner.save(job)
            callbacks[0][3]('late timeout')
            callbacks[0][2](dict(state='queued',prompt_id='stale'))
            self.assertEqual(runner.record(ident)['state'],'complete')
            self.assertEqual(runner.record(ident)['prompt_id'],'confirmed')

    def test_native_open_api_is_separate_from_clip_card_and_ignores_late_workspace_reply(self):
        from test_multi_output import workflow
        from prompt_studio import clip_flow
        p=workflow('A');p.update(origin=dict(server='http://127.0.0.1:8188',path='A.json'),frontend_id='native-A')
        self.w.generation_panel.save_profile(p);self.add('PCS original')
        clip=next(iter(self.canvas.clips));self.canvas.commit(lambda s:clip_flow.set_binding(s,'A',clip,('6','text')))
        client=self.w.comfy;client.connected=True;client.native_open_supported=True
        calls=[];timers=[]
        def request(route,data=None,done=None,**kwargs):
            calls.append((route,data));done(dict(id=data['id'],state='pending'))
        with patch.object(client,'request',request),patch('prompt_studio.native_workflow.QTimer.singleShot',lambda delay,fn:timers.append(fn)):
            self.assertFalse(hasattr(self.canvas.clips[clip].panel,'open_native'))
            from prompt_studio.native_workflow import open_bound_workflow
            open_bound_workflow(self.w,'A')
            self.assertEqual([route for route,_ in calls],['workflow/native/open'])
            self.assertEqual(calls[0][1]['workflow'],'A');self.assertTrue(client.opening_native)
            original_workspace=self.w.state['workspace']
            try:
                self.w.state['workspace']='other-workspace';timers.pop(0)()
            finally:self.w.state['workspace']=original_workspace
            self.assertFalse(client.opening_native);self.assertEqual(len(calls),1)

    def test_pcs_run_only_dispatches_native_command_then_tracks_real_receipt(self):
        from unittest.mock import patch
        from test_multi_output import workflow
        from prompt_studio import clip_flow
        p=workflow('A');p.update(origin=dict(server='http://127.0.0.1:8188',path='A.json'),frontend_id='native-A')
        self.w.generation_panel.save_profile(p);self.add('PCS original')
        clip=next(iter(self.canvas.clips));self.canvas.commit(lambda s:clip_flow.set_binding(s,'A',clip,('6','text')))
        from prompt_studio import stage_model,multi_output
        def add_stage(s):
            key=stage_model.add(s,workflow='A');s['multi_output']['stages'][key]['output']=None
            multi_output.connect(s,clip,key,'control')
        self.canvas.commit(add_stage)
        client=self.w.comfy;client.native_supported=True;client.connected=True
        runner=client.generation;service=Service(Path(self.tmp.name)/'backend',self.tmp.name,self.tmp.name);queue=service.native_queue
        live=dict(session='tab',client_id='sid',epoch=0,ready=True,bindings_protocol=1,identity=dict(workflow='',path='A.json',frontend_id='native-A'))
        queue.poll(live);calls=[];timers=[]
        def request(route,data=None,done=None,failed=None,**kwargs):
            calls.append(route)
            if route=='workflow/native/start':done(queue.start(data))
            elif route=='workflow/native/status':done(queue.status(data['id']))
            elif route.startswith('/history/'):
                done({route.split('/')[-1]:dict(prompt=(0,payload['prompt_id'],payload['prompt'],payload['extra_data']),status=dict(completed=True,status_str='success'),outputs={'9':{'images':[{'filename':'one.png'},{'filename':'two.png'}]}})})
            else:self.fail('Unexpected execution route: '+route)
        with patch.object(client,'request',request):
            runner.run(1);client.input_flow.chain.pump();self.assertEqual(calls,['workflow/native/start'])
            command=queue.poll(live)['commands'][0]
            actual=copy.deepcopy(p['graph']);actual['5']['inputs'].update(width=1216,height=832,batch_size=2)
            for field in command['texts']:actual[field['node']]['inputs'][field['field']]=field['text']
            queue.prepare(dict(command,session='tab',client_id='sid',output=actual,workflow=dict(id='native-A',nodes=[])))
            payload=dict(prompt=actual,client_id='sid',extra_data=dict(extra_pnginfo=dict(workflow=dict(extra=dict(pcs_native_operation=command['id'])))))
            service.prepare_prompt(payload);queue.reply(dict(command,session='tab',prompt_id=payload['prompt_id']))
            from PySide6.QtTest import QTest
            QTest.qWait(550)
            runner.observe(dict(running_ids=[payload['prompt_id']],queued_ids=[],executing_node='3'))
            job=runner.records()[0];self.assertEqual(job['node'],'3')
            self.assertEqual(job['payload']['prompt']['5']['inputs']['batch_size'],2)
            self.assertEqual(job['payload']['prompt']['6']['inputs']['text'],'(PCS original:1.0)')
            runner.observe(dict(running_ids=[],queued_ids=[]))
            self.assertIsNone(runner.batch);self.assertEqual(runner.records()[0]['output_count'],2)
            self.assertNotIn('/prompt',calls)


if __name__=='__main__':unittest.main()
