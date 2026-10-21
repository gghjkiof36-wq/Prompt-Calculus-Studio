"""The real shell consumes owner failures and keeps durable recovery records."""
import copy
import sqlite3
import unittest
from unittest.mock import patch

from prompt_calculus_studio.draft_cache import DraftCache
from prompt_calculus_studio.local_documents import io
import test_workspace_shell as fixture


class CleanupShellTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    setUp = fixture.WorkspaceShellTests.setUp
    tearDown = fixture.WorkspaceShellTests.tearDown
    open = fixture.WorkspaceShellTests.open
    events = fixture.WorkspaceShellTests.events

    def test_owner_save_error_reaches_shell_and_other_owner_finishes(self):
        first = self.open('Scene', retained=True)
        second = self.open('Action', retained=True)
        first_owner, second_owner = first.document.execution, second.document.execution
        expected = copy.deepcopy(first.document.state)
        notices = []
        self.w.show_notice = notices.append
        with patch.object(first_owner, 'persist', side_effect=sqlite3.OperationalError('synthetic owner save failure')):
            self.assertTrue(self.w.close())
        self.assertTrue(first_owner.closed)
        self.assertTrue(second_owner.closed)
        self.assertTrue(self.w.runtimes.closed)
        self.assertTrue(any('synthetic owner save failure' in notice for notice in notices))
        report = DraftCache(self.w.data).read('execution-close-errors')
        self.assertIn('synthetic owner save failure', report['error'])
        self.assertTrue(report['recovery_paths'])
        self.assertEqual(DraftCache(first_owner.store.directory).read('execution-close-state')['state'], expected)
        with self.assertRaises(sqlite3.ProgrammingError):second_owner.store.db.execute('SELECT 1')
        self.assertEqual(io.load(self.root, first.path.name).document['payload'], first.document.document['payload'])

    def test_failed_recovery_storage_keeps_window_and_can_retry(self):
        first = self.open('Scene', retained=True)
        second = self.open('Action', retained=True)
        owner = first.document.execution
        state = copy.deepcopy(owner.state)
        with patch.object(owner, 'persist', side_effect=sqlite3.OperationalError('synthetic save error')), patch.object(
                DraftCache, 'write', side_effect=OSError('synthetic full disk')):
            self.assertFalse(self.w.close())
        self.assertFalse(owner.closed)
        self.assertTrue(second.document.execution.closed)
        self.assertEqual(owner.state,state)
        self.assertTrue(self.w.close())
        self.assertTrue(owner.closed)
        report = DraftCache(self.w.data).read('execution-close-errors')
        self.assertIn('synthetic full disk',report['error'])
