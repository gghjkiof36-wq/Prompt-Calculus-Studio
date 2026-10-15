"""Bounded status projections keep complete execution evidence intact."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
from prompt_calculus_studio.stage_store import StageStore
import test_prompt_pipeline as fixture


class JournalProjectionTests(unittest.TestCase):
    def test_latest_status_and_open_runs_do_not_decode_completed_history(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);store=StageStore(db)
        payload={'prompt':'unchanged prompt, '+('long text '*10000),'images':['original.png'],'fields':['6/text']}
        for i in range(80):
            store.add('attempt','w','done',stage='s',status='complete',snapshot=payload)
        done=store.add('run','w',status='complete',snapshot=payload)
        failed=store.add('attempt','w','open',stage='s',status='failed',error='native failure',snapshot=payload)
        live=store.add('run','w',status='paused',snapshot=payload)
        before=list(db.execute('SELECT id,body FROM stage_journal ORDER BY position'))
        with patch('prompt_calculus_studio.stage_store.json.loads',wraps=json.loads) as decode:
            self.assertEqual(store.latest_stage_status('w','s'),dict(status='failed',error='native failure'))
            self.assertEqual(store.open_runs('w'),[live]);self.assertEqual(decode.call_count,1)
        self.assertEqual(list(db.execute('SELECT id,body FROM stage_journal ORDER BY position')),before)
        self.assertEqual(store.read(done['id'])['snapshot'],payload)
        store.update(failed['id'],status='complete',error='')
        self.assertEqual(store.latest_stage_status('w','s'),dict(status='complete',error=''))
        # Reopening pauses only unfinished runs and retains full retry evidence.
        again=StageStore(db);self.assertEqual(again.read(live['id'])['snapshot'],payload)
        query="SELECT body FROM stage_journal WHERE kind='attempt' AND workspace='w' AND json_extract(body,'$.stage')='s' ORDER BY position DESC LIMIT 1"
        plan=str(list(db.execute('EXPLAIN QUERY PLAN '+query)))
        self.assertIn('stage_latest',plan);self.assertNotIn('TEMP B-TREE',plan)


class StatusRefreshTests(unittest.TestCase):
    setUp=fixture.PromptPipelineTests.setUp
    tearDown=fixture.PromptPipelineTests.tearDown
    stages=fixture.PromptPipelineTests.stages
    pipeline=fixture.PromptPipelineTests.pipeline

    def test_status_refresh_reads_summary_and_defers_tooltip_expansion(self):
        config=self.pipeline();panel=self.c.flow_cards[config['stage']].panel
        before=copy.deepcopy(self.w.state)
        with patch.object(self.runner,'progress',side_effect=AssertionError('No hover requested')):
            with patch.object(self.runner,'expansion',side_effect=AssertionError('No hover requested')):
                self.w.comfy.stateChanged.emit()
        self.assertEqual(self.w.state,before)
        item=self.runner.store.add('attempt',self.w.state['workspace'],'synthetic',stage=config['stage'],status='failed',error='new error')
        self.w.comfy.stateChanged.emit();self.assertEqual(panel.status.text(),'new error')
        self.runner.store.update(item['id'],status='complete',error='')
        self.w.comfy.stateChanged.emit();self.assertFalse(panel.status.isVisible())

    def test_identical_polls_skip_ui_broadcast_but_process_each_receipt(self):
        client=self.w.comfy;client.enabled=True;client.token='fixture';client.register_library=False
        status=dict(ready=True,lease='same',target='fixture',snapshot_versions=[1,2,3,4,5,6,7],
            capabilities=['native_queue_v1','native_bindings_v1','stage_execution_v1'])
        def request(route,data=None,done=None,failed=None,**kwargs):
            if done:done(copy.deepcopy(status) if route=='desktop/status' else [])
        from contextlib import ExitStack
        signals=[];client.stateChanged.connect(lambda:signals.append(client.message))
        with ExitStack() as stack:
            stack.enter_context(patch.object(client,'request',side_effect=request))
            observers=[stack.enter_context(patch.object(owner,name)) for owner,name in
                       ((client.generation,'observe'),(client.input_flow,'observe'),(client.queue,'observe'),(client.images,'poll'))]
            client.poll();self.assertEqual(len(signals),1)
            for _ in range(5):client.poll()
            self.assertEqual(len(signals),1);self.assertTrue(all(p.call_count==6 for p in observers))
            status['running']=1;client.poll();self.assertEqual(len(signals),2)
            status['snapshot_versions']=[1];client.poll();self.assertEqual(len(signals),3);self.assertFalse(client.can_run)
        client.enabled=False


if __name__=='__main__':unittest.main()
