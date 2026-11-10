"""Projection ownership and durable transition boundaries for frozen execution."""
import copy
import json
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prompt_calculus_studio import stage_context, workspace_scene, prompt_sources
from prompt_calculus_studio.stage_runner import StageRunner
from prompt_calculus_studio.stage_store import StageStore
from test_stage_image_snapshot import document
from test_prompt_material_resolution import flow


class CaptureIsolationTests(unittest.TestCase):
    def test_execution_projection_matches_full_copy_and_preserves_live_reader_roots(self):
        state=document({});cid=next(iter(state['multi_output']['canvases']))
        source=prompt_sources.add(state,root=flow('ab'))
        state['multi_output']['connections'].append(dict(id='content',source=source,destination=cid,kind='content'))
        original=copy.deepcopy(state)
        expected=stage_context.project(state,'.')
        actual=stage_context.project(state,'.',execution=True)
        self.assertEqual(actual,expected)
        self.assertEqual(state,original)
        root=actual['multi_output']['canvases'][cid]['source_members'][0]
        actual['uses'][root]['name']='private result'
        actual['multi_output']['canvases'][cid]['members'].clear()
        self.assertEqual(state,original)
        self.assertNotEqual(actual,expected)

    def test_captured_record_freezes_all_fields_and_returns_independent_ownership(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);store=StageStore(db)
        values=dict(saved={'scene':{'uses':{'a':{'prompt':'saved'}}}},status='waiting')
        original=copy.deepcopy(values)
        with patch('uuid.uuid4',return_value=SimpleNamespace(hex='entry')),patch('time.time',return_value=5):
            item=store.add_captured('entry','workspace','owner',**values)
        body=store.read('entry')
        self.assertEqual(body,dict(id='entry',kind='entry',workspace='workspace',owner='owner',created=5,**original))
        values['saved']['scene']['uses']['a']['prompt']='live edit'
        item['saved']['scene']['uses']['a']['prompt']='returned edit'
        self.assertEqual(store.read('entry'),body)
        self.assertEqual(values['saved']['scene']['uses']['a']['prompt'],'live edit')

    def test_scene_validation_borrows_without_mutating_saved_or_active_documents(self):
        state=document({})
        state['workspace_scenes']['items'][state['workspace']]=workspace_scene.scene(state)
        before=copy.deepcopy(state)
        workspace_scene.validate(state)
        self.assertEqual(state,before)

    def test_write_only_capture_serializes_without_decoding_a_discarded_record(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);store=StageStore(db)
        values={'scene':{'uses':{'a':{'prompt':'saved'}}}}
        with patch('prompt_calculus_studio.stage_store.json.loads',side_effect=AssertionError('unused capture decoded')):
            ident=store.add_captured('entry','workspace',saved=values,_return_record=False)
        values['scene']['uses']['a']['prompt']='edited'
        self.assertEqual(store.read(ident)['saved']['scene']['uses']['a']['prompt'],'saved')

    def test_supply_rollback_restores_cursors_derived_views_and_saved_scene(self):
        state=document({});source=prompt_sources.add(state,root=flow('ab'))
        before=copy.deepcopy(state)
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        runner=SimpleNamespace(store=StageStore(db),window=SimpleNamespace(state=state))
        with self.assertRaises(sqlite3.OperationalError):
            with StageRunner.accepting(runner) as transaction:
                frozen=prompt_sources.build(state,[source],count=1)
                prompt_sources.reserve(state,frozen,register=transaction.register)
                for path in (('multi_output','outputs'),('workspace_scenes',),('uses',),('multi_output','canvases')):transaction.register(*path)
                state['multi_output']['outputs'][state['multi_output']['current_output']]['draft']='unaccepted'
                workspace_scene.capture(state)
                state['uses']['derived']={'prompt':'unaccepted'}
                state['multi_output']['canvases'].clear()
                raise sqlite3.OperationalError('failed acceptance')
        self.assertEqual(state,before)

    def test_supply_backup_does_not_copy_unrelated_roots_or_history(self):
        state=document({});source=prompt_sources.add(state,root=flow('ab'))
        class ReadOnly:
            def __deepcopy__(self,memo):raise AssertionError('unrelated document data copied')
        state['uses']['unrelated']=ReadOnly();state['content_history']=ReadOnly()
        import inspect
        transaction=inspect.unwrap(stage_context.ExecutionTransaction)(state)
        transaction.register(*prompt_sources.source_path(source))
        self.assertEqual(len(transaction.records),1)



class AdvanceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.store=StageStore(self.db)
        self.run=self.store.add('run','w',stack=[],initial={},round=1)
        self.runner=SimpleNamespace(store=self.store,window=SimpleNamespace(state={'uses':{'a':{'prompt':'original'}}}),_advancing_run=None)
        self.runner.save=lambda run:StageRunner.save(self.runner,run)
        self.runner.accepting=lambda **kwargs:StageRunner.accepting(self.runner,**kwargs)

    def test_dispatch_observes_one_committed_stack_write_after_multiple_transitions(self):
        statements=[];self.db.set_trace_callback(statements.append);observed=[]
        def advance(run):
            for index in (1,2,3):
                run['stack']=[dict(index=index)]
                self.runner.save(run)
            return ('frame','stage')
        def dispatch(run,*action):
            self.assertFalse(self.db.in_transaction)
            observed.append((self.store.read(run['id'])['stack'],action))
        self.runner.advance=advance;self.runner.dispatch=dispatch
        StageRunner.step(self.runner,self.run)
        self.assertEqual(observed,[([{'index':3}],('frame','stage'))])
        self.assertEqual(sum(s.startswith('UPDATE stage_journal') for s in statements),1)
        self.assertEqual(sum(s=='COMMIT' for s in statements),1)

    def test_failed_transition_rolls_back_rows_and_document_without_dispatch(self):
        original=self.store.read(self.run['id']);state=copy.deepcopy(self.runner.window.state)
        def advance(run):
            self.store.update(run['id'],round=2)
            self.runner.store._transactions.execution.register('uses','a')
            self.runner.window.state['uses']['a']['prompt']='unaccepted'
            raise sqlite3.OperationalError('synthetic failure')
        self.runner.advance=advance
        self.runner.dispatch=lambda *args:self.fail('must not dispatch')
        with self.assertRaises(sqlite3.OperationalError):StageRunner.step(self.runner,self.run)
        self.assertEqual(self.store.read(self.run['id']),original)
        self.assertEqual(self.runner.window.state,state)
        self.assertIsNone(self.runner._advancing_run)

    def test_manual_text_transition_does_not_copy_unrelated_edit_history(self):
        class History:
            def __deepcopy__(self,memo):raise AssertionError('unrelated history copied')
        self.runner.window.state['content_history']=History()
        self.run['plan']={'stages':{'stage':{'auto':None}}}
        self.runner.advance=lambda run:None
        with patch('prompt_calculus_studio.stage_context.ExecutionTransaction',__import__('inspect').unwrap(stage_context.ExecutionTransaction)):
            StageRunner.step(self.runner,self.run)

    def test_foreign_input_delay_uses_journal_capture_after_live_document_edit(self):
        prepared=[];callbacks=[]
        stage=dict(id='stage',workflow='workflow')
        state=dict(workspace='w',multi_output={'stages':{'stage':{'parameters':None}}},uses={'root':{'prompt':'frozen'}})
        frame=dict(ctx={'saved':{},'sources':{},'results':{}},variants=[{'values':{},'sources':{},'results':{}}],index=0)
        self.run.update(workspace='w',round=1,stack=[frame])
        self.runner.window.state['workspace']='w'
        self.runner.closed=False
        self.runner.client=SimpleNamespace(connected=True)
        self.runner.remember_preview=lambda *args:None
        self.runner.check_route=lambda run:None
        self.runner.accepting=lambda **kwargs:StageRunner.accepting(self.runner,**kwargs)
        self.runner.apply_bindings=lambda foreign,done,*args,**kwargs:callbacks.append(done)
        def prepare(client,snapshot,*args,**kwargs):prepared.append(copy.deepcopy(snapshot))
        with patch('prompt_calculus_studio.stage_runner.prepare_state',return_value=(state,[],{'foreign':[]})), \
             patch('prompt_calculus_studio.native_submission.prepare',side_effect=prepare):
            StageRunner.dispatch(self.runner,self.run,frame,stage)
            state['uses']['root']['prompt']='edited while waiting'
            callbacks[0]()
        self.assertEqual(prepared[0]['uses']['root']['prompt'],'frozen')


class JournalProjectionTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);self.store=StageStore(self.db)

    def test_live_progress_and_presence_do_not_decode_frozen_scenes(self):
        stack=[dict(kind='loop',owner='owner',done=2,feed=dict(total=10,plan={'large':'x'*100000}),ctx={'saved':{'scene':{'large':'x'*100000}}}),
               dict(kind='stage',key='stage',ctx={'saved':{'scene':{'large':'x'*100000}}})]
        run=self.store.add('run','w',server='server',plan={'stages':{}},stack=stack,status='running',message='pending')
        before=list(self.db.execute('SELECT body FROM stage_journal'))
        def small_decode(value,*a,**kw):
            self.assertLess(len(value),1000)
            return json.JSONDecoder().decode(value)
        with patch('prompt_calculus_studio.stage_store.json.loads',side_effect=small_decode):
            self.assertTrue(self.store.has_open_runs('w'));self.assertFalse(self.store.has_open_runs('other'))
            projected=self.store.progress_runs('w')[0]
        self.assertEqual(projected['stack'][0]['feed'],{'total':10})
        self.assertEqual(projected['stack'][0]['done'],2)
        self.assertEqual(projected['stack'][1]['key'],'stage')
        self.assertEqual(list(self.db.execute('SELECT body FROM stage_journal')),before)
        self.store.update(run['id'],status='complete')
        self.assertFalse(self.store.has_open_runs('w'));self.assertEqual(self.store.progress_runs('w'),[])

    def test_attempt_heads_follow_status_parameter_presence_and_workspace_ownership(self):
        stage='stage"quoted'
        item=self.store.add('attempt','w','run',stage=stage,status='submitted',round=2,parent='parent',cancel_requested=True,
            snapshot_state={'multi_output':{'stages':{stage:{'parameters':None}}},'large':'x'*100000})
        head=self.store.attempt_heads()[0]
        self.assertEqual((head['id'],head['owner'],head['workspace'],head['parent']),(item['id'],'run','w','parent'))
        self.assertTrue(head['cancel_requested']);self.assertFalse(head['has_parameters'])
        self.store.update(item['id'],snapshot_state={'multi_output':{'stages':{stage:{'parameters':{'patches':[]}}}}})
        self.assertTrue(self.store.attempt_heads()[0]['has_parameters'])
        self.store.update(item['id'],status='complete')
        self.assertEqual(self.store.attempt_heads(),[])
