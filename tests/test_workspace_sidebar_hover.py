"""The real workspace borrows its live sidebar without moving document views."""
import copy
import unittest
from unittest.mock import patch
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

    def hover_route(self, route):
        entry = self.w.routes[route]
        QTest.mouseMove(entry, entry.rect().center())
        for _ in range(70):
            QTest.qWait(10)
            if self.w.sidebar_peek.is_open and self.w.sidebar_peek.hover_route == route:
                QTest.qWait(160)
                return self.w.sidebar_peek.target
        self.fail(str((route, self.errors, self.w.sidebar_peek.hover_route,
                       self.w.sidebar_peek.is_open, self.w.sidebar_peek._pending)))

    def test_export_can_preview_gallery_without_loading_images_then_open_album(self):
        self.collapse()
        self.w.open_route('export')
        self.events()
        host = self.w.utility
        gallery = host.gallery.load()
        gallery.catalog.put('album', dict(id='peek-album', name='懸停資料夾'))
        history = list(self.w.history)
        content = host.surface_stack.currentWidget()
        with patch.object(gallery, 'refresh', wraps=gallery.refresh) as refresh:
            target = self.hover_route('gallery')
            self.assertIs(target, gallery.folder_panel)
            self.assertEqual(self.w.active_route, 'export')
            self.assertIs(host.surface_stack.currentWidget(), content)
            self.assertEqual(self.w.history, history)
            self.assertEqual(refresh.call_count, 0)
            item = gallery.albums.findItems('懸停資料夾', Qt.MatchFlag.MatchExactly)[0]
            QTest.mouseClick(gallery.albums.viewport(), Qt.MouseButton.LeftButton,
                            pos=gallery.albums.visualItemRect(item).center())
            self.events()
            self.assertEqual(self.w.active_route, 'gallery')
            self.assertEqual(gallery.album, 'peek-album')
            self.assertEqual(refresh.call_count, 1)
            self.assertEqual(len(self.w.history), len(history)+1)
            self.assertIs(target.parentWidget(), gallery.sidebar_split)
            self.assertFalse(self.w.sidebar_peek.is_open)

    def test_gallery_can_preview_home_and_switch_hover_destinations(self):
        self.collapse()
        self.w.open_route('gallery')
        self.events()
        history = list(self.w.history)
        for route in ('home', 'export', 'home'):
            target = self.hover_route(route)
            self.assertEqual(self.w.active_route, 'gallery')
            self.assertEqual(self.w.history, history)
            if route == 'home':
                self.assertIs(target, self.w.sidebar)
                self.assertTrue(self.w.tree.isVisible())
        self.away()
        QTest.qWait(350)
        self.assertFalse(self.w.sidebar_peek.is_open)
        self.assertIs(self.w.sidebar.parentWidget(), self.w.home_split)
        self.assertEqual(self.w.active_route, 'gallery')

    def test_settings_preview_leaves_active_explore_page_until_clicked(self):
        self.collapse()
        self.w.open_route('explore')
        self.events()
        settings = self.w.utility.settings_page
        active_page = settings.pages.currentWidget()
        history = list(self.w.history)
        target = self.hover_route('settings')
        self.assertIsNot(target, settings.navigation_area)
        self.assertEqual(target.heading.text(), '設定')
        self.assertEqual(settings.section, 'explore')
        self.assertIs(settings.pages.currentWidget(), active_page)
        self.assertEqual(self.w.history, history)
        self.assertFalse(settings.civitai.is_loaded)
        item = target.navigation.item(0)
        QTest.mouseClick(target.navigation.viewport(), Qt.MouseButton.LeftButton,
                        pos=target.navigation.visualItemRect(item).center())
        self.events()
        self.assertEqual(self.w.active_route, 'settings')
        self.assertEqual(settings.section, 'appearance')
        self.assertEqual(len(self.w.history), len(history)+1)

    def test_home_preview_file_click_reuses_open_document(self):
        self.collapse()
        self.tab.widget.insertPlainText('unsaved ')
        before = self.tab.widget.toPlainText()
        count = len(self.w.tabs_by_key)
        self.w.open_route('export')
        self.events()
        self.hover_route('home')
        matches = self.w.tree.findItems('Notes', Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)
        self.assertTrue(matches)
        QTest.mouseClick(self.w.tree.viewport(), Qt.MouseButton.LeftButton,
                        pos=self.w.tree.visualItemRect(matches[0]).center())
        self.events()
        self.assertEqual(self.w.active_route, 'home')
        self.assertEqual(self.w.active_key, self.tab.key)
        self.assertEqual(len(self.w.tabs_by_key), count)
        self.assertEqual(self.tab.widget.toPlainText(), before)
        self.assertTrue(self.tab.widget.dirty)

    def test_top_navigation_previews_explore_without_leaving_settings(self):
        self.collapse()
        self.w.open_route('settings')
        self.w.set_navigation_position('top')
        self.events()
        settings = self.w.utility.settings_page
        original = settings.pages.currentWidget()
        history = list(self.w.history)
        target = self.hover_route('explore')
        self.assertEqual(target.heading.text(), '探索')
        self.assertEqual(self.w.active_route, 'settings')
        self.assertEqual(settings.section, 'appearance')
        self.assertIs(settings.pages.currentWidget(), original)
        self.assertEqual(self.w.history, history)
        self.assertFalse(settings.civitai.is_loaded)

    def test_edge_dwell_leave_and_document_geometry(self):
        self.collapse()
        QTest.mouseMove(self.w, QPoint(4, 200))
        QTest.qWait(30)
        self.assertFalse(self.w.sidebar_peek.is_open)
        QTest.qWait(150)
        self.assertTrue(self.w.sidebar_peek.is_open)
        self.assertEqual(self.tab.widget.geometry(), self.before)
        QTest.qWait(120)  # The opening animation completes after the dwell.
        self.assertGreater(self.w.sidebar_peek.overlay.x(), self.w.rail.width())
        self.assertLess(self.w.sidebar_peek.overlay.geometry().bottom(), self.w.height()-1)
        self.assertIs(self.w.notebook.selector.parentWidget(), self.w.tree_heading)
        self.away()
        QTest.qWait(45)
        self.assertTrue(self.w.sidebar_peek.is_open)
        QTest.qWait(280)
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

    def test_document_gutter_triggers_right_fade_in_and_left_fade_out(self):
        self.collapse()
        peek = self.w.sidebar_peek
        point = QPoint(self.w.rail.width()+16, 210)
        QTest.mouseMove(self.w, point)
        for _ in range(35):
            QTest.qWait(5)
            if peek.is_open:
                break
        self.assertTrue(peek.is_open)
        initial_x = peek.overlay.x()
        QTest.qWait(180)
        final_x = peek.overlay.x()
        self.assertGreater(final_x, initial_x)
        self.assertFalse(peek.effect.isEnabled())
        self.assertEqual(self.tab.widget.geometry(), self.before)
        self.away()
        QTest.qWait(190)
        self.assertTrue(peek.is_open)
        self.assertLess(peek.overlay.x(), final_x)
        self.assertLess(peek.effect.opacity(), 1.)
        QTest.qWait(130)
        self.assertFalse(peek.is_open)
        self.assertEqual(self.tab.widget.geometry(), self.before)

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
