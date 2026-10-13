"""Actual Qt viewport events and constrained picker geometry; no live services."""
import copy
import os
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint, QPointF, QRect
from PySide6.QtGui import QImage, QColor, QScreen, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from stage_fixture import StageFixture, APP
from prompt_calculus_studio.function_picker import FunctionPicker, FUNCTIONS
from prompt_calculus_studio.image_viewer import ImageViewerDialog
from prompt_calculus_studio.source_controls import load_many


class CanvasEntrypointTests(StageFixture):
    def setUp(self):
        for name in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
            path=Path('C:/Windows/Fonts')/name
            if path.is_file():QFontDatabase.addApplicationFont(str(path))
        screen=patch.object(QScreen,'availableGeometry',return_value=QRect(0,0,1920,1080))
        screen.start(); self.addCleanup(screen.stop)
        super().setUp()

    def capture(self,widget,name):
        if directory:=os.environ.get('PCS_QA_OUTPUT'):
            path=Path(directory); path.mkdir(parents=True,exist_ok=True)
            self.assertTrue(widget.grab().save(str(path/name)))

    def test_picker_short_large_text_and_normal_layout_have_no_overlap(self):
        for width,height,font in ((640,480,18),(1360,900,11)):
            self.w.state['settings']['ui_size']=font; self.w.apply_theme(preserve_layout=True)
            self.w.resize(width,height); QTest.qWait(25)
            dialog=FunctionPicker(self.c); dialog.show(); QTest.qWait(30)
            try:
                for index in range(len(FUNCTIONS)):
                    dialog.list.setCurrentRow(index); QTest.qWait(10)
                    children=(dialog.preview,dialog.name,dialog.description,dialog.kind)
                    for left,right in zip(children,children[1:]):
                        self.assertLess(left.geometry().bottom(),right.geometry().top())
                    self.assertGreaterEqual(dialog.description.height(),dialog.description.heightForWidth(dialog.description.width()))
                    for text in (dialog.name,dialog.description,dialog.kind):
                        dialog.details.ensureWidgetVisible(text,0,0); APP.processEvents()
                        rect=QRect(text.mapTo(dialog.details.viewport(),QPoint()),text.size())
                        self.assertTrue(dialog.details.viewport().rect().contains(rect),rect)
                    for control in (dialog.search,dialog.list,dialog.add_button,dialog.findChild(QPushButton,'DialogClose')):
                        self.assertTrue(dialog.rect().contains(QRect(control.mapTo(dialog,QPoint()),control.size())))
                dialog.details.verticalScrollBar().setValue(0)
                self.capture(dialog,f'functions-{width}x{height}-{font}pt.png')
                dialog.details.ensureWidgetVisible(dialog.description,0,0)
                if width==640:self.capture(dialog,'functions-640x480-description.png')
            finally:dialog.reject(); dialog.deleteLater(); QTest.qWait(1)

    def test_picker_filters_add_escape_and_backdrop(self):
        self.w.resize(640,480); self.w.state['settings']['ui_size']=18; self.w.apply_theme(preserve_layout=True)
        for category in ('文字','圖片','執行'):
            dialog=FunctionPicker(self.c); dialog.show(); QTest.qWait(15)
            QTest.mouseClick(dialog.filters[category],Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.list.count(),sum(value[2]==category for value in FUNCTIONS))
            ident=dialog.current_id(); calls=[]; dialog.actions[ident]=lambda:calls.append(ident)
            QTest.mouseClick(dialog.add_button,Qt.MouseButton.LeftButton)
            self.assertEqual(calls,[ident]); self.assertFalse(dialog.isVisible())
            dialog.deleteLater()
        for cancel in ('escape','backdrop'):
            dialog=FunctionPicker(self.c); dialog.show(); QTest.qWait(10)
            before=copy.deepcopy(self.c.data())
            if cancel=='escape':QTest.keyClick(dialog,Qt.Key.Key_Escape)
            else:QTest.mouseClick(dialog,Qt.MouseButton.LeftButton,pos=QPoint(2,2))
            self.assertFalse(dialog.isVisible()); self.assertEqual(self.c.data(),before); dialog.deleteLater()

    def source(self,enhanced):
        key=self.c.functions.add_image(enhanced=enhanced,position=QPointF(2600,1500))
        paths=[]
        for i in range(3 if enhanced else 1):
            path=Path(self.tmp.name)/f'preview-{i}.png'
            picture=QImage(96,72,QImage.Format.Format_RGB32); picture.fill(QColor(80+i*50,110,150)); picture.save(str(path)); paths.append(str(path))
        if enhanced:self.assertTrue(load_many(self.c.functions.cards[key].panel,paths))
        else:self.assertTrue(self.c.functions.replace(key,paths[0]))
        APP.processEvents(); return key

    def preview_point(self,key,scale):
        card=self.c.functions.cards[key]; preview=card.panel.preview
        self.c.view.resetTransform(); self.c.view.scale(scale,scale)
        self.c.view.centerOn(card.sceneBoundingRect().center()); QTest.qWait(20)
        point=self.c.view.mapFromScene(card.proxy.mapToScene(QPointF(preview.mapTo(card.panel,preview.rect().center()))))
        self.assertTrue(self.c.view.viewport().rect().contains(point)); return point

    def test_source_and_single_click_and_double_click_open_correct_viewer_once(self):
        for enhanced in (True,False):
            key=self.source(enhanced)
            if enhanced:self.c.functions.cards[key].panel.next.click()
            before=copy.deepcopy(self.c.functions.data()['images'][key]); source=self.c.functions.cards[key].panel.current
            for scale in (.5,.8,1):
                point=self.preview_point(key,scale); views=[]
                def inspect(dialog):
                    dialog.show(); APP.processEvents(); views.append(dialog.windowTitle())
                    self.assertFalse(dialog.picture.isNull())
                    self.capture(dialog,'source-viewer.png' if enhanced else 'single-viewer.png')
                    QTest.mouseClick(dialog.close_button,Qt.MouseButton.LeftButton)
                    return dialog.result()
                with patch.object(ImageViewerDialog,'exec',inspect):
                    QTest.mouseClick(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point)
                    QTest.mouseDClick(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point)
                    QTest.mouseRelease(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point)
                self.assertEqual(views,[source['name']])
                self.assertEqual(self.c.functions.data()['images'][key],before)
            self.c.functions.remove(key)

    def test_viewport_drag_right_click_and_release_outside_do_not_open(self):
        key=self.source(True); point=self.preview_point(key,.8); viewport=self.c.view.viewport()
        before=copy.deepcopy(self.c.functions.data()['images'][key])
        with patch('prompt_calculus_studio.image_viewer.show_image') as viewer:
            QTest.mousePress(viewport,Qt.MouseButton.LeftButton,pos=point)
            QTest.mouseMove(viewport,point+QPoint(45,0)); QTest.mouseMove(viewport,point)
            QTest.mouseRelease(viewport,Qt.MouseButton.LeftButton,pos=point)
            QTest.mouseClick(viewport,Qt.MouseButton.RightButton,pos=point)
            QTest.mousePress(viewport,Qt.MouseButton.LeftButton,pos=point)
            QTest.mouseRelease(viewport,Qt.MouseButton.LeftButton,pos=QPoint(2,2))
            self.assertEqual(viewer.call_count,0)
        self.assertEqual(self.c.functions.data()['images'][key],before)
