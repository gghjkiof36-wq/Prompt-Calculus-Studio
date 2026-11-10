"""Click acceptance, frozen expansion and cursor withdrawal as durable operations."""
import copy
import json
import unittest
from unittest.mock import patch

from PySide6.QtTest import QTest
from prompt_calculus_studio import stage_batches, schedule_batch_actions as actions, prompt_sources
from prompt_calculus_studio.stage_runner import StageRunner, CAPACITY
from test_stage_enqueue_progress import scenario, finish


def journal(case):
    return list(case.runner.store.db.execute('SELECT id,body,position FROM stage_journal ORDER BY position'))


class BatchSnapshotTests(unittest.TestCase):
    def test_upstream_result_reference_resolves_each_outer_cycle(self):
        from test_084_stages import StageTests
        from prompt_calculus_studio import multi_output
        case=StageTests()
        try:
            case.setUp()
            upstream=case.stages()[0]
            source,items,config=case.image_batch(count=1)
            self.assertTrue(case.c.commit(lambda state:multi_output.connect(state,upstream,source,'image')))
            case.executor.graphs['flow']['5']['inputs']['batch_size']=2
            case.runner.start(2)
            self.assertTrue(case.runner.store.rows('batch_snapshot')[0]['deferred'])
            QTest.qWait(60)
            for _ in range(2):
                self.assertEqual(case.workflows()[-1],'flow')
                case.executor.finish(2)
                QTest.qWait(80)
                for _ in range(2):
                    self.assertEqual(case.workflows()[-1],'B',dict(notices=case.notices,heads=case.runner.store.batch_heads(case.w.state['workspace']),workflows=case.workflows()))
                    case.executor.finish()
                    QTest.qWait(80)
            self.assertEqual(case.workflows(),['flow','B','B']*2)
            self.assertEqual(len(case.runner.store.rows('entry')),4)
            self.assertEqual(case.runner.store.rows('batch_snapshot'),[])
            self.assertTrue(all(r['status']=='complete' for r in case.runner.store.rows('run')),case.notices)
        finally:
            case.tearDown();case.doCleanups()

    def test_one_hundred_items_finish_once_across_five_windows(self):
        with scenario(False) as (case, config):
            case.runner.start(100)
            QTest.qWait(50)
            for _ in range(100):
                if not case.executor.pending:QTest.qWait(50)
                self.assertTrue(case.executor.pending,case.notices)
                case.executor.finish()
            QTest.qWait(50)
            self.assertTrue(all(r['status']=='complete' for r in case.runner.store.rows('run')),case.notices)
            self.assertEqual(len(case.runner.store.rows('entry')),100)
            ids=[p['prompt_id'] for p in case.executor.submissions]
            self.assertEqual((len(ids),len(set(ids))), (100,100))
            self.assertEqual(sum(v['status']['status_str']=='success' for v in case.executor.history.values()),100)
            self.assertEqual(case.runner.store.rows('batch_snapshot'),[])

    def test_image_withdraw_restores_reservation_and_same_frozen_inputs(self):
        from test_084_stages import StageTests
        case=StageTests()
        try:
            case.setUp()
            source,items,config=case.image_batch(count=3)
            before=stage_batches.cursors(case.w.state,[source])
            case.runner.start(1)
            for _ in range(3):stage_batches.expand(case.runner,CAPACITY)
            first=[e['saved']['inputs'] for e in case.runner.store.rows('entry')]
            control=case.runner.store.rows('batch_control')[0]
            actions.cancel(case.runner,control['owner'],control['group'])
            self.assertEqual(stage_batches.cursors(case.w.state,[source]),before)
            case.runner.start(1)
            for _ in range(3):stage_batches.expand(case.runner,CAPACITY)
            self.assertEqual([e['saved']['inputs'] for e in case.runner.store.rows('entry')[3:]],first)
        finally:
            case.tearDown();case.doCleanups()

    def test_click_counts_then_bounded_expansion_and_content_cleanup(self):
        with scenario(False) as (case, config):
            case.runner.start(100)
            store=case.runner.store
            self.assertEqual(store.rows('entry'),[])
            self.assertEqual(len(store.rows('batch_snapshot')),1)
            group=store.schedule_groups(case.w.state['workspace'],config['scheduler'])[0]
            self.assertEqual((group['total'],group['pending'],group['serial']),(100,100,1))
            for _ in range(CAPACITY+1):stage_batches.expand(case.runner,CAPACITY)
            self.assertEqual(len(store.rows('entry')),CAPACITY)
            self.assertEqual(store.rows('batch_snapshot')[0]['expanded'],CAPACITY)
            # The acknowledged total is independent of the ready window.
            self.assertEqual(store.schedule_groups(case.w.state['workspace'],config['scheduler'])[0]['pending'],100)
            case.runner.cancel()

    def test_fully_expanded_snapshot_deleted_but_cursor_receipt_remains(self):
        with scenario(False) as (case, config):
            case.runner.start(5)
            for _ in range(5):stage_batches.expand(case.runner,CAPACITY)
            self.assertEqual(case.runner.store.rows('batch_snapshot'),[])
            self.assertEqual(len(case.runner.store.rows('entry')),5)
            control=case.runner.store.rows('batch_control')[0]
            self.assertEqual((control['total'],control['expanded']),(5,5))
            self.assertTrue(control['cursor_before'])
            self.assertNotIn('scene',control)

    def test_withdraw_after_expansion_restores_choices_and_keeps_removed_history(self):
        with scenario(False) as (case, config):
            before=copy.deepcopy(case.w.state)
            case.runner.start(5)
            for _ in range(5):stage_batches.expand(case.runner,CAPACITY)
            first=case.runner.store.rows('entry')
            control=case.runner.store.rows('batch_control')[0]
            actions.cancel(case.runner,config['scheduler'],control['group'])
            self.assertEqual(case.runner.store.schedule_groups(case.w.state['workspace'],config['scheduler']),[])
            self.assertEqual({e['status'] for e in case.runner.store.rows('entry')},{'removed'})
            self.assertEqual(stage_batches.cursors(case.w.state,control['cursor_keys']),stage_batches.cursors(before,control['cursor_keys']))
            case.runner.start(5)
            for _ in range(5):stage_batches.expand(case.runner,CAPACITY)
            second=case.runner.store.rows('entry')[5:]
            self.assertEqual([e['saved']['values'] for e in first],[e['saved']['values'] for e in second])
            self.assertTrue(any('已撤回第 1 組' in text for text in case.notices))

    def test_newer_group_or_started_item_prevents_rewind(self):
        with scenario(False) as (case, config):
            case.runner.start(2);case.runner.start(2)
            controls=case.runner.store.rows('batch_control')
            before=stage_batches.cursors(case.w.state,controls[0]['cursor_keys'])
            actions.cancel(case.runner,config['scheduler'],controls[0]['group'])
            self.assertEqual(stage_batches.cursors(case.w.state,controls[0]['cursor_keys']),before)
            QTest.qWait(50)
            self.assertTrue(case.executor.pending)
            actions.cancel(case.runner,config['scheduler'],controls[1]['group'])
            self.assertEqual(stage_batches.cursors(case.w.state,controls[1]['cursor_keys']),before)
            self.assertTrue(any('位置未退回' in text for text in case.notices))

    def test_withdraw_transaction_failure_restores_every_row_and_cursor(self):
        with scenario(False) as (case, config):
            case.runner.start(5)
            for _ in range(5):stage_batches.expand(case.runner,CAPACITY)
            control=case.runner.store.rows('batch_control')[0]
            before_rows=journal(case);before_state=copy.deepcopy(case.w.state)
            original=case.runner.store.patch
            def failed(ident,**values):
                result=original(ident,**values)
                if values.get('status')=='removed':raise OSError('withdraw write failure')
                return result
            with patch.object(case.runner.store,'patch',side_effect=failed),self.assertRaisesRegex(OSError,'withdraw write'):
                actions.cancel(case.runner,config['scheduler'],control['group'])
            self.assertEqual(journal(case),before_rows)
            self.assertEqual(case.w.state,before_state)

    def test_expansion_failure_does_not_advance_or_lose_accepted_content(self):
        with scenario(False) as (case, config):
            case.runner.start(5)
            before_rows=journal(case);before_state=copy.deepcopy(case.w.state)
            original=case.runner.store.patch
            def failed(ident,**values):
                result=original(ident,**values)
                if 'expanded' in values:raise OSError('expand write failure')
                return result
            with patch.object(case.runner.store,'patch',side_effect=failed),self.assertRaisesRegex(OSError,'expand write'):
                stage_batches.expand(case.runner,CAPACITY)
            self.assertEqual(journal(case),before_rows)
            self.assertEqual(case.w.state,before_state)
            result=finish(case)
            self.assertEqual((result['entries'],result['submitted'],result['completed']),(5,5,5))

    def test_restart_in_middle_of_expansion_continues_without_duplicates(self):
        with scenario(False) as (case, config):
            case.runner.start(7)
            with patch.object(stage_batches,'SLICE_SECONDS',0):
                stage_batches.expand(case.runner,CAPACITY)
            first=case.runner.store.rows('entry')[0]
            self.assertEqual(case.runner.store.rows('batch_snapshot')[0]['expanded'],1)
            case.runner.close()
            case.w.comfy.input_flow.chain=case.runner=StageRunner(case.w.comfy.input_flow)
            case.runner.resume(case.runner.runs(True)[0]['id'])
            result=finish(case)
            self.assertEqual((result['entries'],result['submitted'],result['completed']),(7,7,7))
            self.assertEqual(case.runner.store.read(first['id'])['saved'],first['saved'])
            ids=[a['prompt_id'] for a in case.runner.store.rows('attempt')]
            self.assertEqual(len(ids),len(set(ids)))

    def test_priority_and_bypass_before_first_entry_keep_serials(self):
        with scenario(False) as (case, config):
            case.runner.start(30);case.runner.start(30)
            store=case.runner.store;workspace=case.w.state['workspace'];key=config['scheduler']
            groups=store.schedule_groups(workspace,key)
            actions.prioritize(case.runner,key,groups[1]['id'])
            self.assertEqual([g['serial'] for g in store.schedule_groups(workspace,key)],[2,1])
            stage_batches.expand(case.runner,3)
            self.assertEqual({e['execution_group'] for e in store.rows('entry')},{groups[1]['id']})
            actions.bypass(case.runner,key,groups[1]['id'])
            stage_batches.expand(case.runner,3)
            self.assertIn(groups[0]['id'],{e['execution_group'] for e in store.rows('entry')})

    def test_unexpanded_second_group_cannot_overtake_first_group(self):
        with scenario(False) as (case, config),patch('prompt_calculus_studio.stage_runner.CAPACITY',3):
            case.runner.start(6);case.runner.start(2)
            groups=case.runner.store.schedule_groups(case.w.state['workspace'],config['scheduler'])
            stage_batches.expand(case.runner,3)
            self.assertEqual([g['id'] for g in case.runner.store.schedule_groups(case.w.state['workspace'],config['scheduler'])],[g['id'] for g in groups])
            result=finish(case)
            self.assertEqual(result['completed'],8)
            dispatched=[case.runner.store.read(a['parent'])['execution_group'] for a in case.runner.store.rows('attempt')]
            self.assertEqual(dispatched,[groups[0]['id']]*6+[groups[1]['id']]*2)

    def test_edit_and_undo_before_expansion_never_read_current_document(self):
        with scenario(False) as (case, config):
            case.runner.start(4)
            snapshot=case.runner.store.rows('batch_snapshot')[0]
            def edit(state):
                state['uses']['root']['variants']['groups'][0]['text']='edited after acceptance'
                prompt_sources.source(state,config['reader'])['root']['sequence']['groups'][0]['text']='edited reader'
            case.assertTrue(case.c.commit(edit))
            case.c.undo()
            case.assertTrue(case.c.commit(edit))
            self.assertEqual(case.runner.store.read(snapshot['id']),snapshot)
            # A different visible document is not an expansion input. The
            # owning runner's frozen row is sufficient for every local value.
            with patch.object(case.runner,'context',side_effect=AssertionError('live capture')):
                for _ in range(4):stage_batches.expand(case.runner,CAPACITY)
            values=[v['value'] for e in case.runner.store.rows('entry') for v in e['saved']['values'].values()]
            self.assertFalse(any('edited' in value for value in values))


if __name__=='__main__':unittest.main()
