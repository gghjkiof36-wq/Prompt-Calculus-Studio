"""Channel timers run only for evidence deadlines, responses or commands."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from channel_fixture import Clock,Peer
from prompt_calculus_studio.comfy_event_inbox import EventInbox
from prompt_calculus_studio.comfy_event_transport import ShadowChannel


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.clock=Clock();self.peer=Peer(root/'peer',self.clock)
        self.inbox=EventInbox(root/'pcs');self.addCleanup(self.inbox.close)
        self.core=ShadowChannel(self.inbox,self.peer.send,self.peer.identity,clock=self.clock)
        self.ticks=0
        self.advance(2)

    def advance(self,seconds):
        end=self.clock()+seconds
        self.peer.flush()
        self.core.tick();self.ticks+=1
        while self.clock()<end:
            next_at=self.core.next_wakeup()
            due=[row.due for row in self.peer.pending if not row.aborted and row.path not in self.peer.hold]
            if next_at is not None:due.append(next_at)
            at=min(due) if due else end+1
            if at>end:self.clock.now=end;break
            self.assertGreater(at,self.clock(),'deadline would busy-loop')
            self.clock.now=at;self.peer.flush();self.core.tick();self.ticks+=1

    def test_half_open_detects_at_twelve_and_unconfirmed_at_thirty_without_ticks(self):
        self.peer.hold={'events','events/heartbeat','events/snapshot'}
        observed=min(self.core.last_events,self.core.last_heartbeat)
        self.advance(observed+12-self.clock())
        health=self.inbox.stream(self.core.identity,self.core.scope)['health']
        self.assertEqual(health['state'],'detected')
        self.advance(observed+30-self.clock())
        self.assertEqual(self.inbox.stream(self.core.identity,self.core.scope)['health']['state'],'unconfirmed')
        self.assertLess(self.ticks,30)
        self.assertEqual(self.peer.submissions,1)
        self.peer.hold.clear();self.advance(12)
        self.assertEqual(self.inbox.stream(self.core.identity,self.core.scope)['health']['state'],'receiving')

    def test_storage_backoff_stops_pulling_and_retries_original_page(self):
        self.peer.append()
        self.inbox.fault=lambda point:(_ for _ in ()).throw(sqlite3.OperationalError('full')) if point=='before_received' else None
        self.advance(4)
        self.assertIsNotNone(self.core.storage_retry)
        self.assertEqual(self.core.next_wakeup(),self.core.storage_at)
        self.inbox.fault=None;self.advance(10)
        self.assertIsNone(self.core.storage_retry)
        self.assertEqual(self.core.since,'1')

    def test_running_node_only_diagnoses_at_120_and_does_not_timeout_output(self):
        self.peer.running=['prompt-one']
        self.peer.append(kind='native.executed')
        self.advance(125)
        self.assertEqual(self.inbox.stream(self.core.identity,self.core.scope)['health']['state'],'receiving')
        self.assertEqual(self.inbox.db.execute("SELECT COUNT(*) FROM diagnostics WHERE kind='no_progress'").fetchone()[0],1)
        self.assertEqual(self.core.watches['prompt:prompt-one']['state'],'waiting')
        self.assertEqual(self.peer.submissions,1)


if __name__=='__main__':unittest.main()
