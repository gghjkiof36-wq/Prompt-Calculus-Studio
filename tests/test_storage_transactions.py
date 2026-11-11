"""Shared connection durability, using a second connection as the observer."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from prompt_calculus_studio.core import Storage, initial_state
from prompt_calculus_studio.draft_cache import DraftCache
from prompt_calculus_studio.media import Catalog
from prompt_calculus_studio.stage_runner import StageRunner
from prompt_calculus_studio.stage_store import StageStore
from test_stage_parameters import config


class StorageTransactionTests(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.storage = Storage(area.name)
        self.addCleanup(self.storage.close)
        self.catalog = Catalog(self.storage)
        self.stage = StageStore(self.storage.db)
        self.reader = sqlite3.connect(Path(area.name) / 'studio.sqlite3')
        self.addCleanup(self.reader.close)
        self.state = initial_state()
        self.storage.save(self.state)
        self.run = self.stage.add('run', self.state['workspace'], status='paused', stack=[], plan={})
        self.entries = [self.entry('owner', str(i)) for i in range(3)]
        self.retained = self.entry('retained', 'held', status='retained')
        self.storage.cache('existing', [{'value': 'original'}])
        self.catalog.put('album', dict(id='album', name='fixture'))
        self.catalog.put('image', dict(id='image', name='fixture'), 'album')
        self.sql = []
        self.storage.db.set_trace_callback(self.sql.append)

    def entry(self, owner, label, status='waiting'):
        return self.stage.add('entry', self.state['workspace'], owner, run=self.run['id'],
                              scheduler='q', stages=['one'], status=status, label=label, saved={})

    def snapshot(self, db=None):
        db = self.reader if db is None else db
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: sorted(db.execute('SELECT * FROM "' + name + '"').fetchall(), key=repr) for name in tables}

    def write(self, name):
        if name == 'save':
            self.state['draft'] = 'changed'
            self.storage.save(self.state)
        elif name == 'cache':
            self.storage.cache('new', [{'value': 'changed'}])
        elif name == 'cached':
            with patch('prompt_calculus_studio.core.time.time', return_value=100):
                self.storage.cached('existing')
        elif name == 'clear_cache':
            self.storage.clear_cache()
        elif name == 'use':
            self.storage.use(self.state['workspace'], 'tag', {'value': 'tag'})
        elif name == 'reorder':
            self.stage.reorder('owner', [e['id'] for e in reversed(self.entries)])
        elif name == 'edit_parameters':
            self.stage.edit_parameters(self.entries[0]['id'], 'one', config(), {})
        elif name == 'copy_entry':
            self.stage.copy_entry(self.entries[0]['id'])
        elif name == 'restore_retained':
            self.stage.restore_retained(self.run)
        elif name == 'catalog_put':
            self.catalog.put('album', dict(id='second', name='new'))
        elif name == 'catalog_delete':
            self.catalog.delete('image')
        elif name == 'catalog_source':
            self.catalog.put_source('A' * 64, dict(provider='civitai', model_id=1, version_id=2))
        elif name == 'gallery_delete':
            from prompt_calculus_studio.pages import GalleryPage
            page = SimpleNamespace(catalog=self.catalog, album='album', folder_removal_busy=lambda: False,
                search=Mock(), refresh_albums=Mock(), _view_positions={},
                window=SimpleNamespace(state=self.state, recent=Mock()))
            with patch('prompt_calculus_studio.pages.ask', return_value=True):
                GalleryPage.delete_album(page)
        elif name == 'recent_trim':
            from prompt_calculus_studio.recent import RecentPage
            # Invoke the real completion callback; only the worker/Qt scheduling
            # are replaced. Both Catalog.put and the pruning SQL execute.
            def start(message, work, done, failed):
                done((dict(name='new', created=1000), '', None, False))
                return True
            page = SimpleNamespace(catalog=self.catalog, processing=False, saves=[],
                pending=[dict(path='synthetic', prompt_id='p', node_id='n', image={})],
                _preview_receipts={}, known=set(), pump_timer=Mock(), refresh_timer=Mock(), pump=Mock(),
                window=SimpleNamespace(store=self.storage, is_closed=lambda: False,
                    jobs=SimpleNamespace(active=False, start=start), show_latest_generated=Mock(), notice=Mock()))
            with patch('prompt_calculus_studio.recent.QTimer.singleShot'):
                RecentPage.pump(page)
        else:
            self.fail(name)

    def seed_writer(self, name):
        if name == 'recent_trim':
            with self.storage.transaction():
                for i in range(121):
                    self.catalog.put('recent', dict(id=f'old-{i}', created=i))
        self.sql.clear()

    def check_writer(self, name, rollback):
        self.seed_writer(name)
        before = self.snapshot()
        if rollback:
            with self.assertRaisesRegex(RuntimeError, 'outer failure'):
                with self.stage.transaction():
                    self.write(name)
                    self.assertNotEqual(self.snapshot(self.storage.db), before)
                    self.assertEqual(self.snapshot(), before)
                    self.assertNotIn('COMMIT', self.sql)
                    raise RuntimeError('outer failure')
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(self.snapshot(self.storage.db), before)
            self.assertNotIn('COMMIT', self.sql)
        else:
            self.write(name)
            self.assertNotEqual(self.snapshot(), before)
            self.assertEqual(self.snapshot(), self.snapshot(self.storage.db))
            self.assertIn('COMMIT', self.sql)
        self.assertEqual(self.stage.transaction_depth, 0)
        self.assertEqual(self.storage.transaction_depth, 0)
        self.assertFalse(self.storage.db.in_transaction)

    def test_three_levels_only_outer_commits_and_caught_inner_writes_survive(self):
        before = self.snapshot()
        with self.stage.transaction():
            self.assertEqual(self.storage.transaction_depth, 1)
            with self.storage.transaction():
                self.assertEqual(self.stage.transaction_depth, 2)
                try:
                    with self.stage.transaction():
                        self.assertEqual(self.storage.transaction_depth, 3)
                        self.write('use')
                        raise ValueError('caught inner')
                except ValueError:
                    pass
                self.assertEqual(self.stage.transaction_depth, 2)
            self.write('cache')
            self.assertEqual(self.snapshot(), before)
            self.assertNotIn('COMMIT', self.sql)
        self.assertEqual(self.sql.count('COMMIT'), 1)
        self.assertEqual(self.snapshot(), self.snapshot(self.storage.db))
        self.assertTrue(self.storage.usage(self.state['workspace']))
        self.assertEqual(self.stage.transaction_depth, 0)

    def test_shared_depth_across_stage_instances_and_storage_outer(self):
        second = StageStore(self.storage.db)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            with self.storage.transaction():
                with second.transaction():
                    self.assertEqual(self.stage.transaction_depth, 2)
                    self.stage.copy_entry(self.entries[0]['id'])
                raise ValueError('rollback both stores')
        self.assertEqual(self.snapshot(), before)

    def test_callbacks_order_caught_inner_and_reentrant_transaction(self):
        events = []
        def callback(label):
            self.assertEqual(self.stage.transaction_depth, 0)
            self.assertFalse(self.storage.db.in_transaction)
            self.assertEqual(self.snapshot(), self.snapshot(self.storage.db))
            events.append(label)
        self.storage.after_commit(lambda: callback('immediate'))
        with self.stage.transaction():
            self.write('use')
            self.storage.after_commit(lambda: callback('first'))
            try:
                with self.stage.transaction():
                    self.storage.after_commit(lambda: callback('inner'))
                    raise ValueError('caught')
            except ValueError:
                pass
            def reentrant():
                with self.storage.transaction():
                    self.write('cache')
                    self.storage.after_commit(lambda: callback('reentrant'))
            self.storage.after_commit(reentrant)
            self.storage.after_commit(lambda: callback('last'))
            self.assertEqual(events, ['immediate'])
        self.assertEqual(events, ['immediate', 'first', 'inner', 'reentrant', 'last'])

    def test_callbacks_discarded_on_base_exception_and_never_leak(self):
        called = Mock()
        before = self.snapshot()
        with self.assertRaises(KeyboardInterrupt):
            with self.stage.transaction():
                self.write('use')
                self.storage.after_commit(called)
                with self.stage.transaction():
                    raise KeyboardInterrupt()
        with self.storage.transaction():
            self.write('cache')
        called.assert_not_called()
        self.assertEqual(self.snapshot()['usage'], before['usage'])
        self.assertEqual(self.stage.transaction_depth, 0)

    def test_failed_commit_discards_callbacks_and_rolls_back(self):
        db = self.storage.db
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('CREATE TABLE parent (id INTEGER PRIMARY KEY)')
        db.execute('CREATE TABLE child (parent INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)')
        before = self.snapshot()
        called = Mock()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.storage.transaction():
                self.write('use')
                db.execute('INSERT INTO child VALUES(1)')
                self.storage.after_commit(called)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.stage.transaction_depth, 0)
        self.assertFalse(db.in_transaction)
        with self.storage.transaction():
            pass
        called.assert_not_called()

    def test_callback_failure_propagates_after_commit_without_replay(self):
        later = Mock()
        with self.assertRaisesRegex(ValueError, 'callback failure'):
            with self.storage.transaction():
                self.write('use')
                self.storage.after_commit(Mock(side_effect=ValueError('callback failure')))
                self.storage.after_commit(later)
        self.assertTrue(self.snapshot()['usage'])
        self.assertEqual(self.storage.transaction_depth, 0)
        with self.storage.transaction():
            pass
        later.assert_not_called()

    def test_commit_false_still_writes_now_without_committing(self):
        before = self.snapshot()
        self.state['draft'] = 'pending'
        self.storage.save(self.state, commit=False)
        self.assertEqual(self.snapshot(), before)
        self.assertNotEqual(self.snapshot(self.storage.db), before)
        with self.stage.transaction():
            pass
        self.assertEqual(self.snapshot(), self.snapshot(self.storage.db))

    def persist_window(self):
        from prompt_calculus_studio.window import Window
        cache = DraftCache(self.storage.directory)
        cache.write('local-state', {'retained': True})
        window = SimpleNamespace(store=self.storage, state=self.state, close_cache=cache,
                                 remember_canvas_view=Mock(), notice=Mock())
        return cache, lambda: Window.persist(window)

    def test_persist_failure_keeps_recovery_cache(self):
        cache, persist = self.persist_window()
        before = self.snapshot()
        self.state['draft'] = 'pending'
        with self.assertRaises(ValueError):
            with self.stage.transaction():
                self.assertTrue(persist())
                self.assertEqual(cache.read('local-state'), {'retained': True})
                raise ValueError('outer failed')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(cache.read('local-state'), {'retained': True})
        with self.storage.transaction():
            pass
        self.assertEqual(cache.read('local-state'), {'retained': True})

    def test_persist_success_clears_only_after_outer_commit(self):
        cache, persist = self.persist_window()
        self.state['draft'] = 'saved'
        with self.stage.transaction():
            self.assertTrue(persist())
            self.assertEqual(cache.read('local-state'), {'retained': True})
        self.assertIsNone(cache.read('local-state'))
        self.assertEqual(json.loads(self.reader.execute('SELECT body FROM document').fetchone()[0])['draft'], 'saved')

    def test_persist_outside_transaction_clears_immediately(self):
        cache, persist = self.persist_window()
        self.assertTrue(persist())
        self.assertIsNone(cache.read('local-state'))

    def test_accepting_restores_document_and_journal_on_default_save_failure(self):
        original = copy.deepcopy(self.state)
        before = self.snapshot()
        runner = SimpleNamespace(store=self.stage, window=SimpleNamespace(state=self.state))
        with self.assertRaises(ValueError):
            with StageRunner.accepting(runner) as transaction:
                transaction.register('draft')
                self.state['draft'] = 'changed'
                self.stage.copy_entry(self.entries[0]['id'])
                self.storage.save(self.state)
                raise ValueError('accept failed')
        self.assertEqual(self.state, original)
        self.assertEqual(self.snapshot(), before)

    def test_resume_all_writes_roll_back_at_each_failure_point(self):
        keys = ['q', 'q2']
        for key in keys:
            self.stage.set_scheduler_paused(self.state['workspace'], key, True)
        runner = SimpleNamespace(store=self.stage, window=SimpleNamespace(state=self.state), check_route=Mock(), scheduler_keys=lambda plan: keys,
            client=SimpleNamespace(dispatch_allowed=True), later=Mock(), notify=Mock())
        before = self.snapshot()
        original = self.stage.update
        for fail_at in (1, 2, 3, 4):
            calls = []
            def fail(ident, **values):
                result = original(ident, **values)
                calls.append(ident)
                if len(calls) == fail_at:
                    raise ValueError('injected resume failure')
                return result
            with self.subTest(write=fail_at), patch.object(self.stage, 'update', side_effect=fail):
                with self.assertRaisesRegex(ValueError, 'injected resume'):
                    StageRunner.resume(runner, self.run['id'])
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.snapshot(self.storage.db), before)
                runner.later.assert_not_called()
                runner.notify.assert_not_called()
        StageRunner.resume(runner, self.run['id'])
        self.assertEqual(self.stage.read(self.retained['id'])['status'], 'waiting')
        self.assertFalse(any(self.stage.scheduler_paused(self.state['workspace'], key) for key in keys))
        self.assertEqual(self.stage.read(self.run['id'])['status'], 'running')
        self.assertEqual(self.snapshot(), self.snapshot(self.storage.db))

    def test_order_dialog_multiple_reorders_and_removals_are_atomic(self):
        from prompt_calculus_studio.schedule_order_dialog import ScheduleOrderDialog
        other = [self.entry('other', str(i)) for i in range(3)]
        items = self.entries + other
        runner = SimpleNamespace(store=self.stage, client=SimpleNamespace(url='local'),
            window=SimpleNamespace(state=self.state), later=Mock(), notify=Mock())
        runner.remove_entry = lambda ident: StageRunner.remove_entry(runner, ident)
        canvas = SimpleNamespace(undo_stack=[], redo_stack=[], window=SimpleNamespace(state=self.state,
            store=self.storage, comfy=SimpleNamespace(input_flow=SimpleNamespace(chain=runner))))
        panel = SimpleNamespace(runner=runner, canvas=canvas, refresh=Mock(),
            entries=lambda: [e for e in self.stage.rows('entry') if e['owner'] in ('owner', 'other')])
        order = [e['id'] for e in (self.entries[1], self.entries[0], other[1], other[0])]
        dialog = SimpleNamespace(panel=panel, rows={e['id']: e for e in items},
            ordered_ids=lambda: order, error=Mock(), accept=Mock())
        before = self.snapshot()
        remove = runner.remove_entry
        reorder = self.stage.reorder
        for failure in ('second_reorder', 'second_remove'):
            calls = []
            def injected(*args):
                calls.append(args)
                result = (reorder if failure == 'second_reorder' else remove)(*args)
                if len(calls) == 2:
                    raise ValueError('injected order failure')
                return result
            target, method = (self.stage, 'reorder') if failure == 'second_reorder' else (runner, 'remove_entry')
            with self.subTest(failure=failure), patch.object(target, method, side_effect=injected):
                ScheduleOrderDialog.apply_changes(dialog)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.snapshot(self.storage.db), before)
                self.assertFalse(canvas.undo_stack)
                dialog.accept.assert_not_called()
                panel.refresh.assert_not_called()
        ScheduleOrderDialog.apply_changes(dialog)
        self.assertEqual(self.stage.waiting_ids('owner'), order[:2])
        self.assertEqual(self.stage.waiting_ids('other'), order[2:])
        self.assertEqual([self.stage.read(e['id'])['status'] for e in (self.entries[2], other[2])], ['removed'] * 2)
        self.assertEqual(len(canvas.undo_stack), 1)
        dialog.accept.assert_called_once()


def writer_test(name, rollback):
    def test(self):
        self.check_writer(name, rollback)
    return test


for _writer in ('save', 'cache', 'cached', 'clear_cache', 'use', 'reorder', 'edit_parameters',
                'copy_entry', 'restore_retained', 'catalog_put', 'catalog_delete', 'catalog_source',
                'gallery_delete', 'recent_trim'):
    for _rollback in (False, True):
        setattr(StorageTransactionTests, f'test_{_writer}_' + ('rollback' if _rollback else 'immediate_commit'),
                writer_test(_writer, _rollback))


if __name__ == '__main__':
    unittest.main()
