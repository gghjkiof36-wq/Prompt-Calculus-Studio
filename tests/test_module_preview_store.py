"""Every picker kind uses real empty journal queries without touching user data."""
import hashlib
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from _qt_cleanup import close_window
from prompt_calculus_studio.function_picker import FUNCTIONS, FunctionPicker
from prompt_calculus_studio.module_preview import ModulePreview
from prompt_calculus_studio.stage_store import StageStore
from prompt_calculus_studio.window import Window

APP = QApplication.instance() or QApplication([])


def file_snapshot(root):
    return {p.relative_to(root).as_posix(): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob('*') if p.is_file()}


class ModulePreviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'PROMPT_STUDIO_V08': '1'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.data = Path(os.environ['PROMPT_STUDIO_DATA'])
        self.w = Window(self.data / self._testMethodName)
        self.w.state['settings'].update(online=False, comfy_enabled=False, reduce_motion=True)
        self.w.comfy.timer.stop()
        self.w.comfy.enabled = False
        self.errors = []
        self.w.error = self.errors.append
        hook = patch.object(sys, 'excepthook', lambda k, v, tb: self.errors.append(str(v)))
        hook.start()
        self.addCleanup(hook.stop)
        APP.processEvents()
        self.w.save_timer.stop()
        self.w.changes.stop()
        self.w.persist()

    def tearDown(self):
        close_window(self.w)
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(self.errors, [])

    def assert_closed(self, connection):
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')

    def test_all_picker_kinds_use_empty_memory_store_without_data_io(self):
        before = file_snapshot(self.data)
        connections, reads = [], []
        real_connect = sqlite3.connect
        def connect(database, *args, **kwargs):
            self.assertEqual(database, ':memory:')
            connection = real_connect(database, *args, **kwargs)
            connections.append(connection)
            return connection
        def deny_user_database(*args):
            reads.append(args)
            return sqlite3.SQLITE_DENY
        self.w.store.db.set_authorizer(deny_user_database)
        try:
            with patch('sqlite3.connect', side_effect=connect), \
                    patch.object(self.w.jobs, 'start', side_effect=AssertionError('Preview started a job')), \
                    patch.object(self.w.comfy, 'request', side_effect=AssertionError('Preview requested service work')):
                dialog = FunctionPicker(self.w.canvas)
                try:
                    dialog.show()
                    for index, (kind, *_rest) in enumerate(FUNCTIONS):
                        with self.subTest(kind=kind):
                            old = dialog.preview.context.window.comfy.input_flow.chain.store.db if index else None
                            dialog.list.setCurrentRow(index)
                            APP.processEvents()
                            self.assertIsNotNone(dialog.preview.card)
                            store = dialog.preview.context.window.comfy.input_flow.chain.store
                            self.assertIsInstance(store, StageStore)
                            self.assertEqual(store.db.execute('PRAGMA database_list').fetchone()[2], '')
                            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM stage_journal').fetchone()[0], 0)
                            if old is not None:
                                self.assert_closed(old)
                            if kind == 'schedule':
                                panel = dialog.preview.card.panel
                                self.assertEqual(panel.summary.text(), '沒有等待執行的項目')
                                self.assertFalse(panel.progress.isVisible())
                    dialog.reject()
                    for connection in connections:
                        self.assert_closed(connection)
                finally:
                    dialog.close()
                    dialog.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        finally:
            self.w.store.db.set_authorizer(None)
        self.assertEqual(len(connections), len(FUNCTIONS))
        self.assertEqual(reads, [])
        self.assertEqual(file_snapshot(self.data), before)

    def test_standalone_close_and_parent_destruction_release_connections(self):
        preview = ModulePreview(self.w.canvas)
        preview.show_kind('schedule')
        connection = preview.context.window.comfy.input_flow.chain.store.db
        preview.close()
        self.assert_closed(connection)
        preview.deleteLater()
        dialog = FunctionPicker(self.w.canvas)
        connection = dialog.preview.context.window.comfy.input_flow.chain.store.db
        dialog.deleteLater()  # QObject destruction need not send a child closeEvent.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assert_closed(connection)
