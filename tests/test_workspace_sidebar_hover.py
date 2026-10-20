"""The real workspace borrows its live sidebar without moving document views."""
import copy
import unittest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit
import test_workspace_panes as fixture


class WorkspaceSidebarHoverTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def collapse(self):
        self.tab = self.open('Notes.md')
        self.w.sidebar_preference = False
        self.w.sync_sidebar()
        self.w.set_sidebar_hover(True, 100, 150)
        self.events()
        self.away()
        self.before = self.tab.widget.geometry()

    def away(self):
        QTest.mouseMove(self.w, QPoint(self.w.width()-90, 260))
        self.tab.widget.md_surface.editor.setFocus()

    def reveal(self):
        QTest.mouseMove(self.w, QPoint(4, 200))
        for _ in range(80):
            QTest.qWait(10)
            if self.w.sidebar_peek.is_open:return
        peek=self.w.sidebar_peek
        self.fail(str((self.errors,peek._edge_blocked,peek._pending,self.w.sidebar.isHidden(),
                       peek.open_timer.isActive())))

    def test_edge_dwell_leave_and_document_geometry(self):
        self.collapse()
        QTest.mouseMove(self.w, QPoint(4, 200))
        QTest.qWait(30)
        self.assertFalse(self.w.sidebar_peek.is_open)
        QTest.qWait(150)
        self.assertTrue(self.w.sidebar_peek.is_open)
        self.assertEqual(self.tab.widget.geometry(), self.before)
        self.assertGreater(self.w.sidebar_peek.overlay.x(), self.w.rail.width())
        self.assertLess(self.w.sidebar_peek.overlay.geometry().bottom(), self.w.height()-1)
        self.assertIs(self.w.notebook.selector.parentWidget(), self.w.tree_heading)
        self.away()
        QTest.qWait(45)
        self.assertTrue(self.w.sidebar_peek.is_open)
        QTest.qWait(180)
        self.assertFalse(self.w.sidebar_peek.is_open)
        self.assertTrue(self.w.sidebar.isHidden())
        self.assertIs(self.w.sidebar.parentWidget(), self.w.home_split)
        self.assertEqual(self.tab.widget.geometry(), self.before)

    def test_inline_edit_survives_pointer_leave_and_pin_survives_later_leave(self):
        self.collapse(); self.reveal()
        field=QLineEdit(self.w.sidebar)
        field.setText('保留中的名稱')
        self.w.sidebar.layout().addWidget(field)
        field.show();field.setFocus()
        QTest.mouseMove(self.w, QPoint(self.w.width()-90, 260))
        QTest.qWait(220)
        self.assertTrue(self.w.sidebar_peek.is_open)
        self.assertEqual(field.text(), '保留中的名稱')
        self.w.toggle_sidebar()
        self.events()
        self.assertFalse(self.w.sidebar_peek.is_open)
        self.assertTrue(self.w.sidebar.isVisible())
        self.away();QTest.qWait(220)
        self.assertTrue(self.w.sidebar.isVisible())

    def test_disabled_feature_and_drag_do_not_reveal(self):
        self.collapse()
        self.w.set_sidebar_hover(False, 100, 150)
        QTest.mouseMove(self.w, QPoint(4, 200));QTest.qWait(180)
        self.assertFalse(self.w.sidebar_peek.is_open)
        self.away();self.w.set_sidebar_hover(True, 100, 150)
        QTest.mousePress(self.w, Qt.MouseButton.LeftButton, pos=QPoint(500, 260))
        QTest.mouseMove(self.w, QPoint(4, 200));QTest.qWait(180)
        self.assertFalse(self.w.sidebar_peek.is_open)
        QTest.mouseRelease(self.w, Qt.MouseButton.LeftButton, pos=QPoint(500, 260))
        self.away();self.reveal()
        self.w.set_sidebar_hover(False, 100, 150)
        self.assertFalse(self.w.sidebar_peek.is_open)
        self.assertTrue(self.w.sidebar.isHidden())

    def test_settings_controls_persist_and_route_change_restores_sidebar(self):
        self.collapse();self.reveal()
        self.w.open_route('settings', 'appearance');self.events()
        self.assertFalse(self.w.sidebar_peek.is_open)
        self.assertIs(self.w.sidebar.parentWidget(), self.w.home_split)
        preferences=self.w.utility.settings_page.preferences
        preferences.sidebar_auto_hide.setChecked(False)
        preferences.sidebar_open_delay.setValue(700)
        preferences.sidebar_close_delay.setValue(900)
        self.assertFalse(preferences.sidebar_open_delay.isEnabled())
        self.assertEqual((self.w.sidebar_auto_hide,self.w.sidebar_open_delay,self.w.sidebar_close_delay),
                         (False,700,900))
        self.w.sidebar_auto_hide=True;self.w.sidebar_open_delay=100;self.w.sidebar_close_delay=100
        self.w.load_ui_preferences()
        self.assertEqual((self.w.sidebar_auto_hide,self.w.sidebar_open_delay,self.w.sidebar_close_delay),
                         (False,700,900))

    def test_title_and_disclosure_have_compact_horizontal_layout(self):
        self.w.sidebar_preference=True;self.w.sync_sidebar();self.events()
        selector=self.w.notebook.selector
        self.assertEqual(selector.text(),'內容')
        self.assertFalse(selector.arrow.isNull())
        self.assertGreaterEqual(selector.width(),selector.fontMetrics().horizontalAdvance('內容')+28)
        self.assertLessEqual(selector.height(),36)

    def test_sidebar_menu_keeps_overlay_open_then_leaving_closes_it(self):
        self.collapse();self.reveal()
        selector=self.w.notebook.selector;menu=selector.menu()
        menu.popup(selector.mapToGlobal(QPoint(0,selector.height())))
        QTest.qWait(40);self.away();QTest.qWait(220)
        self.assertTrue(self.w.sidebar_peek.is_open)
        menu.close();self.away()
        for _ in range(100):
            if not self.w.sidebar_peek.is_open:break
            QTest.qWait(10)
        self.assertFalse(self.w.sidebar_peek.is_open)

    def test_canvas_camera_data_and_history_survive_hover_in_both_navigation_positions(self):
        self.tab=self.open('Street.pcs-workflow')
        canvas=self.tab.host.canvas
        self.w.sidebar_preference=False;self.w.sync_sidebar()
        self.w.set_sidebar_hover(True,100,150)
        for position in ('side','top'):
            self.w.set_navigation_position(position);self.events()
            self.away_canvas()
            view=canvas.view
            before=(view.transform(),view.mapToScene(view.viewport().rect().center()),view.geometry())
            data=copy.deepcopy(canvas.data());history=copy.deepcopy(canvas.undo_stack)
            self.reveal()
            self.assertEqual((view.transform(),view.mapToScene(view.viewport().rect().center()),view.geometry()),before)
            self.w.sidebar_peek.close();self.events()
            self.assertEqual(canvas.data(),data);self.assertEqual(canvas.undo_stack,history)
            self.assertEqual((view.transform(),view.mapToScene(view.viewport().rect().center()),view.geometry()),before)

    def away_canvas(self):
        QTest.mouseMove(self.w,QPoint(self.w.width()-90,260))
        self.tab.host.canvas.view.setFocus()


if __name__ == '__main__':
    unittest.main()
