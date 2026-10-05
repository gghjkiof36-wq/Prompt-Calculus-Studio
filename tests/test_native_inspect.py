"""Offline transport handshakes and native draft receipts; no service or GPU."""
import asyncio
import ast
import copy
import functools
import importlib
import ipaddress
import json
import logging
import secrets
import time
import types
import unittest
from pathlib import Path
from urllib.parse import urlsplit
import test_native_queue as fixtures
from prompt_calculus_studio.workflow_import import read_profile

begin=importlib.import_module('integration_test.native_handshake').begin
cancel_operation=importlib.import_module('integration_test.native_handshake').cancel_operation


class NativeHandshakeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.NativeQueueTests.setUp(self)
        self.live.update(workflows=[self.live['identity']],inspect_protocol=1,navigation_protocol=1,apply_protocol=1)
        self.queue.poll(self.live)

    def acknowledge(self,event,value):
        self.assertEqual(event,'prompt_studio_native_probe')
        self.assertEqual(self.queue.poll(dict(self.live,probe=value['probe']))['commands'],[])

    async def start(self,action='queue',request=None,notify=None,timeout=.3):
        def events(event,value):
            if event=='prompt_studio_native_pending':
                self.assertEqual(self.queue.read(value['id'])['state'],'pending')
                self.assertEqual(value['session'],self.live['session'])
                return
            (notify or self.acknowledge)(event,value)
        return await begin(self.queue,request or self.request,'http://127.0.0.1:8188',action,events,timeout)

    async def test_new_command_wakes_exact_frontend_after_handshake_without_interval_poll(self):
        for action in ('queue','apply','inspect'):
            with self.subTest(action=action):
                self.queue.sessions.clear();self.queue.poll(self.live)
                request=dict(self.request,id='wake-'+action)
                if action=='inspect':request=dict(id=request['id'],target=self.live['identity'])
                commands=[];events=[]
                def notify(event,value):
                    events.append(event)
                    if event=='prompt_studio_native_probe':self.acknowledge(event,value)
                    else:
                        self.assertEqual(event,'prompt_studio_native_pending')
                        self.assertEqual(value,dict(id=request['id'],session=self.live['session'],client_id=self.live['client_id']))
                        commands.extend(self.queue.poll(self.live)['commands'])
                await begin(self.queue,request,'http://127.0.0.1:8188',action,notify,.3)
                self.queue.cancel(request['id'],None,None)
                self.assertEqual(events,['prompt_studio_native_probe','prompt_studio_native_pending'])
                self.assertEqual([c['id'] for c in commands],[request['id']])
                self.assertEqual(self.queue.poll(self.live)['commands'],[])

    async def test_first_click_refreshes_stale_heartbeat_before_any_dispatch(self):
        self.queue.sessions['tab']['seen']-=8
        result=await self.start()
        self.assertEqual(result['state'],'pending')
        self.assertEqual(len(self.queue.poll(self.live)['commands']),1)
        self.assertEqual(self.queue.poll(self.live)['commands'],[])

    async def test_transient_native_submission_waits_without_queuing_a_probe(self):
        def notify(event,value):
            self.queue.poll(dict(self.live,ready=False,reason='ComfyUI 正在提交',probe=value['probe']))
            asyncio.get_running_loop().call_later(.06,lambda:self.queue.poll(dict(self.live,probe=value['probe'])))
        self.assertEqual((await self.start(notify=notify))['state'],'pending')
        self.assertEqual(len(self.queue.poll(self.live)['commands']),1)

    async def test_duplicate_browser_and_wrong_frontend_id_still_refused(self):
        def duplicate(event,value):
            self.queue.poll(dict(self.live,probe=value['probe']))
            self.queue.poll(dict(self.live,probe=value['probe'],session='second'))
        with self.assertRaisesRegex(ValueError,'native_ambiguous'):await self.start(notify=duplicate)
        self.queue.sessions.clear()
        self.live['identity']=dict(self.live['identity'],frontend_id='native-copy')
        self.live['workflows']=[self.live['identity']]
        self.request['id']='wrong-identity'
        with self.assertRaisesRegex(ValueError,'native_target_missing'):await self.start(timeout=.06)
        with self.service.connect() as db:
            self.assertTrue(all(json.loads(row[0])['state']=='failed' for row in db.execute('SELECT body FROM native_operations')))
            diagnostic=json.loads(db.execute('SELECT body FROM native_diagnostics WHERE id=?',('wrong-identity',)).fetchone()[0])
            self.assertEqual(diagnostic['reason'],'native_target_missing')
            self.assertNotIn('snapshot',diagnostic)

    async def test_known_second_browser_must_acknowledge_before_selecting_first(self):
        self.queue.poll(dict(self.live,session='second'))
        with self.assertRaisesRegex(ValueError,'native_not_ready'):await self.start(timeout=.06)
        self.assertEqual(self.queue.status(self.request['id'])['state'],'failed')
        self.assertEqual(self.queue.poll(self.live)['commands'],[])

    async def test_unknown_existing_attempt_returns_receipt_without_probe_or_resend(self):
        await self.start();operation=self.queue.read(self.request['id']);operation['state']='unconfirmed';self.queue.save(operation)
        def forbid(*args):raise AssertionError('existing attempt must not start again')
        self.assertEqual((await self.start(notify=forbid))['state'],'unconfirmed')
        self.assertEqual(self.queue.poll(self.live)['commands'],[])

    async def test_busy_after_acceptance_stays_undelivered_then_runs_once(self):
        await self.start()
        self.assertEqual(self.queue.poll(dict(self.live,ready=False))['commands'],[])
        self.assertEqual(self.queue.status(self.request['id'])['state'],'pending')
        self.assertEqual(len(self.queue.poll(self.live)['commands']),1)
        self.assertEqual(self.queue.poll(self.live)['commands'],[])

    async def test_inflight_serializer_receipt_is_fresh_without_periodic_poll(self):
        await self.start();command=self.queue.poll(self.live)['commands'][0]
        self.queue.sessions['tab']['seen']-=8
        graph=copy.deepcopy(self.snapshot['state']['generation']['profiles'][0]['graph'])
        self.queue.prepare(dict(command,session='tab',client_id=self.live['client_id'],output=graph,workflow=dict(id='native-A',nodes=[])))
        self.assertEqual(self.queue.status(command['id'])['state'],'prepared')

    async def test_late_busy_probe_cannot_overwrite_acknowledged_switch(self):
        target=copy.deepcopy(self.live['identity'])
        self.live['identity']=dict(workflow='',path='B.json',frontend_id='native-B')
        await self.start();command=self.queue.poll(self.live)['commands'][0]
        self.queue.activate(dict(command,session='tab',client_id=self.live['client_id'],opened_identity=target,opened_epoch=1))
        self.assertEqual(self.queue.poll(dict(self.live,ready=False,probe='late'))['commands'],[])
        self.assertEqual(self.queue.sessions['tab']['identity'],target);self.assertEqual(self.queue.sessions['tab']['epoch'],1)
        graph=copy.deepcopy(self.snapshot['state']['generation']['profiles'][0]['graph'])
        self.queue.prepare(dict(command,session='tab',client_id=self.live['client_id'],output=graph,workflow=dict(id='native-A',nodes=[])))
        self.assertEqual(self.queue.status(command['id'])['state'],'prepared')

    async def test_inspect_uses_same_ownership_lane_and_returns_custom_unsaved_nodes(self):
        request=dict(id='inspect-1',target=dict(path='folder/A.json',frontend_id='native-A'))
        await self.start('inspect',request)
        command=self.queue.poll(self.live)['commands'][0]
        # A simultaneous Generate cannot dispatch while Inspect owns the graph.
        with self.assertRaisesRegex(ValueError,'native_busy'):await self.start(timeout=.06)
        self.assertNotIn('texts',command);self.assertNotIn('images',command)
        graph=copy.deepcopy(self.snapshot['state']['generation']['profiles'][0]['graph'])
        graph['90']=dict(class_type='CustomUnsavedNode',inputs={'field':'native serialization'})
        graph['91']=dict(class_type='CLIPTextEncode',inputs={'text':'unsaved third'})
        graph['92']=dict(class_type='CLIPTextEncode',inputs={'text':'unsaved fourth'})
        inspection=dict(identity=self.live['identity'],output=graph,workflow=dict(id='native-A',nodes=[dict(id=90,type='CustomUnsavedNode')]))
        result=self.queue.reply(dict(self.live,id='inspect-1',inspection=inspection))
        self.assertEqual(result['state'],'inspected');self.assertNotIn('prompt_id',result)
        profile=read_profile(dict(result['inspection'],format='prompt_studio_native_inspection',version=1),'A','flow',dict(path='folder/A.json'))
        self.assertEqual(profile['graph'],graph);self.assertEqual(profile['frontend_id'],'native-A')
        self.request['id']='new-click-after-inspection'
        self.assertEqual((await self.start())['state'],'pending')
        with self.service.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)

    async def test_new_binding_discovers_id_by_exact_path_readonly_then_pins_it(self):
        request=dict(id='inspect-1',target=dict(path='folder/A.json'))
        await self.start('inspect',request)
        operation=self.queue.read(request['id'])
        self.assertEqual(operation['target']['frontend_id'],'native-A')
        with self.assertRaisesRegex(ValueError,'不提交生成'):
            self.queue.poll(self.live)
            self.queue.prepare(dict(self.live,id=request['id']))

    async def test_inspect_reply_cannot_substitute_other_workflow(self):
        await self.start('inspect',dict(id='inspect-1',target=dict(path='folder/A.json')))
        self.queue.poll(self.live)
        with self.assertRaisesRegex(ValueError,'綁定不符'):
            self.queue.reply(dict(self.live,id='inspect-1',inspection=dict(identity=self.live['identity'],workflow=dict(id='other',nodes=[]),output={})))

    async def test_cancel_during_handshake_prevents_late_dispatch_for_queue_apply_and_inspect(self):
        for action in ('queue','apply','inspect'):
            with self.subTest(action=action):
                request=(dict(id=action,target=dict(path='folder/A.json',frontend_id='native-A')) if action=='inspect' else dict(self.request,id=action))
                notified=asyncio.Event();probes=[]
                def hold(_event,value):probes.append(value['probe']);notified.set()
                waiting=asyncio.create_task(self.start(action,request,hold,timeout=.4));await notified.wait()
                self.assertEqual(self.queue.status(action)['state'],'awaiting_browser')
                self.assertEqual(self.queue.cancel(action,None,None)['state'],'failed')
                self.assertEqual(self.queue.poll(dict(self.live,probe=probes[-1]))['commands'],[])
                self.assertEqual((await waiting)['state'],'failed')
                before=len(probes)
                self.assertEqual((await self.start(action,request,hold))['state'],'failed')
                self.assertEqual(len(probes),before);self.assertEqual(self.queue.poll(self.live)['commands'],[])

    async def test_cancel_waiting_for_other_owner_and_cancelled_transport_never_dispatch(self):
        await self.start();self.queue.poll(self.live)
        request=dict(id='read-waiting',target=dict(path='folder/A.json'))
        waiting=asyncio.create_task(self.start('inspect',request,timeout=.4));await asyncio.sleep(.02)
        self.assertEqual(self.queue.status(request['id'])['state'],'awaiting_browser')
        self.queue.cancel(request['id'],None,None);self.assertEqual((await waiting)['state'],'failed')
        self.queue.cancel_pending();self.request['id']='disconnected-start'
        notified=asyncio.Event()
        waiting=asyncio.create_task(self.start(notify=lambda *_:notified.set(),timeout=.4));await notified.wait()
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):await waiting
        self.assertEqual(self.queue.status(self.request['id'])['state'],'failed')
        self.assertFalse(self.queue.entry_lock.locked());self.assertEqual(self.queue.poll(self.live)['commands'],[])

    async def test_apply_cancel_before_delivery_never_waits_for_a_browser_ack(self):
        await self.start('apply')
        def forbidden(*_):raise AssertionError('undelivered operation has no browser owner to stop')
        result=await cancel_operation(self.queue,self.request['id'],None,None,forbidden,.05)
        self.assertEqual(result['state'],'failed');self.assertEqual(self.queue.poll(self.live)['commands'],[])

    async def test_delivered_apply_cancel_waits_for_exact_browser_confirmation(self):
        await self.start('apply');command=self.queue.poll(self.live)['commands'][0]
        receipt=dict(command,session=self.live['session'],client_id=self.live['client_id'])
        self.assertTrue(self.queue.check_apply(receipt)['ok']);notified=asyncio.Event();events=[]
        def notify(event,value):events.append((event,value));notified.set()
        task=asyncio.create_task(cancel_operation(self.queue,command['id'],None,None,notify,.2));await notified.wait()
        self.assertFalse(task.done());self.assertEqual(events[0][0],'prompt_studio_native_cancel')
        with self.assertRaisesRegex(ValueError,'已停止'):self.queue.check_apply(receipt)
        with self.assertRaises(ValueError):self.queue.cancel_applied(dict(receipt,client_id='other-client'))
        with self.assertRaises(ValueError):self.queue.cancel_applied(dict(receipt,session='other-tab'))
        self.queue.cancel_applied(receipt);self.assertEqual((await task)['state'],'failed')
        self.assertTrue(self.queue.read(command['id'])['cancel_confirmed'])
        self.assertEqual(self.queue.reply(dict(receipt,applied=True))['state'],'failed')

    async def test_missing_apply_cancel_ack_is_bounded_and_does_not_hold_submission_lane(self):
        await self.start('apply');command=self.queue.poll(self.live)['commands'][0]
        result=await cancel_operation(self.queue,command['id'],None,None,lambda *_:None,.04)
        self.assertEqual(result['state'],'cancelling');self.assertFalse(self.queue.entry_lock.locked())
        self.assertFalse(self.queue.read(command['id'])['cancel_confirmed'])
        with self.assertRaisesRegex(ValueError,'已停止'):
            self.queue.check_apply(dict(command,session=self.live['session'],client_id=self.live['client_id']))
        self.request['id']='independent-after-cancel'
        self.assertEqual((await self.start())['state'],'pending')

    async def test_old_browser_without_apply_cancel_protocol_cannot_receive_apply(self):
        self.live.pop('apply_protocol')
        with self.assertRaisesRegex(ValueError,'更新擴充'):await self.start('apply')
        self.assertEqual(self.queue.poll(self.live)['commands'],[])

    async def test_timed_out_delivered_apply_still_requires_cancel_confirmation(self):
        await self.start('apply');command=self.queue.poll(self.live)['commands'][0]
        operation=self.queue.read(command['id']);operation['created']-=31;self.queue.save(operation)
        self.assertEqual(self.queue.status(command['id'])['state'],'unconfirmed')
        result=await cancel_operation(self.queue,command['id'],None,None,lambda *_:None,.03)
        self.assertEqual(result['state'],'cancelling')
        self.assertTrue(self.queue.read(command['id'])['cancel_requested'])
        with self.assertRaisesRegex(ValueError,'已停止'):
            self.queue.check_apply(dict(command,session=self.live['session'],client_id=self.live['client_id']))


class NativeRouteBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_inspect_keeps_actual_local_route_origin_peer_host_and_token_checks(self):
        # Compile the actual decorator without importing ComfyUI, registering
        # routes, creating user directories or starting an HTTP service.
        tree=ast.parse((Path(__file__).resolve().parents[1]/'comfyui_prompt_calculus_studio/__init__.py').read_text(encoding='utf8'))
        route=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='native_inspect')
        self.assertIn('local_route',[n.id for n in route.decorator_list if isinstance(n,ast.Name)])
        class Forbidden(Exception):
            def __init__(self,**_):pass
        context=dict(functools=functools,ipaddress=ipaddress,urlsplit=urlsplit,secrets=secrets,_token='test-token',logging=logging,
            web=types.SimpleNamespace(HTTPForbidden=Forbidden,HTTPException=Forbidden),CaptureRejected=type('Rejected',(Exception,),{}))
        definition=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='local_route')
        exec(compile(ast.Module(body=[definition],type_ignores=[]),'<actual local_route>','exec'),context)
        calls=[]
        async def inspect(request):calls.append(request);return 'accepted'
        guarded=context['local_route'](inspect)
        def request(**values):
            return types.SimpleNamespace(**dict(dict(remote='127.0.0.1',host='127.0.0.1:8188',method='POST',headers={'X-Prompt-Studio':'test-token'}),**values))
        self.assertEqual(await guarded(request()),'accepted')
        for override in [dict(remote='192.0.2.1'),dict(host='evil.example:8188'),dict(headers={'X-Prompt-Studio':'bad'}),
            dict(headers={'X-Prompt-Studio':'test-token','Origin':'null'}),dict(headers={'X-Prompt-Studio':'test-token','Origin':'http://evil.example'}),
            dict(headers={'X-Prompt-Studio':'test-token','Sec-Fetch-Site':'cross-site'})]:
            with self.subTest(override=override),self.assertRaises(Forbidden):await guarded(request(**override))
        self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()
