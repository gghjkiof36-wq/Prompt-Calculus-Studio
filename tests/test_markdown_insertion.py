"""Shared-view input and persistence during a real asynchronous HTML paste."""
import time
import unittest

from PySide6.QtCore import QMimeData, QTimer
from PySide6.QtTest import QTest

from prompt_calculus_studio.markdown_source import utf16_position
import test_workspace_panes as fixture


class InsertionViewTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    open = fixture.WorkspacePaneTests.open
    events = fixture.WorkspacePaneTests.events

    def paste_then(self, action):
        path = self.root/'Notes.md'
        original_bytes = b'\xef\xbb\xbf' + '原文\r\n保留末行\n'.encode('utf-8')
        path.write_bytes(original_bytes)
        tab = self.open('Notes.md', new_tab=True)
        buffer = tab.widget.buffer
        original = buffer.snapshot().text
        initial_chars = buffer.text.characterCount()
        state = {}
        poll = QTimer(self.w)
        poll.setInterval(0)

        def after_partial_chunk():
            insertion = buffer.mutation
            if insertion is None or buffer.text.characterCount() <= initial_chars:
                return
            poll.stop()
            try:
                # Observe actual progress; never stop or alter the product timer.
                self.assertLess(buffer.text.characterCount(),
                                utf16_position(insertion.result, len(insertion.result))+1)
                state['pasted'] = insertion.result
                action(tab, state)
            except Exception as error:
                state['error'] = error

        poll.timeout.connect(after_partial_chunk)
        mime = QMimeData()
        mime.setHtml('<p>保留內容 abc 😀</p>' * 40000)
        tab.widget.md_surface.editor.insertFromMimeData(mime)
        poll.start()
        deadline = time.monotonic()+20
        try:
            while (not state or buffer.mutation is not None) and time.monotonic() < deadline:
                QTest.qWait(3)
                time.sleep(.001)
            if 'error' in state:
                raise state['error']
            self.assertIn('pasted', state)
            self.assertIsNone(buffer.mutation)
            self.assertEqual(self.errors, [])
            return tab, original, original_bytes, state
        finally:
            poll.stop()

    def assert_source(self, tab, expected):
        buffer = tab.widget.buffer
        self.assertEqual(buffer.mapping_error, '')
        self.assertEqual(buffer.snapshot().text, expected)
        self.assertEqual(buffer.ledger.text_of(buffer.ledger.pieces), expected)
        self.assertEqual(tab.widget.md_surface.editor.toPlainText(), expected)
        return tab.widget.encoded()

    def reopen(self, path, expected_bytes, expected_text):
        reopened = self.w.open_path(path, True, new_tab=True)
        deadline = time.monotonic()+15
        while reopened.widget.buffer.opening and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertFalse(reopened.widget.buffer.opening)
        self.assertEqual(self.assert_source(reopened, expected_text), expected_bytes)
        self.assertEqual(path.read_bytes(), expected_bytes)

    def test_new_split_view_input_save_reopen_and_separate_paste_undo(self):
        marker = 'NEWVIEWTYPED'

        def input_in_new_view(tab, state):
            duplicate = self.w.duplicate_view(tab, True)
            state['duplicate'] = duplicate
            editor = duplicate.widget.md_surface.editor
            editor.setFocus()
            QTest.keyClicks(editor, marker)
            self.assertIn(marker, editor.toPlainText())

        tab, original, original_bytes, state = self.paste_then(input_in_new_view)
        duplicate, pasted = state['duplicate'], state['pasted']
        expected = marker+pasted
        saved = self.assert_source(duplicate, expected)
        self.assertEqual(self.assert_source(tab, expected), saved)
        self.assertTrue(saved.startswith(b'\xef\xbb\xbf'))
        self.assertTrue(saved.endswith(original_bytes[3:]))
        self.assertTrue(self.w.save_tab(duplicate, quiet=True), duplicate.widget.error)
        self.assertEqual(tab.path.read_bytes(), saved)
        duplicate.widget.undo()
        self.assert_source(tab, pasted)  # Typed text is separate from the paste.
        tab.widget.undo()
        self.assertEqual(self.assert_source(duplicate, original), original_bytes)
        duplicate.widget.redo()
        self.assert_source(tab, pasted)  # The entire paste is one Undo/Redo step.
        tab.widget.redo()
        self.assertEqual(self.assert_source(duplicate, expected), saved)
        path = tab.path
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), pane=self.w.pane_for(tab)))
        self.assertEqual(self.assert_source(duplicate, expected), saved)
        self.assertTrue(self.w.close_tab(self.w.index_of(duplicate.key), pane=self.w.pane_for(duplicate)))
        self.reopen(path, saved, expected)

    def test_switch_document_and_close_new_view_keeps_both_owners(self):
        marker = 'NEWVIEWTYPED'

        def switch_and_close(tab, state):
            other = self.w.open_path(self.root/'Empty.md', True, new_tab=True)
            state['other'] = other
            QTest.keyClicks(other.widget.md_surface.editor, 'OTHERDOC')
            duplicate = self.w.duplicate_view(tab, True)
            QTest.keyClicks(duplicate.widget.md_surface.editor, marker)
            self.assertTrue(self.w.close_tab(self.w.index_of(duplicate.key), pane=self.w.pane_for(duplicate)))
            self.w.focus_tab(other)

        tab, _, _, state = self.paste_then(switch_and_close)
        other = state['other']
        expected = marker+state['pasted']
        saved = self.assert_source(tab, expected)
        self.assertEqual(self.assert_source(other, 'OTHERDOC'), b'OTHERDOC')
        self.assertEqual(self.w.active_key, other.key)
        self.assertTrue(self.w.save_tab(tab, quiet=True), tab.widget.error)
        self.assertTrue(self.w.save_tab(other, quiet=True), other.widget.error)
        self.assertEqual(other.path.read_bytes(), b'OTHERDOC')
        path = tab.path
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), pane=self.w.pane_for(tab)))
        self.reopen(path, saved, expected)
