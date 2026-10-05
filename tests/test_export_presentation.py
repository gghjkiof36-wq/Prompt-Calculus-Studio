"""Painted export rows must follow PCS palettes, including transparent views."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QPoint,QRect
from PySide6.QtGui import QColor,QPalette
from PySide6.QtWidgets import QApplication,QScrollArea
from PySide6.QtTest import QTest
from prompt_calculus_studio.widgets import RoundMenu
from prompt_calculus_studio.theme import visual_tokens
from prompt_calculus_studio.window import Window

APP=QApplication.instance() or QApplication([])


class ExportPresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'qa')
        self.w=Window(Path(self.tmp.name)/'data')
        self.w.state['settings'].update(material='solid',online=False)
        self.w.resize(1280,800); self.w.show()
        self.page=self.w.clean_export
        self.page.inputs=['sample.png']
        self.page.rows=[dict(source='sample.png',width=400,height=480,format='png'),
                        dict(source='other.png',error='Cannot read image')]
        self.page.render(); self.w.show_page(self.page)
        APP.processEvents()

    def tearDown(self):
        self.w.close(); APP.processEvents(); self.tmp.cleanup()

    def test_folder_menu_follows_each_visible_trigger(self):
        # Both entries must route the same actions, even when the sidebar
        # button is hidden by the empty state or a narrow viewport.
        page=self.page
        for empty in (True,False):
            with self.subTest(empty=empty):
                if empty:
                    page.inputs=[];page.rows=[]
                else:
                    page.inputs=['sample.png'];page.rows=[dict(source='sample.png',width=400,height=480,format='png')]
                    page.manual_sidebar=True
                page.render();APP.processEvents()
                trigger=page.start_folder_button if empty else page.add_folder_button
                self.assertTrue(trigger.isVisible())
                with patch.object(RoundMenu,'open_at',autospec=True) as opened:
                    QTest.mouseClick(trigger,Qt.MouseButton.LeftButton)
                    menu,position=opened.call_args.args
                    self.assertEqual(position,trigger.mapToGlobal(QPoint(0,trigger.height()+4)))
                    self.assertEqual([action.text() for action in menu.actions()],['媒體庫資料夾…','磁碟資料夾…'])
                    menu.deleteLater()

    def test_real_paint_rows_and_header_use_all_three_palettes(self):
        table=self.page.table
        for palette in ('graphite','mist','paper'):
            with self.subTest(palette=palette):
                self.w.state['settings']['visual_palette']=palette
                self.w.apply_theme(); self.page.refresh_colors()
                # Reproduce the transparent Qt stylesheet palette that produced
                # black cells even after applying the application's QPalette.
                black=QPalette(table.palette())
                for role in (QPalette.ColorRole.Base,QPalette.ColorRole.AlternateBase,
                             QPalette.ColorRole.Window,QPalette.ColorRole.Highlight):
                    black.setColor(role,QColor('#000000'))
                table.setPalette(black); table.horizontalHeader().setPalette(black)
                table.clearSelection(); table.clearFocus(); APP.processEvents()
                tokens=visual_tokens(self.w.state['settings'])
                image=table.viewport().grab().toImage()
                base=QColor(tokens['base'])
                for row in range(2):
                    top=table.rowViewportPosition(row)
                    for column in range(3):
                        rect=table.visualItemRect(table.item(row,column))
                        for x in (rect.left(),rect.left()+1,rect.right()-1,rect.right()):
                            self.assertEqual(image.pixelColor(x,top+4),base,
                                             (palette,row,column,x,'row edge'))
                        self.assertEqual(image.pixelColor(rect.right()-5,top+18),base)
                    # A single thin rule separates rows; no black inset boxes.
                    self.assertNotEqual(image.pixelColor(4,top+table.rowHeight(row)-1),base)
                header=table.horizontalHeader().viewport().grab().toImage()
                for column in range(3):
                    x=table.columnViewportPosition(column)+table.columnWidth(column)-3
                    self.assertEqual(header.pixelColor(x,4),base)
                # Keep status semantics after changing palettes.
                error=QColor(tokens['error'])
                self.assertTrue(any(image.pixelColor(x,y)==error
                    for y in range(table.rowViewportPosition(1)+8,table.rowViewportPosition(1)+36)
                    for x in range(table.columnViewportPosition(2)+8,table.viewport().width()-4)))
                table.selectRow(0); table.clearFocus(); APP.processEvents()
                selected=table.viewport().grab().toImage()
                for column in range(3):
                    rect=table.visualItemRect(table.item(0,column))
                    for x in (rect.left(),rect.left()+1,rect.right()-1,rect.right()):
                        self.assertEqual(selected.pixelColor(x,4),QColor(tokens['selected']))

    def test_short_windows_scroll_settings_without_covering_export_actions(self):
        page=self.page
        for font in (11,18):
            self.w.state['settings']['ui_size']=font;self.w.apply_theme()
            for width,height in ((800,640),(640,480)):
                with self.subTest(font=font,size=(width,height)):
                    self.w.resize(width,height);page.compact_view.setCurrentIndex(1)
                    for _ in range(4):APP.processEvents()
                    self.assertEqual(self.w.size().toTuple(),(width,height))
                    self.assertEqual(page.options_card.findChildren(QScrollArea),[page.settings_scroll])
                    viewport=page.settings_scroll.viewport()
                    visible_bottom=viewport.mapTo(page,QPoint(0,viewport.height())).y()
                    self.assertLess(visible_bottom,page.directory.mapTo(page,QPoint(0,0)).y())
                    for control in (page.directory,page.preview_button,page.export_button):
                        self.assertTrue(control.isVisible())
                        self.assertGreaterEqual(control.height(),control.fontMetrics().height()+10)
                        self.assertLessEqual(control.mapTo(page,QPoint(0,control.height())).y(),page.size().height())
                    page.resize.setCurrentIndex(page.resize.findData('exact'))
                    page.width.setValue(1234);page.height.setValue(768)
                    page.settings_tabs.setCurrentIndex(0)
                    for _ in range(4):APP.processEvents()
                    self.assertGreater(page.settings_scroll.verticalScrollBar().maximum(),0)
                    for control in (page.mode,page.format,page.resize,page.width,page.height):
                        # Qt scrolls a spinbox's caret rather than its frame.
                        # Reveal the resolution row to check both full controls.
                        target=page.exact if control in (page.width,page.height) else control
                        page.settings_scroll.ensureWidgetVisible(target,0,0);APP.processEvents()
                        rect=QRect(control.mapTo(viewport,QPoint()),control.size())
                        self.assertTrue(viewport.rect().contains(rect),(font,width,control,rect))
                    page.settings_scroll.ensureWidgetVisible(page.aspect,0,4);APP.processEvents()
                    center=page.aspect.mapTo(viewport,page.aspect.rect().center())
                    self.assertTrue(viewport.rect().contains(center),center)
                    page.settings_tabs.setCurrentIndex(1)
                    for _ in range(4):APP.processEvents()
                    page.settings_scroll.ensureWidgetVisible(page.sha,0,4);APP.processEvents()
                    center=page.sha.mapTo(viewport,page.sha.rect().center())
                    self.assertTrue(viewport.rect().contains(center),center)
                    self.assertEqual((page.values()['width'],page.values()['height']),(1234,768))

    def test_quarter_window_starts_with_clean_format_and_size_fully_visible(self):
        page=self.page;before=page.values()
        self.w.state['settings']['ui_size']=11
        for palette in ('graphite','paper'):
            with self.subTest(palette=palette):
                self.w.state['settings']['visual_palette']=palette;self.w.apply_theme()
                self.w.resize(640,480);page.compact_view.setCurrentIndex(1)
                for _ in range(6):APP.processEvents()
                page.settings_scroll.verticalScrollBar().setValue(0)
                viewport=page.settings_scroll.viewport()
                for control in (page.mode,page.format,page.resize):
                    rect=QRect(control.mapTo(viewport,QPoint()),control.size())
                    self.assertTrue(viewport.rect().contains(rect),(palette,rect,viewport.rect()))
                    self.assertGreaterEqual(control.height(),control.fontMetrics().height()+10)
                self.assertEqual(page.settings_scroll.horizontalScrollBar().maximum(),0)
                header=[page.header.itemAt(i).widget() for i in range(page.header.count()) if page.header.itemAt(i).widget()]
                rectangles=[QRect(control.mapTo(page,QPoint()),control.size()) for control in header]
                self.assertFalse(any(a.intersects(b) for i,a in enumerate(rectangles) for b in rectangles[i+1:]))
                self.assertEqual(page.values(),before)


if __name__=='__main__': unittest.main()
