"""Fixed-watermark snapshots, filtered coverage, byte limits and actual guards."""
import ast
import asyncio
import functools
import ipaddress
import json
import logging
import secrets
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch
from test_comfy_integration import Service
from prompt_calculus_studio import comfy_event_protocol as p

ROOT=Path(__file__).resolve().parents[1]


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.service=Service(root,root,root);self.log=self.service.events
        self.identity=(self.log.server_instance,self.log.epoch)
        self.scope=p.scope();self.digest=p.scope_digest(self.scope)

    def append(self, ident, payload=None, scope=None):
        with self.log.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            result=self.log.append(db,'fixture',payload or dict(state='queued'),entity=ident,scope=scope)
        self.log.committed()
        return result

    def snapshot(self, token='', after=''):
        return self.log.snapshot(self.identity,self.scope,self.digest,token,after)

    def test_snapshot_with_concurrent_writes_is_fixed_repeatable_and_catchup_exact(self):
        for n in range(140):self.append(f'op:{n:03d}')
        first=self.snapshot();self.assertEqual(first['as_of_seq'],'140')
        self.assertEqual(len(first['receipts']),128);self.assertFalse(first['done'])
        def write():
            self.append('op:000',dict(state='complete'));self.append('op:999')
        worker=threading.Thread(target=write);worker.start();worker.join(10);self.assertFalse(worker.is_alive())
        second=self.snapshot(first['snapshot_token'],first['next_after'])
        repeat=self.snapshot(first['snapshot_token'],first['next_after'])
        self.assertEqual(second,repeat);self.assertEqual(second['as_of_seq'],'140')
        receipts=first['receipts']+second['receipts']
        self.assertEqual(len({e['entity'] for e in receipts}),140)
        self.assertEqual(receipts[0]['payload']['state'],'queued');self.assertTrue(second['done'])
        changes=self.log.events('140',0,self.identity,self.scope,self.digest)
        self.assertEqual([e['entity'] for e in changes['events']],['op:000','op:999'])
        self.assertEqual(changes['covered_to'],'142')
        projected={e['entity']:e['payload'] for e in receipts}
        projected.update({e['entity']:e['payload'] for e in changes['events']})
        self.assertEqual(len(projected),141);self.assertEqual(projected['op:000']['state'],'complete')

    def test_pages_respect_record_and_exact_utf8_byte_limits(self):
        for n in range(140):self.append(f'count:{n}')
        page=self.log.events('0',0,self.identity,self.scope,self.digest)
        self.assertEqual(len(page['events']),128)
        self.assertEqual(page['covered_to'],'128')
        end=p.validate_page(page,dict(server_instance=self.identity[0],epoch=self.identity[1]),self.scope,'0')
        for n in range(12):self.append(f'large:{n:02d}:'+('x'*1000),dict(text='漢'*21000))
        cursor=str(end);seen=[]
        while True:
            page=self.log.events(cursor,0,self.identity,self.scope,self.digest)
            self.assertLessEqual(len(p.encode(page)),p.MAX_PAGE_BYTES)
            self.assertLessEqual(len(page['events']),128)
            seen.extend(e['seq'] for e in page['events'])
            if page['covered_to']==page['latest_seq']:break
            self.assertNotEqual(page['covered_to'],cursor);cursor=page['covered_to']
        self.assertEqual(len(seen),24);self.assertEqual(len(seen),len(set(seen)))
        page=self.snapshot();snapshots=[]
        while True:
            self.assertLessEqual(len(p.encode(page)),p.MAX_PAGE_BYTES)
            self.assertLessEqual(len(page['receipts']),128)
            snapshots.extend(e['entity'] for e in page['receipts'])
            if page['done']:break
            self.assertNotEqual(page['next_after'],page['after'])
            page=self.snapshot(page['snapshot_token'],page['next_after'])
        self.assertEqual(len(snapshots),152);self.assertEqual(len(set(snapshots)),152)

    def test_filtered_coverage_advances_over_other_scopes_without_leakage(self):
        a=dict(self.scope,library_id='a');b=dict(self.scope,library_id='b')
        self.append('a',scope=a);self.append('b',scope=b);self.append('c',scope=b)
        page=self.log.events('0',0,self.identity,a,p.scope_digest(a))
        self.assertEqual([e['entity'] for e in page['events']],['a'])
        self.assertEqual(page['covered_to'],'3')
        self.assertEqual(p.validate_page(page,dict(server_instance=self.identity[0],epoch=self.identity[1]),a,'0'),3)
        identity=dict(server_instance=self.identity[0],epoch=self.identity[1])
        for invalid in (dict(page,covered_to='4'),dict(page,latest_seq='2'),dict(page,reset_required=True)):
            with self.assertRaises(ValueError):p.validate_page(invalid,identity,a,'0')
        with self.assertRaises(ValueError):p.validate_page(page,dict(server_instance=self.identity[0],epoch=self.identity[1]),b,'0')

    def test_wrong_server_epoch_scope_token_and_cursor_rejected(self):
        for identity,scope,digest in [(('wrong',self.identity[1]),self.scope,self.digest),
                                     ((self.identity[0],'old'),self.scope,self.digest),
                                     (self.identity,dict(self.scope,library_id='other'),self.digest),
                                     (self.identity,dict(domain='chat.bridge',user_scope='default'),self.digest)]:
            with self.assertRaises(ValueError):self.log.events('0',0,identity,scope,digest)
        for value in ('-1','01',1,'1.0','9223372036854775808'):
            with self.assertRaises(ValueError):self.log.events(value,0,self.identity,self.scope,self.digest)
        first=self.snapshot()
        other=dict(self.scope,library_id='elsewhere')
        with self.assertRaises(ValueError):self.log.snapshot(self.identity,other,p.scope_digest(other),first['snapshot_token'])
        with self.assertRaises(ValueError):self.snapshot('invented-token')
        self.log.clock=lambda:time.time()+61
        with self.assertRaises(ValueError):self.snapshot(first['snapshot_token'])

    def test_long_poll_commit_wakes_without_busy_loop_and_idle_deadline_is_bounded(self):
        result=[];started=threading.Event()
        def read():
            started.set();result.append(self.log.events('0',1,self.identity,self.scope,self.digest))
        thread=threading.Thread(target=read);thread.start();started.wait(1)
        self.append('wake');thread.join(2);self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]['covered_to'],'1')
        begin=time.monotonic();idle=self.log.events('1',.05,self.identity,self.scope,self.digest)
        self.assertGreaterEqual(time.monotonic()-begin,.04);self.assertLess(time.monotonic()-begin,.5)
        self.assertEqual(idle['events'],[])

    def test_inconsistent_long_poll_waits_without_spinning_and_gap_wakes_it(self):
        self.log.record_loss('native buffer overflow', prompt_id='lost')
        with self.log.connection() as db:
            self.assertEqual(db.execute('SELECT consistent FROM event_meta').fetchone()[0], 0)
        with patch.object(self.log, 'page', wraps=self.log.page) as pages:
            begin=time.monotonic()
            idle=self.log.events('0',1,self.identity,self.scope,self.digest)
            elapsed=time.monotonic()-begin
            self.assertGreaterEqual(elapsed,.95);self.assertLess(elapsed,2)
            self.assertLessEqual(pages.call_count,3)
            print(json.dumps(dict(measurement='inconsistent_long_poll',seconds=elapsed,page_calls=pages.call_count)))
        self.assertFalse(idle['log_consistent']);self.assertEqual(idle['events'],[])
        result=[];read=threading.Event()
        original=self.log.page
        def page(*args):
            value=original(*args);read.set();return value
        with patch.object(self.log,'page',side_effect=page):
            thread=threading.Thread(target=lambda:result.append(self.log.events('0',2,self.identity,self.scope,self.digest)))
            thread.start();self.assertTrue(read.wait(1))
            self.assertTrue(self.log.recover_gap())
            thread.join(1);self.assertFalse(thread.is_alive())
        self.assertTrue(result[0]['log_consistent'])
        self.assertEqual([e['kind'] for e in result[0]['events']],[p.LOG_GAP])
        with self.log.connection() as db:
            self.assertEqual(db.execute('SELECT consistent,reason FROM event_meta').fetchone(), (1,''))

    def test_gap_reaches_filtered_subscribers_and_protocol_validates_it(self):
        self.append('other',scope=dict(self.scope,library_id='other'))
        self.log.record_loss('native event lost',persist=False)
        self.assertTrue(self.log.recover_gap())
        requested=dict(self.scope,library_id='library',workspace_id='workspace',entities=['owned'])
        page=self.log.events('0',0,self.identity,requested,p.scope_digest(requested))
        self.assertEqual([e['kind'] for e in page['events']],[p.LOG_GAP])
        self.assertEqual(p.validate_page(page,dict(server_instance=self.identity[0],epoch=self.identity[1]),requested,'0'),2)
        self.assertFalse(p.matches_scope(dict(page['events'][0],scope=dict(domain='chat.bridge',user_scope='default')),requested))
        with self.log.connection() as db:
            self.assertEqual(db.execute('SELECT consistent,reason FROM event_meta').fetchone(), (1,''))
        self.assertTrue(self.log.heartbeat([])['log_consistent'])

    def test_capability_has_installation_root_and_recognition_is_qt_free(self):
        value=dict(capabilities=[p.CAPABILITY],extension_info=dict(root='C:/ComfyUI/custom_nodes/pcs',version='fixture'),
                   event_channel=dict(schema=p.SCHEMA,transport='long-poll',server_instance=self.identity[0],epoch=self.identity[1],limits=p.limits()))
        recognized=p.recognize(value)
        self.assertEqual(recognized['extension_info']['root'],value['extension_info']['root'])
        self.assertIsNone(p.recognize(dict(capabilities=[])))
        self.assertEqual(self.log.heartbeat(['p'])['running'],['p'])
        self.assertTrue(self.log.heartbeat([])['log_consistent'])


class RouteSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_unreadable_route_reports_unknown_running_without_a_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);service=Service(root,root,root)
            tree=ast.parse((ROOT/'comfyui_prompt_calculus_studio/__init__.py').read_text(encoding='utf8'))
            route=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='events_heartbeat')
            route.decorator_list=[]
            def unreadable():raise OSError('queue inaccessible')
            queue=types.SimpleNamespace(get_current_queue_volatile=unreadable)
            context=dict(service=service,asyncio=asyncio,event_query=lambda _:(None,p.scope(),None),
                event_response=lambda value:value,PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(prompt_queue=queue)))
            exec(compile(ast.Module(body=[route],type_ignores=[]),'<actual heartbeat route>','exec'),context)
            heartbeat=await context['events_heartbeat'](None)
            self.assertIsNone(heartbeat['running']);self.assertFalse(heartbeat['queue_readable'])
            self.assertTrue(heartbeat['log_consistent'])
            with service.events.connection() as db:
                self.assertEqual(db.execute('SELECT consistent,reason FROM event_meta').fetchone(), (1,''))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log WHERE json_extract(body,'$.kind')=?",(p.LOG_GAP,)).fetchone()[0],0)
            queue.get_current_queue_volatile=lambda:([('number','p')],[])
            recovered=await context['events_heartbeat'](None)
            self.assertTrue(recovered['queue_readable']);self.assertEqual(recovered['running'],['p'])

    async def test_all_channel_routes_enforce_actual_local_guard_and_token_on_get(self):
        tree=ast.parse((ROOT/'comfyui_prompt_calculus_studio/__init__.py').read_text(encoding='utf8'))
        for name in ('events_capability','events','events_heartbeat','events_snapshot'):
            route=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
            self.assertIn('event_route',[n.id for n in route.decorator_list if isinstance(n,ast.Name)])
        class Forbidden(Exception):
            def __init__(self,**_):pass
        service=types.SimpleNamespace(native_queue=types.SimpleNamespace(require_supported=lambda:None))
        context=dict(functools=functools,ipaddress=ipaddress,urlsplit=urlsplit,secrets=secrets,_token='token',logging=logging,
            service=service,web=types.SimpleNamespace(HTTPForbidden=Forbidden,HTTPException=Forbidden),CaptureRejected=type('Rejected',(Exception,),{}))
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('local_route','event_route')]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<actual channel guards>','exec'),context)
        calls=[]
        async def get(request):calls.append(request);return 'accepted'
        guarded=context['event_route'](get)
        def request(**values):return types.SimpleNamespace(**dict(dict(remote='127.0.0.1',host='localhost:8188',method='GET',headers={'X-Prompt-Studio':'token'}),**values))
        self.assertEqual(await guarded(request()),'accepted')
        for override in (dict(remote='192.0.2.1'),dict(host='evil:8188'),dict(headers={}),dict(headers={'X-Prompt-Studio':'bad'}),
                         dict(headers={'X-Prompt-Studio':'token','Origin':'null'}),dict(headers={'X-Prompt-Studio':'token','Origin':'http://evil'}),
                         dict(headers={'X-Prompt-Studio':'token','Sec-Fetch-Site':'cross-site'})):
            with self.subTest(override=override),self.assertRaises(Forbidden):await guarded(request(**override))
        self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()
