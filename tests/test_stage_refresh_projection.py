"""Schedule display reads summaries while detail/edit owners keep full originals."""
import copy
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from prompt_calculus_studio.stage_runner import StageRunner
from prompt_calculus_studio.stage_store import StageStore


class EntryStatusProjectionTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.store=StageStore(self.db)
        self.runner=SimpleNamespace(store=self.store)
        self.run=self.store.add('run','workspace',status='paused',plan={'frozen':'x'*80000})
        self.root=self.entry('workspace',status='running')
        self.child=self.entry('workspace',status='running',parent=self.root['id'])

    def entry(self,workspace,**values):
        return self.store.add('entry',workspace,'owner',run=self.run['id'],scheduler='scheduler',
                              label='QA',saved={'scene':{'frozen':'x'*80000}},**values)

    def status(self,item):
        return StageRunner.entry_status(self.runner,item)

    def test_latest_descendant_and_cancel_precedence_preserve_original_bodies(self):
        old=self.store.add('attempt','workspace',self.run['id'],parent=self.child['id'],status='failed')
        latest=self.store.add('attempt','workspace',self.run['id'],parent=self.child['id'],status='submitted')
        self.store.update(old['id'],created=9999999);self.store.update(latest['id'],created=1)
        other=self.entry('workspace',status='running')
        self.store.add('attempt','workspace',self.run['id'],parent=other['id'],status='result_error')
        self.store.add('attempt','elsewhere',self.run['id'],parent=self.root['id'],status='failed')
        before=list(self.db.execute('SELECT id,body FROM stage_journal ORDER BY position'))
        self.assertEqual(self.status(self.root),'submitted')
        self.assertEqual(self.status(self.child),'submitted')
        self.assertEqual(list(self.db.execute('SELECT id,body FROM stage_journal ORDER BY position')),before)
        self.store.update(latest['id'],cancel_requested=True)
        self.assertEqual(self.status(self.root),'cancelling')
        self.store.update(latest['id'],cancel_requested=False,status='complete')
        self.assertEqual(self.status(self.root),'paused')
        self.store.update(self.run['id'],status='running')
        self.assertEqual(self.status(self.root),'running')

    def test_nonrunning_entries_return_without_reading_execution_history(self):
        with patch.object(self.store,'rows',side_effect=AssertionError('full history read')), \
             patch.object(self.store,'read',side_effect=AssertionError('full body read')):
            for status in ('waiting','paused','failed','cancelled','removed','retained','complete'):
                item=dict(self.root,status=status)
                self.assertEqual(self.status(item),status)

    def test_parent_cycle_is_bounded_and_does_not_include_unrelated_attempt(self):
        self.store.update(self.root['id'],parent=self.child['id'])
        self.store.add('attempt','workspace',self.run['id'],parent=self.child['id'],status='collecting')
        other=self.entry('workspace',status='running')
        self.store.update(other['id'],parent=other['id'])
        self.store.add('attempt','workspace',self.run['id'],parent=other['id'],status='failed')
        self.assertEqual(self.status(self.root),'collecting')

    def test_running_projection_does_not_fetch_complete_frozen_records(self):
        self.store.add('attempt','workspace',self.run['id'],parent=self.child['id'],
                       status='unconfirmed',snapshot_state={'original':'x'*80000})
        with patch.object(self.store,'rows',side_effect=AssertionError('full history read')), \
             patch.object(self.store,'read',side_effect=AssertionError('full body read')):
            self.assertEqual(self.status(self.root),'unconfirmed')

    def test_all_active_attempt_states_and_missing_run_fallback(self):
        attempt=self.store.add('attempt','workspace',self.run['id'],parent=self.root['id'],status='preparing')
        for status in ('preparing','submitted','collecting','failed','unconfirmed','result_error'):
            self.store.update(attempt['id'],status=status)
            self.assertEqual(self.status(self.root),status)
        self.store.update(attempt['id'],status='complete')
        self.db.execute('DELETE FROM stage_journal WHERE id=?',(self.run['id'],))
        self.assertEqual(self.status(self.root),'running')

    def test_latest_lookup_uses_parent_index_and_body_is_unchanged(self):
        for number in range(20):
            unrelated=self.entry('workspace',status='complete')
            self.store.add('attempt','workspace',self.run['id'],parent=unrelated['id'],status='complete',
                           snapshot_state={'frozen':'x'*80000})
        self.store.add('attempt','workspace',self.run['id'],parent=self.child['id'],status='submitted',
                       snapshot_state={'frozen':'x'*80000})
        before=list(self.db.execute('SELECT id,body FROM stage_journal ORDER BY position'))
        statements=[];self.db.set_trace_callback(statements.append)
        try:self.assertEqual(self.status(self.root),'submitted')
        finally:self.db.set_trace_callback(None)
        query=next(query for query in statements if 'WITH RECURSIVE' in query)
        plan=str(list(self.db.execute('EXPLAIN QUERY PLAN '+query)))
        self.assertIn('stage_parent',plan)
        self.assertEqual(plan.count('stage_parent (kind=? AND workspace=? AND <expr>=?)'),2)
        self.assertEqual(list(self.db.execute('SELECT id,body FROM stage_journal ORDER BY position')),before)


class EntryDisplayProjectionTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.store=StageStore(self.db)

    def add(self,**fields):
        values=dict(status='waiting',scheduler='s',run='r',parent=None,label='Saved item',saved={})
        values.update(fields)
        return self.store.add('entry','w','owner',**values)

    def test_image_precedence_and_batch_counts_match_existing_display(self):
        from prompt_calculus_studio.stage_widgets import entry_image
        first=dict(name='first',relative='first.png');second=dict(name='second',relative='second.png')
        cases=[{},dict(source=first,saved={'batches':{'b':[second,second]}}),
               dict(saved={'values':{'text':{'type':'text','value':'private long text'},
                                      'a':{'type':'image','value':first}},
                           'inputs':{'b':{'type':'image','value':second}}}),
               dict(saved={'inputs':{'b':{'type':'image','value':second}}}),
               dict(saved={'batches':{'empty':[],'first':[first],'later':[second,second]}}),
               dict(saved={'batches':{'empty':[]}}),
               dict(saved={'values':{'a':{'type':'image','value':{}}},
                           'inputs':{'b':{'type':'image','value':second}},'batches':{'b':[first]}})]
        originals=[self.add(**fields) for fields in cases]
        self.add(scheduler='different');self.add(status='complete')
        self.store.add('entry','elsewhere','owner',status='waiting',scheduler='s',label='Elsewhere',saved={})
        summaries=self.store.entry_summaries('w','s')
        self.assertEqual([r['id'] for r in summaries],[r['id'] for r in originals])
        for item,summary in zip(originals,summaries):
            image=entry_image(item);counts=[len(value) for value in item['saved'].get('batches',{}).values()]
            count=1 if item.get('source') else max(counts) if counts else 1 if image else None
            self.assertEqual(summary['image'],image);self.assertEqual(summary['image_count'],count)
            self.assertNotIn('saved',summary)
            self.assertEqual(self.store.read(item['id']),item)

    def test_display_does_not_decode_frozen_graphs_or_large_plain_text(self):
        original=self.add(saved={'scene':{'frozen':'f'*200000},
                                 'values':{'text':{'type':'text','value':'t'*200000}}})
        before=list(self.db.execute('SELECT id,body FROM stage_journal'))
        loaded=[];decode=json.loads
        def bounded(raw,*args,**kwargs):
            loaded.append(len(raw));self.assertLess(len(raw),1000)
            return decode(raw,*args,**kwargs)
        with patch('prompt_calculus_studio.stage_store.json.loads',side_effect=bounded):
            result=self.store.entry_summaries('w','s')
        self.assertEqual(len(result),1);self.assertTrue(loaded)
        self.assertEqual(list(self.db.execute('SELECT id,body FROM stage_journal')),before)
        self.assertEqual(self.store.read(original['id']),original)

    def test_new_index_preserves_old_rows_and_callers_transaction(self):
        original=self.add(status='complete')
        before=list(self.db.execute('SELECT id,body FROM stage_journal'))
        self.db.execute('DROP INDEX stage_parent');self.db.commit()
        self.db.execute('CREATE TABLE pending(value)')
        self.db.execute('INSERT INTO pending VALUES (1)')
        StageStore(self.db)
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(list(self.db.execute('SELECT id,body FROM stage_journal')),before)
        self.assertEqual([r[1] for r in self.db.execute('PRAGMA table_info(stage_journal)')],
                         ['id','kind','workspace','owner','position','body'])
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT * FROM pending').fetchall(),[])
        self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE name='stage_parent'").fetchone())
        self.assertEqual(self.store.read(original['id']),original)


import test_084_stages as fixture


class ScheduleRefreshProjectionTests(unittest.TestCase):
    setUp=fixture.StageTests.setUp
    tearDown=fixture.StageTests.tearDown

    def test_one_projection_per_entry_and_full_manual_detail_remains_available(self):
        from collections import Counter
        from PySide6.QtCore import Qt
        from prompt_calculus_studio.flow_data import add_scheduler
        keys=[]
        self.assertTrue(self.c.commit(lambda state:keys.append(add_scheduler(state))))
        key=keys[0];panel=self.c.flow_cards[key].panel.open_page().content;store=self.runner.store
        workspace=self.w.state['workspace']
        run=store.add('run',workspace,status='paused',server=self.w.comfy.url,plan={})
        saved={'scene':{'keep':'original frozen graph'},'values':{'text':{'type':'text','value':'original'}}}
        entries=[store.add('entry',workspace,'owner',run=run['id'],scheduler=key,label='Item '+str(i),
                           status='running' if i==0 else 'waiting',saved=saved) for i in range(3)]
        attempt=store.add('attempt',workspace,run['id'],parent=entries[0]['id'],status='submitted')
        panel.open_group('legacy:'+run['id']);panel.list.setCurrentRow(1)
        for status,label in (('submitted','已提交'),('collecting','保存結果'),('failed','失敗')):
            store.update(attempt['id'],status=status)
            with patch.object(self.runner,'entry_status',wraps=self.runner.entry_status) as project, \
                 patch.object(store,'read',side_effect=AssertionError('Refresh should not load full detail')):
                panel.refresh()
            self.assertEqual(Counter(call.args[0]['id'] for call in project.call_args_list),
                             Counter({item['id']:1 for item in entries}))
            self.assertIn(label,panel.list.item(0).text());self.assertIn('3 項',panel.summary.text())
            self.assertEqual(panel.list.currentItem().data(Qt.ItemDataRole.UserRole),entries[1]['id'])
        self.assertEqual(panel.selected(),entries[1])
        self.assertEqual(panel.entries(),entries)
        self.assertEqual(store.read(entries[0]['id'])['saved'],saved)
