"""Offscreen acceptance, all studio open routes, and cross-table failures."""
import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QObject
from PySide6.QtTest import QTest
from prompt_calculus_studio import journal_codec as codec, queue_history, storage_layout as layout
from prompt_calculus_studio.stage_context import execution_scope
from prompt_calculus_studio.stage_store import StageStore
from prompt_calculus_studio.stage_runner import StageRunner, CAPACITY
from journal_fixtures import legacy, snapshot
from test_stage_enqueue_progress import scenario


class JournalBehaviorTests(unittest.TestCase):
    def test_one_and_ten_entries_submit_and_complete_once_at_product_capacity(self):
        self.assertEqual(CAPACITY, 20)
        for count in (1, 10):
            with self.subTest(count=count), scenario(False) as (case, config):
                case.runner.start(count)
                QTest.qWait(50)
                for _ in range(count):
                    if not case.executor.pending:QTest.qWait(50)
                    self.assertTrue(case.executor.pending, case.notices)
                    case.executor.finish()
                QTest.qWait(50)
                counts = dict(requested=count, entries=len(case.runner.store.rows('entry')),
                              submitted=len(case.executor.submissions),
                              completed=sum(v['status']['status_str'] == 'success' for v in case.executor.history.values()))
                self.assertEqual(set(counts.values()), {count})
                self.assertTrue(all(r['status'] == 'complete' for r in case.runner.store.rows('run')), case.notices)
                print(json.dumps(dict(capacity=CAPACITY, **counts)), flush=True)

    def test_execution_failure_rolls_back_live_header_content_and_refs(self):
        with closing(sqlite3.connect(':memory:')) as db:
            store = StageStore(db)
            entry = store.add('entry', 'w', saved={'scene': {'text': 'old'}})
            window = SimpleNamespace(state={'cursor': {'position': 1}}, document_changed=Mock())
            for target, operation in (('stage_header', 'UPDATE'), ('stage_content', 'INSERT'), ('stage_content_ref', 'INSERT')):
                with self.subTest(target=target):
                    before = snapshot(db); live = copy.deepcopy(window.state)
                    db.execute(f"CREATE TEMP TRIGGER injected BEFORE {operation} ON {target} BEGIN SELECT RAISE(ABORT,'failure'); END")
                    with self.assertRaises(sqlite3.DatabaseError):
                        with execution_scope(window, store) as tx:
                            tx.register('cursor')
                            window.state['cursor']['position'] += 1
                            store.patch(entry['id'], saved={'scene': {'text': 'new'}})
                    self.assertEqual(window.state, live)
                    self.assertEqual(snapshot(db), before)
                    self.assertEqual(tx.changes, frozenset())
                    self.assertFalse(window.document_changed.called)
                    db.execute('DROP TRIGGER injected')
            with execution_scope(window, store) as tx:
                tx.register('cursor'); window.state['cursor']['position'] += 1
                store.patch(entry['id'], saved={'scene': {'text': 'new'}})
            self.assertEqual(tx.changes, frozenset({('cursor',)}))
            window.document_changed.assert_called_once_with(frozenset({'cursor'}), reason='execution', kind='content')

    def test_undo_failure_including_pause_control_gc_and_sort_is_one_transaction(self):
        from test_087_queue_history import QueueHistoryTests
        case = QueueHistoryTests()
        try:
            case.setUp()
            ids = [case.a['id'], case.b['id']]
            for ident in ids:case.store.patch(ident, saved={'scene': {'text': 'original'}})
            def edit():
                for ident in ids:case.store.patch(ident, saved={'scene': {'text': 'edited'}})
                case.store.reorder(case.run['id'], list(reversed(ids)))
            queue_history.perform(case.canvas, ids, edit)
            before = snapshot(case.db); history = copy.deepcopy(case.canvas.undo_stack)
            original = case.store.replace_record
            calls = []
            def fail(body, position):
                original(body, position); calls.append(body['id'])
                if len(calls) == 2:raise ValueError('injected after second replacement and GC')
            with patch.object(case.store, 'replace_record', side_effect=fail):case.undo()
            self.assertEqual(len(calls), 2)
            self.assertEqual(snapshot(case.db), before)
            self.assertEqual(case.canvas.undo_stack, history)
            self.assertEqual(case.canvas.redo_stack, [])
            case.undo()
            self.assertEqual([e['id'] for e in case.store.rows('entry')], ids)
            self.assertTrue(all(e['saved']['scene']['text'] == 'original' for e in case.store.rows('entry')))
            self.assertEqual(case.store.read(case.run['id'])['status'], 'paused')
            self.assertTrue(case.store.scheduler_paused('w', 'q'))
            case.redo()
            self.assertEqual([e['id'] for e in case.store.rows('entry')], list(reversed(ids)))
            self.assertTrue(all(e['saved']['scene']['text'] == 'edited' for e in case.store.rows('entry')))
        finally:case.doCleanups()

    def test_window_runner_document_and_runtime_routes_reject_old_database_unchanged(self):
        from prompt_calculus_studio.window import Window
        from prompt_calculus_studio.document_host import DesktopDocument
        from prompt_calculus_studio.workspace_runtime import WorkspaceRuntime, unfinished
        for entrance in ('window', 'runner', 'retarget', 'acquire', 'recover', 'history'):
            with self.subTest(entrance=entrance), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder); owner = directory / 'owner'; owner.mkdir()
                path = owner / 'studio.sqlite3'
                with closing(sqlite3.connect(path)) as db:legacy(db)
                before = hashlib.sha256(path.read_bytes()).hexdigest()
                notices = []
                binding = dict(token='owner', path=str(directory/'file'), document_id='file',
                               receipt={'sha256': 'hash'}, runtime_document='runtime')
                runtime = SimpleNamespace(directory=directory, executions={}, errors={}, rows=lambda: [binding],
                                          workspace=SimpleNamespace(data=directory, show_notice=notices.append))
                document = SimpleNamespace(path=directory/'file', key='file', receipt=SimpleNamespace(sha256='hash'), state=None)
                if entrance == 'recover':
                    WorkspaceRuntime.recover(runtime)
                    self.assertEqual(notices, [layout.OLD_MESSAGE])
                    self.assertEqual(runtime.errors, {'owner': layout.OLD_MESSAGE})
                else:
                    with self.assertRaises(layout.LayoutError) as caught:
                        if entrance == 'window':Window(owner)
                        elif entrance == 'runner':
                            with closing(sqlite3.connect(path)) as db:
                                client = QObject()
                                StageRunner(SimpleNamespace(client=client, window=SimpleNamespace(store=SimpleNamespace(db=db))))
                        elif entrance == 'acquire':WorkspaceRuntime.acquire(runtime, document)
                        elif entrance == 'history':WorkspaceRuntime.history_owner(runtime, binding)
                        else:
                            document.execution = SimpleNamespace(detach=Mock())
                            document.retired_executions = []; document.data = owner
                            document.runtimes = SimpleNamespace(acquire=lambda *args, **kwargs: None)
                            document.document = {'assets': []}
                            DesktopDocument.retarget_execution(document)
                    self.assertEqual(str(caught.exception), layout.OLD_MESSAGE)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        with closing(sqlite3.connect(':memory:')) as db:
            store = StageStore(db)
            item = store.add('input', 'w', sent=True, ended=False)
            self.assertTrue(unfinished(db))
            store.patch(item['id'], ended=True)
            self.assertFalse(unfinished(db))

    def test_preview_is_explicit_new_layout_and_read_library_remains_readonly(self):
        from prompt_calculus_studio.module_preview import PreviewRuntime
        preview = PreviewRuntime(None)
        try:
            self.assertTrue(layout.inspect_layout(preview.input_flow.chain.store.db))
            self.assertEqual(preview.input_flow.chain.store.rows('run'), [])
        finally:preview.close()
        # Extension route is deliberately independent of journal layout. The
        # existing integration suite exercises its document/revision response.
        import inspect
        from test_comfy_integration import Service
        code = inspect.getsource(Service.read_library)
        self.assertIn('mode=ro', code)
        self.assertNotIn('StageStore', code)
        self.assertNotIn('storage_layout', code)
        from prompt_calculus_studio.core import initial_state
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'studio.sqlite3'
            with closing(sqlite3.connect(path)) as db:
                legacy(db)
                db.execute('UPDATE document SET body=?', (json.dumps(initial_state()),)); db.commit()
            before = path.read_bytes()
            service = object.__new__(Service)
            old_result = service.read_library(path)
            self.assertEqual(path.read_bytes(), before)
            with closing(sqlite3.connect(path)) as db:layout.transform_journal(db)
            before = path.read_bytes()
            self.assertEqual(service.read_library(path), old_result)
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':unittest.main()
