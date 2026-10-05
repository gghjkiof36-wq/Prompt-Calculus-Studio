"""Collapsed native sidebars can be borrowed without moving page content."""
import unittest

from PySide6.QtCore import QElapsedTimer, QEvent, QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QSplitter, QVBoxLayout, QWidget

from prompt_calculus_studio.sidebar_peek import SidebarPeekController

APP=QApplication.instance() or QApplication([])


class SidebarPeekTests(unittest.TestCase):
    def setUp(self):
        self.w=QWidget(); self.w.resize(800,500); self.w.state={'settings':{'visual_palette':'graphite'}}
        layout=QVBoxLayout(self.w); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        self.header=QLabel('Navigation'); self.header.setFixedHeight(48); layout.addWidget(self.header)
        self.body=QWidget(); layout.addWidget(self.body,1)
        self.body_layout=QHBoxLayout(self.body); self.body_layout.setContentsMargins(0,0,0,0); self.body_layout.setSpacing(0)
        self.side=QFrame(); self.side.setFixedWidth(220)
        side_layout=QVBoxLayout(self.side); self.editor=QLineEdit('kept selection'); side_layout.addWidget(self.editor); side_layout.addStretch()
        self.content=QFrame(); self.content.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.body_layout.addWidget(self.side); self.body_layout.addWidget(self.content,1); self.side.hide()
        self.target=self.side
        self.peek=SidebarPeekController(self.w,lambda:self.target,open_delay=40,close_delay=50)
        self.w.show(); QTest.qWait(30); self.w.activateWindow(); self.content.setFocus()
        QTest.mouseMove(self.content,QPoint(400,200)); QTest.qWait(10)

    def tearDown(self):
        self.peek.shutdown(); self.w.close(); self.w.deleteLater(); APP.processEvents()

    def enter_edge(self):QTest.mouseMove(self.w,QPoint(2,100))

    def open(self):
        QTest.mouseMove(self.content,QPoint(400,200))
        self.enter_edge(); QTest.qWait(100); self.assertTrue(self.peek.is_open)

    def test_dwell_is_delayed_and_open_overlay_does_not_resize_content(self):
        geometry=self.content.geometry(); parent=self.side.parentWidget(); minimum=self.side.minimumSize(); maximum=self.side.maximumSize()
        self.enter_edge(); QTest.qWait(10); self.assertFalse(self.peek.is_open)
        QTest.qWait(55); self.assertTrue(self.peek.is_open)
        self.assertEqual(self.content.geometry(),geometry); self.assertEqual(self.peek.overlay.y(),48)
        self.assertGreaterEqual(self.peek.overlay.width(),200); self.assertLessEqual(self.peek.overlay.width(),340)
        image=self.peek.overlay.grab().toImage(); self.assertEqual(image.pixelColor(4,image.height()//2).alpha(),255)
        self.assertTrue(self.peek.close()); self.assertFalse(self.peek.is_open)
        self.assertIs(self.side.parentWidget(),parent); self.assertEqual(self.body_layout.indexOf(self.side),0)
        self.assertTrue(self.side.isHidden()); self.assertEqual((self.side.minimumSize(),self.side.maximumSize()),(minimum,maximum))
        self.assertEqual(self.content.geometry(),geometry); self.assertEqual(self.editor.text(),'kept selection')

    def test_leaving_edge_or_dragging_does_not_open_sidebar(self):
        self.enter_edge(); QTest.qWait(10); QTest.mouseMove(self.content,QPoint(100,200)); QTest.qWait(70)
        self.assertFalse(self.peek.is_open)
        QTest.mousePress(self.content,Qt.MouseButton.LeftButton,pos=QPoint(100,200))
        self.enter_edge(); QTest.qWait(70); self.assertFalse(self.peek.is_open)
        QTest.mouseRelease(self.content,Qt.MouseButton.LeftButton,pos=QPoint(100,200))

    def test_leave_delay_and_editor_focus_keep_active_sidebar_open(self):
        self.open(); self.editor.setFocus(); QTest.mouseMove(self.content,QPoint(400,200)); QTest.qWait(80)
        self.assertTrue(self.peek.is_open)
        self.content.setFocus(); QTest.qWait(15); self.assertTrue(self.peek.is_open)
        QTest.qWait(65); self.assertFalse(self.peek.is_open)

    def test_popup_owned_by_sidebar_keeps_it_open_until_popup_closes(self):
        self.open(); menu=QMenu(self.side); menu.addAction('Folder action')
        menu.popup(self.side.mapToGlobal(QPoint(180,80))); QTest.qWait(20)
        QTest.mouseMove(self.content,QPoint(500,300)); QTest.qWait(80); self.assertTrue(self.peek.is_open)
        closed=QSignalSpy(self.peek.close_timer.timeout)
        menu.close(); self.content.setFocus()
        self.assertFalse(menu.isVisible()); self.assertIsNone(APP.activePopupWidget())
        self.assertIs(APP.focusWidget(),self.content)
        # The 50 ms single-shot is correctly armed by popup Hide, but on a busy
        # event loop a fixed 80 ms qWait can end before its timeout is delivered.
        # Wait for the observable close, keeping a bound and the real timer.
        elapsed=QElapsedTimer();elapsed.start()
        while self.peek.is_open and elapsed.elapsed()<1000:QTest.qWait(10)
        self.assertFalse(self.peek.is_open,('sidebar did not close',self.peek.close_timer.remainingTime()))
        self.assertEqual(closed.count(),1)
        self.assertIs(self.side.parentWidget(),self.body)
        self.assertEqual(self.body_layout.indexOf(self.side),0); self.assertTrue(self.side.isHidden())
        menu.deleteLater()

    def test_pin_restores_original_layout_and_visible_sidebars_do_not_peek(self):
        self.open(); self.assertTrue(self.peek.pin()); QTest.qWait(20)
        self.assertFalse(self.peek.is_open); self.assertFalse(self.side.isHidden()); self.assertIs(self.side.parentWidget(),self.body)
        self.assertEqual(self.body_layout.indexOf(self.side),0); self.assertEqual(self.side.width(),220)
        self.enter_edge(); QTest.qWait(70); self.assertFalse(self.peek.is_open)

    def test_route_change_cancels_pending_target_and_splitter_order_is_restored(self):
        self.enter_edge(); self.target=None; QTest.qWait(70); self.assertFalse(self.peek.is_open)
        self.body_layout.removeWidget(self.side); self.body_layout.removeWidget(self.content)
        splitter=QSplitter(); self.body_layout.addWidget(splitter)
        splitter.addWidget(self.side); splitter.addWidget(self.content); self.side.hide(); self.target=self.side
        QTest.qWait(20); self.open(); self.peek.close(); QTest.qWait(20)
        self.assertIs(self.side.parentWidget(),splitter); self.assertEqual(splitter.indexOf(self.side),0)
        self.assertEqual(splitter.indexOf(self.content),1); self.assertTrue(self.side.isHidden())
        self.open(); self.peek.pin(); QTest.qWait(20)
        self.assertGreater(splitter.sizes()[0],0); self.assertFalse(self.side.isHidden())

    def test_nested_box_and_grid_layout_slots_are_found_and_restored(self):
        self.body_layout.removeWidget(self.side); self.body_layout.removeWidget(self.content)
        column=QVBoxLayout(); nested=QHBoxLayout(); self.body_layout.addLayout(column)
        column.addWidget(QLabel('Section')); column.addLayout(nested)
        nested.addWidget(self.side); nested.addWidget(self.content,3); self.side.hide()
        self.side.setFixedWidth(240); QTest.qWait(20)
        before=self.content.geometry(); self.open()
        self.assertEqual(self.peek.overlay.width(),240); self.assertEqual(self.content.geometry(),before)
        self.peek.close(); QTest.qWait(10); self.assertEqual(nested.indexOf(self.side),0); self.assertEqual(nested.stretch(1),3)
        nested.removeWidget(self.side); nested.removeWidget(self.content)
        grid=QGridLayout(); nested.addLayout(grid); grid.addWidget(self.side,0,0,2,1); grid.addWidget(self.content,0,1,2,1); self.side.hide()
        QTest.qWait(20); self.open(); self.peek.close()
        self.assertEqual(grid.getItemPosition(grid.indexOf(self.side)),(0,0,2,1))

    def test_changed_provider_closes_an_open_sidebar_without_a_stale_overlay(self):
        self.open(); self.target=None
        QTest.mouseMove(self.content,QPoint(500,200)); QTest.qWait(20)
        self.assertFalse(self.peek.is_open); self.assertTrue(self.side.isHidden())
        self.assertIs(self.side.parentWidget(),self.body)

    def test_shutdown_releases_tracking_and_prevents_future_hover(self):
        self.assertTrue(self.content.hasMouseTracking()); self.open(); self.peek.shutdown()
        self.assertFalse(self.content.hasMouseTracking()); self.assertFalse(self.peek.is_open)
        QTest.mouseMove(self.content,QPoint(400,200)); self.enter_edge(); QTest.qWait(70)
        self.assertFalse(self.peek.is_open); self.assertFalse(self.peek.open_timer.isActive())

    def test_generic_enter_event_from_cursor_recovery_is_accepted(self):
        APP.sendEvent(self.editor,QEvent(QEvent.Type.Enter))
        self.assertFalse(self.peek.is_open)

    def test_hover_entry_uses_six_pixels_and_pending_dwell_tolerates_ten(self):
        QTest.mouseMove(self.w,QPoint(8,100)); QTest.qWait(70); self.assertFalse(self.peek.is_open)
        self.enter_edge(); QTest.qWait(15); QTest.mouseMove(self.w,QPoint(8,100)); QTest.qWait(70)
        self.assertTrue(self.peek.is_open)

    def test_pinned_narrow_overlay_ignores_leave_and_outside_click_until_closed(self):
        self.w.resize(800,640); QTest.qWait(20); before=self.content.geometry()
        self.assertTrue(self.peek.show_pinned()); self.assertTrue(self.peek.is_pinned_overlay)
        self.assertEqual(self.content.geometry(),before)
        QTest.mouseMove(self.content,QPoint(500,200)); QTest.qWait(80)
        QTest.mouseClick(self.content,Qt.MouseButton.LeftButton,pos=QPoint(500,200)); QTest.qWait(80)
        self.assertTrue(self.peek.is_open); self.assertFalse(self.peek.close_timer.isActive())
        self.w.resize(780,600); QTest.qWait(20)
        self.assertEqual(self.peek.overlay.height(),self.w.height()-48)
        self.peek.close(); self.assertFalse(self.peek.is_pinned_overlay); self.assertTrue(self.side.isHidden())

    def test_hover_can_be_pinned_as_overlay_and_uses_opaque_sidebar_color(self):
        from prompt_calculus_studio.theme import visual_tokens
        self.open(); original=self.side.parentWidget(); self.peek.show_pinned()
        self.assertTrue(self.peek.is_pinned_overlay); self.assertIs(self.side.parentWidget(),original)
        pixel=self.peek.overlay.grab().toImage().pixelColor(4,self.peek.overlay.height()//2)
        self.assertEqual(pixel.name(),visual_tokens(self.w.state['settings'])['sidebar']); self.assertEqual(pixel.alpha(),255)


class NativeSidebarPeekTests(unittest.TestCase):
    from test_media_layout import MediaLayoutTests as _Fixture
    setUp=_Fixture.setUp
    tearDown=_Fixture.tearDown
    settle=_Fixture.settle

    def open_native(self):
        self.w.sidebar_peek.open_timer.setInterval(40); self.w.sidebar_peek.close_timer.setInterval(50)
        QTest.mouseMove(self.w,QPoint(self.w.width()//2,200))
        QTest.mouseMove(self.w,QPoint(2,100)); QTest.qWait(75)
        self.assertTrue(self.w.sidebar_peek.is_open)

    def test_settings_nested_sidebar_peeks_then_global_button_pins(self):
        self.w.settings('appearance'); self.settle(); p=self.w.settings_page
        if p.navigation_area.isVisible():self.w.studio_navigation.sidebar.click()
        self.settle(); before=p.reading.geometry(); original_parent=p.navigation_area.parentWidget()
        self.open_native(); self.assertIs(self.w.sidebar_peek.target,p.navigation_area)
        self.assertEqual(p.reading.geometry(),before)
        QTest.mouseClick(self.w.studio_navigation.sidebar,Qt.MouseButton.LeftButton); self.settle()
        self.assertFalse(self.w.sidebar_peek.is_open); self.assertTrue(p.navigation_area.isVisible()); self.assertTrue(p.manual_navigation)
        self.assertIs(p.navigation_area.parentWidget(),original_parent)
        self.w.studio_navigation.sidebar.click(); self.settle(); self.open_native()
        self.w.set_interface_mode('canvas'); self.settle()
        self.assertFalse(self.w.sidebar_peek.is_open); self.assertIs(p.navigation_area.parentWidget(),original_parent)

    def test_gallery_nested_sidebar_peeks_and_pins_at_original_location(self):
        self.w.show_page(self.w.gallery); self.settle()
        sidebar=self.w.gallery.folder_panel
        if sidebar.isVisible():self.w.studio_navigation.sidebar.click()
        self.settle(); original_parent=sidebar.parentWidget(); before=self.w.gallery.media_workspace.geometry()
        self.open_native(); self.assertIs(self.w.sidebar_peek.target,sidebar)
        self.assertEqual(self.w.gallery.media_workspace.geometry(),before)
        QTest.mouseClick(self.w.studio_navigation.sidebar,Qt.MouseButton.LeftButton); self.settle()
        self.assertFalse(self.w.sidebar_peek.is_open); self.assertTrue(sidebar.isVisible())
        self.assertIs(sidebar.parentWidget(),original_parent); self.assertTrue(self.w.gallery.sidebar_expanded)


if __name__=='__main__':unittest.main()
