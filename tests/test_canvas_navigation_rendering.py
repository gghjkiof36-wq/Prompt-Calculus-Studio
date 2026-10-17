"""Navigation reuse must keep native controls, camera and live state accurate."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QImage, QPainter, QPainterPath, QPen, QTransform, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QGraphicsScene, QGraphicsView, QLineEdit, QPushButton, QVBoxLayout, QWidget, QTextEdit
from PySide6.QtTest import QTest

from prompt_calculus_studio.canvas_items import ModuleProxyWidget, paint_module_surface
from prompt_calculus_studio.canvas_raster import paint_scope
from prompt_calculus_studio import module_chrome, ui_icons
from prompt_calculus_studio.connection_pill import ConnectionPill

APP = QApplication.instance() or QApplication([])


class ProxyPaintTests(unittest.TestCase):
    def setUp(self):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')
        self.view=QGraphicsView();self.scene=QGraphicsScene(self.view);self.view.setScene(self.scene)
        self.view.setBackgroundBrush(QColor('#242829'))
        self.view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.panel=QWidget();self.panel.setFont(QFont('Microsoft JhengHei UI',12))
        self.panel.setStyleSheet('QWidget {color:#eeeeee; background:#242829;} QPushButton, QLineEdit, QComboBox {border:1px solid #525757; border-radius:8px; padding:6px;}')
        layout=QVBoxLayout(self.panel)
        self.button=QPushButton('選擇節點');self.edit=QLineEdit('街景、場景文字')
        self.text=QTextEdit('Morning light\n'+('測試換行與縮放文字\n'*12));self.text.setFixedHeight(90)
        self.combo=QComboBox();self.combo.addItems(['選擇文字節點','另一節點'])
        for control in (self.button,self.edit,self.text,self.combo):layout.addWidget(control)
        self.proxy=ModuleProxyWidget();self.proxy.setWidget(self.panel);self.scene.addItem(self.proxy)
        self.proxy.setGeometry(QRectF(0,0,320,220));self.scene.setSceneRect(-500,-500,1500,1500)
        self.view.resize(620,440);self.view.show();self.view.centerOn(self.proxy)
        self.events()

    def tearDown(self):
        self.view.close();self.view.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents()

    def events(self):
        APP.processEvents();APP.processEvents()

    def capture(self, clipped=True):
        self.proxy.setFlag(self.proxy.GraphicsItemFlag.ItemUsesExtendedStyleOption,clipped)
        return self.view.viewport().grab().toImage()

    def assert_matches_direct(self):
        actual=self.capture()
        expected=self.capture(False)
        delta=max(abs(a-b) for a,b in zip(bytes(actual.constBits()),bytes(expected.constBits())))
        self.assertLessEqual(delta,3)
        return actual

    def test_clipped_forms_preserve_glyphs_and_edges_at_fractional_zoom_and_pan(self):
        for scale in (.12,.3,.77,1.,1.5,2.5):
            self.view.setTransform(QTransform.fromScale(scale,scale));self.events()
            self.assert_matches_direct()
            for delta in (4,7):
                self.view.horizontalScrollBar().setValue(self.view.horizontalScrollBar().value()+delta)
                self.events();self.assert_matches_direct()

    def test_text_selection_styles_and_disabled_controls_stay_live(self):
        previous=self.assert_matches_direct()
        changes=(lambda:self.edit.setText('更新後的文字'),lambda:self.edit.selectAll(),
                 lambda:self.button.setEnabled(False),lambda:self.combo.setCurrentIndex(1),
                 lambda:self.panel.setStyleSheet(self.panel.styleSheet().replace('#eeeeee','#81b3e3')))
        for index,change in enumerate(changes):
            change();self.events();actual=self.assert_matches_direct()
            if index!=2:self.assertNotEqual(actual,previous,index)
            previous=actual

    def test_scroll_focus_hover_and_live_multiline_text_remain_visible(self):
        self.assert_matches_direct()
        for change in (lambda:self.text.verticalScrollBar().setValue(45),
                       lambda:self.text.setPlainText('新的結果\nsecond line'),
                       lambda:self.text.setFocus(),
                       lambda:self.text.selectAll(),
                       lambda:QTest.mouseMove(self.view.viewport(),self.view.mapFromScene(self.proxy.mapToScene(QPointF(self.button.pos()+self.button.rect().center()))))):
            change();self.events();self.assert_matches_direct()

class NavigationChromeTests(unittest.TestCase):
    def test_header_measurements_follow_title_width_and_font_without_sharing_mutable_actions(self):
        module_chrome._HEADER_LAYOUTS.clear()
        font=QFont('Microsoft JhengHei UI',12)
        with patch.object(module_chrome,'QFontMetricsF',wraps=module_chrome.QFontMetricsF) as measure:
            first=module_chrome.header_rects(320,'場景設定',font)
            first[2]['extra']=QRectF()
            second=module_chrome.header_rects(320,'場景設定',font)
            self.assertNotIn('extra',second[2]);self.assertEqual(first[0],second[0]);self.assertEqual(measure.call_count,1)
            module_chrome.header_rects(220,'場景設定',font)
            font.setPointSize(16);module_chrome.header_rects(320,'場景設定',font)
            module_chrome.header_rects(320,'不同名稱',font)
            self.assertEqual(measure.call_count,4)

    def test_connection_refresh_does_not_restyle_unchanged_status(self):
        pill=ConnectionPill(lambda:None)
        self.addCleanup(pill.deleteLater)
        pill.refresh({})
        with patch.object(pill,'setStyleSheet',wraps=pill.setStyleSheet) as apply:
            pill.refresh({});apply.assert_not_called()
            pill.refresh({'visual_palette':'paper'});self.assertEqual(apply.call_count,1)
        pill.refresh({},SimpleNamespace(connected=True))
        self.assertIn('已連線',pill.brand.text())
        pill.refresh({},SimpleNamespace(connection_checking=True))
        self.assertIn('連線中',pill.brand.text());self.assertFalse(pill.isEnabled())

    def test_icon_pixmaps_reuse_pixels_but_follow_foreground_and_state(self):
        from PySide6.QtCore import QSize
        ui_icons._PIXMAPS.clear()
        engine=ui_icons._LineIconEngine('home','navigation',20,True)
        size=QSize(24,24)
        with patch.object(engine,'paint',wraps=engine.paint) as draw:
            first=engine.pixmap(size,QIcon.Mode.Normal,QIcon.State.Off)
            second=engine.pixmap(size,QIcon.Mode.Normal,QIcon.State.Off)
            self.assertEqual(first.toImage(),second.toImage());self.assertEqual(draw.call_count,1)
            filled=engine.pixmap(size,QIcon.Mode.Normal,QIcon.State.On)
            self.assertNotEqual(first.toImage(),filled.toImage())
            old=APP.palette();palette=APP.palette();palette.setColor(palette.ColorRole.WindowText,QColor('#61bd88'))
            try:
                APP.setPalette(palette)
                changed=engine.pixmap(size,QIcon.Mode.Normal,QIcon.State.Off)
                self.assertNotEqual(first.toImage(),changed.toImage())
            finally:APP.setPalette(old)

    def test_covered_shadow_matches_original_opaque_card_at_all_scales(self):
        def original(painter,outline,nested=False,strength=1.,covered=False):
            # Frozen pre-optimization shadow, including the covered interior.
            painter.save();shape=outline.translated(0,3 if nested else 6)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for spread,alpha in ((12,3),(9,5),(6,8),(3,12)):
                painter.setPen(QPen(QColor(0,0,0,round((alpha//2 if nested else alpha)*strength)),spread,
                                   Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
                painter.drawPath(shape)
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(0,0,0,round((20 if nested else 45)*strength)))
            painter.drawPath(shape);painter.restore()
        path=QPainterPath();path.addRoundedRect(QRectF(0,0,340,260),16,16)
        def render(scale,alpha,direct):
            image=QImage(760,620,QImage.Format.Format_ARGB32_Premultiplied);image.setDevicePixelRatio(1.25);image.fill(QColor('#111111'))
            painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.translate(25.35,25.75);painter.scale(scale,scale)
            painter.setPen(QPen(QColor('#484848'),1))
            colors={'surface':'#242829','canvas_body':'#242829','canvas_header':'#292d2e','canvas_alpha':alpha}
            try:
                with paint_scope(False):
                    if direct:
                        with patch.object(module_chrome,'paint_shadow',original):paint_module_surface(painter,path,colors,44)
                    else:paint_module_surface(painter,path,colors,44)
            finally:painter.end()
            return image
        for scale in (.12,.3,.77,1,1.5,3):
            for alpha in (180,255):
                actual=render(scale,alpha,False);expected=render(scale,alpha,True)
                delta=max(abs(a-b) for a,b in zip(bytes(actual.constBits()),bytes(expected.constBits())))
                self.assertLessEqual(delta,3,(scale,alpha))


import test_workspace_shell as fixtures


class CanvasNavigationTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.WorkspaceShellTests.setUpClass.__func__)
    setUp=fixtures.WorkspaceShellTests.setUp
    tearDown=fixtures.WorkspaceShellTests.tearDown
    open=fixtures.WorkspaceShellTests.open
    events=fixtures.WorkspaceShellTests.events

    def wheel(self,view,point,delta):
        event=QWheelEvent(QPointF(point),QPointF(view.viewport().mapToGlobal(point)),QPoint(),QPoint(0,delta),
                          Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
        APP.sendEvent(view.viewport(),event);self.events()

    def test_pan_zoom_limits_and_anchor_preserve_content_history_and_live_items(self):
        tab=self.open('Street','pcs-workflow');canvas=tab.host.canvas;view=canvas.view
        canvas.zoom_to(.7);view.centerOn(8000,8000);self.events()
        before=canvas.history_state();history=list(canvas.undo_stack);ports=dict(canvas.ports)
        point=QPoint(160,120);anchor=view.mapToScene(point)
        self.wheel(view,point,120)
        self.assertAlmostEqual(view.transform().m11(),.7*1.15)
        self.assertLess((view.mapToScene(point)-anchor).manhattanLength(),4)
        self.wheel(view,point,6000);self.assertAlmostEqual(view.transform().m11(),3.)
        self.wheel(view,point,-6000);self.assertAlmostEqual(view.transform().m11(),.12)
        canvas.zoom_to(.8);view.centerOn(140,180);self.events()
        start=view.mapToScene(point)
        def mouse(kind,pos,button,buttons):
            APP.sendEvent(view.viewport(),QMouseEvent(kind,QPointF(pos),QPointF(view.viewport().mapToGlobal(pos)),button,buttons,Qt.KeyboardModifier.NoModifier))
        mouse(QEvent.Type.MouseButtonPress,point,Qt.MouseButton.MiddleButton,Qt.MouseButton.MiddleButton)
        end=point+QPoint(40,24)
        mouse(QEvent.Type.MouseMove,end,Qt.MouseButton.NoButton,Qt.MouseButton.MiddleButton)
        mouse(QEvent.Type.MouseButtonRelease,end,Qt.MouseButton.MiddleButton,Qt.MouseButton.NoButton)
        self.events()
        self.assertLess((view.mapToScene(end)-start).manhattanLength(),3)
        self.assertEqual(canvas.history_state(),before);self.assertEqual(canvas.undo_stack,history);self.assertEqual(canvas.ports,ports)

    def test_returning_to_tab_does_not_reset_camera_or_bypass_raster_reuse(self):
        tab=self.open('Street','pcs-workflow');canvas=tab.host.canvas;view=canvas.view
        canvas.zoom_to(.77);view.centerOn(140,180);self.events()
        origin=view.mapToScene(QPoint());transform=view.transform()
        note=self.open('Notes','md',True)
        with patch.object(view,'resetTransform',wraps=view.resetTransform) as reset, patch.object(view,'scale',wraps=view.scale) as scale:
            for _ in range(3):
                self.w.focus_tab(tab);self.events();self.w.focus_tab(note);self.events()
            self.w.focus_tab(tab);self.events()
            reset.assert_not_called();scale.assert_not_called()
        self.assertEqual(view.transform(),transform)
        self.assertLess((view.mapToScene(QPoint())-origin).manhattanLength(),3)

    def test_wheel_on_scrollable_module_keeps_scroll_ownership_at_boundary(self):
        tab=self.open('Street','pcs-workflow');canvas=tab.host.canvas;view=canvas.view
        card=next(iter(canvas.outputs.values()));editor=card.panel.editor
        editor.setPlainText('\n'.join(f'line {i}' for i in range(80)))
        canvas.zoom_to(.8);view.centerOn(card);self.events()
        local=editor.viewport().mapTo(card.panel,editor.viewport().rect().center())
        point=view.mapFromScene(card.proxy.mapToScene(QPointF(local)))
        bar=editor.verticalScrollBar();self.assertGreater(bar.maximum(),0);bar.setValue(0)
        scale=view.transform().m11()
        self.wheel(view,point,-120);self.assertGreater(bar.value(),0)
        bar.setValue(bar.maximum());self.wheel(view,point,-120)
        self.assertEqual(view.transform().m11(),scale)


if __name__=='__main__':unittest.main()
