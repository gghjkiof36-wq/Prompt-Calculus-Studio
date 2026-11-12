"""Post-document delivery durability, bounded pages and incremental retention."""
import copy
from pathlib import Path
import sqlite3
import tempfile
import unittest
from channel_fixture import Clock,Peer
from prompt_calculus_studio import comfy_event_protocol as p
from prompt_calculus_studio.comfy_event_inbox import EventInbox


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.clock=Clock();self.peer=Peer(self.root,self.clock)
        self.inbox=EventInbox(self.root/'pcs',clock=self.clock);self.addCleanup(lambda:self.inbox.close())
        self.identity=self.peer.identity;self.scope=p.scope()
        self.snapshot()

    def page(self,since='0'):
        value=self.peer.log.events(since,0,tuple(self.identity.values()),self.scope,p.scope_digest(self.scope))
        self.inbox.page(self.identity,self.scope,since,value)

    def snapshot(self):
        value=self.peer.log.snapshot(tuple(self.identity.values()),self.scope,p.scope_digest(self.scope))
        self.inbox.snapshot_page(self.identity,self.scope,value)
        return value

    def test_delivery_failure_reopens_twice_replays_twice_without_advancing(self):
        self.inbox.delivered(self.inbox.delivery(self.identity,self.scope,'owner'))
        self.peer.append();self.page();page=self.inbox.delivery(self.identity,self.scope,'owner')
        self.inbox.fault=lambda _:(_ for _ in ()).throw(sqlite3.OperationalError('full'))
        with self.assertRaises(sqlite3.Error):self.inbox.delivered(page)
        self.assertEqual(self.inbox.stream(self.identity,self.scope)['applied'],'0')
        for _ in range(2):
            self.inbox.close();self.inbox=EventInbox(self.root/'pcs',clock=self.clock)
            self.assertEqual(self.inbox.delivery(self.identity,self.scope,'owner'),page)
        for _ in range(2):self.inbox.delivered(page)
        self.assertEqual(self.inbox.stream(self.identity,self.scope)['applied'],'1')
        self.assertIsNone(self.inbox.delivery(self.identity,self.scope,'owner'))

    def test_snapshot_watermark_then_events_and_slow_owner_prevents_pruning(self):
        self.peer.append();self.page();snapshot=self.snapshot()
        first=self.inbox.delivery(self.identity,self.scope,'one')
        second=self.inbox.delivery(self.identity,self.scope,'unopenable')
        self.peer.append(entity='next');self.page(snapshot['as_of_seq'])
        self.inbox.delivered(first)
        after=self.inbox.delivery(self.identity,self.scope,'one')
        self.assertEqual([item['seq'] for item in after['events']],['2'])
        self.inbox.delivered(after)
        self.clock.now=p.RETENTION_SECONDS+1;self.inbox.prune(self.identity)
        self.assertEqual(self.inbox.db.execute('SELECT count FROM budgets').fetchone()[0],2)
        self.inbox.delivered(second)
        self.inbox.delivered(self.inbox.delivery(self.identity,self.scope,'unopenable'))
        self.inbox.prune(self.identity)
        self.assertEqual(self.inbox.db.execute('SELECT count,size FROM budgets').fetchone(),(0,0))

    def test_replacement_epoch_snapshot_covers_prior_owner_before_cleanup(self):
        self.peer.append();self.page();self.inbox.delivery(self.identity,self.scope,'owner')
        old=copy.deepcopy(self.identity)
        self.peer.reload();self.identity=self.peer.identity;self.snapshot()
        self.inbox.delivered(self.inbox.delivery(self.identity,self.scope,'owner'))
        self.clock.now=p.RETENTION_SECONDS+1;self.inbox.prune(self.identity)
        self.assertIsNone(self.inbox.stream(old,self.scope))
        self.assertIsNotNone(self.inbox.stream(self.identity,self.scope))

    def test_pages_use_counters_and_gui_delivery_is_bounded(self):
        for n in range(12):self.peer.append(entity='item:'+str(n),payload=dict(state='queued',text='x'*60000))
        since='0'
        while int(since)<12:
            self.page(since);since=self.inbox.stream(self.identity,self.scope)['received']
        sql=[];self.inbox.db.set_trace_callback(sql.append)
        self.inbox._budget(*self.identity.values(),0,0)
        self.assertFalse(any('COUNT(' in s.upper() or 'SUM(' in s.upper() for s in sql))
        seen=[]
        while (page:=self.inbox.delivery(self.identity,self.scope,'owner')) is not None:
            self.assertLessEqual(len(p.encode(page)),p.MAX_PAGE_BYTES)
            seen.extend(item['seq'] for item in page['events']);self.inbox.delivered(page)
        self.assertEqual(seen,[str(i) for i in range(1,13)])

    def test_stale_coverage_and_wrong_scope_never_advance(self):
        self.peer.append()
        value=self.peer.log.events('0',0,tuple(self.identity.values()),self.scope,p.scope_digest(self.scope))
        for change in (dict(from_cursor='2'),dict(oldest_seq='2'),dict(scope_digest='wrong'),dict(epoch='wrong')):
            with self.assertRaises(ValueError):self.inbox.page(self.identity,self.scope,'0',dict(value,**change))
        self.assertEqual(self.inbox.stream(self.identity,self.scope)['received'],'0')


if __name__=='__main__':unittest.main()
