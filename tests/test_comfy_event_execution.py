"""Canvas click/Stage fixture: receipt ingestion never enables HTTP shadow work."""
import copy
import json
import threading
from types import SimpleNamespace
import unittest

from channel_fixture import Clock,Peer
import test_workspace_execution as execution_fixture
from prompt_calculus_studio.comfy_event_inbox import EventInbox
from prompt_calculus_studio.comfy_event_transport import ShadowChannel


class ShadowExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):execution_fixture.WorkspaceExecutionTests.setUpClass()

    def setUp(self):
        self.flow=execution_fixture.WorkspaceExecutionTests('runTest');self.flow.setUp()
        self.addCleanup(self.flow.tearDown)
        self.clock=Clock()
        self.peer=Peer(self.flow.base/'server',self.clock,service=self.flow.peer.service)
        self.inbox=EventInbox(self.flow.data);self.addCleanup(self.inbox.close)
        self.core=ShadowChannel(self.inbox,self.peer.send,self.peer.identity,clock=self.clock)
        self.queue=SimpleNamespace(mutex=threading.RLock(),queue=[],currently_running={},
            get_history=lambda prompt_id=None:{key:value for key,value in self.flow.peer.history.items() if key==prompt_id})
        previous=self.flow.transport_request
        def transport(client,route,data=None,done=None,failed=None,raw=None,content_type='application/json',**options):
            if route in ('workflow/native/cancel','workflow/native/recovery'):
                try:
                    if route.endswith('/cancel'):
                        result=self.flow.peer.native.cancel(data['id'],self.queue,lambda:None)
                        remaining={row[1] for row in self.queue.queue}
                        for prompt in list(self.flow.peer.pending):
                            if prompt not in remaining:self.flow.peer.pending.pop(prompt)
                    else:result=self.flow.peer.native.recovery(data['id'],self.queue)
                    if done:done(result)
                except Exception as exc:
                    if failed:failed(str(exc))
                    else:raise
                return
            return previous(client,route,data,done,failed,raw,content_type,**options)
        self.flow.transport_request=transport
        def command(value):
            if value['kind']=='poll':
                for entity,outcome in value['evidence']:self.inbox.observe_poll(self.peer.identity,entity,outcome,value['owner'])
            elif value['kind']=='watch':self.core.watch(value['entity'],value['phase'])
            elif value['kind']=='owner':self.inbox.owner(value['value'])
        self.flow.w.comfy_connection.channel=SimpleNamespace(url=self.flow.a.host.comfy.url,
            command=SimpleNamespace(emit=command),running=False,stop=lambda:None)
        self.peer.advance(self.core,2)

    def receive_without_mutation(self):
        document=self.flow.a.document
        before=copy.deepcopy(document.state)
        rows=list(document.execution.store.db.execute('SELECT body FROM generation_jobs ORDER BY id'))
        self.peer.advance(self.core,3)
        self.assertEqual(document.state,before)
        self.assertEqual(list(document.execution.store.db.execute('SELECT body FROM generation_jobs ORDER BY id')),rows)
        self.assertEqual(self.inbox.stream(self.peer.identity,self.core.scope)['applied'],'0')

    def matches(self):
        return [json.loads(row[0]) for row in self.inbox.db.execute("SELECT body FROM diagnostics WHERE kind='shadow_match'")]

    def assert_event_only(self, outcome):
        events=[row[0] for row in self.inbox.db.execute("SELECT outcome FROM observations WHERE source='event'")]
        self.assertIn(outcome,events)
        self.assertEqual(self.inbox.db.execute("SELECT COUNT(*) FROM observations WHERE source='poll'").fetchone()[0],0)
        self.assertEqual(self.matches(),[])

    def test_complete_generation_does_not_run_http_shadow_comparison(self):
        self.flow.click(self.flow.a)
        self.assertEqual(len(self.flow.peer.submissions),1)
        prompt=next(iter(self.flow.peer.pending))
        for kind in ('execution_start','executing','progress','executed'):
            self.peer.append(entity='execution:'+prompt,kind='native.'+kind,prompt=prompt,payload=dict(prompt_id=prompt,node='9'))
        self.receive_without_mutation()
        self.flow.finish()
        self.peer.append(entity='execution:'+prompt,kind='native.execution_success',prompt=prompt)
        self.receive_without_mutation()
        self.assert_event_only('success')
        self.assertEqual(len(self.flow.peer.submissions),1)

    def test_cancel_receipt_does_not_run_http_shadow_comparison(self):
        self.flow.click(self.flow.a)
        self.queue.queue=[(0,p['prompt_id'],p['prompt'],p['extra_data']) for p in self.flow.peer.pending.values()]
        self.flow.a.host.comfy.interrupt(clear_pending=True)
        self.flow.tick()
        self.receive_without_mutation()
        self.assert_event_only('cancelled')
        self.assertEqual(len(self.flow.peer.submissions),1)

    def test_fenced_missing_does_not_run_http_shadow_comparison(self):
        self.flow.click(self.flow.a)
        job=next(iter(self.flow.a.host.comfy.generation.jobs.values()))
        items=[(0,p['prompt_id'],p['prompt'],p['extra_data']) for p in self.flow.peer.pending.values()]
        self.flow.peer.native.reconcile(job['native_operation'],items)
        self.flow.peer.pending.clear()
        received=[]
        self.flow.a.host.comfy.request('workflow/native/recovery',dict(id=job['native_operation']),done=received.append)
        self.receive_without_mutation()
        self.assertTrue(received)
        self.assert_event_only('missing')
        self.assertEqual(len(self.flow.peer.submissions),1)


if __name__=='__main__':unittest.main()
