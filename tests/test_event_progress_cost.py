"""Transient events must not write document receipts or scan retained Queue rows."""
import copy
import sqlite3
import unittest
from unittest.mock import patch
import test_event_acceptance_atomic as acceptance
from prompt_calculus_studio.comfy_event_transport import DeliveryFilter,_Worker


class ProgressCostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): acceptance.AcceptanceTests.setUpClass()

    def setUp(self):
        self.a=acceptance.AcceptanceTests();self.a.setUp();self.addCleanup(self.a.doCleanups)
        self.ident,self.prompt=self.a.start()
        self.client=self.a.client;self.events=self.client.events;self.db=self.client.window.store.db
        self.seq=10000

    def event(self,kind,**payload):
        self.seq+=1
        return dict(schema='pcs.execution-events.v1',**self.a.identity,seq=str(self.seq),
            entity='execution:'+self.prompt,prompt_id=self.prompt,kind='native.'+kind,
            scope={},receipt_revision=0,payload=payload)

    def rows(self): return self.db.execute('SELECT COUNT(*) FROM channel_applied_events').fetchone()[0]

    def running(self):
        self.events.receive(self.event('executing',node='9'),lambda:None)
        self.assertEqual(self.events.job(self.ident)['state'],'running')

    def test_ten_thousand_progress_and_duplicate_nodes_have_no_writes(self):
        self.running();before=self.rows();sql=[];done=[]
        self.db.set_trace_callback(sql.append)
        with patch.object(self.client.queue.store,'rows',side_effect=AssertionError('hot full scan')):
            for number in range(10000):
                kind=('progress','executing','executed','execution_cached')[number%4]
                self.events.receive(self.event(kind,node=str(number//50)),lambda:done.append(True))
        self.db.set_trace_callback(None)
        self.assertEqual(len(done),10000);self.assertEqual(self.rows(),before)
        self.assertFalse(any(s.lstrip().upper().startswith(('BEGIN','INSERT','UPDATE','DELETE')) for s in sql))
        self.assertEqual(self.client.generation.record(self.ident)['node'],'199')
        self.assertEqual(len(self.a.peer.submissions),1)

    def test_progress_never_changes_queued_state_and_replay_is_harmless(self):
        before=copy.deepcopy(self.events.job(self.ident));count=self.rows()
        event=self.event('progress',node='9',value=1,max=100)
        for _ in range(2):self.events.receive(event,lambda:None)
        self.assertEqual(self.events.job(self.ident),before);self.assertEqual(self.rows(),count)

    def test_noop_terminal_and_parameter_receipt_do_not_grow(self):
        self.a.peer.finish();self.a.terminal(self.prompt);self.a.drain();count=self.rows()
        for _ in range(50):self.events.receive(self.event('execution_success'),lambda:None)
        self.assertEqual(self.rows(),count)

    def test_queue_index_changes_only_after_commit_and_never_full_scans(self):
        queue=self.client.queue.store
        item=dict(id='frozen',attempt='frozen-attempt',state='running',capture='cap',
            work=dict(scope=dict(server=self.client.url)),attempts=[dict(created=1)],prompt_id='frozen-prompt')
        import json
        with self.db:self.db.execute('INSERT INTO work_queue VALUES (?,?,?)',('frozen',1,json.dumps(item)))
        queue.published(item)
        with patch.object(queue,'rows',side_effect=AssertionError('hot full scan')):
            self.assertEqual(self.events.job('frozen-attempt')['queue_job'],'frozen')
            self.assertIn('frozen-attempt',{j['id'] for j in self.events.active()})
            with self.assertRaises(sqlite3.OperationalError):
                with self.events.chain.accepting():
                    queue.update('frozen',state='complete')
                    raise sqlite3.OperationalError('rollback')
            self.assertIn('frozen-attempt',self.events.queue_active)
            with self.events.chain.accepting():queue.update('frozen',state='complete')
            self.assertNotIn('frozen-attempt',self.events.queue_active)
            self.events.health(dict(state='receiving',running=[self.prompt]))

    def test_worker_filters_progress_but_requires_committed_running_for_nodes(self):
        gate=DeliveryFilter();page=dict(owner='one',events=[self.event('progress'),self.event('executing',node='9')])
        self.assertEqual(len(gate.select(page)['events']),1)
        gate.accepted('one',[self.prompt])
        self.assertEqual(gate.select(page)['events'],[])
        self.assertEqual(list(gate.display['one']),[self.prompt])
        gate.display.clear();gate.select(page);self.assertEqual(gate.display,{})
        foreign=dict(page,events=[dict(self.event('executing',node='foreign'),scope=dict(workspace_id='other'))])
        gate.select(foreign,dict(workspace_id='owner-workspace'))
        self.assertEqual(gate.display,{})
        terminal=self.event('execution_success')
        self.assertEqual(gate.select(dict(page,events=[terminal]))['events'],[terminal])
        gate.accepted('one',[])
        self.assertEqual(len(gate.select(page)['events']),1)

    def report_rows(self,kind):
        import json
        count=self.rows()
        print(json.dumps(dict(lifecycle=kind,receipts=count,submissions=len(self.a.peer.submissions))))
        self.assertLessEqual(count,8)
        self.assertEqual(len(self.a.peer.submissions),1)

    def test_worker_only_pages_deliver_and_display_is_at_most_five_hz(self):
        from types import SimpleNamespace
        worker=_Worker(self.client.window.store.directory,self.client.url,'fixture',self.a.identity)
        worker.core=SimpleNamespace(identity=self.a.identity,scope={},storage_retry=None,tick=lambda:None,
            _apply=lambda action:action())
        db=SimpleNamespace(total_changes=0);delivered=[];pages=[];displays=[]
        worker.inbox=SimpleNamespace(db=db,delivery=lambda *args:pages.pop(0) if pages else None,delivered=delivered.append)
        worker.owners={'one':dict(workspace_id='owner')};worker.filter.accepted('one',[self.prompt]);worker.prune_at=1000
        worker.deliveryReady.connect(lambda page:self.fail('Transient page reached GUI acceptance'))
        worker.healthChanged.connect(displays.append)
        with patch('prompt_calculus_studio.comfy_event_transport.QTimer.singleShot'):
            for index in range(100):
                pages.append(dict(owner='one',snapshot='',events=[self.event('progress'),self.event('executing',node=str(index))]))
                db.total_changes+=1
                with patch('prompt_calculus_studio.comfy_event_transport.time.monotonic',return_value=index/100):worker.cycle()
        self.assertEqual(len(delivered),100);self.assertLessEqual(len(displays),5)
        self.assertTrue(all(page['events']==[] for page in delivered))
        self.assertTrue(displays)

    def test_complete_receipt_count_is_bounded(self):
        self.running()
        for _ in range(500):self.events.receive(self.event('progress',node='9'),lambda:None)
        self.a.peer.finish();self.a.terminal(self.prompt);self.a.drain()
        self.report_rows('complete')

    def test_cancel_receipt_count_is_bounded(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.running();self.a.lost_cancel=True
        controls=self.a.f.a.host.canvas.execution_bar.controls;controls.refresh()
        QTest.mouseClick(controls.stop,Qt.MouseButton.LeftButton);self.a.drain()
        self.assertEqual(self.client.generation.record(self.ident)['terminal_action'],'cancel')
        self.report_rows('cancel')

    def test_missing_receipt_count_is_bounded(self):
        self.a.peer.pending.clear()
        self.client.generation.recheck(self.ident);self.events.reconcile.tick()
        self.client.generation.abandon(self.ident)
        self.assertEqual(self.client.generation.record(self.ident)['state'],'retired')
        self.report_rows('missing')


if __name__=='__main__':unittest.main()
