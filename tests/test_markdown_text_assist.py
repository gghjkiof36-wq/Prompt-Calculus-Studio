"""Captured Markdown adoption through real shared views and the existing writer."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QContextMenuEvent, QInputMethodEvent, QTextCursor

from prompt_calculus_studio.markdown_source import utf16_position
from prompt_calculus_studio.markdown_text_assist import MarkdownTextTarget, add_text_assist_action, open_text_assist
from prompt_calculus_studio.local_ai.text_assist import TextAssistController
from prompt_calculus_studio.widgets import RoundMenu
import test_workspace_panes as fixture


RAW = b'\xef\xbb\xbf' + '# 起點 😀\r\n第一😀選取\t內容\n後段\r末尾\r\n'.encode('utf-8')
SELECTED = '第一😀選取'


class FakeService(QObject):
    delta = Signal(object, str)
    finished = Signal(object, str, str)
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.models = [dict(id='model', name='隔離測試模型')]
        self.selected_model = 'model'
        self.can_accept = True
        self.state = 'idle'
        self.error = ''
        self.requests, self.cancelled = [], []

    def settings_for_model(self, model):
        return dict(context_size=8192, max_tokens=2048, temperature=.2,
                    top_p=.9, seed=42, gpu_layers=-1)

    def model_preset(self, model):
        return dict(system_prompt='')

    def validate_submission(self, request):
        return None

    def submit(self, request):
        self.requests.append(request)
        self.state = 'generating'
        self.changed.emit()
        return True

    def cancel(self, request_id):
        self.cancelled.append(request_id)
        return True

    def complete(self, text):
        request = self.requests[-1]
        self.delta.emit(request, text)
        self.state = 'idle'
        self.finished.emit(request, 'completed', text)
        self.changed.emit()


class MarkdownTextAssistTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def page(self):
        (self.root/'Assist.md').write_bytes(RAW)
        tab = self.open('Assist.md', retained=True)
        tab.widget.md_surface.apply_mode('source', focus=False)
        return tab

    def select(self, tab, text=SELECTED):
        surface = tab.widget.md_surface
        snapshot = tab.widget.buffer.snapshot()
        start = snapshot.text.index(text)
        cursor = surface.editor.textCursor()
        cursor.setPosition(utf16_position(snapshot.text, start))
        cursor.setPosition(utf16_position(snapshot.text, start+len(text)), QTextCursor.MoveMode.KeepAnchor)
        surface.editor.setTextCursor(cursor)
        return surface.selection()

    def target(self, tab):
        return MarkdownTextTarget(tab.widget.md_surface, *self.select(tab))

    def controller(self, target):
        service = FakeService()
        controller = TextAssistController(service, source_text=target.source_text,
                                          source_scope=target.scope, parent=target)
        target.bind(controller)
        return service, controller

    def test_menu_freezes_unicode_selection_or_clicked_paragraph(self):
        tab = self.page()
        surface = tab.widget.md_surface
        snapshot, start, end = self.select(tab)
        self.assertEqual(snapshot.text[start:end], SELECTED)
        self.assertGreater(surface.editor.textCursor().selectionStart(), start)
        menu = RoundMenu(surface)
        with patch('prompt_calculus_studio.markdown_text_assist.open_text_assist') as opened:
            action = add_text_assist_action(menu, surface, start+2)
            surface.editor.moveCursor(QTextCursor.MoveOperation.End)
            action.trigger()
            self.assertEqual(opened.call_args.args, (surface, snapshot, start, end))
            self.assertEqual(opened.call_args.kwargs, dict(kind='selection'))
            self.select(tab)
            clicked = snapshot.text.index('後段')
            action = add_text_assist_action(menu, surface, clicked)
            action.trigger()
            _, frozen, a, b = opened.call_args.args
            self.assertEqual(frozen.text[a:b], '後段')
            self.assertEqual(opened.call_args.kwargs, dict(kind='paragraph'))
        self.assertFalse(tab.dirty)
        self.assertEqual(tab.widget.encoded(), RAW)
        menu.deleteLater()

    def test_completed_preview_keeps_source_bytes_and_undo_unchanged(self):
        tab = self.page()
        target = self.target(tab)
        service, controller = self.controller(target)
        snapshot = tab.widget.buffer.snapshot()
        self.assertTrue(controller.start('改得簡潔'))
        service.complete('<think>不可寫入原稿的思考</think>精簡候選 😀')
        self.assertEqual(controller.candidate_for_apply(), '精簡候選 😀')
        self.assertEqual(tab.widget.buffer.snapshot(), snapshot)
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertEqual(tab.path.read_bytes(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())
        self.assertFalse(tab.dirty)

    def test_legacy_loaded_service_reports_restart_without_changing_source_or_selection(self):
        tab = self.page()
        surface = tab.widget.md_surface
        snapshot, start, end = self.select(tab)
        cursor = surface.editor.textCursor()
        selection = (cursor.anchor(), cursor.position())
        active_key = self.w.active_key
        with patch('prompt_calculus_studio.local_ai.service.for_workspace', return_value=object()), \
                patch('prompt_calculus_studio.local_ai.text_assist.TextAssistController') as controller, \
                patch('prompt_calculus_studio.local_ai.text_assist_dialog.TextAssistDialog') as dialog:
            self.assertIsNone(open_text_assist(surface, snapshot, start, end))
        controller.assert_not_called()
        dialog.assert_not_called()
        self.assertEqual(surface.message, '請重新開啟 PCS，以使用文字協作。')
        self.assertEqual(tab.widget.buffer.snapshot(), snapshot)
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertEqual(tab.path.read_bytes(), RAW)
        self.assertEqual((surface.editor.textCursor().anchor(), surface.editor.textCursor().position()), selection)
        self.assertEqual(self.w.active_key, active_key)
        self.assertIs(self.w.tabs_by_key[tab.key], tab)
        self.assertTrue(self.w.isVisible())
        self.assertFalse(tab.dirty)
        self.assertFalse(tab.widget.document().isUndoAvailable())

    def test_replace_shared_view_one_undo_exact_unmodified_bytes(self):
        tab = self.page()
        right = self.w.duplicate_view(tab, True)
        right.widget.md_surface.apply_mode('source', focus=False)
        target = self.target(tab)
        self.assertTrue(target.apply('replace', '新😀\r\n換行'))
        expected = RAW.replace(SELECTED.encode(), '新😀\r\n換行'.encode())
        self.assertIs(tab.widget.buffer, right.widget.buffer)
        self.assertEqual(right.widget.encoded(), expected)
        self.assertEqual(tab.path.read_bytes(), RAW)
        self.assertTrue(tab.dirty)
        right.widget.undo()
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())
        tab.widget.redo()
        self.assertEqual(right.widget.encoded(), expected)
        with self.assertRaisesRegex(ValueError, '已套用'):
            target.apply('replace', '不能再次套用')

    def test_insert_preserves_source_and_is_one_undo(self):
        tab = self.page()
        right = self.w.duplicate_view(tab, True)
        right.widget.md_surface.apply_mode('source', focus=False)
        target = self.target(tab)
        self.assertTrue(target.apply('insert', '附加😀\n第二行'))
        expected = RAW.replace(SELECTED.encode(),
                               (SELECTED+'\r\n\r\n附加😀\r\n第二行\r\n\r\n').encode())
        self.assertEqual(tab.widget.encoded(), expected)
        right.widget.undo()
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())
        tab.widget.redo()
        self.assertEqual(right.widget.encoded(), expected)

    def test_save_as_same_snapshot_rejects_old_candidate(self):
        tab = self.page()
        target = self.target(tab)
        snapshot = tab.widget.buffer.snapshot()
        result = self.w.commands.files.run('save_as', self.w.commands.context('tab', tab=tab),
                                          name='Copy', directory=self.root)
        self.assertEqual(result, self.root/'Copy.md')
        self.assertEqual(tab.widget.buffer.snapshot(), snapshot)
        self.assertNotEqual(tab.widget.buffer.save_key, target.save_key)
        with self.assertRaisesRegex(ValueError, '位置已變更'):
            target.apply('replace', '舊候選')
        self.assertEqual(result.read_bytes(), RAW)
        self.assertEqual((self.root/'Assist.md').read_bytes(), RAW)
        self.assertFalse(tab.dirty)

    def test_closed_origin_cannot_apply_to_surviving_shared_buffer(self):
        tab = self.page()
        target = self.target(tab)
        right = self.w.duplicate_view(tab, True)
        buffer = right.widget.buffer
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), decision='discard',
                                        make_home=False, pane=self.w.pane_for(tab)))
        self.assertIs(right.widget.buffer, buffer)
        with self.assertRaisesRegex(ValueError, '原文件已關閉'):
            target.apply('replace', '舊候選')
        self.assertEqual(right.widget.encoded(), RAW)
        self.assertFalse(right.dirty)

    def test_other_shared_view_edit_invalidates_running_request_and_late_reply(self):
        tab = self.page()
        target = self.target(tab)
        service, controller = self.controller(target)
        right = self.w.duplicate_view(tab, True)
        right.widget.md_surface.apply_mode('source', focus=False)
        self.assertTrue(controller.start('整理'))
        request = service.requests[-1]
        right.widget.moveCursor(QTextCursor.MoveOperation.End)
        right.widget.insertPlainText('使用者新增')
        self.assertTrue(controller.invalidated)
        self.assertEqual(service.cancelled, [request.request_id])
        service.delta.emit(request, '晚回覆')
        service.finished.emit(request, 'completed', '晚回覆')
        self.assertFalse(controller.can_apply)
        self.assertEqual(controller.raw_text, '')
        with self.assertRaises(ValueError):
            target.apply('replace', '晚回覆')
        self.assertEqual(tab.widget.encoded(), RAW+'使用者新增'.encode())

    def test_edit_after_preview_invalidates_candidate(self):
        tab = self.page()
        target = self.target(tab)
        service, controller = self.controller(target)
        self.assertTrue(controller.start('整理'))
        service.complete('已完成候選')
        self.assertTrue(controller.can_apply)
        tab.widget.moveCursor(QTextCursor.MoveOperation.End)
        tab.widget.insertPlainText('後來編輯')
        self.assertTrue(controller.invalidated)
        self.assertFalse(controller.can_apply)
        with self.assertRaises(ValueError):
            target.apply('replace', '已完成候選')
        self.assertEqual(tab.path.read_bytes(), RAW)

    def test_ime_and_read_mode_guard_adoption_without_losing_candidate(self):
        tab = self.page()
        target = self.target(tab)
        surface = tab.widget.md_surface
        # A real IME preedit at a selected range first replaces that selection.
        # Move only the caret: this case isolates pending composition from an
        # already changed source, which is covered by the stale-source tests.
        surface.editor.moveCursor(QTextCursor.MoveOperation.End)
        self.app.sendEvent(surface.editor, QInputMethodEvent('尚未送出', []))
        self.assertTrue(tab.widget.buffer.composing)
        with self.assertRaises(ValueError):
            target.apply('replace', '候選')
        self.app.sendEvent(surface.editor, QInputMethodEvent('', []))
        self.assertFalse(tab.widget.buffer.composing)
        surface.apply_mode('read', focus=False)
        with self.assertRaises(ValueError):
            target.apply('replace', '候選')
        self.assertEqual(tab.widget.encoded(), RAW)
        surface.apply_mode('source', focus=False)
        self.assertTrue(target.apply('replace', '候選'))

    def test_save_close_reopen_uses_existing_writer_and_history_contract(self):
        tab = self.page()
        target = self.target(tab)
        target.apply('replace', '已採用😀')
        expected = RAW.replace(SELECTED.encode(), '已採用😀'.encode())
        self.assertTrue(self.w.save_tab(tab))
        self.assertEqual(tab.path.read_bytes(), expected)
        tab.widget.undo()
        self.assertEqual(tab.widget.encoded(), RAW)
        tab.widget.redo()
        self.assertTrue(self.w.save_tab(tab))
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), make_home=False,
                                        pane=self.w.pane_for(tab)))
        reopened = self.open('Assist.md', retained=True)
        self.assertEqual(reopened.widget.encoded(), expected)
        self.assertFalse(reopened.widget.document().isUndoAvailable())

    def test_writer_failure_retains_adopted_draft_and_shared_undo(self):
        tab = self.page()
        right = self.w.duplicate_view(tab, True)
        right.widget.md_surface.apply_mode('source', focus=False)
        target = self.target(tab)
        target.apply('replace', '需要保存')
        expected = RAW.replace(SELECTED.encode(), '需要保存'.encode())
        with patch('prompt_calculus_studio.workspace_files.QSaveFile.commit', return_value=False):
            self.assertFalse(self.w.save_tab(tab, quiet=True))
        self.assertEqual(tab.path.read_bytes(), RAW)
        self.assertEqual(right.widget.encoded(), expected)
        self.assertTrue(tab.dirty)
        self.assertTrue(right.widget.error)
        self.assertFalse(self.w.autosave.timers[self.w.autosave.key(tab)].isActive())
        right.widget.undo()
        self.assertEqual(tab.widget.encoded(), RAW)
        tab.widget.redo()
        self.assertEqual(right.widget.encoded(), expected)
        self.assertTrue(self.w.save_tab(right))
        self.assertEqual(tab.path.read_bytes(), expected)

    def test_right_click_dialog_preview_apply_save_and_reopen(self):
        tab = self.page()
        surface = tab.widget.md_surface
        self.select(tab)
        service = FakeService()
        menus = []
        position = surface.editor.cursorRect(surface.editor.textCursor()).center()
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, position,
                                  surface.editor.viewport().mapToGlobal(position))
        with patch('prompt_calculus_studio.markdown_view.QMenu.open_at',
                   lambda menu, *_args, **_kwargs: menus.append(menu)), \
                patch('prompt_calculus_studio.local_ai.service.for_workspace', return_value=service):
            surface.editor.contextMenuEvent(event)
            self.assertEqual(len(menus), 1)
            action = next(action for action in menus[0].actions() if action.text() == '文字協作…')
            self.assertTrue(action.isEnabled())
            action.trigger()
        dialog = surface.text_assist_dialog
        self.assertEqual(dialog.controller.source_text, SELECTED)
        self.assertTrue(tab.retained)
        dialog.instruction.setPlainText('保留意思並精簡')
        dialog.start_button.click()
        self.assertEqual(len(service.requests), 1)
        service.complete('<think>推理不是正文</think>整合候選😀')
        self.assertEqual(dialog.preview.toPlainText(), '整合候選😀')
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertTrue(dialog.replace_button.isEnabled())
        dialog.replace_button.click()
        expected = RAW.replace(SELECTED.encode(), '整合候選😀'.encode())
        self.assertEqual(tab.widget.encoded(), expected)
        self.assertTrue(self.w.save_tab(tab))
        self.assertEqual(tab.path.read_bytes(), expected)
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), make_home=False,
                                        pane=self.w.pane_for(tab)))
        reopened = self.open('Assist.md', retained=True)
        self.assertEqual(reopened.widget.encoded(), expected)
        self.assertFalse(reopened.widget.document().isUndoAvailable())
        # Closing the original page also owns/destructs its context menu.


if __name__ == '__main__':
    unittest.main()
