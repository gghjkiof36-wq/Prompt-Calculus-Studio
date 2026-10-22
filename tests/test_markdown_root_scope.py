"""HP-02-001: logical root changes invalidate only affected reading views."""
import unittest
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.markdown_render import render
import test_markdown_embed_background as embedded_fixture


class RootScopeTests(unittest.TestCase):
    # Reuse the isolated real-worker fixture without inheriting its other tests.
    setUp = embedded_fixture.EmbeddedBackgroundTests.setUp
    tearDown = embedded_fixture.EmbeddedBackgroundTests.tearDown
    wait = embedded_fixture.EmbeddedBackgroundTests.wait
    open_parent = embedded_fixture.EmbeddedBackgroundTests.open_parent
    ready = embedded_fixture.EmbeddedBackgroundTests.ready
    hold_first_read = embedded_fixture.EmbeddedBackgroundTests.hold_first_read
    publish_observer = embedded_fixture.EmbeddedBackgroundTests.publish_observer

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def prepare(self):
        self.nested = self.root/'nested'
        self.nested.mkdir()
        self.parent = self.nested/'Scope.md'
        self.parent.write_bytes(b'![[ScopeAlias]]\n')
        self.child.write_bytes(b'---\naliases: [ScopeAlias]\n---\nBROAD_ROOT_ONLY\n')
        self.other = self.root/'Other.md'
        self.other.write_bytes(b'Other\n')
        self.unrelated = self.root.parent/'unrelated'
        self.unrelated.mkdir()
        self.open_parent()
        self.original = self.parent.read_bytes(), self.child.read_bytes()
        self.buffer = self.surface.page.buffer
        self.snapshot = self.buffer.snapshot()

    def assert_source_preserved(self):
        self.assertEqual((self.parent.read_bytes(), self.child.read_bytes()), self.original)
        self.assertIs(self.surface.page.buffer, self.buffer)
        self.assertIs(self.buffer.snapshot(), self.snapshot)
        self.assertFalse(self.surface.page.dirty)

    def start_reading(self):
        self.prepare()
        self.surface.set_mode('read')
        self.wait(lambda: self.ready('BROAD_ROOT_ONLY'))
        return self.surface.reader, self.surface.reader.rich

    def test_root_change_before_first_publication_rejects_old_worker(self):
        self.prepare()
        controller, entered, release, _ = self.hold_first_read()
        bodies = []
        with controller, self.publish_observer(bodies):
            self.surface.set_mode('read')
            self.wait(entered.is_set)
            rich = self.surface.reader.rich
            self.assertEqual(rich.read_request['root'], self.root)
            self.assertTrue(self.window.add_root(self.nested))
            release.set()
            self.wait(lambda: rich.projection is not None and rich.render_state == 'ready'
                      and rich.presented_version == rich.version)
        expected = render(self.snapshot.text, self.parent, self.nested).body
        self.assertEqual(rich.projection.body, expected)
        self.assertFalse(any('BROAD_ROOT_ONLY' in body for body in bodies), bodies)
        self.assertEqual(rich.read_request['root'], self.nested)
        self.assert_source_preserved()

    def test_published_reader_refreshes_on_add_and_remove_root(self):
        reader, rich = self.start_reading()
        history = list(self.window.history)
        self.assertTrue(self.window.add_root(self.nested))
        self.wait(lambda: self.ready('找不到筆記'))
        self.assertNotIn('BROAD_ROOT_ONLY', rich.projection.body)
        self.assertTrue(self.window.remove_root(self.nested))
        self.wait(lambda: self.ready('BROAD_ROOT_ONLY'))
        self.assertEqual(rich.read_request['root'], self.root)
        self.assertEqual(self.window.history, history)
        self.assert_source_preserved()

    def test_hidden_reader_invalidates_without_starting_replacement(self):
        reader, rich = self.start_reading()
        other = self.window.open_path(self.other, retained=True, new_tab=True)
        self.assertFalse(reader.isVisible())
        for operation, marker in ((lambda: self.window.add_root(self.nested), '找不到筆記'),
                                  (lambda: self.window.remove_root(self.nested), 'BROAD_ROOT_ONLY')):
            with self.subTest(marker=marker):
                version, projection = rich.version, rich.projection
                with patch.object(rich.projection_worker, 'request', wraps=rich.projection_worker.request) as request:
                    self.assertTrue(operation())
                    self.assertIsNone(reader._render_key)
                    QTest.qWait(250)
                    request.assert_not_called()
                    self.assertEqual(rich.version, version)
                    self.assertIs(rich.projection, projection)
                self.window.focus_tab(self.tab)
                self.wait(lambda: self.ready(marker))
                self.window.focus_tab(other)
        self.assert_source_preserved()

    def test_unrelated_duplicate_and_rolled_back_roots_preserve_cache(self):
        reader, rich = self.start_reading()
        version, projection, key = rich.version, rich.projection, reader._render_key
        self.assertTrue(self.window.add_root(self.unrelated))
        self.assertTrue(self.window.add_root(self.root))
        self.assertTrue(self.window.remove_root(self.unrelated))
        for operation in (lambda: self.window.add_root(self.nested),
                          lambda: self.window.remove_root(self.root)):
            with patch.object(self.window, 'save_locations', side_effect=OSError('scope save failed')):
                self.assertFalse(operation())
            self.assertEqual(self.errors.pop(), 'scope save failed')
        QTest.qWait(250)
        self.assertEqual(rich.version, version)
        self.assertIs(rich.projection, projection)
        self.assertEqual(reader._render_key, key)
        self.assertEqual(reader.render_key(), key)
        self.assert_source_preserved()

    def test_root_identity_guards_cache_and_late_callbacks_without_notification(self):
        reader, rich = self.start_reading()
        version, key = rich.version, reader.render_key()
        # Hold only the UI notification: identity checks must independently
        # reject callbacks already queued before the successful root change.
        with patch.object(reader, 'root_changed'):
            self.assertTrue(self.window.add_root(self.nested))
        self.assertFalse(rich.read_request['invalid'])
        self.assertNotEqual(reader.render_key(), key)
        self.assertFalse(rich.request_current(version, inputs=False, mode=False))
        self.assertFalse(rich.presentation_current(version))
        reader.root_changed()
        self.wait(lambda: self.ready('找不到筆記'))
        self.assert_source_preserved()


if __name__ == '__main__':
    unittest.main()
