"""Delayed notebook results must never navigate a different selected scope."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from PySide6.QtCore import QThreadPool, Qt
from prompt_calculus_studio.notebook_index import inventory
import test_workspace_panes as fixture


class NotebookContextTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def setUp(self):
        fixture.WorkspacePaneTests.setUp(self)
        self.a = self.root
        self.b = Path(self.area.name)/'other'
        self.b.mkdir()
        for root, text in ((self.a, 'ONLY_ROOT_A'), (self.b, 'ONLY_ROOT_B')):
            (root/'Same.md').write_text(text, encoding='utf-8')
        self.w.locations.roots.append(self.b)
        self.m = self.w.notebook
        self.m.root_timer.stop()
        self.records = {root: inventory(root, {}, {})[0] for root in (self.a, self.b)}
        # Retain real IndexJob signals, but release completions in an exact order.
        self.pool = patch('prompt_calculus_studio.notebook_manager.QThreadPool',
                          SimpleNamespace(globalInstance=lambda: SimpleNamespace(start=lambda job: None)))
        self.pool.start()
        self.m.show('search')
        self.m.panel.query.setText('ONLY_ROOT')
        self.m.refresh()

    def tearDown(self):
        self.m.stop()
        self.pool.stop()
        fixture.WorkspacePaneTests.tearDown(self)

    def deliver(self, job):
        job.signals.ready.emit(job, self.records[job.root], None, [])

    def select(self, root):
        self.m.panel.root.setCurrentIndex(self.m.panel.root.findData(str(root)))

    def test_root_change_rejects_old_completion_before_debounce_and_opens_correct_note(self):
        old = self.m.job
        self.select(self.b)
        self.assertTrue(old.cancelled.is_set())
        self.assertTrue(self.m.timer.isActive())
        self.deliver(old)
        self.assertFalse(self.m.panel.records)
        self.assertEqual(self.m.panel.results.topLevelItemCount(), 0)
        self.m.refresh()
        self.deliver(self.m.job)
        item = self.m.panel.results.topLevelItem(0)
        self.assertEqual(item.data(0, Qt.ItemDataRole.UserRole)['path'], str(self.b/'Same.md'))
        self.m.panel.activate(item)
        self.assertEqual(self.m.surface().page.path, self.b/'Same.md')
        self.assertEqual(self.m.surface().page.encoded(), b'ONLY_ROOT_B')
        self.assertEqual((self.a/'Same.md').read_bytes(), b'ONLY_ROOT_A')

    def test_return_to_same_root_does_not_revive_previous_job(self):
        old = self.m.job
        self.select(self.b)
        self.select(self.a)
        self.m.refresh()
        fresh = self.m.job
        self.deliver(old)
        self.assertIs(self.m.job, fresh)
        self.assertFalse(self.m.panel.records)
        self.deliver(fresh)
        self.assertIn(str(self.a/'Same.md'), self.m.panel.records)

    def test_query_case_mode_and_closed_manager_reject_stale_results(self):
        for change in (lambda: self.m.panel.query.setText('changed'),
                       lambda: self.m.panel.case.click(),
                       lambda: self.m.show('tags'),
                       lambda: self.m.show('files'),
                       self.m.stop):
            self.m.show('search')
            self.m.refresh()
            old = self.m.job
            change()
            self.deliver(old)
            self.assertFalse(self.m.panel.records)

    def test_current_note_identity_checked_even_before_poll(self):
        self.open('Same.md')
        self.m.show('links')
        old = self.m.job
        self.open('Notes.md', retained=True, new_tab=True)
        self.deliver(old)
        self.assertFalse(self.m.panel.records)

    def test_scope_change_clears_already_displayed_navigation(self):
        self.deliver(self.m.job)
        self.assertGreater(self.m.panel.results.topLevelItemCount(), 0)
        self.select(self.b)
        self.assertFalse(self.m.panel.records)
        self.assertEqual(self.m.panel.results.topLevelItemCount(), 0)


if __name__ == '__main__':
    unittest.main()
