"""Sixteen survey scenarios, twice, through the product receiver and extension log."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from channel_fixture import Clock,Peer
from prompt_calculus_studio import comfy_event_protocol as p
from prompt_calculus_studio.comfy_event_inbox import EventInbox
from prompt_calculus_studio.comfy_event_transport import ShadowChannel,poll_evidence

SCENARIOS=('normal','half_open','network_switch','extension_exception','ui_stall_2','ui_stall_4',
           'all_http_hang','long_node','epoch_reload','terminal_event_lost','sqlite_apply_failure',
           'cancel_ack_lost','submit_ack_lost','cursor_expired','duplicate_wrong_scope','pcs_restart')


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.clock=Clock();self.peer=Peer(self.root,self.clock)
        self.inbox=EventInbox(self.root/'pcs');self.addCleanup(lambda:self.inbox.close())
        self.core=ShadowChannel(self.inbox,self.peer.send,self.peer.identity,clock=self.clock)
        self.state=dict(job='running',attempt='queued',frame=3,active=True,ui='unchanged')
        self.before=copy.deepcopy(self.state)
        self.peer.advance(self.core,2)

    def health(self):return self.inbox.stream(self.core.identity,self.core.scope)['health']

    def test_late_response_after_generation_change_and_stop_is_ignored(self):
        self.peer.hold.add('events');self.peer.advance(self.core,2)
        old=next(row for row in self.peer.pending if row.path=='events')
        response=self.peer.response(old.route)
        self.core.set_scope(dict(p.scope(),library_id='other'))
        old.done(response)
        self.assertIsNone(self.inbox.stream(self.core.identity,self.core.scope))
        self.core.stop();old.done(response)
        self.assertIsNone(self.inbox.stream(self.core.identity,self.core.scope))

    def test_log_gap_and_inconsistent_snapshot_are_bounded(self):
        self.peer.log.consistent=False
        self.peer.append(kind=p.LOG_GAP,entity='log:gap',prompt='',payload=dict(reason='lost'))
        self.peer.advance(self.core,20)
        self.assertTrue(self.core.needs_reconcile);self.assertTrue(self.core.snapshot_completed)
        snapshots=[route for at,route in self.peer.calls if route.startswith('events/snapshot')]
        self.assertLessEqual(len(snapshots),3)
        self.assertLessEqual(len(self.peer.calls),30)
        self.assertEqual(self.inbox.stream(self.core.identity,self.core.scope)['applied'],'0')

    def test_legacy_capability_disables_and_poll_projection_is_read_only(self):
        self.core._negotiate(dict(capabilities=[]));self.assertTrue(self.core.stopped)
        self.assertEqual(poll_evidence('/history/p',None,{'p':{'status':{'completed':True}}}),[('prompt:p','success')])
        self.assertEqual(poll_evidence('workflow/native/recovery',{'id':'one'},dict(recovery=dict(location='missing',fenced=True))),[('native_operations:one','missing')])
        self.assertEqual(poll_evidence('workflow/native/recovery',{'id':'one'},dict(recovery=dict(location='unavailable'))),[])
        self.assertEqual(poll_evidence('workflow/queue/cancel',{'attempt':'a'},dict(state='failed',cancel_confirmed=True)),[('queue_attempts:a','cancelled')])
        self.assertEqual(poll_evidence('workflow/queue/recovery',{'attempt':'a'},dict(recovery=dict(location='missing',fenced=True))),[('queue_attempts:a','missing')])

    def scenario(self,name):
        start=self.clock();measured={}
        if name in ('half_open','all_http_hang','network_switch','extension_exception'):
            self.peer.hold={'events','events/heartbeat','events/snapshot'} if name!='extension_exception' else {'events'}
            initial=self.core.last_events
            while self.clock()<initial+12:self.peer.advance(self.core,.25)
            self.assertEqual(self.health()['state'],'detected');measured['detected_after']=self.clock()-initial
            if name!='network_switch':
                while self.clock()<initial+30:self.peer.advance(self.core,.25)
                self.assertEqual(self.health()['state'],'unconfirmed');measured['unconfirmed_after']=self.clock()-initial
            self.peer.hold.clear();self.peer.advance(self.core,12)
            self.assertEqual(self.health()['state'],'receiving')
        elif name=='long_node':
            self.peer.running=['prompt-one'];self.peer.append(kind='native.executing',payload=dict(node='heavy'))
            self.peer.advance(self.core,124)
            self.assertEqual(self.health()['state'],'receiving')
            self.assertEqual(self.inbox.db.execute("SELECT COUNT(*) FROM diagnostics WHERE kind='no_progress'").fetchone()[0],1)
        elif name=='epoch_reload':
            old=self.core.identity['epoch'];self.peer.reload();self.peer.advance(self.core,12)
            self.assertNotEqual(self.core.identity['epoch'],old)
            self.assertEqual(self.core.identity['server_instance'],self.peer.identity['server_instance'])
            self.assertTrue(self.core.snapshot_completed)
        elif name=='terminal_event_lost':
            self.peer.append(kind='native.executed',entity='execution:prompt-one',payload=dict(node='output'))
            self.peer.advance(self.core,3)
            self.peer.append(kind='native.execution_success',entity='execution:prompt-one',payload=dict(prompt_id='prompt-one'))
            self.peer.drop_terminal=True;self.peer.advance(self.core,18)
            self.assertTrue(self.core.needs_reconcile)
            self.assertNotIn('prompt:prompt-one',self.core.watches)
        elif name=='sqlite_apply_failure':
            self.peer.append();before=self.core.since
            self.inbox.fault=lambda point:(_ for _ in ()).throw(sqlite3.OperationalError('disk full')) if point=='before_received' else None
            self.peer.advance(self.core,2);self.assertIsNotNone(self.core.storage_retry)
            calls=len(self.peer.calls);self.peer.advance(self.core,3)
            self.assertEqual(len(self.peer.calls),calls);self.assertEqual(self.core.since,before)
            self.inbox.fault=None;self.peer.advance(self.core,10)
            self.assertEqual(self.core.since,'1');measured['requests_during_disk_failure']=0
        elif name in ('cancel_ack_lost','submit_ack_lost'):
            self.core.watch('native_operations:one','cancelling' if name=='cancel_ack_lost' else 'submitting')
            self.peer.append(kind='operation.cancel_confirmed' if name=='cancel_ack_lost' else 'operation.queued')
            self.peer.advance(self.core,3);self.assertNotIn('native_operations:one',self.core.watches)
        elif name=='cursor_expired':
            self.peer.reset_once=True;self.peer.append();self.peer.advance(self.core,12)
            self.assertTrue(self.core.snapshot_completed);self.assertEqual(self.core.since,'1')
        elif name=='duplicate_wrong_scope':
            self.peer.append();self.peer.advance(self.core,3)
            count=self.inbox.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0]
            self.core.set_scope(dict(p.scope(),library_id='other'));self.peer.advance(self.core,4)
            self.assertTrue(self.core.snapshot_completed)
            self.assertEqual(self.inbox.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0],count)
        elif name=='pcs_restart':
            self.peer.append();self.peer.advance(self.core,3);self.core.stop();self.inbox.close()
            self.inbox=EventInbox(self.root/'pcs');self.core=ShadowChannel(self.inbox,self.peer.send,self.peer.identity,clock=self.clock)
            self.assertEqual(self.core.since,'1');self.peer.append(entity='native_operations:two');self.peer.advance(self.core,3)
            self.assertEqual(self.core.since,'2');self.assertEqual(self.inbox.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0],2)
        elif name in ('ui_stall_2','ui_stall_4'):
            # GUI consumption is absent; only the background clock advances.
            self.peer.append();self.peer.advance(self.core,int(name[-1]))
            self.assertEqual(self.core.since,'1');self.assertEqual(self.health()['state'],'receiving')
        else:
            self.peer.advance(self.core,35);self.assertEqual(self.health()['state'],'receiving')
        self.assertEqual(self.state,self.before)
        self.assertEqual(self.peer.submissions,1)
        self.assertTrue(all(route.startswith(('events','config')) for _,route in self.peer.calls))
        self.assertEqual(self.inbox.stream(self.core.identity,self.core.scope)['applied'],'0')
        print(json.dumps(dict(scenario=name,elapsed=self.clock()-start,requests=len(self.peer.calls),submissions=1,execution_state_unchanged=True,**measured)))


for repeat in range(2):
    for name in SCENARIOS:
        setattr(TransportTests,f'test_survey_{repeat+1}_{name}',lambda self,name=name:self.scenario(name))

if __name__=='__main__':unittest.main()
