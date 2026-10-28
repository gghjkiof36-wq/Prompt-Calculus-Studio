"""Bounded chat pointer/keyboard/menu contracts; no files, model or service."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace
import unittest
import weakref
from PySide6.QtCore import QObject, QPoint, Signal, Qt, QEvent
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from prompt_calculus_studio.local_ai.chat_page import ChatPage, ChatModelMenu


class Service(QObject):
    changed = Signal()
    state, error, selected_model, can_submit, approval_mode = 'unloaded', '', 'one', True, 'ask'
    models = [dict(id='one', name='DeepSeek R1 8B Q4_K_M', path='unused-one.gguf'),
              dict(id='two', name='Qwen compact', path='unused-two.gguf')]
    def __init__(self):
        super().__init__()
        self.selected = []
    def select_model(self, identity):
        self.selected.append(identity); self.selected_model = identity
        self.changed.emit(); return True


class Document(QObject):
    changed = Signal()
    busy, edit_locked, notice, error = False, False, '', ''
    def __init__(self):
        super().__init__()
        self.service, self.views = Service(), weakref.WeakSet()
        self.payload = dict(draft='', turns=[dict(id=str(i), text=f'第 {i + 1} 個問題', attempts=[
            dict(id=f'a{i}', text='<think>先釐清條件。</think>\n這是回答。\n\n' + '保留段落。\n\n' * 3,
                 status='complete', model='one')]) for i in range(5)])
    def release_edit(self, _view): pass
    def message_version_count(self, turn): return 2 if turn == '0' else 1
    def set_draft(self, value): self.payload['draft'] = value; return True


class ChatHoverAndMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.doc = Document()
        self.page = ChatPage(self.doc, SimpleNamespace(display_settings=lambda: dict(ui_size=12)))
        self.page.resize(700, 700); self.page.show()
        QTest.qWait(120)
        self.page._follow_output = False
        self.page.scroll.verticalScrollBar().setValue(0)
        self.turn = self.page._turns['0']
        self.vp = self.page.scroll.viewport()
        self.page.composer.setFocus()
        QTest.qWait(40)

    def tearDown(self):
        popup = self.app.activePopupWidget()
        if popup: popup.close()
        self.page.close(); self.page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def move(self, widget, point):
        QTest.mouseMove(widget, point); QTest.qWait(30)

    def test_entire_viewport_height_band_reveals_without_layout_shift(self):
        y = self.turn.user_group.mapTo(self.vp, QPoint()).y() + 10
        before = (self.turn.geometry(), self.page.scroll.verticalScrollBar().maximum())
        for x in (2, self.vp.width() - 3):
            self.move(self.vp, QPoint(x, y))
            self.assertTrue(self.turn.user_copy_button._revealed)
            self.assertTrue(self.turn.user_edit_button._revealed)
            self.assertEqual(before, (self.turn.geometry(), self.page.scroll.verticalScrollBar().maximum()))
        self.move(self.page.composer, QPoint(20, 15))
        self.assertFalse(self.turn.user_copy_button._revealed)
        self.assertTrue(self.turn.user_versions_button.isVisible())
        self.assertTrue(next(iter(self.turn.attempts.values())).actions.isVisible())

    def test_child_text_hover_propagates_to_full_band(self):
        self.move(self.turn.user_text.viewport(), QPoint(10, 10))
        self.assertTrue(self.turn.user_edit_button._revealed)
        self.move(self.page.composer, QPoint(20, 15))
        self.assertFalse(self.turn.user_edit_button._revealed)

    def test_mouse_click_does_not_keep_actions_visible_after_leaving(self):
        self.move(self.turn.user_copy_button, QPoint(15, 15))
        QTest.mouseClick(self.turn.user_copy_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.app.clipboard().text(), self.turn._user_source)
        self.move(self.page.composer, QPoint(20, 15))
        self.assertFalse(self.turn.user_copy_button._revealed)
        self.assertFalse(self.turn.user_edit_button._revealed)

    def test_keyboard_can_reveal_and_activate_reserved_controls(self):
        self.move(self.page.composer, QPoint(20, 15))
        copy = self.turn.user_copy_button
        copy.setFocus(Qt.FocusReason.TabFocusReason); QTest.qWait(20)
        self.assertTrue(copy._revealed)
        self.assertTrue(self.turn.user_edit_button._revealed)
        QTest.keyClick(copy, Qt.Key.Key_Space)
        self.assertEqual(self.app.clipboard().text(), self.turn._user_source)
        self.page.composer.setFocus(); QTest.qWait(20)
        self.assertFalse(copy._revealed)

    def test_scroll_under_stationary_pointer_recomputes_band(self):
        y = self.turn.user_group.mapTo(self.vp, QPoint()).y() + 10
        self.move(self.vp, QPoint(2, y))
        self.assertTrue(self.turn.user_edit_button._revealed)
        self.page.scroll.verticalScrollBar().setValue(150); QTest.qWait(60)
        self.assertFalse(self.turn.user_edit_button._revealed)

    def test_model_menu_compact_search_select_without_loading(self):
        menu = ChatModelMenu(self.page); menu.open_for(self.page.model_button); QTest.qWait(40)
        self.assertLessEqual(menu.width(), 365)
        self.assertTrue(all(entry.height() <= 30 for _, entry, _ in menu.choices))
        self.assertFalse(any('管理模型' in action.text() for action in menu.actions()))
        self.assertTrue(menu.choices[0][0].isChecked())
        menu.search.setText('qwen'); QTest.qWait(20)
        self.assertFalse(menu.choices[0][0].isVisible())
        self.assertTrue(menu.choices[1][0].isVisible())
        QTest.keyClick(menu.search, Qt.Key.Key_Down)
        QTest.keyClick(menu, Qt.Key.Key_Return); QTest.qWait(30)
        self.assertEqual(self.doc.service.selected, ['two'])
        self.assertEqual(self.doc.service.state, 'unloaded')

    def test_model_menu_opens_above_right_edge_and_no_result_keyboard_is_safe(self):
        menu = ChatModelMenu(self.page); menu.open_for(self.page.model_button); QTest.qWait(30)
        anchor = self.page.model_button.mapToGlobal(QPoint())
        self.assertLess(menu.y() + menu.height(), anchor.y())
        self.assertEqual(menu.x() + menu.width(), anchor.x() + self.page.model_button.width())
        menu.search.setText('no matching model'); QTest.qWait(20)
        self.assertTrue(menu.empty_action.isVisible())
        QTest.keyClick(menu.search, Qt.Key.Key_Down)
        QTest.keyClick(menu.search, Qt.Key.Key_Escape)
        self.assertFalse(self.doc.service.selected)


if __name__ == '__main__':
    unittest.main()
