"""A: real shared Qt editing, byte preservation and recoverable failures."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QUrl
from PySide6.QtGui import QInputMethodEvent, QTextCursor, QTextDocument
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.markdown_source import SourceEdit, SourceSnapshot, text_style_edit
from prompt_calculus_studio.markdown_projection import MarkdownProjection
from prompt_calculus_studio.workspace_files import MarkdownPage


MIXED = ('---\r\ntags: [test]\nunknown: {nested: true}\r\n---\n'
         '# 筆記 😀\r\n\n文字\u00a0保留\r\n'
         '| 名稱 | 內容 |\n| --- | --- |\n| 中文 | A \\| B |\r\n'
         '\n```python\r\nprint("<script> & 兩個  空白")\r\n```\n'
         '\n$$\r\nE = mc^2\r\n$$\n<!-- unknown stays -->\n'
         '<custom data-x="keep">untouched</custom>\r\n最後\u2028行\u2029末行')


class MarkdownFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-md-a-')
        self.path = Path(self.area.name)/'Mixed.md'
        self.raw = b'\xef\xbb\xbf' + MIXED.encode('utf-8')
        self.path.write_bytes(self.raw)
        self.pages = [MarkdownPage(self.path)]
        self.page = self.pages[0]

    def tearDown(self):
        for page in self.pages:
            page.close()
            page.deleteLater()
        self.app.processEvents()
        self.area.cleanup()

    def duplicate(self):
        page = MarkdownPage(self.path, self.page.buffer)
        self.pages.append(page)
        return page

    def test_noop_projection_and_save_leave_bytes_and_history_unchanged(self):
        original = self.page.buffer.snapshot()
        projection = MarkdownProjection(original)
        self.assertIn('筆記', projection.toPlainText())
        self.assertEqual(self.page.buffer.snapshot(), original)
        self.assertFalse(self.page.dirty)
        self.assertEqual(self.page.encoded(), self.raw)
        self.assertTrue(self.page.save())
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(self.page.document().isUndoAvailable())

    def test_mixed_sample_style_shared_undo_autosave_receipt_and_reopen(self):
        right = self.duplicate()
        snapshot = self.page.buffer.snapshot()
        start = snapshot.text.index('文字')
        self.page.apply_text_style(start, start+5, family='Microsoft JhengHei UI', size=14, color='#4388AA')
        self.assertEqual(self.page.document(), right.document())
        expected = self.raw.replace('文字\u00a0保留'.encode(),
            '<span style="font-family:Microsoft JhengHei UI;font-size:14pt;color:#4388aa">文字\u00a0保留</span>'.encode())
        self.assertEqual(self.page.encoded(), expected)
        self.assertTrue(right.save())
        self.assertEqual(self.path.read_bytes(), expected)
        projection = MarkdownProjection(right.buffer.snapshot())
        self.assertIn('文字', projection.toPlainText())
        cursor = projection.find('文字')
        self.assertEqual(cursor.charFormat().fontPointSize(), 14)
        self.assertEqual(cursor.charFormat().foreground().color().name(), '#4388aa')
        right.undo()
        self.assertEqual(self.page.encoded(), self.raw)
        self.assertTrue(self.page.save())
        self.assertTrue(self.page.document().isRedoAvailable())
        self.page.redo()
        self.assertTrue(right.save())
        reopened = MarkdownPage(self.path)
        self.pages.append(reopened)
        self.assertEqual(reopened.encoded(), expected)
        self.assertFalse(reopened.document().isUndoAvailable())

    def test_snapshot_from_another_file_and_unclosed_fence_do_not_apply(self):
        other = MarkdownPage(self.path)
        self.pages.append(other)
        with self.assertRaisesRegex(ValueError, '內容已變更'):
            other.buffer.apply(self.page.buffer.snapshot(), [SourceEdit(0, 0, 'cross-file')])
        for source in ('```python\ncode without closing fence', '> ~~~python\n> code'):
            start = source.index('code')
            with self.assertRaises(ValueError):
                text_style_edit(SourceSnapshot(source, 0), start, start+4, color='#123456')

    def test_atomic_multi_range_unicode_offsets_and_stale_edits(self):
        snapshot = self.page.buffer.snapshot()
        start = snapshot.text.index('筆記')
        end = snapshot.text.index('末行')
        self.page.buffer.apply(snapshot, [SourceEdit(start, start+2, '新筆記'), SourceEdit(end, end+2, '結尾')])
        self.assertIn('新筆記 😀', self.page.buffer.snapshot().text)
        self.assertTrue(self.page.buffer.snapshot().text.endswith('結尾'))
        self.assertEqual(self.page.encoded(), self.raw.replace('筆記'.encode(), '新筆記'.encode())
                         .replace('末行'.encode(), '結尾'.encode()))
        with self.assertRaisesRegex(ValueError, '內容已變更'):
            self.page.buffer.apply(snapshot, [SourceEdit(start, start+2, 'stale')])
        self.page.undo()
        self.assertEqual(self.page.encoded(), self.raw)

    def test_repeated_lines_keep_exact_endings_through_native_delete_undo_and_branch(self):
        raw = b'a\r\na\na\ra\r\na\n'
        self.path.write_bytes(raw)
        self.assertTrue(self.page.refresh_external())
        cursor = self.page.textCursor()
        cursor.setPosition(2)
        cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
        self.page.setTextCursor(cursor)
        self.page.insertPlainText('b\n')
        self.assertEqual(self.page.encoded(), b'a\r\nb\r\na\ra\r\na\n')
        self.assertTrue(self.page.save())
        self.page.undo()
        self.assertEqual(self.page.encoded(), raw)
        self.page.redo()
        self.page.undo()
        self.page.moveCursor(QTextCursor.MoveOperation.End)
        self.page.insertPlainText('tail')
        self.assertEqual(self.page.encoded(), raw+b'tail')
        self.assertFalse(self.page.document().isRedoAvailable())
        with self.assertRaisesRegex(ValueError, '重疊'):
            current = self.page.buffer.snapshot()
            self.page.buffer.apply(current, [SourceEdit(1, 5, 'A'), SourceEdit(3, 6, 'B')])
        self.assertEqual(self.page.encoded(), raw+b'tail')

    def test_code_math_unknown_markup_are_not_styled(self):
        for target in ('print', 'mc^2', 'nested', 'data-x'):
            snapshot = self.page.buffer.snapshot()
            start = snapshot.text.index(target)
            with self.assertRaises(ValueError):
                self.page.apply_text_style(start, start+len(target), color='#123456')
        self.assertFalse(self.page.dirty)
        self.assertEqual(self.page.encoded(), self.raw)

    def test_entities_and_whitespace_sensitive_source_are_not_reinterpreted_by_style(self):
        for source, target in (('A &amp; B', '&amp;'), ('A &#x4e2d; B', 'x4e2d'),
                               ('A    B', 'A    B'), ('A\\&B', 'A\\&B')):
            snapshot = SourceSnapshot(source, 0)
            start = source.index(target)
            with self.assertRaises(ValueError):
                text_style_edit(snapshot, start, start+len(target), color='#123456')

    def test_preedit_blocks_transactions_save_and_external_reload_then_commits(self):
        right = self.duplicate()
        self.page.moveCursor(QTextCursor.MoveOperation.End)
        self.page.insertPlainText(' local')
        self.app.sendEvent(self.page, QInputMethodEvent('中文', []))
        self.assertTrue(self.page.buffer.composing)
        with self.assertRaisesRegex(ValueError, '組字'):
            right.apply_text_style(4, 6, color='#123456')
        self.assertFalse(right.save())
        self.assertFalse(right.refresh_external())
        self.assertEqual(self.path.read_bytes(), self.raw)
        commit = QInputMethodEvent()
        commit.setCommitString('中文')
        self.app.sendEvent(self.page, commit)
        self.assertFalse(self.page.buffer.composing)
        self.assertTrue(right.save())
        self.assertTrue(self.path.read_bytes().endswith(' local中文'.encode()))
        right.undo()
        self.assertFalse(self.page.buffer.snapshot().text.endswith('中文'))

    def test_failed_write_and_conflict_preserve_shared_draft_and_redo(self):
        right = self.duplicate()
        self.page.insertPlainText('本機')
        self.page.undo()
        self.assertTrue(right.document().isRedoAvailable())
        right.redo()
        with patch('prompt_calculus_studio.workspace_files.QSaveFile.commit', return_value=False):
            self.assertFalse(right.save())
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertTrue(self.page.dirty)
        self.assertTrue(self.page.document().isUndoAvailable())
        save_error = self.page.error
        external = self.raw + b'\nexternal'
        self.path.write_bytes(external)
        self.assertFalse(right.refresh_external())
        self.assertEqual(self.page.error, save_error)  # Polling cannot clear a failed write.
        self.assertFalse(right.save())
        self.assertIn('外部修改', self.page.error)
        self.assertEqual(self.path.read_bytes(), external)
        self.assertIn('本機', self.page.buffer.snapshot().text)
        self.path.unlink()
        self.assertFalse(right.save())
        self.assertFalse(self.path.exists())
        self.assertIn('本機', self.page.buffer.snapshot().text)

    def test_clean_external_reload_shared_positions_and_old_undo_retired(self):
        right = self.duplicate()
        self.page.insertPlainText('old')
        self.assertTrue(self.page.save())
        self.assertTrue(right.document().isUndoAvailable())
        right.moveCursor(QTextCursor.MoveOperation.End)
        self.path.write_bytes(b'# External\nnew\n')
        self.assertTrue(right.refresh_external())
        self.assertEqual(self.page.buffer.snapshot().text, '# External\nnew\n')
        self.assertFalse(self.page.dirty)
        self.assertFalse(right.document().isUndoAvailable())
        self.assertEqual(right.textCursor().position(), right.document().characterCount()-1)
        self.assertFalse(right.refresh_external())
        self.path.write_bytes(b'\xffinvalid')
        self.assertFalse(right.refresh_external())
        self.assertEqual(self.page.buffer.snapshot().text, '# External\nnew\n')

    def test_resource_projection_never_opens_local_or_remote_attachments(self):
        snapshot = SourceSnapshot('![remote](https://example.invalid/image.png)\n'
                                  '<img src="file:///private.png"><script>alert(1)</script>', 0)
        projection = MarkdownProjection(snapshot)
        for target in ('https://example.invalid/image.png', 'file:///private.png', '//server/share/x'):
            self.assertEqual(bytes(projection.loadResource(QTextDocument.ResourceType.ImageResource, QUrl(target))), b'')
        self.assertEqual(projection.source, snapshot)

    def test_size_rejected_atomically_and_original_endings_restored_after_delete_save_undo(self):
        current = self.page.buffer.snapshot()
        with self.assertRaisesRegex(ValueError, '8 MiB'):
            self.page.buffer.apply(current, [SourceEdit(0, 0, 'x' * (8*1024*1024))])
        self.assertEqual(self.page.encoded(), self.raw)
        start = current.text.index('| 名稱')
        end = current.text.index('```python')
        self.page.buffer.apply(current, [SourceEdit(start, end, '')])
        self.assertTrue(self.page.save())
        self.page.undo()
        self.assertEqual(self.page.encoded(), self.raw)


if __name__ == '__main__':
    unittest.main()
