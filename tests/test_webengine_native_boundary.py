"""Browser loading must not change the workspace's Windows rendering surface."""
import os
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtGui import QSurface
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from PySide6.QtWebEngineWidgets import QWebEngineView

from prompt_calculus_studio.webengine_host import configure_canvas_composition, prepare_native_host
import test_markdown_reader_lifecycle as fixture


class NativeBrowserBoundaryTests(unittest.TestCase):
    setUpClass = classmethod(fixture.RichReaderLifecycleTests.setUpClass.__func__)
    setUp = fixture.RichReaderLifecycleTests.setUp
    make_window = fixture.RichReaderLifecycleTests.make_window
    dispose = fixture.RichReaderLifecycleTests.dispose
    tearDown = fixture.RichReaderLifecycleTests.tearDown
    wait = fixture.RichReaderLifecycleTests.wait
    assert_reading = fixture.RichReaderLifecycleTests.assert_reading
    observations = []

    def watch_shell(self):
        if self.app.platformName() != 'windows':
            self.skipTest('Windows native child / raster backing store')
        self.window.show()
        QTest.qWait(30)
        self.handle = int(self.window.winId())
        self.events = []
        events = self.events

        class Watch(QObject):
            def eventFilter(self, obj, event):
                if event.type() in (QEvent.Type.Hide, QEvent.Type.WinIdChange):
                    events.append(str(event.type()))
                return False

        self.watch = Watch(self.window)
        self.window.installEventFilter(self.watch)
        self.flag = QCoreApplication.testAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)
        self.check_shell('before')

    def check_shell(self, stage):
        self.assertEqual(int(self.window.winId()), self.handle)
        self.assertEqual(self.window.windowHandle().surfaceType(), QSurface.SurfaceType.RasterSurface)
        self.assertEqual(self.events, [])
        self.assertEqual(QCoreApplication.testAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings), self.flag)
        native = [w for w in self.window.findChildren(QWidget)
                  if w.testAttribute(Qt.WidgetAttribute.WA_NativeWindow)]
        self.assertTrue(all(isinstance(w, QWebEngineView) or w.objectName() == 'LocalChatContentHost' for w in native),
                        [(type(w).__name__, w.objectName()) for w in native])
        self.observations.append(dict(test=self.id(), stage=stage, shell_handle=self.handle,
            surface=str(self.window.windowHandle().surfaceType()), native_children=len(native),
            browsers=[dict(visible=w.isVisible(), surface=str(w.windowHandle().surfaceType())
                           if w.windowHandle() else None) for w in native]))

    def test_loaded_reader_and_mode_tab_resize_keep_root_raster(self):
        self.watch_shell()
        tab = self.window.open_path(self.welcome)
        surface = tab.widget.md_surface
        surface.set_mode('read')
        self.assert_reading(surface)
        self.assertTrue(surface.reader.rich.testAttribute(Qt.WidgetAttribute.WA_NativeWindow))
        self.assertFalse(surface.reader.testAttribute(Qt.WidgetAttribute.WA_NativeWindow))
        self.check_shell('reader-ready')
        for mode in ('write', 'source', 'read'):
            surface.set_mode(mode)
            QTest.qWait(30)
            if mode == 'read':
                self.assert_reading(surface)
            else:
                self.assertFalse(surface.reader.rich.isVisible())
            self.check_shell(mode)
        self.window.resize(1000, 700)
        QTest.qWait(80)
        self.assert_reading(surface)
        self.window.move_view(tab)
        QTest.qWait(80)
        self.assert_reading(surface)
        self.check_shell('moved-pane')
        self.window.open_path(self.other)
        QTest.qWait(30)
        self.assertFalse(surface.reader.rich.isVisible())
        self.check_shell('other-tab')

    def test_lazy_rich_chat_ready_hide_recreate_keep_root_raster(self):
        from prompt_calculus_studio.local_ai.chat_page import ChatPage, ChatText
        from test_chat_hover_model_menu import Document
        self.watch_shell()
        document = Document()
        document.payload['turns'] = document.payload['turns'][-1:]
        attempt = document.payload['turns'][0]['attempts'][0]
        attempt['text'] = '原有純文字仍可閱讀。'
        page = ChatPage(document, self.window)
        previous = self.window.workspace_stack.currentWidget()
        self.window.workspace_stack.addWidget(page)
        self.window.workspace_stack.setCurrentWidget(page)
        self.wait(lambda: '4' in page._turns)
        body = page._turns['4'].attempts['a4'].content
        self.assertIsNone(body.rich)
        native_handle = int(page._content_host.winId())
        if self.window.canvas_hardware_acceleration_active:
            self.assertEqual(page._content_host.windowHandle().surfaceType(), QSurface.SurfaceType.OpenGLSurface)
        self.check_shell('plain-chat')
        # A live native text selection intentionally holds the first rich
        # projection behind its fallback, without mocking/disconnecting slots.
        attempt['text'] = '中文公式 $x^2+y^2=1$。\n\n```python\nprint(1)\n```\n\n' + '可捲動的段落。\n\n'*30
        document.changed.emit()
        self.wait(lambda: body.rich is not None)
        cursor = body.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        body.setTextCursor(cursor)
        self.wait(lambda: body._rich_ready_height is not None)
        self.assertTrue(body.viewport().isVisible())
        self.assertFalse(body._rich_presented)
        self.assertFalse(body.rich.testAttribute(Qt.WidgetAttribute.WA_NativeWindow))
        self.assertIs(body.rich.nativeParentWidget(), page._content_host)
        point = body.viewport().mapTo(body, QPoint(4, 4))
        target = body.childAt(point)
        self.assertTrue(target is body.viewport() or body.viewport().isAncestorOf(target))
        cursor.clearSelection()
        body.setTextCursor(cursor)
        self.wait(lambda: body.rich is not None and body._rich_presented)
        self.assertTrue(body.rich.isVisible())
        self.assertEqual(int(page._content_host.winId()), native_handle)
        self.assertFalse(body.testAttribute(Qt.WidgetAttribute.WA_NativeWindow))
        bar = page.scroll.verticalScrollBar()
        bar.setValue(max(0, bar.maximum()//2))
        QTest.qWait(30)
        self.assertFalse(page.scroll.viewport().testAttribute(Qt.WidgetAttribute.WA_NativeWindow))
        self.check_shell('chat-ready')
        self.window.workspace_stack.setCurrentWidget(previous)
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertIsNone(body.rich)
        self.check_shell('chat-hidden')
        self.window.workspace_stack.setCurrentWidget(page)
        self.wait(lambda: body.rich is not None and body._rich_presented)
        self.assertTrue(body.rich.isVisible())
        self.assertEqual(int(page._content_host.winId()), native_handle)
        self.check_shell('chat-recreated')
        self.window.workspace_stack.setCurrentWidget(previous)
        self.window.workspace_stack.removeWidget(page)
        page.deleteLater()

    def test_pdf_is_born_in_export_host_and_keeps_root_raster(self):
        from prompt_calculus_studio.markdown_pdf import PdfExport
        self.watch_shell()
        tab = self.window.open_path(self.welcome)
        surface = tab.widget.md_surface
        surface.set_mode('write')
        output = self.root/'isolated.pdf'
        job = PdfExport(surface, output)
        results = []
        job.finished.connect(lambda ok, text: results.append((ok, text)))
        self.assertIs(job.view.parentWidget(), job.host)
        self.wait(lambda: bool(results))
        self.assertTrue(results[0][0], results)
        self.assertTrue(output.read_bytes().startswith(b'%PDF-'))
        self.assertEqual(self.welcome.read_text(encoding='utf-8'), self.original)
        self.check_shell('pdf-complete')

    def test_notebook_preview_reparent_after_loading_does_not_promote_siblings(self):
        from prompt_calculus_studio.notebook_dialogs import NotePreview
        self.watch_shell()
        tab = self.window.open_path(self.other)
        preview = NotePreview(tab.widget.md_surface)
        preview.set_note('# 預覽\n\n| A | B |\n|---|---|\n| 中文 | 內容 |\n', self.other)
        preview.setParent(self.window.workspace_stack)
        preview.setGeometry(20, 30, 600, 400)
        preview.show()
        self.wait(lambda: preview.rich.render_state == 'ready')
        self.assertTrue(preview.rich.isVisible())
        self.assertFalse(preview.testAttribute(Qt.WidgetAttribute.WA_NativeWindow))
        self.check_shell('note-preview-ready')
        preview.close()
        preview.deleteLater()

    def test_scoped_sibling_flag_restores_on_exception(self):
        attribute = Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings
        original = QCoreApplication.testAttribute(attribute)

        class BrokenView:
            def setAttribute(self, value):
                if value == Qt.WidgetAttribute.WA_NativeWindow:
                    raise RuntimeError('injected native setup failure')

        try:
            for previous in (False, True):
                QCoreApplication.setAttribute(attribute, previous)
                with patch('prompt_calculus_studio.webengine_host.QApplication.platformName', return_value='windows'):
                    with self.assertRaisesRegex(RuntimeError, 'injected'):
                        prepare_native_host(BrokenView())
                self.assertEqual(QCoreApplication.testAttribute(attribute), previous)
        finally:
            QCoreApplication.setAttribute(attribute, original)


class CanvasCompositionPolicyTests(unittest.TestCase):
    def test_windows_canvas_selects_matching_quick_api_and_respects_overrides(self):
        from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

        with patch('prompt_calculus_studio.webengine_host.QApplication.platformName', return_value='windows'), \
                patch.object(QQuickWindow, 'setGraphicsApi') as select_api, patch.dict(os.environ, clear=True):
            self.assertTrue(configure_canvas_composition(True))
            select_api.assert_called_once_with(QSGRendererInterface.GraphicsApi.OpenGL)
            select_api.reset_mock()
            self.assertFalse(configure_canvas_composition(False))
            for backend, compatible in [('d3d11', False), ('software', False), ('vulkan', False),
                                        ('gl', True), ('gles2', True), ('opengl', True)]:
                with self.subTest(backend=backend), patch.dict(os.environ, QSG_RHI_BACKEND=backend):
                    self.assertEqual(configure_canvas_composition(True), compatible)
                    self.assertEqual(os.environ['QSG_RHI_BACKEND'], backend)
            with patch.dict(os.environ, QT_QUICK_BACKEND='software', QSG_RHI_BACKEND='opengl'):
                self.assertFalse(configure_canvas_composition(True))
            select_api.assert_not_called()


if __name__ == '__main__':
    unittest.main()
