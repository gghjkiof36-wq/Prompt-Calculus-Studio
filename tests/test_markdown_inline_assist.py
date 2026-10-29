"""Inline selection and disposable preview through a real offscreen workspace."""
import json
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QTextCursor
from PySide6.QtTest import QTest
from shiboken6 import isValid

from prompt_calculus_studio.markdown_source import SourceEdit, utf16_position
import test_workspace_panes as fixture


RAW = b'\xef\xbb\xbf' + '# 起點 😀\r\n\r\n第一😀選取 內容\n\n後段\r末尾\r\n'.encode('utf-8')
SELECTED = '第一😀選取'


class FakeService(QObject):
    delta = Signal(object, str)
    finished = Signal(object, str, str)
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.models = [dict(id='model', name='隔離測試模型')]
        self.selected_model = 'model'
        self.can_accept, self.state, self.error = True, 'idle', ''
        self.requests, self.cancelled = [], []

    def settings_for_model(self, model):
        return dict(context_size=8192, max_tokens=2048, temperature=.2,
                    top_p=.9, seed=42, gpu_layers=-1)

    def model_preset(self, model):
        return dict(system_prompt='')

    def validate_submission(self, request):
        pass

    def submit(self, request):
        self.requests.append(request)
        self.state = 'generating'
        self.changed.emit()
        return True

    def cancel(self, request_id):
        self.cancelled.append(request_id)
        return True

    def complete(self, text, request=None):
        request = request or self.requests[-1]
        self.delta.emit(request, text)
        self.state = 'idle'
        self.finished.emit(request, 'completed', text)
        self.changed.emit()


class MarkdownInlineAssistTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def setUp(self):
        self.service = FakeService()
        self.service_patch = patch('prompt_calculus_studio.local_ai.service.for_workspace', return_value=self.service)
        self.service_patch.start()
        self.addCleanup(self.service_patch.stop)
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

    def page(self, raw=RAW):
        (self.root/'Inline.md').write_bytes(raw)
        tab = self.open('Inline.md', retained=True)
        tab.widget.md_surface.apply_mode('source', focus=False)
        self.w.focus_tab(tab)
        tab.widget.md_surface.editor.setFocus()
        self.settle()
        return tab

    def select(self, tab, text=SELECTED, *, reverse=False, settle=True):
        surface = tab.widget.md_surface
        snapshot = tab.widget.buffer.snapshot()
        start = snapshot.text.index(text)
        positions = [utf16_position(snapshot.text, start), utf16_position(snapshot.text, start+len(text))]
        if reverse:
            positions.reverse()
        cursor = surface.editor.textCursor()
        cursor.setPosition(positions[0])
        cursor.setPosition(positions[1], QTextCursor.MoveMode.KeepAnchor)
        surface.editor.setTextCursor(cursor)
        if settle:
            self.settle()
        return snapshot, start, start+len(text)

    def panel(self, tab):
        panel = tab.widget.md_surface.inline_assist.panel
        self.assertIsNotNone(panel)
        self.assertTrue(isValid(panel))
        return panel

    def start(self, tab, instruction='保留意思，整理這段文字'):
        panel = self.panel(tab)
        panel.instruction.setPlainText(instruction)
        QTest.mouseClick(panel.start_button, Qt.MouseButton.LeftButton)
        self.settle()
        self.assertTrue(panel.controller.busy)
        return panel

    def projected(self, tab):
        projection = tab.widget.md_surface.inline_assist.projection
        self.assertIsNotNone(projection)
        self.assertTrue(projection.isReadOnly())
        return projection

    def test_selection_opens_child_input_without_focus_or_source_change(self):
        tab = self.page()
        snapshot, start, end = self.select(tab)
        surface, panel = tab.widget.md_surface, self.panel(tab)
        self.assertTrue(panel.isVisible())
        self.assertFalse(panel.isWindow())
        self.assertTrue(surface.editor.hasFocus())
        self.assertFalse(self.service.requests)
        self.assertEqual(panel.target.source_text, SELECTED)
        self.assertEqual(surface.selection(), (snapshot, start, end))
        QTest.mouseClick(panel.instruction.viewport(), Qt.MouseButton.LeftButton)
        self.settle()
        self.assertTrue(panel.instruction.hasFocus())
        self.assertEqual(surface.selection(), (snapshot, start, end))
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertEqual(tab.path.read_bytes(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())

    def test_drag_waits_for_release_and_shift_can_extend_selection(self):
        tab = self.page()
        editor = tab.widget.md_surface.editor
        text = tab.widget.buffer.snapshot().text
        start = text.index(SELECTED)
        a = QTextCursor(editor.document()); a.setPosition(utf16_position(text, start))
        b = QTextCursor(editor.document()); b.setPosition(utf16_position(text, start+len(SELECTED)))
        QTest.mousePress(editor.viewport(), Qt.MouseButton.LeftButton, pos=editor.cursorRect(a).center())
        QTest.mouseMove(editor.viewport(), editor.cursorRect(b).center())
        self.settle()
        self.assertIsNone(tab.widget.md_surface.inline_assist.panel)
        QTest.mouseRelease(editor.viewport(), Qt.MouseButton.LeftButton, pos=editor.cursorRect(b).center())
        self.settle()
        self.assertEqual(self.panel(tab).target.source_text, SELECTED)
        QTest.keyClick(editor, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        self.settle()
        self.assertEqual(self.panel(tab).target.source_text, SELECTED+' ')
        self.assertTrue(editor.hasFocus())
        self.assertEqual(tab.widget.encoded(), RAW)

    def test_reverse_selection_keeps_keyboard_active_endpoint(self):
        tab = self.page()
        self.select(tab, reverse=True, settle=False)
        editor = tab.widget.md_surface.editor
        before = (editor.textCursor().anchor(), editor.textCursor().position())
        self.settle()
        self.assertEqual((editor.textCursor().anchor(), editor.textCursor().position()), before)
        QTest.keyClick(editor, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        self.settle()
        self.assertEqual(self.panel(tab).target.source_text, SELECTED[1:])

    def test_right_click_keeps_edit_menu_without_text_assist_entry(self):
        tab = self.page()
        self.select(tab)
        surface = tab.widget.md_surface
        menus = []
        position = surface.editor.cursorRect().center()
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, position,
                                  surface.editor.viewport().mapToGlobal(position))
        with patch('prompt_calculus_studio.markdown_view.QMenu.open_at',
                   lambda menu, *_args, **_kwargs: menus.append(menu)):
            self.app.sendEvent(surface.editor.viewport(), event)
        self.assertEqual(len(menus), 1)
        actions = [action.text() for action in menus[0].actions()]
        self.assertFalse(any('文字協作' in text or '要求修改' in text for text in actions), actions)
        self.assertTrue(any('複製' in text for text in actions), actions)
        self.assertFalse(self.service.requests)

    def test_stream_projection_delete_and_late_reply_never_write_source(self):
        tab = self.page()
        right = self.w.duplicate_view(tab, True)
        self.w.focus_tab(tab)
        tab.widget.md_surface.editor.setFocus()
        self.select(tab)
        panel = self.start(tab)
        controller = panel.controller
        request = self.service.requests[-1]
        self.assertEqual(self.projected(tab).projected_source, tab.widget.buffer.snapshot().text)
        self.service.delta.emit(request, '<think>內部思考</think>候選😀')
        self.settle()
        projection = self.projected(tab)
        self.assertEqual(projection.projected_source, panel.target.snapshot.text.replace(SELECTED, '候選😀'))
        self.assertNotIn('內部思考', projection.toPlainText())
        self.assertEqual(right.widget.encoded(), RAW)
        self.assertEqual(tab.path.read_bytes(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())
        QTest.mouseClick(panel.delete_button, Qt.MouseButton.LeftButton)
        self.settle()
        self.assertIsNone(tab.widget.md_surface.inline_assist.projection)
        self.assertIsNone(tab.widget.md_surface.inline_assist.panel)
        self.assertTrue(controller.disposed)
        self.assertEqual(self.service.cancelled, [request.request_id])
        self.service.delta.emit(request, '晚到文字')
        self.service.finished.emit(request, 'completed', '<think>內部思考</think>候選😀晚到文字')
        self.settle()
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())
        self.assertIsNone(tab.widget.md_surface.inline_assist.panel)

    def test_retry_uses_original_snapshot_and_replaces_projection(self):
        tab = self.page()
        self.select(tab)
        panel = self.start(tab)
        self.service.complete('第一候選')
        self.settle()
        self.assertTrue(panel.keep_button.isEnabled())
        QTest.mouseClick(panel.retry_button, Qt.MouseButton.LeftButton)
        self.settle()
        self.assertEqual(len(self.service.requests), 2)
        first, second = self.service.requests
        self.assertNotEqual(first.request_id, second.request_id)
        for request in (first, second):
            body = json.loads(request.input['messages'][-1]['content'])
            self.assertEqual(body['source_text'], SELECTED)
        self.assertEqual(self.projected(tab).projected_source, panel.target.snapshot.text)
        self.service.complete('第二候選')
        self.settle()
        projected = self.projected(tab).projected_source
        self.assertNotIn('第一候選', projected)
        self.assertEqual(projected, panel.target.snapshot.text.replace(SELECTED, '第二候選'))
        self.assertEqual(tab.widget.encoded(), RAW)
        QTest.mouseClick(panel.delete_button, Qt.MouseButton.LeftButton)
        self.settle()
        self.assertFalse(tab.widget.document().isUndoAvailable())

    def test_keep_replace_is_shared_single_undo_and_saves_reopens_exact_bytes(self):
        tab = self.page()
        right = self.w.duplicate_view(tab, True)
        self.w.focus_tab(tab)
        tab.widget.md_surface.editor.setFocus()
        self.select(tab)
        panel = self.start(tab)
        self.service.complete('保留😀\n第二行')
        self.settle()
        QTest.mouseClick(panel.keep_button, Qt.MouseButton.LeftButton)
        self.settle()
        expected = RAW.replace(SELECTED.encode(), '保留😀\r\n第二行'.encode())
        self.assertIsNone(tab.widget.md_surface.inline_assist.projection)
        self.assertEqual(right.widget.encoded(), expected)
        self.assertEqual(tab.path.read_bytes(), RAW)
        self.assertTrue(self.w.save_tab(tab))
        self.assertEqual(tab.path.read_bytes(), expected)
        right.widget.undo()
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())
        tab.widget.redo()
        self.assertEqual(right.widget.encoded(), expected)
        self.assertTrue(self.w.save_tab(tab))
        for item in (right, tab):
            self.assertTrue(self.w.close_tab(self.w.index_of(item.key), make_home=False, pane=self.w.pane_for(item)))
        reopened = self.open('Inline.md', retained=True)
        self.assertEqual(reopened.widget.encoded(), expected)

    def test_keep_menu_insert_is_one_undo(self):
        tab = self.page()
        self.select(tab)
        panel = self.start(tab)
        self.service.complete('接續😀')
        self.settle()
        menus = []
        with patch('prompt_calculus_studio.local_ai.text_assist_panel.RoundMenu.open_for',
                   lambda menu, *_args, **_kwargs: menus.append(menu)):
            QTest.mouseClick(panel.keep_menu_button, Qt.MouseButton.LeftButton)
            self.assertEqual(len(menus), 1)
            action = next(action for action in menus[0].actions() if action.text() == '接在原文後')
            action.trigger()
        self.settle()
        expected = RAW.replace(SELECTED.encode(), (SELECTED+'\r\n\r\n接續😀\r\n\r\n').encode())
        self.assertEqual(tab.widget.encoded(), expected)
        tab.widget.undo()
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())

    def test_shared_edit_invalidates_projection_and_rejects_late_candidate(self):
        tab = self.page()
        right = self.w.duplicate_view(tab, True)
        self.w.focus_tab(tab)
        tab.widget.md_surface.editor.setFocus()
        self.select(tab)
        panel = self.start(tab)
        request = self.service.requests[-1]
        self.service.delta.emit(request, '舊候選')
        self.settle()
        right.widget.moveCursor(QTextCursor.MoveOperation.End)
        right.widget.insertPlainText('使用者新增')
        self.settle()
        self.assertTrue(panel.controller.invalidated)
        self.assertIsNone(tab.widget.md_surface.inline_assist.projection)
        self.service.finished.emit(request, 'completed', '舊候選')
        self.settle()
        self.assertFalse(panel.keep_button.isEnabled())
        self.assertEqual(tab.widget.encoded(), RAW+'使用者新增'.encode())
        self.assertEqual(tab.path.read_bytes(), RAW)

    def test_scroll_narrow_view_and_hidden_page_do_not_detach_or_steal_focus(self):
        raw = RAW + ('\n'.join('其他段落 '+str(i) for i in range(100))).encode()
        tab = self.page(raw)
        self.select(tab)
        surface, panel = tab.widget.md_surface, self.panel(tab)
        self.w.resize(900, 700)
        self.settle()
        viewport = surface.editor.viewport()
        bounds = QRect(viewport.mapTo(surface, QPoint()), viewport.size())
        self.assertTrue(bounds.contains(panel.geometry()), (bounds, panel.geometry()))
        surface.editor.verticalScrollBar().setValue(surface.editor.verticalScrollBar().maximum())
        self.settle()
        self.assertFalse(panel.isVisible())
        surface.editor.verticalScrollBar().setValue(0)
        self.settle()
        self.assertTrue(panel.isVisible())
        other = self.open('Notes.md', retained=True, new_tab=True)
        self.settle()
        self.assertFalse(panel.isVisible())
        self.assertEqual(self.w.active_pane.strip.bar.tabData(self.w.active_pane.strip.bar.currentIndex()), other.key)
        self.assertEqual(tab.widget.encoded(), raw)

    def test_composition_and_read_mode_hide_actions_without_writing(self):
        tab = self.page()
        self.select(tab)
        surface, panel = tab.widget.md_surface, self.panel(tab)
        token = id(tab.widget)
        tab.widget.buffer.composing.add(token)
        tab.widget.buffer.compositionChanged.emit()
        self.settle()
        self.assertFalse(panel.isVisible())
        self.assertFalse(self.service.requests)
        tab.widget.buffer.composing.discard(token)
        tab.widget.buffer.compositionChanged.emit()
        self.settle()
        self.assertTrue(panel.isVisible())
        surface.apply_mode('read', focus=False)
        self.settle()
        self.assertIsNone(surface.inline_assist.panel)
        self.assertIsNone(surface.inline_assist.projection)
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())

    def test_hidden_running_page_can_finish_without_becoming_active(self):
        tab = self.page()
        self.select(tab)
        panel = self.start(tab)
        other = self.open('Notes.md', retained=True, new_tab=True)
        other.widget.md_surface.editor.setFocus()
        self.settle()
        self.service.complete('在背景完成的候選')
        self.settle()
        self.assertEqual(self.w.active_pane.strip.bar.tabData(self.w.active_pane.strip.bar.currentIndex()), other.key)
        self.assertFalse(panel.isVisible())
        self.assertFalse(tab.widget.md_surface.isVisible())
        self.assertTrue(other.widget.md_surface.editor.hasFocus())
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertTrue(panel.controller.can_apply)
        self.w.focus_tab(tab)
        self.settle()
        surface = tab.widget.md_surface
        view = surface.inline_assist.projection
        self.assertTrue(panel.isVisible(), dict(surface_visible=surface.isVisible(),
                        surface_hidden=surface.isHidden(), page_visible=tab.widget.isVisible(),
                        active=self.w.active_key, target=tab.key, pages_current=self.w.pages.currentWidget() is tab.widget,
                        projection_visible=view.isVisible(), current_is_projection=surface.stack.currentWidget() is view,
                        allowed=surface.inline_assist._allowed(), panel_rect=str(panel.geometry()),
                        end=view.end_position, cursor_rect=str(view.cursorRect()),
                        scroll=view.verticalScrollBar().value(), max_scroll=view.verticalScrollBar().maximum()))
        self.assertIn('在背景完成的候選', self.projected(tab).projected_source)

    def test_projection_blocks_editor_commands_and_restores_them_on_delete(self):
        tab = self.page()
        self.select(tab)
        panel = self.start(tab)
        self.service.complete('候選正文')
        self.settle()
        surface, projection = tab.widget.md_surface, self.projected(tab)
        before = projection.projected_source
        self.assertFalse(surface.toolbar.isEnabled())
        self.assertTrue(all(not shortcut.isEnabled() for shortcut in surface.command_shortcuts.values()))
        projection.setFocus()
        QTest.keyClick(projection, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(projection, Qt.Key.Key_Backspace)
        self.assertFalse(surface.apply([SourceEdit(0, 0, '不能寫入')]))
        self.settle()
        self.assertEqual(projection.projected_source, before)
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())
        QTest.mouseClick(panel.delete_button, Qt.MouseButton.LeftButton)
        self.settle()
        self.assertTrue(surface.toolbar.isEnabled())
        self.assertTrue(all(shortcut.isEnabled() for shortcut in surface.command_shortcuts.values()))

    def test_review_actions_stay_in_a_small_pill_above_the_frame_after_resize(self):
        tab = self.page()
        self.select(tab)
        panel = self.start(tab)
        self.service.complete('這是原位置的新候選文字。')
        self.settle()
        surface, projection = tab.widget.md_surface, self.projected(tab)

        def assert_above_frame():
            viewport = projection.viewport()
            origin = viewport.mapTo(surface, QPoint())
            frame = projection.review_rect().translated(origin)
            bounds = QRect(origin, viewport.size())
            self.assertTrue(panel.isVisible())
            self.assertTrue(bounds.contains(panel.geometry()), (bounds, panel.geometry()))
            self.assertLess(panel.geometry().bottom(), frame.top(), (panel.geometry(), frame))
            self.assertLess(panel.width(), viewport.width() * .75)
            self.assertTrue(panel.keep_button.isVisible())
            self.assertFalse(panel.instruction.isVisible())
            return frame

        wide = assert_above_frame()
        self.w.resize(900, 720)
        self.settle()
        narrow = assert_above_frame()
        self.assertLessEqual(narrow.width(), wide.width())
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())

    def test_comparison_keeps_old_text_visible_but_adopts_only_clean_candidate(self):
        tab = self.page()
        self.select(tab)
        panel = self.start(tab)
        candidate = '已整理的候選😀'
        self.service.complete(candidate)
        self.settle()
        projection = self.projected(tab)
        displayed = projection.toPlainText()
        self.assertIn(SELECTED, displayed)
        self.assertIn(candidate, displayed)
        old = QTextCursor(projection.document())
        old.setPosition(projection.original_range[0])
        old.setPosition(projection.original_range[1], QTextCursor.MoveMode.KeepAnchor)
        self.assertEqual(old.selectedText(), SELECTED)
        self.assertTrue(old.charFormat().fontStrikeOut())
        new = QTextCursor(projection.document())
        new.setPosition(projection.candidate_range[0])
        new.setPosition(projection.candidate_range[1], QTextCursor.MoveMode.KeepAnchor)
        self.assertEqual(new.selectedText(), candidate)
        self.assertFalse(new.charFormat().fontStrikeOut())
        highlight = new.charFormat().background()
        self.assertNotEqual(highlight.style(), Qt.BrushStyle.NoBrush)
        self.assertGreater(highlight.color().blue(), highlight.color().green())
        self.assertGreater(highlight.color().blue(), highlight.color().red())
        expected_source = panel.target.snapshot.text.replace(SELECTED, candidate)
        self.assertEqual(projection.projected_source, expected_source)
        self.assertNotIn(SELECTED, projection.projected_source)
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertEqual(tab.path.read_bytes(), RAW)
        QTest.mouseClick(panel.keep_button, Qt.MouseButton.LeftButton)
        self.settle()
        self.assertEqual(tab.widget.buffer.snapshot().text, expected_source)
        self.assertEqual(tab.widget.encoded(), RAW.replace(SELECTED.encode(), candidate.encode()))
        tab.widget.undo()
        self.assertEqual(tab.widget.encoded(), RAW)
        self.assertFalse(tab.widget.document().isUndoAvailable())

