import copy
import json
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace,MethodType
from test_multi_output import workspace,workflow
from prompt_calculus_studio import clip_flow,workflow_flow,multi_output
from prompt_calculus_studio.core import Storage,validate_state
from prompt_calculus_studio.workflow_manager import WorkflowManager
from prompt_calculus_studio.workflow_deletion import prepare_remove,prepare_restore,commit_change


class WorkflowDeletionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.store=Storage(tmp.name);self.addCleanup(self.store.db.close)
        self.store.db.execute('CREATE TABLE workflow_deletions (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        state,_,output=workspace();self.state=workflow_flow.upgrade(clip_flow.upgrade(state))
        self.a=next(iter(self.state['multi_output']['clip_inputs']))
        self.state['generation']['profiles'].append(workflow('B'))
        self.b=clip_flow.add(self.state);multi_output.connect(self.state,output,self.b,'clip');clip_flow.set_binding(self.state,'B',self.b,('6','text'))
        self.store.save(self.state);self.notices=[]
        panel=SimpleNamespace(changed=lambda:None)
        self.window=SimpleNamespace(state=self.state,store=self.store,generation_panel=panel,notice=self.notices.append,comfy=SimpleNamespace(epoch=0,url='http://127.0.0.1:8188',stopped=False))
        self.manager=SimpleNamespace(window=self.window,loading=False,request_serial=0,details=lambda:None,rebuild=lambda:None,undo_button=SimpleNamespace(setEnabled=lambda value:None))
        for name in ('operation_failed','finish_remove','finish_restore','undo_remove','operation_context','operation_current'):
            setattr(self.manager,name,MethodType(getattr(WorkflowManager,name),self.manager))

    def journal(self):return self.store.db.execute('SELECT id,body FROM workflow_deletions').fetchall()

    def test_delete_a_clears_only_its_selection_and_b_remains_executable(self):
        protected=copy.deepcopy(self.state);self.manager.finish_remove('flow')
        self.assertEqual([p['id'] for p in workflow_flow.execution_profiles(self.state)],['B'])
        self.assertIsNone(self.state['multi_output']['clip_inputs'][self.a]['workflow'])
        self.assertEqual(self.state['multi_output']['clip_inputs'][self.b]['workflow'],'B')
        for field in ('outputs','canvases','workflow_order','connections'):self.assertEqual(self.state['multi_output'][field],protected['multi_output'][field])
        self.assertEqual(self.state['uses'],protected['uses']);validate_state(self.store.load());self.assertEqual(len(self.journal()),1)

    def test_missing_clip_restore_refused_without_poisoning_state_database_or_journal(self):
        self.manager.finish_remove('flow');clip_flow.remove(self.state,self.a);self.store.save(self.state)
        before=copy.deepcopy(self.state);saved=self.store.db.execute('SELECT body FROM document').fetchone();journal=self.journal()
        self.manager.finish_restore();self.assertEqual(self.state,before);self.assertEqual(self.journal(),journal)
        self.assertEqual(self.store.db.execute('SELECT body FROM document').fetchone(),saved)
        self.assertTrue(self.notices);self.store.save(self.state)

    def test_database_failure_rolls_back_document_and_journal_and_live_state(self):
        before=copy.deepcopy(self.state);saved=self.store.db.execute('SELECT body FROM document').fetchone()
        self.store.db.execute("CREATE TRIGGER fail_document BEFORE INSERT ON document BEGIN SELECT RAISE(ABORT,'synthetic disk failure'); END")
        self.manager.finish_remove('flow');self.assertEqual(self.state,before);self.assertFalse(self.journal())
        self.assertEqual(self.store.db.execute('SELECT body FROM document').fetchone(),saved)
        self.store.db.execute('DROP TRIGGER fail_document');self.manager.finish_remove('flow');before=copy.deepcopy(self.state);journal=self.journal()
        self.store.db.execute("CREATE TRIGGER fail_document BEFORE INSERT ON document BEGIN SELECT RAISE(ABORT,'synthetic disk failure'); END")
        self.manager.finish_restore();self.assertEqual(self.state,before);self.assertEqual(self.journal(),journal)

    def test_preparing_candidates_never_mutates_callers_and_stale_journal_cannot_be_consumed(self):
        before=copy.deepcopy(self.state);candidate,record=prepare_remove(self.state,'flow');self.assertEqual(self.state,before)
        commit_change(self.store,candidate,record=record)
        restore=prepare_restore(candidate,record);self.assertEqual(self.state,before)
        changed=copy.deepcopy(record);changed['chosen']={}
        with self.assertRaisesRegex(ValueError,'紀錄'):commit_change(self.store,restore,consume=changed)
        self.assertEqual(len(self.journal()),1)

    def test_remote_restore_preflight_and_changed_local_state_do_not_replay_completed_move(self):
        calls=[]
        self.manager.catalog=SimpleNamespace(restore=lambda record,done,failed:calls.append((record,done)))
        self.manager.finish_remove('flow',dict(server=self.window.comfy.url,path='A.json',trash='trash/A.json'))
        self.manager.undo_remove();self.assertEqual(len(calls),1)
        clip_flow.remove(self.state,self.a);self.store.save(self.state);before=copy.deepcopy(self.state)
        calls[0][1]()
        self.assertEqual(self.state,before);self.assertIsNone(self.manager.deleted['remote']);self.assertEqual(len(self.journal()),1)
        self.manager.undo_remove();self.assertEqual(len(calls),1);self.assertEqual(self.state,before)

    def test_late_remote_callback_after_server_change_cannot_touch_local_state_or_log(self):
        calls=[];self.manager.catalog=SimpleNamespace(restore=lambda record,done,failed:calls.append(done))
        self.manager.finish_remove('flow',dict(server=self.window.comfy.url,path='A.json',trash='trash/A.json'))
        self.manager.undo_remove();before=copy.deepcopy(self.state);journal=self.journal()
        self.window.comfy.epoch+=1;self.window.comfy.url='http://127.0.0.1:8189';calls[0]()
        self.assertEqual(self.state,before);self.assertEqual(self.journal(),journal)

    def test_saved_reopened_restore_recovers_only_original_a_choices_and_preserves_edits(self):
        # Both CLIPs retain bindings for A and B, with different current choices.
        clip_flow.set_binding(self.state,'B',self.a,('7','text'))
        clip_flow.set_binding(self.state,'flow',self.a,('6','text'))
        clip_flow.set_binding(self.state,'flow',self.b,('8','text_g'))
        clip_flow.set_binding(self.state,'B',self.b,('6','text'))
        before=copy.deepcopy(self.state);self.manager.finish_remove('flow')
        record=json.loads(self.journal()[0][1])
        expected={k:'flow' for k,c in before['multi_output']['clip_inputs'].items() if c.get('workflow')=='flow'}
        self.assertEqual(record['clip_workflows'],expected);self.assertNotIn(self.b,expected)
        self.state['draft']='manual after deletion';self.store.save(self.state)
        directory=self.store.directory;self.store.db.close();self.store=Storage(directory);self.addCleanup(self.store.db.close)
        self.state=self.store.load();self.window.store=self.store;self.window.state=self.state
        self.manager.deleted=json.loads(self.journal()[0][1]);after_delete=copy.deepcopy(self.state)
        self.manager.finish_restore();self.assertFalse(self.journal())
        self.assertEqual({k:c.get('workflow') for k,c in self.state['multi_output']['clip_inputs'].items()},
                         {k:c.get('workflow') for k,c in before['multi_output']['clip_inputs'].items()})
        self.assertEqual(self.state['draft'],'manual after deletion')
        for field in ('outputs','canvases','workflow_order','connections'):
            self.assertEqual(self.state['multi_output'][field],after_delete['multi_output'][field])
        self.assertEqual(self.state['uses'],before['uses'])
        self.assertEqual([p['id'] for p in workflow_flow.execution_profiles(self.state)],['flow','B'])
        self.assertEqual(self.store.load()['multi_output'],self.state['multi_output'])

    def test_old_missing_record_does_not_guess_but_new_empty_record_needs_no_warning(self):
        for legacy in (True,False):
            with self.subTest(legacy=legacy):
                original=copy.deepcopy(self.state)
                for clip in self.state['multi_output']['clip_inputs'].values():
                    if clip.get('workflow')=='flow':clip['workflow']=None
                self.manager.finish_remove('flow');self.assertEqual(self.manager.deleted['clip_workflows'],{})
                if legacy:
                    del self.manager.deleted['clip_workflows']
                    with self.store.db:self.store.db.execute('UPDATE workflow_deletions SET body=?',(json.dumps(self.manager.deleted),))
                self.notices.clear();self.manager.finish_restore();self.assertFalse(self.journal())
                self.assertIsNone(self.state['multi_output']['clip_inputs'][self.a]['workflow'])
                self.assertEqual(self.state['multi_output']['clip_inputs'][self.b]['workflow'],'B')
                self.assertEqual(any('舊紀錄' in v for v in self.notices),legacy)
                self.state.clear();self.state.update(original);self.store.save(self.state)

    def assert_restore_refused(self):
        before=copy.deepcopy(self.state);saved=self.store.db.execute('SELECT body FROM document').fetchone();journal=self.journal()
        self.notices.clear();self.manager.finish_restore()
        self.assertEqual(self.state,before);self.assertEqual(self.journal(),journal)
        self.assertEqual(self.store.db.execute('SELECT body FROM document').fetchone(),saved)
        self.assertTrue(self.notices)

    def test_reselected_b_or_occupied_destination_refuses_entire_restore(self):
        self.manager.finish_remove('flow');blank=copy.deepcopy(self.state)
        clip_flow.set_binding(self.state,'B',self.a,('7','text'));self.store.save(self.state)
        self.assert_restore_refused();self.assertEqual(self.state['multi_output']['clip_inputs'][self.a]['workflow'],'B')
        self.state.clear();self.state.update(blank)
        self.state['multi_output']['bindings'].append(dict(workflow='flow',clip=self.b,node='6',field='text'))
        self.store.save(self.state);self.assert_restore_refused()

    def test_malformed_selection_record_and_invalid_target_preserve_journal(self):
        self.manager.finish_remove('flow');valid=copy.deepcopy(self.manager.deleted)
        records=[]
        for bad in (None,[],False,'A',{self.a:None},{self.a:'B'},{'':'flow'},{1:'flow'},{str(i):'flow' for i in range(101)}):
            records.append(dict(copy.deepcopy(valid),clip_workflows=bad))
        missing=copy.deepcopy(valid);missing['bindings']=[b for b in missing['bindings'] if b['clip']!=self.a];records.append(missing)
        invalid=copy.deepcopy(valid);del invalid['profile']['graph']['6'];records.append(invalid)
        linked=copy.deepcopy(valid);linked['profile']['graph']['6']['inputs']['text']=['8',0];records.append(linked)
        for record in records:
            with self.subTest(record=record.get('clip_workflows')):
                self.manager.deleted=record
                with self.store.db:self.store.db.execute('UPDATE workflow_deletions SET body=?',(json.dumps(record),))
                self.assert_restore_refused()
