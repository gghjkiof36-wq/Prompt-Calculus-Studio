"""Whole-document context, explicit EOF drafting and local rewrite adoption."""
import hashlib
import json
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest

from prompt_calculus_studio.markdown_models import set_markdown_model
from prompt_calculus_studio.markdown_source import utf16_position
from prompt_calculus_studio.markdown_text_assist import MarkdownTextTarget
from test_markdown_inline_assist import FakeService
import test_workspace_panes as fixture


RAW = b'\xef\xbb\xbf' + '# 文件背景\r\n重要前文😀\n第二段資料\r尾句'.encode()
SELECTED = '第二段資料'


class MarkdownDocumentAssistTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def setUp(self):
        self.service = FakeService()
        self.service.models.append(dict(id='other', name='另一隔離模型'))
        service_patch = patch('prompt_calculus_studio.local_ai.service.for_workspace', return_value=self.service)
        service_patch.start()
        self.addCleanup(service_patch.stop)
        fixture.WorkspacePaneTests.setUp(self)
        self.w.resize(1280, 900)
        self.w.activateWindow()
        self.settle()

    def tearDown(self):
        fixture.WorkspacePaneTests.tearDown(self)

    def settle(self):
        self.events()
        QTest.qWait(130)
        self.app.processEvents()

    def page(self, raw=RAW, name='Draft.md'):
        (self.root/name).write_bytes(raw)
        tab = self.open(name, retained=True, new_tab=True)
        tab.widget.md_surface.apply_mode('source', focus=False)
        self.w.focus_tab(tab)
        tab.widget.md_surface.editor.setFocus()
        self.settle()
        return tab

    def start_draft(self, tab, instruction='參考整份文件，續寫一段結尾'):
        composer = tab.widget.md_surface.draft_composer
        composer.instruction.setPlainText(instruction)
        self.settle()
        panel = composer.send()
        self.settle()
        self.assertIsNotNone(panel)
        self.assertTrue(panel.controller.busy)
        self.assertEqual(panel.target.kind, 'draft')
        return panel

    def payload(self, request=None):
        return json.loads((request or self.service.requests[-1]).input['messages'][-1]['content'])

    def projection(self, tab):
        view = tab.widget.md_surface.inline_assist.projection
        self.assertIsNotNone(view)
        return view

    def test_empty_document_draft_preview_keep_undo_save_and_reopen(self):
        tab = self.page(b'')
        panel = self.start_draft(tab, '寫一段新的開場')
        request = self.service.requests[-1]
        self.assertEqual(self.payload(), dict(instruction='寫一段新的開場', operation='draft',
                                              document_text='', source_text=''))
        self.assertEqual((panel.target.start, panel.target.end), (0, 0))
        self.assertEqual(self.projection(tab).projected_source, '')
        candidate = '# 新的開場\n\n第一段😀'
        self.service.complete(candidate)
        self.settle()
        self.assertEqual(self.projection(tab).projected_source, candidate)
        self.assertEqual(tab.widget.encoded(), b'')
        self.assertEqual(tab.path.read_bytes(), b'')
        self.assertFalse(tab.widget.document().isUndoAvailable())
        self.assertEqual(request.scope['document_sha256'], hashlib.sha256(b'').hexdigest())
        QTest.mouseClick(panel.keep_button, Qt.MouseButton.LeftButton)
        self.settle()
        self.assertEqual(tab.widget.buffer.snapshot().text, candidate)
        expected = tab.widget.encoded()
        self.assertTrue(self.w.save_tab(tab))
        self.assertEqual(tab.path.read_bytes(), expected)
        tab.widget.undo()
        self.assertEqual(tab.widget.encoded(), b'')
        self.assertFalse(tab.widget.document().isUndoAvailable())
        tab.widget.redo()
        self.assertEqual(tab.widget.encoded(), expected)
        self.assertTrue(self.w.save_tab(tab))
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), make_home=False, pane=self.w.pane_for(tab)))
        reopened = self.open('Draft.md', retained=True, new_tab=True)
        self.assertEqual(reopened.widget.encoded(), expected)

    def test_nonempty_draft_freezes_full_context_and_preserves_original_bytes(self):
        tab = self.page()
        original = tab.widget.buffer.snapshot().text
        panel = self.start_draft(tab)
        self.assertEqual(self.payload()['document_text'], original)
        self.assertEqual(self.payload()['source_text'], '')
        self.assertEqual(self.payload()['operation'], 'draft')
        self.assertEqual((panel.target.start, panel.target.end), (len(original), len(original)))
        self.service.complete('新草稿😀\n下一段')
        self.settle()
        expected_source = original+'\n\n新草稿😀\n下一段'
        self.assertEqual(self.projection(tab).projected_source, expected_source)
        self.assertEqual(tab.widget.encoded(), RAW)
        QTest.mouseClick(panel.keep_button, Qt.MouseButton.LeftButton)
        self.settle()
        expected = RAW+'\r\n\r\n新草稿😀\r\n下一段'.encode()
        self.assertEqual(tab.widget.encoded(), expected)
        with patch('prompt_calculus_studio.workspace_files.QSaveFile.commit', return_value=False):
            self.assertFalse(self.w.save_tab(tab, quiet=True))
        self.assertEqual(tab.path.read_bytes(), RAW)
        self.assertEqual(tab.widget.encoded(), expected)
        tab.widget.undo()
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())
        tab.widget.redo()
        self.assertTrue(self.w.save_tab(tab))
        self.assertEqual(tab.path.read_bytes(), expected)

    def test_draft_zero_range_is_eof_only_and_blank_line_separators_are_bounded(self):
        for i, (raw, expected) in enumerate(((b'', b'new'), (b'old', b'old\n\nnew'),
                (b'old\n', b'old\n\nnew'), (b'old\n\n', b'old\n\nnew'),
                (b'\xef\xbb\xbf', b'\xef\xbb\xbfnew'))):
            with self.subTest(raw=raw):
                tab = self.page(raw, 'Boundary'+str(i)+'.md')
                snapshot = tab.widget.buffer.snapshot()
                end = len(snapshot.text)
                target = MarkdownTextTarget(tab.widget.md_surface, snapshot, end, end, kind='draft')
                if end:
                    with self.assertRaises(ValueError):
                        MarkdownTextTarget(tab.widget.md_surface, snapshot, 0, 0, kind='draft')
                target.apply('replace', 'new')
                self.assertEqual(tab.widget.encoded(), expected)
                tab.widget.undo()
                self.assertEqual(tab.widget.encoded(), raw)
                self.assertFalse(tab.widget.document().isUndoAvailable())

    def test_dismiss_draft_preserves_document_instruction_and_ignores_late_reply(self):
        tab = self.page()
        instruction = '先構思下一段'
        panel = self.start_draft(tab, instruction)
        controller, request = panel.controller, self.service.requests[-1]
        self.service.delta.emit(request, '部分草稿')
        self.settle()
        QTest.mouseClick(panel.delete_button, Qt.MouseButton.LeftButton)
        self.settle()
        self.assertTrue(controller.disposed)
        self.assertEqual(self.service.cancelled, [request.request_id])
        self.service.complete('部分草稿晚到內容', request)
        self.settle()
        self.assertIsNone(tab.widget.md_surface.inline_assist.panel)
        self.assertIsNone(tab.widget.md_surface.inline_assist.projection)
        self.assertEqual(tab.widget.md_surface.draft_composer.instruction.toPlainText(), instruction)
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertEqual(tab.path.read_bytes(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())

    def test_shared_document_change_invalidates_full_context_snapshot(self):
        tab = self.page()
        right = self.w.duplicate_view(tab, True)
        self.w.focus_tab(tab)
        self.settle()
        panel = self.start_draft(tab)
        request = self.service.requests[-1]
        frozen = self.payload(request)['document_text']
        right.widget.moveCursor(QTextCursor.MoveOperation.Start)
        right.widget.insertPlainText('新增背景')
        self.settle()
        self.assertTrue(panel.controller.invalidated)
        self.assertEqual(self.payload(request)['document_text'], frozen)
        self.assertEqual(self.service.cancelled, [request.request_id])
        self.service.complete('使用舊背景的草稿', request)
        self.settle()
        self.assertFalse(panel.keep_button.isEnabled())
        self.assertIsNone(tab.widget.md_surface.inline_assist.projection)
        self.assertNotIn('使用舊背景的草稿', tab.widget.buffer.snapshot().text)
        self.assertEqual(tab.path.read_bytes(), RAW)

    def test_selection_receives_entire_document_but_updates_only_selected_range(self):
        tab = self.page()
        surface = tab.widget.md_surface
        snapshot = tab.widget.buffer.snapshot()
        start = snapshot.text.index(SELECTED)
        cursor = surface.editor.textCursor()
        cursor.setPosition(utf16_position(snapshot.text, start))
        cursor.setPosition(utf16_position(snapshot.text, start+len(SELECTED)), QTextCursor.MoveMode.KeepAnchor)
        surface.editor.setTextCursor(cursor)
        self.settle()
        panel = surface.inline_assist.panel
        self.assertIsNotNone(panel)
        panel.instruction.setPlainText('依前後文整理選取段落')
        QTest.mouseClick(panel.start_button, Qt.MouseButton.LeftButton)
        self.settle()
        body = self.payload()
        self.assertEqual(body['operation'], 'rewrite')
        self.assertEqual(body['source_text'], SELECTED)
        self.assertEqual(body['document_text'], snapshot.text)
        self.service.complete('局部新文字')
        self.settle()
        self.assertEqual(tab.widget.encoded(), RAW)
        QTest.mouseClick(panel.keep_button, Qt.MouseButton.LeftButton)
        self.settle()
        self.assertEqual(tab.widget.encoded(), RAW.replace(SELECTED.encode(), '局部新文字'.encode()))
        tab.widget.undo()
        self.assertEqual(tab.widget.encoded(), RAW)

    def test_model_change_returns_draft_instruction_without_automatic_resubmit(self):
        tab = self.page()
        instruction = '請繼續這份文件'
        panel = self.start_draft(tab, instruction)
        old_request = self.service.requests[-1]
        set_markdown_model(tab.widget.md_surface, self.service, 'other')
        self.settle()
        composer = tab.widget.md_surface.draft_composer
        self.assertIsNone(tab.widget.md_surface.inline_assist.panel)
        self.assertTrue(composer.isVisible())
        self.assertEqual(composer.instruction.toPlainText(), instruction)
        self.assertEqual(len(self.service.requests), 1)
        self.assertEqual(self.service.cancelled, [old_request.request_id])
        self.service.complete('舊模型晚回覆', old_request)
        self.settle()
        replacement = composer.send()
        self.settle()
        self.assertIsNotNone(replacement)
        self.assertEqual(len(self.service.requests), 2)
        self.assertEqual(self.service.requests[-1].input['model'], 'other')
        self.assertEqual(self.payload()['operation'], 'draft')
        self.assertEqual(tab.widget.encoded(), RAW)

    def test_hidden_draft_finishes_without_switching_page_or_saving(self):
        tab = self.page()
        panel = self.start_draft(tab)
        other = self.open('Notes.md', retained=True, new_tab=True)
        other.widget.md_surface.editor.setFocus()
        self.settle()
        self.service.complete('在背景完成的草稿')
        self.settle()
        self.assertEqual(self.w.active_key, other.key)
        self.assertFalse(panel.isVisible())
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertEqual(tab.path.read_bytes(), RAW)
        self.w.focus_tab(tab)
        self.settle()
        self.assertTrue(panel.isVisible())
        self.assertTrue(panel.keep_button.isEnabled())

    def test_footer_enter_sends_and_shift_enter_only_adds_instruction_line(self):
        tab = self.page(b'')
        composer = tab.widget.md_surface.draft_composer
        composer.instruction.setPlainText('第一個要求')
        composer.instruction.moveCursor(QTextCursor.MoveOperation.End)
        composer.instruction.setFocus()
        self.settle()
        QTest.keyClick(composer.instruction, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.settle()
        self.assertEqual(composer.instruction.toPlainText(), '第一個要求\n')
        self.assertFalse(self.service.requests)
        composer.instruction.insertPlainText('第二個要求')
        QTest.keyClick(composer.instruction, Qt.Key.Key_Return)
        self.settle()
        self.assertEqual(len(self.service.requests), 1)
        self.assertEqual(self.payload()['instruction'], '第一個要求\n第二個要求')
        self.assertEqual(tab.widget.encoded(), b'')

    def test_save_as_rejects_old_eof_candidate_even_if_document_text_is_identical(self):
        tab = self.page()
        panel = self.start_draft(tab)
        self.service.complete('舊位置的草稿')
        self.settle()
        result = self.w.commands.files.run('save_as', self.w.commands.context('tab', tab=tab),
                                          name='NewDraft', directory=self.root)
        self.assertEqual(result, self.root/'NewDraft.md')
        QTest.mouseClick(panel.keep_button, Qt.MouseButton.LeftButton)
        self.settle()
        self.assertFalse(panel.target.used)
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertEqual((self.root/'Draft.md').read_bytes(), RAW)
        self.assertEqual(result.read_bytes(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())

