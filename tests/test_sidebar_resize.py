"""Sidebar resizing persists across pages without changing the canvas document."""
import copy
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest

from prompt_studio.core import initial_state, validate_state
import test_media_layout as media_fixture
APP=media_fixture.APP


class SidebarResizeTests(unittest.TestCase):
    setUp = media_fixture.MediaLayoutTests.setUp
    tearDown = media_fixture.MediaLayoutTests.tearDown
    settle = media_fixture.MediaLayoutTests.settle

    def pinned(self):
        self.w.sidebar_pinned_preference=True;self.w.sync_context_sidebar();self.settle()
        return self.w.sidebar_resize

    def drag(self, width):
        controller=self.w.sidebar_resize;controller.sync();handle=controller.handle
        self.assertTrue(handle.isVisible())
        local=handle.rect().center();start=handle.mapToGlobal(local)
        end=QPointF(start.x()+width-controller.current_width(),start.y())
        QTest.mousePress(handle,Qt.MouseButton.LeftButton,pos=local)
        for fraction in (.1,.2,.3,.4,.5,.6,.7,.8,.9,1.):
            position=QPointF(start.x()+(end.x()-start.x())*fraction,start.y())
            APP.sendEvent(handle,QMouseEvent(QEvent.Type.MouseMove,QPointF(handle.mapFromGlobal(position.toPoint())),position,
                                            Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
            APP.processEvents()
        APP.sendEvent(handle,QMouseEvent(QEvent.Type.MouseButtonRelease,QPointF(handle.mapFromGlobal(end.toPoint())),end,
                                        Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier))
        self.settle()

    def test_dragged_width_is_shared_by_sidebar_destinations_and_canvas_opts_out(self):
        self.pinned();self.drag(360)
        self.assertEqual(self.w.state['settings']['context_sidebar_width'],360)
        for route in (lambda:self.w.settings('explore'),lambda:self.w.settings('appearance'),
                      lambda:self.w.show_page(self.w.clean_export),
                      lambda:self.w.show_page(self.w.gallery)):
            route();self.settle();self.w.sidebar_resize.sync()
            self.assertEqual(self.w.context_sidebar_target().width(),360)
            self.assertTrue(self.w.sidebar_resize.handle.isVisible())
        self.w.set_interface_mode('canvas');self.settle();self.w.sidebar_resize.sync()
        self.assertIsNone(self.w.context_sidebar_target());self.assertFalse(self.w.sidebar_resize.handle.isVisible())
        self.assertEqual(self.w.state['settings']['context_sidebar_width'],360)
        self.w.show_page(self.w.gallery);self.settle()
        self.assertEqual(self.w.context_sidebar_target().width(),360)
        self.drag(800);self.assertEqual(self.w.state['settings']['context_sidebar_width'],480)
        self.drag(10);self.assertEqual(self.w.state['settings']['context_sidebar_width'],220)

    def test_width_survives_storage_reopen(self):
        self.pinned();self.drag(370);self.assertTrue(self.w.persist())
        self.w.close();APP.processEvents()
        from prompt_studio.window import Window
        self.w=Window(self.temp.name);self.g=self.w.gallery
        self.w.resize(1440,900);self.w.show();self.w.settings('appearance');self.pinned()
        self.assertEqual(self.w.state['settings']['context_sidebar_width'],370)
        self.assertEqual(self.w.settings_page.navigation_area.width(),370)

    def test_rejected_close_keeps_resize_live_until_close_is_accepted(self):
        controller=self.pinned()
        with patch.object(self.w.models,'save',side_effect=RuntimeError('Synthetic save failure')),patch.object(self.w,'error'):
            self.assertFalse(self.w.close())
        self.assertTrue(self.w.isVisible());self.assertFalse(controller._stopped)
        self.drag(330);self.assertEqual(self.w.state['settings']['context_sidebar_width'],330)
        with patch.object(self.w,'persist',return_value=False):
            self.assertFalse(self.w.close())
        self.assertTrue(self.w.isVisible());self.assertFalse(controller._stopped)
        self.drag(380);self.assertEqual(self.w.state['settings']['context_sidebar_width'],380)
        self.assertTrue(self.w.close());self.assertTrue(controller._stopped)

    def test_close_during_drag_persists_last_width_before_observer_shutdown(self):
        controller=self.pinned()
        controller.begin(controller.handle.mapToGlobal(controller.handle.rect().center()))
        controller.resize_to(410)
        self.assertTrue(controller.dragging)
        self.assertEqual(self.w.state['settings']['context_sidebar_width'],240)
        self.assertTrue(self.w.close());self.assertTrue(controller._stopped)
        from prompt_studio.core import Storage
        store=Storage(self.temp.name)
        try:self.assertEqual(store.load()['settings']['context_sidebar_width'],410)
        finally:store.close()

    def test_resizing_gallery_sidebar_does_not_rebuild_thumbnail_rows(self):
        media_fixture.MediaLayoutTests.add_images(self)
        self.pinned();self.w.display_recovery.stop()
        items=[self.g.images.item(index) for index in range(self.g.images.count())]
        with patch.object(self.g,'refresh',wraps=self.g.refresh) as refresh:
            for width in (1380,1420,1440):
                self.w.resize(width,900);self.settle();self.w.sync_context_sidebar()
            self.drag(350);refresh.assert_not_called()
            self.w.sidebar_pinned_preference=False;self.w.sync_context_sidebar();self.settle()
            self.w.sidebar_pinned_preference=True;self.w.sync_context_sidebar();self.settle()
            refresh.assert_not_called()
        self.assertEqual([self.g.images.item(index) for index in range(self.g.images.count())],items)

    def test_resizing_peek_keeps_content_fixed_and_restores_live_sidebar(self):
        self.w.settings('appearance');self.w.sidebar_pinned_preference=False;self.w.sync_context_sidebar();self.settle()
        target=self.w.context_sidebar_target();parent=target.parentWidget();geometry=self.w.settings_page.reading.geometry()
        self.assertTrue(self.w.sidebar_peek._borrow(target));self.settle()
        self.drag(410)
        self.assertTrue(self.w.sidebar_peek.is_open);self.assertEqual(self.w.sidebar_peek.overlay.width(),410)
        self.assertEqual(self.w.settings_page.reading.geometry(),geometry)
        image=self.w.sidebar_peek.overlay.grab().toImage()
        self.assertEqual(image.pixelColor(4,image.height()//2).alpha(),255)
        self.w.sidebar_peek.close();self.settle()
        self.assertIs(target.parentWidget(),parent);self.assertTrue(target.isHidden())
        self.assertEqual(target.width(),410);self.assertEqual(self.w.settings_page.reading.geometry(),geometry)
        self.pinned();self.assertEqual(target.width(),410)

    def test_temporary_width_limits_do_not_overwrite_saved_preference(self):
        self.w.settings('appearance');self.pinned();self.drag(480)
        self.w.sidebar_pinned_preference=None;self.w.settings_page.manual_navigation=None
        self.w.resize(940,800);self.settle();self.w.sync_context_sidebar();self.settle()
        self.assertLessEqual(self.w.settings_page.navigation_area.width(),420)
        self.assertEqual(self.w.state['settings']['context_sidebar_width'],480)
        self.w.sidebar_pinned_preference=True;self.w.sync_context_sidebar();self.settle()
        self.assertTrue(self.w.sidebar_peek.is_pinned_overlay)
        self.assertLessEqual(self.w.sidebar_peek.overlay.width(),self.w.width()-64)
        self.w.resize(1440,900);self.settle();self.w.sync_context_sidebar();self.settle()
        self.assertFalse(self.w.sidebar_peek.is_open)
        self.assertEqual(self.w.settings_page.navigation_area.width(),480)

    def test_canvas_overlay_keeps_zoom_position_blank_draft_history_and_bindings(self):
        self.w.set_interface_mode('canvas');self.pinned();canvas=self.w.canvas;view=canvas.view
        from prompt_studio.drafts import edit
        output=next(iter(canvas.data()['outputs']));canvas.commit(lambda state:edit(state,'',output));self.settle()
        view.resetTransform();view.scale(.9,.9);view.centerOn(1100,400);self.settle()
        center=view.mapToScene(view.viewport().rect().center());transform=view.transform()
        data=copy.deepcopy(canvas.data());history=copy.deepcopy(canvas.undo_stack)
        geometry=self.w.canvas_content.geometry();width=self.w.state['settings']['context_sidebar_width']
        # Startup's delayed display recovery is unrelated to resizing and can
        # otherwise fire in the middle of this interaction on slower runners.
        self.w.display_recovery.stop()
        with patch.object(self.w,'apply_theme') as theme,patch.object(canvas,'refresh') as refresh:
            self.w.show_recent_sheet();self.settle()
            self.assertIsNone(self.w.context_sidebar_target());self.w.sidebar_resize.sync()
            self.assertFalse(self.w.sidebar_resize.handle.isVisible())
            self.assertEqual(self.w.recent_sheet.geometry(),self.w.surface_stack.rect())
            self.assertEqual(self.w.canvas_content.geometry(),geometry)
            self.w.close_recent_sheet();self.settle()
        self.assertEqual(view.transform(),transform)
        after=view.mapToScene(view.viewport().rect().center())
        self.assertLessEqual(abs(after.x()-center.x()),2);self.assertLessEqual(abs(after.y()-center.y()),2)
        self.assertEqual(canvas.data(),data);self.assertEqual(canvas.undo_stack,history)
        self.assertEqual(canvas.data()['outputs'][output]['draft'],'')
        self.assertEqual(self.w.canvas_content.geometry(),geometry)
        self.assertEqual(self.w.state['settings']['context_sidebar_width'],width)
        theme.assert_not_called();refresh.assert_not_called()


class SidebarWidthValidationTests(unittest.TestCase):
    def test_missing_values_keep_old_documents_valid_and_invalid_values_are_rejected(self):
        state=initial_state();state['settings'].pop('context_sidebar_width');state['settings'].pop('gallery_columns')
        validate_state(state)
        for key,bad in (('context_sidebar_width',True),('context_sidebar_width',219),('context_sidebar_width',481),
                        ('gallery_columns',True),('gallery_columns',7),('gallery_columns',21)):
            candidate=copy.deepcopy(state);candidate['settings'][key]=bad
            with self.assertRaises(ValueError):validate_state(candidate)


if __name__=='__main__':unittest.main()
