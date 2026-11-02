"""Execution groups, bounded display and isolated ordering over persistent rows."""
import copy
import sqlite3
import unittest
from unittest.mock import patch
from prompt_calculus_studio.stage_store import StageStore


class GroupStoreTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);self.store=StageStore(self.db)

    def entry(self,group,number,**kw):
        fields=dict(run='run',scheduler='q',execution_group=group,status='waiting',label='batch',parent=None,
                    saved={'values':{'p':{'type':'text','value':str(number)}},'scene':{'frozen':'x'*8000}})
        fields.update(kw)
        return self.store.add('entry','w','same-owner',**fields)

    def test_group_projection_and_paging_do_not_load_frozen_content(self):
        for i in range(235):self.entry('first',i)
        for i in range(120):self.entry('second',i)
        before=list(self.db.execute('SELECT id,body,position FROM stage_journal'))
        with patch.object(self.store,'rows',side_effect=AssertionError('Full history read')),patch.object(self.store,'read',side_effect=AssertionError('Full body read')):
            groups=self.store.schedule_groups('w','q')
            page=self.store.entry_summaries('w','q',group='first',limit=50,offset=200)
            self.assertEqual([g['total'] for g in groups],[235,120]);self.assertEqual(len(page),35)
            self.assertTrue(all('saved' not in r for r in page))
        self.assertEqual(list(self.db.execute('SELECT id,body,position FROM stage_journal')),before)
        self.assertEqual(StageStore(self.db).schedule_groups('w','q'),groups)

    def test_page_reorder_leaves_other_group_and_unseen_positions_unchanged(self):
        for i in range(70):self.entry('first',i)
        for i in range(15):self.entry('second',i)
        page=self.store.entry_summaries('w','q',group='first',limit=50)
        ids=[r['id'] for r in page];expected=dict(ids=ids,statuses=[r['status'] for r in page],offset=0)
        before=dict(self.db.execute('SELECT id,position FROM stage_journal'))
        self.store.reorder_page('w','q','first',list(reversed(ids)),expected)
        after=dict(self.db.execute('SELECT id,position FROM stage_journal'))
        self.assertEqual({k:v for k,v in after.items() if k not in ids},{k:v for k,v in before.items() if k not in ids})
        self.assertEqual([r['id'] for r in self.store.entry_summaries('w','q',group='first',limit=50)],list(reversed(ids)))
        with self.assertRaises(ValueError):self.store.reorder_page('w','q','first',ids,expected)
        self.assertEqual(dict(self.db.execute('SELECT id,position FROM stage_journal')),after)

    def test_unconfirmed_and_cross_owner_entries_cannot_be_reordered(self):
        a=self.entry('g',0);b=self.entry('g',1,status='running')
        expected=dict(ids=[a['id'],b['id']],statuses=['waiting','running'],offset=0)
        with self.assertRaises(ValueError):self.store.reorder_page('w','q','g',[b['id'],a['id']],expected)
        self.assertEqual(self.store.read(b['id']),b)

    def test_pending_feed_survives_a_temporarily_empty_materialized_queue(self):
        run=self.store.add('run','w',status='paused',execution_group='g',stack=[dict(kind='loop',owner='o',node={'key':'q'},
            ctx={'execution_group':'g'},feed={'next':100,'total':240})])
        self.assertEqual(self.store.schedule_groups('w','q')[0]['pending'],140)
        entry=self.entry('g',0,status='complete');groups=self.store.schedule_groups('w','q')
        self.assertEqual(groups[0]['total'],141)
        self.store.patch(run['id'],stack=[])
        self.assertFalse(self.store.schedule_groups('w','q'))
        self.assertEqual(self.store.schedule_groups('w','q',include='g')[0]['total'],1)
        self.assertEqual(self.store.read(entry['id']),entry)

    def test_legacy_group_is_a_projection_and_patch_keeps_null_and_snapshot(self):
        entry=self.entry(None,0);self.store.patch(entry['id'],source=None,error='')
        before=list(self.db.execute('SELECT body FROM stage_journal'))
        self.assertEqual(self.store.schedule_groups('w','q')[0]['id'],'legacy:run')
        self.assertEqual(list(self.db.execute('SELECT body FROM stage_journal')),before)
        self.assertEqual(self.store.read(entry['id'])['saved'],entry['saved'])
        self.assertIsNone(self.store.read(entry['id'])['source'])

    def test_undo_eligibility_uses_indexed_existence_not_full_history(self):
        from prompt_calculus_studio.queue_history import capture,untouched
        entries=[self.entry('g',i) for i in range(55)]
        values=capture(self.store,[e['id'] for e in entries[:50]])
        with patch.object(self.store,'rows',side_effect=AssertionError('Full history read')):
            self.assertTrue(untouched(self.store,values))
            self.store.add('attempt','w','run',parent=entries[0]['id'],status='unconfirmed')
            self.assertFalse(untouched(self.store,values))

    def test_group_order_and_bypass_persist_without_editing_frozen_rows(self):
        a=[self.entry('a',i) for i in range(2)];b=[self.entry('b',i) for i in range(2)]
        before=list(self.db.execute("SELECT body,position FROM stage_journal WHERE kind='entry' ORDER BY position"))
        self.store.reorder_groups('w','q',['b','a'],['a','b'])
        self.assertEqual(self.store.next_entry('same-owner')['id'],b[0]['id'])
        self.store.set_batch_control('w','q','b',bypassed=True)
        self.assertEqual(self.store.next_entry('same-owner')['id'],a[0]['id'])
        self.assertEqual(list(self.db.execute("SELECT body,position FROM stage_journal WHERE kind='entry' ORDER BY position")),before)
        reopened=StageStore(self.db)
        self.assertEqual([g['id'] for g in reopened.schedule_groups('w','q')],['b','a'])
        self.assertTrue(reopened.schedule_groups('w','q')[0]['bypassed'])
        with self.assertRaises(ValueError):self.store.reorder_groups('w','q',['a','b'],['a','b'])


import test_084_stages as fixture
from prompt_calculus_studio import multi_output as model
from prompt_calculus_studio.flow_data import add_scheduler,CAPACITY


class GroupExecutionTests(unittest.TestCase):
    setUp=fixture.StageTests.setUp
    tearDown=fixture.StageTests.tearDown
    stages=fixture.StageTests.stages
    click=fixture.StageTests.click
    image_batch=fixture.StageTests.image_batch

    def queue(self,count):
        stage=self.stages()[0];keys=[]
        def setup(state):
            key=add_scheduler(state);keys.append(key);model.connect(state,stage,key+'::flow','flow')
        self.assertTrue(self.c.commit(setup));self.controls.count.setValue(count);self.click()
        return keys[0]

    def test_two_execute_clicks_append_to_same_run_but_keep_frozen_groups(self):
        key=self.queue(120);store=self.runner.store;original=store.rows('entry')
        self.assertEqual(len(original),120)
        self.c.commit(lambda s:s['uses']['root'].update(prompt='second click'))
        self.controls.count.setValue(2);self.click()
        entries=store.rows('entry');groups=store.schedule_groups(self.w.state['workspace'],key)
        self.assertEqual([g['total'] for g in groups],[120,2]);self.assertEqual(len({e['run'] for e in entries}),1)
        self.assertEqual(entries[:120],original)
        self.assertEqual(entries[-1]['saved']['values'][self.clip]['value'],'second click')
        self.assertNotEqual(entries[0]['execution_group'],entries[-1]['execution_group'])
        self.controls.count.setValue(1000);self.controls.refresh();self.assertEqual(self.controls.count.value(),1000)
        before=store.rows('entry')
        with self.assertRaises(ValueError):self.runner.start(1001)
        self.assertEqual(store.rows('entry'),before)
        self.runner.timer.stop();self.assertEqual(StageStore(self.w.store.db).schedule_groups(self.w.state['workspace'],key),groups)

    def test_accept_failure_rolls_back_all_group_rows_and_document(self):
        key=self.queue(1);store=self.runner.store;before=store.rows('entry');state=copy.deepcopy(self.w.state)
        add=store.add;accepted=[]
        def fail(kind,*args,**kwargs):
            if kind=='entry':
                accepted.append(1)
                if len(accepted)==4:raise ValueError('fixture storage failure')
            return add(kind,*args,**kwargs)
        with patch.object(store,'add',side_effect=fail),self.assertRaises(ValueError):self.runner.start(120)
        self.assertEqual(store.rows('entry'),before);self.assertEqual(self.w.state,state)

    def test_automatic_batch_over_100_refills_inside_the_same_group(self):
        source,images,config=self.image_batch(count=105);self.click();store=self.runner.store
        groups=store.schedule_groups(self.w.state['workspace'],config['inner']);self.assertEqual(len(groups),1)
        self.assertEqual(groups[0]['total'],105);self.assertEqual(groups[0]['pending'],5)
        self.assertEqual(store.active_count(self.w.state['workspace'],config['inner']),CAPACITY)
        self.executor.finish()
        later=store.schedule_groups(self.w.state['workspace'],config['inner'])
        self.assertEqual(later[0]['id'],groups[0]['id']);self.assertEqual(later[0]['total'],105)
        self.assertEqual(later[0]['pending'],4)

    def test_later_outer_click_passes_its_group_to_nested_image_feed(self):
        source,images,config=self.image_batch(outer=True);self.click();self.click();store=self.runner.store
        outer=store.schedule_groups(self.w.state['workspace'],config['outer'])
        first=store.entry_summaries(self.w.state['workspace'],config['inner'])
        self.assertEqual(len(first),3)
        for _ in range(3):self.executor.finish()
        children=store.rows('entry',active=True)
        children=[e for e in children if e['scheduler']==config['inner']]
        self.assertEqual(len(children),3)
        self.assertTrue(all(e['execution_group']==outer[1]['id'] for e in children))

    def test_priority_changes_next_dispatch_but_not_current_native_job(self):
        from prompt_calculus_studio.schedule_batch_actions import prioritize
        key=self.queue(3);store=self.runner.store;first=store.rows('attempt')[0]
        self.c.commit(lambda s:s['uses']['root'].update(prompt='priority'))
        self.controls.count.setValue(2);self.click()
        groups=store.schedule_groups(self.w.state['workspace'],key)
        prioritize(self.runner,key,groups[1]['id'])
        self.assertEqual(len(self.executor.submissions),1)
        self.assertEqual(store.rows('attempt')[0],first)
        self.executor.finish()
        self.assertEqual(self.executor.submissions[-1]['prompt']['6']['inputs']['text'],'priority')
        self.executor.finish();self.executor.finish()
        self.assertNotEqual(self.executor.submissions[-1]['prompt']['6']['inputs']['text'],'priority')

    def test_bypass_keeps_group_until_restored_and_other_group_runs(self):
        from prompt_calculus_studio.schedule_batch_actions import bypass
        key=self.queue(2);store=self.runner.store
        self.c.commit(lambda s:s['uses']['root'].update(prompt='second'))
        self.controls.count.setValue(1);self.click()
        group=store.schedule_groups(self.w.state['workspace'],key)[0]['id']
        bypass(self.runner,key,group);self.executor.finish()
        self.assertEqual(self.executor.submissions[-1]['prompt']['6']['inputs']['text'],'second')
        self.executor.finish();self.assertFalse(self.executor.pending)
        self.assertTrue(store.schedule_groups(self.w.state['workspace'],key)[0]['bypassed'])
        bypass(self.runner,key,group);self.runner.pump()
        self.assertEqual(len(self.executor.submissions),3)

    def test_priority_can_promote_a_future_run_after_current_item_finishes(self):
        from prompt_calculus_studio.schedule_batch_actions import prioritize
        from prompt_calculus_studio.stage_model import compile_plan
        key=self.queue(2);store=self.runner.store
        self.c.commit(lambda s:s['uses']['root'].update(prompt='future run'))
        future=self.runner.create_run(compile_plan(self.w.state),1,status='waiting')
        prioritize(self.runner,key,future['execution_group']);self.runner.pump()
        self.assertEqual(len(self.executor.submissions),1)
        self.executor.finish()
        self.assertEqual(self.executor.submissions[-1]['prompt']['6']['inputs']['text'],'future run')
        self.executor.finish()
        from PySide6.QtTest import QTest
        QTest.qWait(100)
        self.assertEqual(len(self.executor.submissions),3,dict(notices=self.notices,runs=[(r['status'],r.get('pause_reason'),[(f['kind'],f.get('active'),f.get('job')) for f in r['stack']]) for r in self.runner.runs()],entries=[(r['execution_group'],r['status']) for r in store.rows('entry')]))

    def test_cancel_group_reconciles_current_and_keeps_sibling_batch(self):
        from prompt_calculus_studio.schedule_batch_actions import cancel
        key=self.queue(3);store=self.runner.store
        self.c.commit(lambda s:s['uses']['root'].update(prompt='keep'))
        self.controls.count.setValue(2);self.click()
        groups=store.schedule_groups(self.w.state['workspace'],key)
        siblings=[e for e in store.rows('entry') if e['execution_group']==groups[1]['id']]
        cancel(self.runner,key,groups[0]['id']);self.runner.pump()
        self.assertEqual(len(self.executor.cancelled),1)
        self.assertEqual(self.executor.submissions[-1]['prompt']['6']['inputs']['text'],'keep')
        self.assertTrue(all(e['status']=='cancelled' for e in store.rows('entry') if e['execution_group']==groups[0]['id']))
        for e in siblings:self.assertEqual(store.read(e['id'])['saved'],e['saved'])
        self.executor.finish();self.executor.finish()
        self.assertEqual(len(self.executor.submissions),3)

    def test_cancel_automatic_feed_does_not_refill_unmaterialized_tail(self):
        from prompt_calculus_studio.schedule_batch_actions import cancel
        source,images,config=self.image_batch(count=105);self.click();store=self.runner.store
        group=store.schedule_groups(self.w.state['workspace'],config['inner'])[0]['id']
        cancel(self.runner,config['inner'],group);self.runner.pump();self.executor.tick()
        self.assertEqual(len(self.executor.submissions),1)
        self.assertFalse(store.schedule_groups(self.w.state['workspace'],config['inner']))

    def test_cancel_releases_own_image_reservation_without_changing_selection(self):
        from prompt_calculus_studio.schedule_batch_actions import cancel
        from prompt_calculus_studio.core import SUPPORTED_STATE_VERSIONS
        self.w.comfy.snapshot_versions=list(SUPPORTED_STATE_VERSIONS)
        source,images,config=self.image_batch(count=4,outer=True);self.click();store=self.runner.store
        image=self.w.state['canvas_functions']['images'][source]
        self.assertIn('supply_reservation',image);index=image['index']
        group=store.schedule_groups(self.w.state['workspace'],config['outer'])[0]['id']
        cancel(self.runner,config['outer'],group);self.runner.pump();self.executor.tick()
        self.assertNotIn('supply_reservation',image);self.assertEqual(image['index'],index)
        self.assertFalse(store.schedule_groups(self.w.state['workspace'],config['inner']))
        self.click()
        self.assertEqual(len(store.entry_summaries(self.w.state['workspace'],config['inner'])),4)

    def test_lost_cancel_reply_does_not_dispatch_future_group_or_resubmit(self):
        from prompt_calculus_studio.schedule_batch_actions import cancel,prioritize
        from prompt_calculus_studio.stage_model import compile_plan
        key=self.queue(2);store=self.runner.store
        future=self.runner.create_run(compile_plan(self.w.state),1,status='waiting')
        first=store.schedule_groups(self.w.state['workspace'],key)[0]['id']
        prioritize(self.runner,key,future['execution_group'])
        request=self.w.comfy.request;cancel_calls=[]
        def lose(route,*args,**kw):
            if route=='workflow/native/cancel':cancel_calls.append(1);kw['failed']('fixture lost cancel reply')
            else:return request(route,*args,**kw)
        with patch.object(self.w.comfy,'request',side_effect=lose):
            cancel(self.runner,key,first);self.runner.pump();self.runner.pump()
        self.assertEqual(cancel_calls,[1]);self.assertEqual(len(self.executor.submissions),1)
        self.assertTrue(store.rows('attempt')[0]['cancel_requested'])
        self.executor.finish(error=True)
        from PySide6.QtTest import QTest
        QTest.qWait(100)
        self.assertEqual(len(self.executor.submissions),2)
        self.assertFalse(store.rows('attempt')[0]['cancel_requested'])

    def test_cards_and_list_mark_execution_and_reorder_actual_queue(self):
        from prompt_calculus_studio.stage_widgets import SchedulePageContent
        from prompt_calculus_studio.schedule_group_view import EXECUTING,BYPASSED
        key=self.queue(2);self.controls.count.setValue(1);self.click()
        panel=SchedulePageContent(self.c,key);self.addCleanup(panel.deleteLater)
        active=panel.groups[0]['id'];other=panel.groups[1]['id']
        self.assertTrue(panel.groups_list.item(0).data(EXECUTING))
        self.assertFalse(panel.groups_list.item(1).data(EXECUTING))
        panel.groups_list.move_row(other,0)
        self.assertEqual(panel.groups[0]['id'],other)
        panel.batch_action('bypass',other)
        self.assertTrue(panel.groups_list.item(0).data(BYPASSED))
        panel.set_group_view('list')
        self.assertTrue(panel.groups_list.item(0).data(BYPASSED))
        self.assertTrue(panel.groups_list.item(1).data(EXECUTING))
        panel.batch_action('bypass',other)
        self.assertFalse(panel.groups_list.item(0).data(BYPASSED))
        panel.open_group(active)
        from PySide6.QtCore import Qt
        self.assertTrue(panel.list.item(0).data(int(Qt.ItemDataRole.UserRole)+3))


if __name__=='__main__':unittest.main()
