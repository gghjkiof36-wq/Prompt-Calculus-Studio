"""Long-term probes keep the original lane and never resend an operation."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_aux_event_acceptance as acceptance
from prompt_calculus_studio.workspace_connection import WorkspaceComfyClient
from prompt_calculus_studio.workspace_runtime import unfinished


class BackoffTests(unittest.TestCase):
    setUpClass=classmethod(lambda cls:acceptance.AuxiliaryAcceptanceTests.setUpClass())

    def setUp(self):
        self.h=acceptance.AuxiliaryAcceptanceTests('runTest');self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.ident=self.h.launch('open');self.h.unavailable=True
        self.calls=patch.object(self.h.client,'request',wraps=self.h.client.request)
        self.spy=self.calls.start();self.addCleanup(self.calls.stop)

    def count(self):
        return sum(call.args[0]=='workflow/native/recovery' for call in self.spy.call_args_list)

    def advance(self, seconds):
        for _ in range(seconds):
            self.h.h.clock.now+=1
            if self.h.h.clock.now%8==0:self.h.aux.heartbeat()
            self.h.aux.tick()

    def assert_unresolved(self):
        self.assertEqual(self.h.store.read(self.ident)['error'],'無法確認')
        self.assertTrue(unfinished(self.h.client.window.store.db))
        self.assertEqual(self.h.calls,[('open',self.ident)])
        self.assertEqual(len(self.h.peer.submissions),0)

    def test_hour_has_cross_window_backoff_not_450_queries(self):
        self.advance(3600)
        self.assertLessEqual(self.count(),23)
        self.assertGreaterEqual(self.count(),15)
        row=self.h.aux.pending[self.ident]
        self.assertTrue(row['expired']);self.assertEqual(row['long_step'],3)
        self.assert_unresolved()
        before=self.count();self.advance(600)
        self.assertEqual(self.count()-before,2,'never permanently abandon recovery')

    def test_heartbeats_do_not_reset_but_explicit_refresh_and_reconnect_probe_once(self):
        self.advance(45);row=self.h.aux.pending[self.ident];at=row['at'];before=self.count()
        for _ in range(20):self.h.aux.heartbeat();self.h.aux.tick()
        self.assertEqual(row['at'],at);self.assertEqual(self.count(),before)
        self.h.aux.require(self.ident,'user_refresh',force=True);self.h.aux.tick()
        self.assertEqual(self.count(),before+1);self.assertTrue(row['expired']);self.assert_unresolved()
        self.h.aux.health(dict(state='detected',needs_reconcile=True))
        self.h.aux.heartbeat();self.h.aux.tick()
        self.assertEqual(self.count(),before+2)
        for _ in range(10):self.h.aux.heartbeat();self.h.aux.tick()
        self.assertEqual(self.count(),before+2)

    def test_new_evidence_recovers_after_long_backoff_and_does_not_resend(self):
        self.advance(400);self.assert_unresolved()
        self.h.complete(self.ident);self.h.unavailable=False
        self.h.drain()
        self.h.assert_once(self.ident,'open')

    def test_new_nonterminal_event_queries_once_without_resetting_backoff(self):
        self.advance(45);before=self.count();item=self.h.store.read(self.ident)
        event=self.h.aux.evidence_event(item,dict(state='delivered'))
        event['scope']=dict(library_id=item['library'],workspace_id=item['workspace'])
        done=[]
        for _ in range(2):
            self.h.aux.receive(event,lambda:done.append(True));self.h.aux.tick()
        self.assertEqual(done,[True,True]);self.assertEqual(self.count(),before+1)
        self.assertTrue(self.h.aux.pending[self.ident]['expired']);self.assert_unresolved()

    def test_reopen_twice_probes_original_and_preserves_lane(self):
        self.advance(40)
        for _ in range(2):
            self.h.f.reopen();self.h.bind()
            self.h.aux.bind()  # Rebase startup deadlines onto the fixture clock.
            self.h.aux.tick()
            self.assertTrue(self.h.aux.pending[self.ident]['expired'])
            self.assert_unresolved()
        self.h.complete(self.ident);self.h.unavailable=False
        self.h.aux.require(self.ident,'user_refresh',force=True);self.h.aux.tick()
        self.h.assert_once(self.ident,'open')


class StoppedMessageTests(unittest.TestCase):
    def test_stopped_precedes_unconfirmed_aux(self):
        client=SimpleNamespace(stopped=True,events=SimpleNamespace(aux=SimpleNamespace(active={'x':dict(error='無法確認')})))
        self.assertEqual(WorkspaceComfyClient.scheduling_message.fget(client),'')


if __name__=='__main__':unittest.main()
