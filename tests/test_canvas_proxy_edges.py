"""Native-style embedded surfaces stay seamless at canvas zoom factors."""
import copy,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QPoint,QPointF,QRect,QCoreApplication,QEvent
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QStyleFactory,QStyle,QStyleOptionComboBox
from prompt_studio.canvas_starter import guide
from prompt_studio.theme import visual_tokens


class CanvasProxyEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)

    def setUp(self):
        from prompt_studio.window import Window
        self.original_style=self.app.style().objectName()
        styles={name.lower():name for name in QStyleFactory.keys()}
        if 'windows11' not in styles:self.skipTest('Windows 11 Qt style is not available')
        self.app.setStyle(styles['windows11'])
        self.tmp=tempfile.TemporaryDirectory(prefix='pcs-canvas-edge-')
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_DATA':self.tmp.name});self.env.start()
        self.w=Window(self.tmp.name);self.w.state['settings'].update(online=False,material='solid');self.w.apply_theme()
        self.w.comfy.timer.stop();self.w.display_recovery.stop()
        self.w.resize(1440,1200);self.w.show();self.w.enter_canvas();QTest.qWait(60)
        self.c=self.w.canvas;self.c.onboarding.dismiss();self.refs=guide(self.w.state)

    def tearDown(self):
        self.w.close();QTest.qWait(40);self.w.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.app.processEvents();self.env.stop();self.tmp.cleanup();self.app.setStyle(self.original_style)

    def test_actual_native_canvas_proxy_edges_have_no_second_dark_outline(self):
        cards=[self.c.flow_cards[self.refs['scheduler']],self.c.outputs[self.refs['output']],self.c.clips[self.refs['clip']],self.c.flow_cards[self.refs['stage']]]
        # Existing scene positions and the underlying graph remain unchanged.
        graph=copy.deepcopy(self.c.data());undo=copy.deepcopy(self.c.undo_stack)
        for palette in ('graphite','paper','mist'):
            self.w.state['settings']['visual_palette']=palette;self.w.apply_theme(preserve_layout=True)
            body=visual_tokens(self.w.state['settings'])['canvas_body']
            for scale in (.35,.86,1):
                self.c.view.resetTransform();self.c.view.scale(scale,scale)
                for card in cards:
                    self.c.view.centerOn(card);QTest.qWait(10)
                    image=self.c.view.viewport().grab().toImage();rect=card.proxy.rect();dpr=image.devicePixelRatio()
                    samples=[]
                    for offset in (-1,0,1):
                        for fraction in (.35,.5,.65):
                            p=self.c.view.mapFromScene(card.proxy.mapToScene(QPointF(rect.width()*fraction,rect.height())))+QPoint(0,offset)
                            samples.append(image.pixelColor(round(p.x()*dpr),round(p.y()*dpr)).name())
                        p=self.c.view.mapFromScene(card.proxy.mapToScene(QPointF(rect.width(),rect.height()*.45)))+QPoint(offset,0)
                        samples.append(image.pixelColor(round(p.x()*dpr),round(p.y()*dpr)).name())
                    self.assertEqual(set(samples),{body},(palette,scale,card.key,samples,card.proxy.geometry().getRect()))
        self.assertEqual(self.c.data(),graph);self.assertEqual(self.c.undo_stack,undo)

    def test_large_font_workspace_caption_fits_and_long_names_remain_discoverable(self):
        self.w.state['settings']['ui_size']=18;self.w.apply_theme(preserve_layout=True);self.w.resize(640,640);QTest.qWait(40)
        combo=self.w.canvas_workspace;bar=self.w.canvas_workspace_bar;bar.refresh()
        option=QStyleOptionComboBox();combo.initStyleOption(option)
        field=combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,combo)
        self.assertGreaterEqual(field.width(),combo.fontMetrics().horizontalAdvance('預設工作區'))
        self.assertTrue(self.w.canvas_header.rect().contains(QRect(bar.mapTo(self.w.canvas_header,QPoint()),bar.size())))
        long_name='這是一個需要完整提示說明的很長工作區名稱'
        combo.setItemText(combo.currentIndex(),long_name);self.app.processEvents()
        self.assertEqual(combo.toolTip(),long_name);self.assertEqual(combo.currentText(),long_name)
        for button in (bar.list_mode,bar.history):self.assertTrue(button.isVisible());self.assertTrue(bar.rect().contains(button.geometry()))

    def test_resized_proxy_surfaces_keep_edges_clean_at_fractional_zoom(self):
        # Include image/text source panels with legacy Panel/InsetPanel roles.
        self.c.functions.add_image()
        self.c.add_flow_node('image_inputs',QPointF(0,0));QTest.qWait(20)
        from prompt_studio.canvas_items import ModuleProxyWidget
        cards=list({item.parentItem() for item in self.c.view.scene().items() if isinstance(item,ModuleProxyWidget)})
        graph=copy.deepcopy(self.c.data());sizes=copy.deepcopy(self.w.state['text_sizes']);undo=copy.deepcopy(self.c.undo_stack)
        saved=Path(os.environ.get('PCS_TEST_ARTIFACT_DIR',self.tmp.name));saved.mkdir(parents=True,exist_ok=True)
        for palette in ('graphite','paper','mist'):
            self.w.state['settings']['visual_palette']=palette;self.w.apply_theme(preserve_layout=True)
            body=visual_tokens(self.w.state['settings'])['canvas_body']
            for card in cards:
                # Raise only the inspected card; enlarged adjacent cards may overlap.
                card.setZValue(100)
                for width,height in ((500.7,620.3),(360.4,170.6)):
                    card.requested_size=[width,height];card.layout_card();QTest.qWait(5)
                    self.assertTrue(card.boundingRect().contains(card.proxy.geometry()))
                    self.assertEqual(card.proxy.size().width(),card.panel.width())
                    self.assertEqual(card.proxy.size().height(),card.panel.height())
                    for scale in (.44,.86,1,1.15):
                        self.c.view.resetTransform();self.c.view.scale(scale,scale);self.c.view.centerOn(card);QTest.qWait(5)
                        frame=self.c.view.viewport().grab().toImage();dpr=frame.devicePixelRatio();rect=card.proxy.rect()
                        points=[QPointF(rect.width()*f,rect.height()) for f in (.3,.5,.7)]
                        points+=[QPointF(rect.width(),rect.height()*f) for f in (.3,.5,.7)]
                        samples=[]
                        for point in points:
                            center=self.c.view.mapFromScene(card.proxy.mapToScene(point))
                            for dx,dy in ((0,0),(-1,0),(0,-1),(1,0),(0,1)):
                                pixel=QPoint(round((center.x()+dx)*dpr),round((center.y()+dy)*dpr))
                                if frame.rect().contains(pixel):samples.append(frame.pixelColor(pixel).name())
                        self.assertTrue(samples)
                        self.assertEqual(set(samples),{body},(palette,scale,type(card).__name__,width,height,samples))
                    if palette=='graphite' and width>500:frame.save(str(saved/(type(card).__name__+'-'+getattr(card,'kind','body')+'.png')))
                card.setZValue(0)
        self.assertEqual(self.c.data(),graph);self.assertEqual(self.w.state['text_sizes'],sizes);self.assertEqual(self.c.undo_stack,undo)

    def test_wrapped_notices_and_actions_fit_after_resize_and_font_change(self):
        graph=copy.deepcopy(self.c.data());sizes=copy.deepcopy(self.w.state['text_sizes'])
        cards=[self.c.clips[self.refs['clip']],self.c.flow_cards[self.refs['stage']]]
        for size in (11,18,22):
            self.w.state['settings']['ui_size']=size;self.w.apply_theme(preserve_layout=True)
            for card in cards:
                action=card.panel.binding if hasattr(card.panel,'binding') else card.panel.edit_button
                for height in (650.6,100.2):
                    card.requested_size=[360.4,height];card.layout_card()
                    notice=card.panel.status
                    notice.setText('佇列與後端紀錄中找不到這項任務；可能已移除或清空，未自動重送。')
                    notice.show();QTest.qWait(25)
                    self.assertGreaterEqual(action.height(),action.sizeHint().height())
                    self.assertLessEqual(action.y(),10)
                    self.assertGreaterEqual(notice.height(),notice.heightForWidth(notice.width()))
                    self.assertTrue(card.panel.rect().contains(notice.geometry()))
                    self.assertFalse(action.geometry().intersects(notice.geometry()))
                    self.assertLessEqual(notice.y()-action.geometry().bottom(),10)
        self.assertEqual(self.c.data(),graph);self.assertEqual(self.w.state['text_sizes'],sizes)


if __name__=='__main__':unittest.main()
