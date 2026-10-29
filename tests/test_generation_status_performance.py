"""Bounded periodic queries and current-receipt UI publication."""
import copy
import json
import sqlite3
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from prompt_calculus_studio.generation_runner import ACTIVE, GenerationRunner
from prompt_calculus_studio.workspace_runtime import unfinished
import test_prompt_pipeline as fixture


class Client(QObject):
    stateChanged=Signal()

    def __init__(self,db):
        super().__init__()
        self.window=SimpleNamespace(store=SimpleNamespace(db=db))
        self.url='http://127.0.0.1:1';self.epoch=0;self.stopped=False


class GenerationIndexTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE generation_jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL, created REAL NOT NULL)')
        self.client=Client(self.db)

    def put(self,ident,state,created,**fields):
        value=dict(id=ident,state=state,created=created,workspace='w',server=self.client.url,
                   frozen={'text':'retain original whitespace  \n','nested':[{'value':1}]},**fields)
        # Deliberately keep non-canonical JSON spacing as part of the evidence.
        raw=json.dumps(value,ensure_ascii=False,indent=2)
        self.db.execute('INSERT INTO generation_jobs VALUES (?,?,?)',(ident,raw,created))
        return value

    def test_indexes_preserve_legacy_payloads_and_original_retention_rules(self):
        for i in range(205):self.put('done-'+str(i),'complete',i)
        self.put('failed','failed',206)
        for i,state in enumerate(ACTIVE):self.put('active-'+state,state,300+i)
        self.put('queued-owned','complete',-3,queue_job='q')
        self.put('chain-owned','failed',-2,chain='c')
        self.put('unknown',None,-1)
        self.db.commit()
        before=dict(self.db.execute('SELECT id,body FROM generation_jobs'))
        runner=GenerationRunner(self.client)
        self.assertEqual(dict(self.db.execute('SELECT id,body FROM generation_jobs')),before)
        self.assertEqual(set(runner.jobs),{'active-'+state for state in ACTIVE})
        self.assertEqual([r[1] for r in self.db.execute('PRAGMA table_info(generation_jobs)')],['id','body','created'])
        active_query="SELECT 1 FROM generation_jobs WHERE json_extract(body,'$.state') IN ('submitting','queued','running','unconfirmed') LIMIT 1"
        retention="SELECT id FROM generation_jobs WHERE json_extract(body,'$.queue_job') IS NULL AND json_extract(body,'$.chain') IS NULL AND json_extract(body,'$.state') NOT IN ('submitting','queued','running','unconfirmed') ORDER BY created DESC LIMIT -1 OFFSET 200"
        self.assertIn('generation_state',str(list(self.db.execute('EXPLAIN QUERY PLAN '+active_query))))
        self.assertIn('generation_retention',str(list(self.db.execute('EXPLAIN QUERY PLAN '+retention))))
        self.assertTrue(unfinished(self.db))
        newest=dict(id='new',state='complete',created=500,server=self.client.url)
        runner.save(newest)
        after=dict(self.db.execute('SELECT id,body FROM generation_jobs'))
        expected=set(before)-{'done-'+str(i) for i in range(7)}
        self.assertEqual(set(after),expected|{'new'})
        self.assertTrue(all(after[key]==before[key] for key in expected))
        self.db.execute("DELETE FROM generation_jobs WHERE json_extract(body,'$.state') IN ('submitting','queued','running','unconfirmed')")
        self.assertFalse(unfinished(self.db))

    def test_schema_initialization_does_not_commit_callers_pending_write(self):
        self.put('pending','complete',1)
        self.assertTrue(self.db.in_transaction)
        GenerationRunner(self.client)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT count(*) FROM generation_jobs').fetchone()[0],0)


class ActiveSummaryTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.client=Client(self.db);self.runner=GenerationRunner(self.client)
        self.signals=[];self.client.stateChanged.connect(lambda:self.signals.append(self.runner.message))
        self.job=dict(id='job-a',prompt_id='prompt-a',server=self.client.url,state='running',
                      created=time.time(),last_seen=time.time(),node='7',parameter_receipt={'fields':[]})
        self.runner.jobs[self.job['id']]=self.job
        self.status=dict(running_ids=['prompt-a'],queued_ids=[],executing_node='7')

    def test_repeated_heartbeats_suppress_only_the_periodic_summary(self):
        with patch.object(self.runner,'refresh_parameters',wraps=self.runner.refresh_parameters) as receipt:
            for i in range(6):
                self.job['last_seen']=time.time();self.runner.observe(self.status)
            self.assertEqual(receipt.call_count,6)
        self.assertEqual(len(self.signals),1)
        self.runner.status('same explicit message');self.runner.status('same explicit message')
        self.assertEqual(self.signals[-2:],['same explicit message']*2)
        self.runner.observe(self.status)
        self.assertEqual(len(self.signals),4)

    def test_equal_counts_new_job_node_clear_and_mutated_receipt_publish(self):
        self.runner.observe(self.status)
        self.status['executing_node']='8';self.runner.observe(self.status)
        self.status['executing_node']=None;self.runner.observe(self.status)
        replacement=copy.deepcopy(self.job);replacement.update(id='job-b',prompt_id='prompt-b')
        self.runner.jobs={replacement['id']:replacement};self.status['running_ids']=['prompt-b']
        self.runner.observe(self.status)
        replacement['parameter_receipt']['fields'].append({'next_value':12})
        self.runner.observe(self.status)
        self.assertEqual(len(self.signals),5)
        self.runner.observe(self.status);self.assertEqual(len(self.signals),5)
        self.status.update(running_ids=[],queued_ids=['prompt-b']);self.runner.observe(self.status)
        self.assertIn('生成中 0 項 · 等待中 1 項',self.signals[-1])

    def test_deferred_publication_disconnect_and_idle_reset(self):
        self.assertTrue(self.runner.observe(self.status,publish_status=False));self.assertEqual(self.signals,[])
        self.assertFalse(self.runner.observe(self.status,publish_status=False))
        self.runner.disconnected();self.assertTrue(self.runner.observe(self.status))
        self.client.epoch+=1;self.assertTrue(self.runner.observe(self.status))
        self.runner.jobs.clear();self.assertFalse(self.runner.observe(dict(running_ids=[],queued_ids=[])))
        self.runner.jobs[self.job['id']]=self.job;self.assertTrue(self.runner.observe(self.status))
        self.assertEqual(len(self.signals),3)


class PollPublicationTests(unittest.TestCase):
    setUp=fixture.PromptPipelineTests.setUp
    tearDown=fixture.PromptPipelineTests.tearDown
    stages=fixture.PromptPipelineTests.stages
    pipeline=fixture.PromptPipelineTests.pipeline

    def test_all_observers_process_every_poll_before_one_current_node_notification(self):
        self.pipeline();client=self.w.comfy;client.enabled=True;client.token='fixture';client.register_library=False
        job=dict(id='qa-job',prompt_id='qa-prompt',workspace=self.w.state['workspace'],server=client.url,
                 state='running',created=time.time(),last_seen=time.time(),node='7',parameter_receipt={'fields':[]})
        client.generation.jobs[job['id']]=job
        status=dict(ready=True,lease='same',target='fixture',running=1,pending=0,
                    running_ids=['qa-prompt'],queued_ids=[],executing_node='7',snapshot_versions=[1,2,3,4,5,6,7],
                    capabilities=['native_queue_v1','native_bindings_v1','stage_execution_v1'])
        order=[];signals=[]
        def notified():
            signals.append((client.input_flow.last_status.get('executing_node'),tuple(order)))
        client.stateChanged.connect(notified)
        def request(route,data=None,done=None,failed=None,**kwargs):
            if done:done(copy.deepcopy(status) if route=='desktop/status' else [])
        try:
            with ExitStack() as stack:
                stack.enter_context(patch.object(client,'request',side_effect=request))
                observe=stack.enter_context(patch.object(client.input_flow,'observe',wraps=client.input_flow.observe))
                stack.enter_context(patch.object(client.input_flow.chain,'observe',side_effect=lambda value:order.append('input')))
                queue=stack.enter_context(patch.object(client.queue,'observe',side_effect=lambda:order.append('queue')))
                images=stack.enter_context(patch.object(client.images,'poll',side_effect=lambda:order.append('images')))
                for _ in range(6):client.poll()
                self.assertEqual(len(signals),1);self.assertEqual(signals[0],('7',('input','queue','images')))
                self.assertEqual((observe.call_count,queue.call_count,images.call_count),(6,6,6))
                order.clear();status['executing_node']='8';client.poll()
                self.assertEqual(signals[-1],('8',('input','queue','images')))
                status['executing_node']=None;client.poll();self.assertIsNone(signals[-1][0])
                self.assertEqual(len(signals),3)
                status['running_ids']=['replacement'];job['prompt_id']='replacement';client.poll()
                self.assertEqual(len(signals),4)
                client.generation.disconnected();client.poll();self.assertEqual(len(signals),5)
        finally:
            client.enabled=False;client.generation.jobs.clear()


if __name__=='__main__':unittest.main()
