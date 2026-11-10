"""Synchronous save batching, byte fidelity and strict validation boundaries."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from prompt_calculus_studio import content_history, core, workspace_scene
from prompt_calculus_studio.composition import node
from prompt_calculus_studio.state_loading import prepare_state


class StorageSaveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = core.Storage(temp.name)
        self.addCleanup(self.store.close)
        self.reader = sqlite3.connect(Path(temp.name) / 'studio.sqlite3')
        self.addCleanup(self.reader.close)
        self.state = prepare_state(core.initial_state(), multi=True)
        self.store.save(self.state)
        self.original = self.reader.execute('SELECT body FROM document').fetchone()[0]
        self.sql = []
        self.store.db.set_trace_callback(self.sql.append)

    def writes(self):
        return [s for s in self.sql if s.startswith('INSERT OR REPLACE INTO document')]

    def test_nested_saves_and_persist_write_once_before_outer_commit(self):
        from prompt_calculus_studio.window import Window
        cache = Mock()
        window = SimpleNamespace(store=self.store, state=self.state, close_cache=cache,
                                 remember_canvas_view=Mock(), notice=Mock())
        with self.store.transaction():
            with self.store.coalesce_saves():
                self.state['draft'] = 'first'
                self.store.save(self.state, commit=False)
                with self.store.transaction(), self.store.coalesce_saves():
                    self.state['draft'] = ''
                    self.store.save(self.state)
                self.assertTrue(Window.persist(window))
                self.assertEqual(self.writes(), [])
                cache.clear.assert_not_called()
            self.assertEqual(len(self.writes()), 1)
            self.assertEqual(self.reader.execute('SELECT body FROM document').fetchone()[0], self.original)
            self.assertEqual(json.loads(self.store.db.execute('SELECT body FROM document').fetchone()[0])['draft'], '')
            cache.clear.assert_not_called()
        cache.clear.assert_called_once_with('local-state')
        self.assertEqual(sum(s == 'COMMIT' for s in self.sql), 1)
        self.assertEqual(json.loads(self.reader.execute('SELECT body FROM document').fetchone()[0])['draft'], '')

    def test_sync_caches_are_visible_to_intermediate_readers_and_final_body(self):
        for view in ('canvas', 'list'):
            with self.subTest(view=view), self.store.transaction(), self.store.coalesce_saves():
                self.state.update(selection_view=view, draft='', draft_base='manual base')
                self.state['temporary'] = ['local']
                self.store.save(self.state)
                output = self.state['multi_output']['outputs'][self.state['multi_output']['current_output']]
                draft = output if view == 'canvas' else output['list_draft']
                self.assertEqual((draft['draft'], draft['draft_base']), ('', 'manual base'))
                self.assertEqual(output['list_state']['temporary'], ['local'])
                scene = self.state['workspace_scenes']['items'][self.state['workspace']]
                self.assertEqual(scene, workspace_scene.scene(self.state))
                self.assertIsNot(scene['multi_output'], self.state['multi_output'])
            self.assertEqual(self.store.db.execute('SELECT body FROM document').fetchone()[0],
                             json.dumps(self.state, ensure_ascii=False))

    def test_caught_nested_failure_keeps_requests_and_outer_failure_discards_them(self):
        with self.store.transaction(), self.store.coalesce_saves():
            try:
                with self.store.coalesce_saves():
                    self.state['draft'] = 'caught inner'
                    self.store.save(self.state)
                    raise ValueError('inner')
            except ValueError:
                pass
        self.assertEqual(len(self.writes()), 1)
        with self.assertRaisesRegex(OSError, 'outer'):
            with self.store.transaction(), self.store.coalesce_saves():
                self.state['draft'] = 'discarded'
                self.store.save(self.state)
                raise OSError('outer')
        self.assertFalse(self.store.save_pending)
        self.assertEqual(len(self.writes()), 1)
        self.state['draft'] = 'next'
        self.store.save(self.state)
        self.assertEqual(len(self.writes()), 2)

    def test_coalescing_requires_managed_transaction(self):
        with self.assertRaisesRegex(RuntimeError, 'managed transaction'):
            with self.store.coalesce_saves():
                pass

    def test_persist_does_not_stop_a_newer_edits_timer(self):
        from prompt_calculus_studio.window import Window
        timer = Mock()
        timer.timerId.return_value = 1
        window = SimpleNamespace(store=self.store, state=self.state, close_cache=Mock(), save_timer=timer,
                                 remember_canvas_view=Mock(), notice=Mock())
        with self.store.transaction():
            self.assertTrue(Window.persist(window))
            timer.timerId.return_value = 2
        timer.stop.assert_not_called()
        self.assertTrue(Window.persist(window))
        timer.stop.assert_called_once()

    def history(self):
        self.state['content_history'] = dict(version=1, entries=[dict(id='entry', label='edit', workspace='',
            time=1., content_changed=True, changes=[dict(kind='canvas', target='x', before=None, after=node('x', 'old'))])])
        return self.state['content_history']

    def test_same_identity_history_edits_and_byte_capacity_are_rechecked(self):
        journal = self.history()
        entry = journal['entries'][-1]
        self.store.save(self.state)
        entry['label'] = 1
        with self.assertRaisesRegex(ValueError, '^內容歷史格式無效。$'):
            self.store.save(self.state)
        entry['label'] = 'edit'
        entry['changes'][0]['after']['prompt'] = '中' * content_history.BYTE_LIMIT
        with self.assertRaisesRegex(ValueError, '^內容歷史超過容量限制。$'):
            self.store.save(self.state)
        entry['changes'][0]['after']['prompt'] = 'fixed'
        self.store.save(self.state)
        self.assertEqual(self.store.load()['content_history'], journal)

    def test_history_count_capacity_is_rejected_before_entry_errors(self):
        self.state['content_history'] = dict(version=1, entries=[None] * (content_history.LIMIT + 1))
        with self.assertRaisesRegex(ValueError, '^內容歷史超過容量限制。$'):
            self.store.save(self.state)

    def test_invalid_top_level_save_keeps_the_version_error(self):
        for state in ('', []):
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, '^不支援的備份版本。$'):
                self.store.save(state)

    def test_save_body_matches_default_encoder_in_every_history_position(self):
        self.history()
        for position in ('first', 'middle', 'last'):
            with self.subTest(position=position):
                state = copy.deepcopy(self.state)
                journal = state.pop('content_history')
                if position == 'first':state = dict(content_history=journal, **state)
                else:
                    state['content_history'] = journal
                    if position == 'middle':state['extension'] = {'text': '中文\n"\\', 'value': None}
                # Preserve the standard encoder's conversion of non-string keys.
                state[42] = 'extra'
                self.store.save(state)
                saved = self.store.db.execute('SELECT CAST(body AS BLOB) FROM document').fetchone()[0]
                self.assertEqual(saved, json.dumps(state, ensure_ascii=False).encode('utf-8'))

    def test_shared_history_and_library_nodes_are_checked_once(self):
        from prompt_calculus_studio import composition
        self.history()
        self.state['items'][0]['composition'] = node('library', 'valid')
        for i in range(2):workspace_scene.create(self.state, str(i), duplicate=True)
        with patch.object(content_history, 'validate', wraps=content_history.validate) as history, \
             patch.object(composition, 'validate_node', wraps=composition.validate_node) as roots:
            core.validate_state(self.state)
        self.assertEqual(history.call_count, 1)
        library = self.state['items'][0]['composition']
        self.assertEqual(sum(c.args[0] is library for c in roots.call_args_list), 1)

    def test_public_validation_does_not_trust_active_scene_and_keeps_error_order(self):
        second = workspace_scene.create(self.state, 'second', duplicate=True)
        active = self.state['workspace_scenes']['items'][self.state['workspace']]
        active['draft'] = 1
        with self.assertRaisesRegex(ValueError, '^手動稿格式錯誤。$'):
            core.validate_state(self.state)
        active['draft'] = None
        self.state['material_navigation'] = {}
        self.state['workspace_scenes']['items'][second]['draft'] = 1
        with self.assertRaisesRegex(ValueError, '^資料夾收納版本不支援，原資料保留。$'):
            core.validate_state(self.state)
        # A first-scene error precedes that shared tail.
        active['draft'] = 1
        with self.assertRaisesRegex(ValueError, '^手動稿格式錯誤。$'):
            core.validate_state(self.state)


class AcceptanceSaveTests(unittest.TestCase):
    # Same real Qt Execute/receipt fixture as the 05 B probes; only the final
    # synchronous Storage.save is failed. No timer/background save substitutes.
    from test_prompt_batches import BatchExecutionTests as _B
    setUp = _B.setUp
    tearDown = _B.tearDown
    stages = _B.stages
    click = _B.click
    workflows = _B.workflows
    submitted_texts = _B.submitted_texts
    pipeline = _B.pipeline
    setup_batch = _B.setup_batch

    def check_failure(self, count):
        from prompt_calculus_studio import prompt_sources
        self.setup_batch(length=count)
        self.w.store.save(self.w.state)
        before = copy.deepcopy(self.w.state)
        body = self.w.store.db.execute('SELECT body FROM document').fetchone()[0]
        original = self.w.store.save
        flushes = []
        def fail_final(state, **kw):
            if self.w.store._pending_saves is None:
                flushes.append(self.w.store.transaction_depth)
                raise OSError('injected final save failure')
            return original(state, **kw)
        with patch.object(self.w.store, 'save', side_effect=fail_final):
            self.click()
        self.assertEqual(len(flushes), 1)
        self.assertGreater(flushes[0], 0)
        self.assertEqual(self.runner.store.rows('entry'), [])
        self.assertEqual(self.w.state['uses'], before['uses'])
        self.assertEqual(prompt_sources.definitions(self.w.state), prompt_sources.definitions(before))
        self.assertEqual(self.w.store.db.execute('SELECT body FROM document').fetchone()[0], body)
        self.assertEqual(self.executor.submissions, [])
        self.click()
        from prompt_calculus_studio.stage_batches import expand
        for _ in range(count):expand(self.runner,20)
        self.assertEqual(len(self.runner.store.rows('entry')), count)

    def test_failed_final_save_n1_can_be_clicked_again(self):self.check_failure(1)
    def test_failed_final_save_n10_can_be_clicked_again(self):self.check_failure(10)


if __name__ == '__main__':unittest.main()
