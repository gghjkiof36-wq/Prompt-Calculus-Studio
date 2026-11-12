import copy
from pathlib import Path
import sqlite3
import tempfile
import unittest

from channel_fixture import Clock,Peer
from prompt_calculus_studio import comfy_event_protocol as p
from prompt_calculus_studio.comfy_event_inbox import EventInbox


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.peer=Peer(self.root,Clock())
        self.inbox=EventInbox(self.root/'pcs');self.addCleanup(lambda:self.inbox.close())
        self.scope=p.scope();self.identity=self.peer.identity

    def page(self,since='0'):
        return self.peer.log.events(since,0,tuple(self.identity.values()),self.scope,p.scope_digest(self.scope))

    def test_cursor_idempotence_restart_and_atomic_failure(self):
        self.peer.append();page=self.page()
        def fault(point):
            if point=='before_received':raise sqlite3.OperationalError('disk full')
        self.inbox.fault=fault
        with self.assertRaises(sqlite3.Error):self.inbox.page(self.identity,self.scope,'0',page)
        self.assertEqual(self.inbox.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0],0)
        self.assertIsNone(self.inbox.stream(self.identity,self.scope))
        self.inbox.fault=None
        self.inbox.page(self.identity,self.scope,'0',page)
        self.inbox.page(self.identity,self.scope,'0',page)
        self.assertEqual(self.inbox.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0],1)
        self.inbox.close();self.inbox=EventInbox(self.root/'pcs')
        self.assertEqual(self.inbox.stream(self.identity,self.scope)['received'],'1')
        self.assertEqual(self.inbox.stream(self.identity,self.scope)['applied'],'0')
        bad=copy.deepcopy(page);bad['events'][0]['payload']['state']='different'
        with self.assertRaises(ValueError):self.inbox.page(self.identity,self.scope,'0',bad)

    def test_snapshot_watermark_concurrent_event_and_epoch_scope_isolation(self):
        for n in range(140):self.peer.append(entity=f'item:{n:03d}')
        identity=tuple(self.identity.values());digest=p.scope_digest(self.scope)
        first=self.peer.log.snapshot(identity,self.scope,digest)
        self.inbox.snapshot_page(self.identity,self.scope,first)
        self.assertEqual(self.inbox.stream(self.identity,self.scope)['received'],'0')
        self.peer.append(entity='item:new')
        second=self.peer.log.snapshot(identity,self.scope,digest,first['snapshot_token'],first['next_after'])
        self.inbox.snapshot_page(self.identity,self.scope,second,token=first['snapshot_token'],after=first['next_after'],as_of=first['as_of_seq'])
        self.assertEqual(self.inbox.stream(self.identity,self.scope)['received'],'140')
        self.inbox.page(self.identity,self.scope,'140',self.page('140'))
        self.assertEqual(self.inbox.stream(self.identity,self.scope)['received'],'141')
        self.assertEqual(self.inbox.db.execute('SELECT COUNT(*) FROM snapshot_items').fetchone()[0],140)
        self.assertIsNone(self.inbox.stream(dict(self.identity,epoch='other'),self.scope))
        self.assertIsNone(self.inbox.stream(self.identity,dict(self.scope,library_id='other')))

    def test_shadow_comparison_matches_and_disagrees_without_document_access(self):
        for outcome,kind in [('success','native.execution_success'),('error','native.execution_error'),('cancelled','operation.cancel_confirmed'),('missing','operation.missing')]:
            prompt='p-'+outcome
            self.peer.append(entity='e:'+outcome,kind=kind,prompt=prompt)
            self.inbox.observe_poll(self.identity,'prompt:'+prompt,outcome,'owner-one')
        self.inbox.page(self.identity,self.scope,'0',self.page())
        self.assertEqual(self.inbox.db.execute("SELECT COUNT(*) FROM diagnostics WHERE kind='shadow_match'").fetchone()[0],4)
        self.inbox.observe_poll(self.identity,'prompt:p-success','error','owner-one')
        self.assertEqual(self.inbox.db.execute("SELECT COUNT(*) FROM diagnostics WHERE kind='shadow_mismatch'").fetchone()[0],1)
        self.assertEqual(self.inbox.stream(self.identity,self.scope)['applied'],'0')

    def test_snapshot_rejects_mixed_identity_and_bad_coverage(self):
        self.peer.append();page=self.peer.log.snapshot(tuple(self.identity.values()),self.scope,p.scope_digest(self.scope))
        for changes in [dict(server_instance='wrong'),dict(scope_digest='wrong'),dict(as_of_seq='99'),dict(after='invented'),dict(next_after='wrong')]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.inbox.snapshot_page(self.identity,self.scope,dict(page,**changes))
        self.assertIsNone(self.inbox.stream(self.identity,self.scope))

    def test_owner_survives_close_and_sidecar_inventory(self):
        from prompt_calculus_studio.workspace_recovery_inventory import channel_sidecars
        value=dict(token='retained',document_id='one',library_id='stable',path='a',attached=True,servers=['old'])
        self.inbox.owner(value);self.inbox.owner(dict(value,attached=False,servers=['new']))
        self.assertEqual(self.inbox.db.execute('SELECT COUNT(*) FROM owners').fetchone()[0],1)
        self.assertIn('old',self.inbox.db.execute('SELECT body FROM owners').fetchone()[0])
        self.assertIn('new',self.inbox.db.execute('SELECT body FROM owners').fetchone()[0])
        self.assertIn(self.inbox.path,channel_sidecars(self.root/'pcs'))


if __name__=='__main__':unittest.main()
