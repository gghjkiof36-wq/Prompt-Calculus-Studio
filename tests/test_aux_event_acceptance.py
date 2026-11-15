"""C3: real UI entry points, native receipts, journal transactions; no GPU."""
import asyncio
import copy
import importlib
import sqlite3
import unittest
from unittest.mock import patch
from PySide6.QtCore import QEventLoop, QTimer
import test_event_acceptance_atomic as acceptance
from prompt_calculus_studio.native_workflow import open_bound_workflow
from prompt_calculus_studio.workspace_runtime import unfinished


class AuxiliaryAcceptanceTests(unittest.TestCase):
    setUpClass=classmethod(lambda cls:acceptance.AcceptanceTests.setUpClass())

    def setUp(self):
        self.h=acceptance.AcceptanceTests('runTest');self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.f=self.h.f;self.peer=self.h.peer;self.calls=[];self.commands={};self.received=[];self.errors=[]
        self.lose_ack=False;self.unavailable=False;self.hold=True;self.hang=False
        self.token=self.f.a.document.runtime_token;self.http_failures={}
        self.peer.live.update(inspect_protocol=1,capture_protocol=1)
        identity=dict(self.peer.live['identity'],path='folder/A.json')
        self.peer.live.update(identity=identity,workflows=[identity])
        self.peer.identities['flow']=identity
        self.f.a.host.canvas.commit(lambda s:s['generation']['profiles'][0].update(origin=dict(server=self.client.url,path='folder/A.json')))
        self.original=self.f.transport_request
        def request(client,route,data=None,done=None,failed=None,raw=None,content_type='application/json',**options):
            actions={'workflow/native/open':'open','workflow/native/inspect':'inspect',
                     'workflow/queue/capture':'capture','workflow/native/apply':'apply'}
            if route in actions:
                action=actions[route];self.calls.append((action,data['id']))
                self.http_failures[data['id']]=failed
                self.peer.native.poll(self.peer.live)
                begin=importlib.import_module('integration_test.native_handshake').begin
                def notify(event,value):
                    if event=='prompt_studio_native_probe':self.peer.native.poll(dict(self.peer.live,probe=value['probe']))
                try:
                    value=asyncio.run(begin(self.peer.native,data,client.url,action,notify,.05))
                    commands=self.peer.native.poll(self.peer.live)['commands']
                    self.commands.update({c['id']:c for c in commands})
                    if not self.hold:self.complete(data['id'])
                    if done and not self.lose_ack:done(value)
                except ValueError as exc:
                    if failed:failed(exc)
                    else:raise
                return
            if route=='workflow/native/recovery' and self.hang:return
            if route=='workflow/native/recovery' and self.unavailable:
                if failed:failed('HTTP 500')
                return
            return self.original(client,route,data,done,failed,raw,content_type,**options)
        self.f.transport_request=request
        self.bind()

    @property
    def client(self):return self.f.w.scheduler.clients[self.token]
    @property
    def aux(self):return self.client.events.aux
    @property
    def store(self):return self.client.input_flow.chain.store

    def bind(self):
        self.h.bind()
        for client in self.f.w.scheduler.clients.values():
            client.events.aux.clock=self.h.clock
            client.native_open_supported=client.native_inspect_supported=True

    def wait(self):
        loop=QEventLoop();QTimer.singleShot(5,loop.quit);loop.exec()

    def drain(self):
        self.h.drain();self.aux.tick();self.wait()

    def launch(self,action,*,enqueue=True):
        self.received.clear();self.errors.clear()
        client=self.client;runner=client.input_flow.chain
        profile=client.window.state['generation']['profiles'][0]
        if action=='open':open_bound_workflow(client.window,'flow')
        elif action=='inspect':
            client.workflow_catalog.read_draft('folder/A.json',profile,lambda *value:self.received.append(value),self.errors.append)
        elif action=='capture':
            client.queue.preparing='capture-fixture'
            client.queue.prepare(client.snapshot(),profile,None,'capture-fixture',self.errors.append,
                done=self.received.append,store_result=enqueue)
        else:
            self.assertTrue(self.f.w.scheduler.acquire_operation(client))
            groups={'flow':[(dict(kind='text',target=dict(node='6',field='text')),dict(value='C3 input'))]}
            runner.apply_bindings(groups,lambda:self.received.append('applied'),self.errors.append)
            self.wait()
        self.assertTrue(self.calls,self.f.notices+self.errors)
        ident=self.calls[-1][1]
        self.assertEqual(self.store.read(ident)['action'],action)
        return ident

    def complete(self,ident):
        command=self.commands[ident];native=self.peer.native
        action=native.read(ident)['action'];live=self.peer.live
        operation=native.read(ident)
        workflow=operation.get('workflow','flow')
        target=self.peer.identities[workflow]
        if operation.get('needs_navigation') and not operation.get('resolved_identity'):
            native.activate(dict(command,session=live['session'],client_id=live['client_id'],
                opened_identity=target,opened_epoch=live['epoch']+1))
            live.update(identity=target,epoch=live['epoch']+1)
        graph=copy.deepcopy(self.peer.graphs[workflow])
        for binding in command.get('texts',[])+command.get('images',[]):
            if binding.get('text_source')!='web':graph[binding['node']]['inputs'][binding['field']]=binding['text']
        if action=='capture':
            native.prepare(dict(command,session=live['session'],client_id=live['client_id'],output=graph,
                workflow=dict(id=live['identity']['frontend_id'],nodes=[])))
        elif action=='inspect':
            from stage_parameter_fixture import profile_fields
            profile=copy.deepcopy(self.client.window.state['generation']['profiles'][0]);profile['graph']=graph
            native.reply(dict(command,session=live['session'],inspection=dict(identity=live['identity'],output=graph,
                workflow=dict(id=live['identity']['frontend_id'],nodes=[]),parameters=profile_fields(profile))))
        elif action=='open':native.reply(dict(command,session=live['session'],opened=True))
        else:native.reply(dict(command,session=live['session'],applied=True))

    def assert_once(self,ident,action,*,enqueue=True):
        record=self.store.read(ident)
        self.assertTrue(record['ended'],record)
        self.assertNotIn(ident,self.aux.active)
        self.assertEqual([v for v in self.calls if v[1]==ident],[(action,ident)])
        self.assertEqual(len(self.peer.submissions),0)
        matches=[r for r in self.client.queue.store.rows() if r['capture']==ident]
        self.assertEqual(len(matches),int(action=='capture' and enqueue))
        self.assertTrue(record['continuation_accepted'])
        self.assertFalse(unfinished(self.client.window.store.db),'settled receipt releases its lane')

    def normal(self,action):
        before=copy.deepcopy(self.client.window.state)
        ident=self.launch(action);self.complete(ident)
        self.assertFalse(self.store.read(ident)['ended'],'HTTP ACK cannot accept terminal')
        self.drain();self.assert_once(ident,action)
        if action=='inspect':
            result=self.store.read(ident)['result']
            self.assertIn('profile',result);self.assertIn('inspection',result)
            self.assertEqual(result['profile']['parameter_description']['nodes'],result['inspection']['parameters']['nodes'])
            self.assertEqual(self.client.window.state,before,'inspect is not an edit')
        events=self.h.peer.service.events
        count=self.client.window.store.db.execute('SELECT count(*) FROM channel_applied_events').fetchone()[0]
        self.aux.accept(ident,self.peer.native.recovery(ident,None))
        self.assertEqual(self.client.window.store.db.execute('SELECT count(*) FROM channel_applied_events').fetchone()[0],count)

    def lost_reopen(self,action):
        self.lose_ack=True;ident=self.launch(action);self.complete(ident)
        self.unavailable=True
        for _ in range(2):self.f.reopen();self.bind()
        self.assertFalse(self.store.read(ident)['ended'])
        self.unavailable=False
        for _ in range(2):
            self.aux.require(ident,'fixture',force=True);self.aux.tick();self.drain()
        self.assert_once(ident,action)
        self.assertFalse(self.received,'callbacks cannot survive restart')

    def uncertainty(self,action):
        ident=self.launch(action);self.unavailable=True
        for _ in range(31):self.h.clock.now+=1;self.aux.tick()
        self.assertEqual(self.store.read(ident)['status'],'unconfirmed')
        self.assertFalse(self.store.read(ident)['ended'])
        self.assertIn(ident,self.aux.active)
        self.complete(ident);self.unavailable=False;self.drain()
        self.assert_once(ident,action)

    def wrong_identity(self,action):
        ident=self.launch(action);self.complete(ident)
        original=self.peer.native.recovery(ident,None)
        variants=[]
        for field in ('server_instance','epoch'):
            value=copy.deepcopy(original);value['event_identity'][field]='wrong';variants.append(value)
        value=copy.deepcopy(original);value['scope']['workspace_id']='wrong';variants.append(value)
        value=copy.deepcopy(original);value['target']['frontend_id']='wrong';variants.append(value)
        for value in variants:
            with self.assertRaises(ValueError):self.aux.accept(ident,value)
            self.assertFalse(self.store.read(ident)['ended'])
        before=self.store.read(ident)
        self.store.patch(ident,runtime_owner='wrong')
        with self.assertRaises(ValueError):self.aux.accept(ident,original)
        self.store.patch(ident,runtime_owner=before['runtime_owner'])
        self.drain();self.assert_once(ident,action)

    def cancel_race(self,action):
        ident=self.launch(action);self.complete(ident);self.aux.cancel(ident);self.drain()
        self.assert_once(ident,action)

    def missing(self,action):
        ident=self.launch(action)
        with self.peer.service.connect() as db:db.execute('DELETE FROM native_operations WHERE id=?',(ident,))
        self.aux.cancel(ident);self.drain()
        self.assertTrue(self.store.read(ident)['ended'],self.store.read(ident))
        self.assertEqual(self.store.read(ident)['status'],'retired')
        with self.assertRaises(ValueError):self.complete(ident)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(self.peer.submissions),0)

    def atomic(self,point):
        ident=self.launch('capture');self.complete(ident)
        self.client.events.store.fault=lambda name:(_ for _ in ()).throw(sqlite3.OperationalError(point)) if name==point else None
        self.drain()
        self.assertFalse(self.store.read(ident)['ended'])
        self.assertEqual(self.client.queue.store.rows(),[])
        self.client.events.store.fault=None;self.unavailable=True
        for _ in range(2):self.f.reopen();self.bind()
        self.unavailable=False
        for _ in range(2):self.aux.require(ident,'fixture',force=True);self.aux.tick();self.drain()
        self.assert_once(ident,'capture')

    def test_capture_atomic_before_continuation(self):self.atomic('before_aux_continuation')
    def test_capture_atomic_before_receipt(self):self.atomic('before_receipt')
    def test_capture_atomic_before_commit(self):self.atomic('before_aux_commit')

    def test_template_capture_never_enqueues_or_resumes_variants(self):
        self.lose_ack=True;ident=self.launch('capture',enqueue=False);self.complete(ident)
        self.f.reopen();self.bind();self.drain()
        self.assert_once(ident,'capture',enqueue=False)
        self.assertEqual(self.received,[])

    def test_template_capture_lost_http_does_not_continue_even_if_event_arrives(self):
        self.lose_ack=True;ident=self.launch('capture',enqueue=False);self.complete(ident);self.drain()
        self.assert_once(ident,'capture',enqueue=False)
        self.assertEqual(self.received,[],'event cannot restart a template continuation without its original HTTP')
        self.http_failures[ident]('lost original reply')
        self.assertEqual(self.errors,['無法確認']);self.assertEqual(self.received,[])

    def test_apply_cancel_settle_does_not_release_early(self):
        ident=self.launch('apply');self.aux.cancel(ident);self.drain()
        self.assertFalse(self.store.read(ident)['ended'])
        self.peer.native.cancel_applied(dict(self.peer.live,id=ident))
        self.drain();self.assertTrue(self.store.read(ident)['ended'])
        self.assertEqual(self.store.read(ident)['status'],'cancelled')
        self.assertEqual(len(self.calls),1)

    def test_bounded_recovery_even_if_transport_never_calls_back(self):
        self.hang=True;ident=self.launch('inspect')
        for _ in range(31):self.h.clock.now+=1;self.aux.tick()
        self.assertEqual(self.store.read(ident)['status'],'unconfirmed')
        self.assertTrue(unfinished(self.client.window.store.db))
        self.assertLessEqual(self.aux.pending[ident]['tries'],3)
        self.hang=False;self.complete(ident);self.drain();self.assert_once(ident,'inspect')

    def test_expired_inspect_does_not_apply_or_read_saved_fallback(self):
        before=copy.deepcopy(self.client.window.state);self.unavailable=True
        ident=self.launch('inspect')
        for _ in range(31):self.h.clock.now+=1;self.aux.tick()
        self.assertEqual(self.errors,['無法確認']);self.assertFalse(self.received)
        self.complete(ident);self.unavailable=False;self.drain()
        self.assert_once(ident,'inspect');self.assertFalse(self.received)
        self.assertEqual(self.client.window.state,before)
        self.assertFalse(any(route.startswith('/userdata') for route in self.h.calls))

    def test_unscoped_inspect_is_selected_only_for_its_original_owner(self):
        from prompt_calculus_studio.comfy_event_transport import DeliveryFilter
        ident=self.launch('inspect');self.complete(ident)
        log=self.peer.service.events
        from prompt_calculus_studio.comfy_event_protocol import scope
        events=log.page('0',scope())['events']
        own=DeliveryFilter();own.aux[self.token]={'native_operations:'+ident}
        for token in (self.token,self.f.b.document.runtime_token):
            page=own.select(dict(owner=token,events=events))
            matching=[e for e in page['events'] if e['entity']=='native_operations:'+ident]
            self.assertEqual(bool(matching),token==self.token)
        self.drain();self.assert_once(ident,'inspect')

    def test_no_periodic_native_status_for_covered_apply(self):
        ident=self.launch('apply');before=len(self.h.calls)
        for _ in range(4):self.client.poll()
        self.assertNotIn('workflow/native/status',self.h.calls[before:])
        self.assertEqual(self.client.window.store.db.execute('SELECT count(*) FROM workspace_operations').fetchone()[0],0)
        self.complete(ident);self.drain();self.assert_once(ident,'apply')

    def test_disconnect_cannot_overwrite_free_input_before_its_receipt(self):
        self.f.click(self.f.a);self.drain()
        runner=self.client.input_flow.chain;run=runner.runs()[0]['id']
        runner.store.patch(run,free_state='applying')
        runner.apply_bindings({'flow':[(dict(kind='text',target=dict(node='6',field='text')),dict(value='free'))]},
            lambda:None,self.errors.append,owner=run,continuation=dict(kind='free'))
        self.wait();ident=self.calls[-1][1];self.complete(ident)
        runner.disconnected()
        self.assertEqual(self.store.read(run)['free_state'],'applying')
        self.assertFalse(self.store.read(ident)['ended'])
        self.drain()
        self.assertEqual(self.store.read(run)['free_state'],'applied')
        self.assertEqual(self.store.read(run)['status'],'paused')
        self.assertEqual(self.calls,[('apply',ident)])
        self.assertEqual(len(self.peer.submissions),1)

    def cancelled_run(self,clear,resume):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.f.click(self.f.a);self.drain()
        runner=self.client.input_flow.chain;run=runner.runs()[0]
        runner.apply_bindings({'flow':[(dict(kind='text',target=dict(node='6',field='text')),dict(value='owned'))]},
            lambda:None,self.errors.append,owner=run['id'])
        self.wait();ident=self.calls[-1][1]
        stored=self.store.read(run['id'])
        self.assertIn(ident,stored['input_ids']);self.assertNotIn('input_operations',stored)
        self.store.patch(run['id'],batch_cancel_resume=resume)
        if clear:
            from PySide6.QtCore import QPoint
            controls=self.f.a.host.canvas.execution_bar.controls
            def choose(menu,pos):
                next(a for a in menu.actions() if a.text()=='取消此工作區全部流程（含等待項目）').trigger()
            with patch('prompt_calculus_studio.run_controls.RoundMenu.open_at',choose):controls.stop_menu(QPoint())
        else:runner.cancel(run['id'])
        self.drain()
        self.assertEqual(self.store.read(run['id'])['status'],'cancelled')
        self.assertFalse(self.store.read(ident)['ended']);self.assertTrue(unfinished(self.client.window.store.db))
        self.peer.native.cancel_applied(dict(self.peer.live,id=ident));self.drain()
        self.assertTrue(self.store.read(ident)['ended']);self.assertFalse(unfinished(self.client.window.store.db))
        self.assertEqual(len(self.peer.submissions),1)
        self.assertEqual(self.calls,[('apply',ident)])

    def foreign_setup(self):
        from prompt_calculus_studio import clip_flow,multi_output
        def setup(s):
            profile=copy.deepcopy(s['generation']['profiles'][0]);profile.update(id='D',name='D',frontend_id='native-D')
            profile['origin']['path']='folder/D.json';s['generation']['profiles'].append(profile)
            cid=clip_flow.add(s);stage=next(iter(s['multi_output']['stages']))
            multi_output.connect(s,s['multi_output']['current_output'],cid,'clip')
            clip_flow.set_binding(s,'D',cid,('7','text'));multi_output.connect(s,cid,stage,'control')
        self.assertTrue(self.f.a.host.canvas.commit(setup))
        profile=self.client.window.state['generation']['profiles'][-1]
        self.peer.graphs['D']=copy.deepcopy(profile['graph'])
        self.peer.identities['D']=dict(workflow='D',path='folder/D.json',frontend_id='native-D')
        self.peer.live['workflows']=list(self.peer.identities.values())

    def test_foreign_apply_continuation_after_lost_ack_dispatches_original_once(self):
        self.foreign_setup();self.lose_ack=True;self.f.click(self.f.a)
        self.assertEqual(len(self.calls),1,self.f.notices)
        ident=self.calls[0][1];self.assertEqual(len(self.peer.submissions),0)
        self.complete(ident);self.drain();self.drain()
        self.assertEqual(len(self.peer.submissions),1,self.f.notices)
        self.assertEqual(self.calls,[('apply',ident)])
        self.assertTrue(self.store.read(ident)['ended'])
        receipt=self.peer.native.recovery(ident,None)
        for _ in range(2):self.aux.accept(ident,receipt);self.drain()
        self.assertEqual(len(self.peer.submissions),1)

    def test_foreign_apply_reopen_keeps_restart_pause_and_resume_uses_receipt(self):
        self.foreign_setup();self.lose_ack=True;self.f.click(self.f.a)
        ident=self.calls[0][1];run=self.client.input_flow.chain.runs()[0]['id']
        self.complete(ident);self.unavailable=True
        for _ in range(2):self.f.reopen();self.bind()
        self.unavailable=False;self.drain()
        self.assertTrue(self.store.read(ident)['ended'])
        self.assertEqual(self.store.read(run)['pause_reason'],'restart')
        self.assertEqual(len(self.peer.submissions),0)
        attempts=self.store.rows('attempt',owner=run)
        self.assertEqual([(a['status'],a.get('cancel_requested')) for a in attempts],[('preparing',None)],attempts)
        self.client.input_flow.chain.resume(run);self.wait();self.drain()
        self.assertEqual(len(self.peer.submissions),1,self.f.notices)
        self.assertEqual(self.calls,[('apply',ident)])

    def foreign_missing_job(self,status,evidence):
        self.foreign_setup();self.f.click(self.f.a)
        operation=self.calls[0][1];self.complete(operation);self.drain();self.drain()
        runner=self.client.input_flow.chain
        attempt=runner.store.rows('attempt')[0];ident=attempt['id'];owner=attempt['owner']
        self.assertEqual(attempt['submission_intent'],'issued')
        self.assertTrue(attempt['foreign_batch'])
        runner.pause(owner);runner.preparing=None;self.client.events.reconcile.close()
        with runner.accepting():
            runner.store.patch(ident,status=status,**({} if evidence else dict(operation_evidence=None)))
            self.client.window.store.db.execute('DELETE FROM generation_jobs WHERE id=?',(ident,))
        self.client.generation.jobs.pop(ident,None)
        controls=self.f.a.host.canvas.execution_bar.controls
        controls.refresh();controls.execute()
        for _ in range(2):self.client.events.invariants.check(ident)
        if evidence:self.assertIsNotNone(self.client.generation.record(ident),'issued foreign generation must still repair its missing projection')
        else:
            self.assertIsNone(self.client.generation.record(ident))
            self.assertEqual(runner.store.read(ident)['error'],'資料不一致' if status=='submitted' else '無法確認')
        for _ in range(2):
            self.f.reopen();self.bind();self.client.events.invariants.check(ident)
            self.assertEqual(bool(self.client.generation.record(ident)),evidence)
            self.assertTrue(unfinished(self.client.window.store.db))
        self.assertEqual(len(self.peer.submissions),1)
        self.assertEqual(self.calls,[('apply',operation)])


for _action in ('open','inspect','capture','apply'):
    for _case in ('normal','lost_reopen','uncertainty','wrong_identity','cancel_race','missing'):
        setattr(AuxiliaryAcceptanceTests,'test_'+_action+'_'+_case,
            lambda self,action=_action,case=_case:getattr(self,case)(action))

for _clear in (False,True):
    for _resume in (False,True):
        setattr(AuxiliaryAcceptanceTests,f'test_cancelled_input_clear_{_clear}_resume_{_resume}',
            lambda self,clear=_clear,resume=_resume:self.cancelled_run(clear,resume))

for _status in ('submitted','preparing'):
    for _evidence in (False,True):
        setattr(AuxiliaryAcceptanceTests,f'test_foreign_missing_{_status}_evidence_{_evidence}',
            lambda self,status=_status,evidence=_evidence:self.foreign_missing_job(status,evidence))

if __name__=='__main__':unittest.main()
