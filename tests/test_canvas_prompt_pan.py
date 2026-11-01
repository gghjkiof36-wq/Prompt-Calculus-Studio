"""A new camera capture must contain the live Prompt output widget."""
import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QFontDatabase, QImage, QMouseEvent, QTextCursor
from PySide6.QtWidgets import QGraphicsItem

from prompt_calculus_studio import multi_output
import test_workspace_shell as fixtures


class CanvasPromptPanTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.WorkspaceShellTests.setUpClass.__func__)
    setUp = fixtures.WorkspaceShellTests.setUp
    tearDown = fixtures.WorkspaceShellTests.tearDown
    open = fixtures.WorkspaceShellTests.open
    events = fixtures.WorkspaceShellTests.events

    def editor_ink(self, view, card):
        editor = card.panel.editor.viewport()
        origin = editor.mapTo(card.panel, QPoint())
        left = view.mapFromScene(card.proxy.mapToScene(QPointF(origin)))
        right = view.mapFromScene(card.proxy.mapToScene(QPointF(origin + QPoint(editor.width(), editor.height()))))
        image = view.viewport().grab().toImage()
        dpr = image.devicePixelRatioF()
        bounds = QRect(round(left.x()*dpr)+2, round(left.y()*dpr)+2,
                       round((right.x()-left.x())*dpr)-4, round((right.y()-left.y())*dpr)-4)
        self.assertTrue(image.rect().contains(bounds))
        gray = image.copy(bounds).convertToFormat(QImage.Format.Format_Grayscale8)
        pixels, stride = bytes(gray.constBits()), gray.bytesPerLine()
        # Glyph pixels in each third also catch a partial loss of lower lines.
        return tuple(sum(p > 105 for y in range(i*gray.height()//3, (i+1)*gray.height()//3)
                         for p in pixels[y*stride:y*stride+gray.width()]) for i in range(3))

    def pan(self, view, card):
        def mouse(kind, point, button, buttons):
            event = QMouseEvent(kind, QPointF(point), QPointF(view.viewport().mapToGlobal(point)),
                                button, buttons, Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(view.viewport(), event)
            self.events()

        start = QPoint(20, 220)
        mouse(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton)
        frames = []
        try:
            for distance in (8, 40, 88):
                point = start + QPoint(distance, 0)
                mouse(QEvent.Type.MouseMove, point, Qt.MouseButton.NoButton, Qt.MouseButton.MiddleButton)
                self.assertIsNotNone(view.pan)
                frames.append(self.editor_ink(view, card))
        finally:
            mouse(QEvent.Type.MouseButtonRelease, start+QPoint(88, 0),
                  Qt.MouseButton.MiddleButton, Qt.MouseButton.NoButton)
        return frames

    def test_second_blank_canvas_pan_shows_new_multiline_prompt_after_edit(self):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')
        self.w.resize(1400, 950)
        host = self.open('Street', 'pcs-workflow').host
        host.set_interface_mode('canvas')
        canvas = host.canvas
        view = canvas.view
        key, card = next(iter(canvas.outputs.items()))
        editor = card.panel.editor
        editor.setPlainText('短文')
        canvas.zoom_to(.85); view.centerOn(card); self.events()
        self.assertEqual(card.proxy.cacheMode(), QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.pan(view, card)  # Populate the QImage render target's proxy pixels.
        view.centerOn(card); self.events()

        text = '\n'.join(('場景 01  清晨的山谷與溪水', '場景 02  石板小橋和松樹',
                          '場景 03  微風中的芒草', '場景 04  遠山輪廓與積雲',
                          'SCENE 05  paper lanterns at dusk', 'SCENE 06  sunset over river'))
        editor.setPlainText(text)
        editor.moveCursor(QTextCursor.MoveOperation.Start)
        view.setFocus(); self.events()
        self.assertIs(canvas.outputs[key], card)
        before = self.editor_ink(view, card)
        self.assertTrue(all(value > 80 for value in before), before)
        captures, hits = view.pan_raster.captures, view.pan_raster.hits
        moving = self.pan(view, card)
        after = self.editor_ink(view, card)
        for frame in [*moving, after]:
            for displayed, direct in zip(frame, before):
                self.assertAlmostEqual(displayed, direct, delta=max(20, direct*.12))
        self.assertEqual(view.pan_raster.captures, captures+1)
        self.assertGreater(view.pan_raster.hits, hits)
        self.assertEqual(editor.toPlainText(), text)
        self.assertEqual(canvas.data()['outputs'][key]['draft'], text)
        self.assertEqual(multi_output.compile_output(host.state, key)['final_prompt'], text)
