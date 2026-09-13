"""Visible settings edges, material inheritance and canvas action sizing.

All transport is stubbed; screenshots and widget events use Qt offscreen.
"""
import os,tempfile,unittest
from unittest.mock import patch
from PySide6.QtCore import QPoint,Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QListWidgetItem
from prompt_studio.window import Window

APP=QApplication.instance() or QApplication([])

def offline_request(client,route,data=None,done=None,**kwargs):
    if done and route.startswith('/userdata?'):
        done([f'Workflow {i}.json' for i in range(1,10)])

class SettingsEdgesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_V081':'1'}); self.env.start()
        self.transport=patch('prompt_studio.comfy_client.ComfyClient.request',offline_request); self.transport.start()
        self.w=Window(self.temp.name); self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.resize(1440,920); self.w.apply_theme(); self.w.show(); self.p=self.w.settings_page
    def tearDown(self):
        self.w.close(); APP.processEvents(); self.transport.stop(); self.env.stop(); self.temp.cleanup()
    def settle(self):QTest.qWait(60)
    def point(self,widget):return widget.mapTo(self.p,QPoint(0,0))
    def test_every_settings_section_covers_bottom_and_right_with_black(self):
        for section in ('interface','appearance','completion','dictionary','civitai','workflows','models','nodes','data'):
            with self.subTest(section=section):
                self.w.settings(section); self.settle()
                r=self.p.reading.geometry(); self.assertEqual(r.right(),self.p.width()-1); self.assertEqual(r.bottom(),self.p.height()-1)
                image=self.p.grab().toImage()
                for x,y in ((image.width()-1,image.height()-1),(image.width()-1,r.top()+60),(r.left()+2,image.height()-1)):
                    color=image.pixelColor(x,y); self.assertEqual((color.name(),color.alpha()),('#111111',255))
    def test_collapsing_navigation_removes_its_entire_material_gutter(self):
        self.w.settings('civitai'); self.settle(); original=self.p.reading.x()
        self.assertGreater(original,200)
        for _ in range(3):
            self.p.sidebar_toggle.click(); self.settle()
            self.assertFalse(self.p.navigation.isVisible()); self.assertEqual(self.p.reading.x(),0)
            self.assertFalse(self.p.return_button.isVisible())
            image=self.p.grab().toImage(); self.assertEqual(image.pixelColor(0,self.p.height()-1).name(),'#111111')
            self.p.sidebar_toggle.click(); self.settle(); self.assertEqual(self.p.reading.x(),original)
    def test_gallery_reaches_bottom_in_full_narrow_and_detail_layouts(self):
        self.w.settings('civitai'); self.settle(); c=self.p.civitai
        # Enough cards to scroll, with no image download or private data.
        for i in range(30):
            item=QListWidgetItem(f'Model {i}'); item.setData(Qt.ItemDataRole.UserRole,dict(id=i,name=f'Model {i}',type='LORA',modelVersions=[])); c.list.addItem(item)
        c.counter.setText('30 個結果')
        for width in (1440,1040):
            self.w.resize(width,920); self.settle()
            for details in (False,True):
                c.detail_pane.setVisible(details); self.settle(); viewport=c.list.viewport()
                bottom=self.point(viewport).y()+viewport.height()
                self.assertEqual(bottom,self.p.height())
                self.assertGreaterEqual(c.counter.width(),c.counter.fontMetrics().horizontalAdvance(c.counter.text()))
                self.assertLess(self.point(c.more).y()+c.more.height(),self.point(viewport).y())
                c.list.verticalScrollBar().setValue(c.list.verticalScrollBar().maximum()); self.settle()
                self.assertEqual(self.point(viewport).y()+viewport.height(),self.p.height())
    def test_sidebar_return_has_bold_text_arrow_and_keeps_draft(self):
        self.w.set_interface_mode('canvas'); self.w.final.setPlainText('keep this draft')
        self.w.settings('civitai'); self.settle(); b=self.p.return_button
        self.assertTrue(self.p.navigation_area.isAncestorOf(b))
        self.assertLess(self.point(b).x()+b.width(),self.p.reading.x())
        self.assertLess(self.point(b).y()+b.height(),self.point(self.p.navigation).y())
        self.assertGreaterEqual(b.font().weight(),QFont.Weight.DemiBold); self.assertFalse(b.icon().isNull())
        b.click(); self.settle(); self.assertTrue(self.w.canvas.isVisible()); self.assertEqual(self.w.final.toPlainText(),'keep this draft')
    def test_material_and_transparency_repaint_header_and_navigation_only(self):
        self.w.settings('appearance'); self.settle(); prefs=self.p.preferences
        with patch('prompt_studio.window.apply_backdrop',return_value=True) as backdrop:
            observed=[]
            for material,opacity in (('mica',20),('mica',80),('acrylic',35),('acrylic',90),('solid',0)):
                prefs.material.setCurrentIndex(prefs.material.findData(material))
                if material!='solid':prefs.transparency.setValue(opacity)
                # Capture the shell too: the settings child deliberately paints
                # no material of its own and inherits the native window surface.
                self.settle(); image=self.w.grab().toImage()
                header=image.pixelColor(self.p.width()//2,8); nav=image.pixelColor(3,image.height()-10)
                self.assertEqual(header.alpha(),nav.alpha())
                self.assertEqual(image.pixelColor(image.width()-1,image.height()-1).alpha(),255)
                observed.append(header.alpha()); self.assertEqual(backdrop.call_args.args[1],material)
            self.assertGreater(observed[0],observed[1]); self.assertGreater(observed[2],observed[3]); self.assertEqual(observed[-1],255)
            self.p.flush(); self.assertEqual(self.w.store.load()['settings']['material'],'solid')
    def test_seven_workflow_rows_remain_visible_after_font_change(self):
        self.w.settings('workflows'); self.settle(); listing=self.p.workflow_manager.list
        for size in (11,18,11):
            self.w.state['settings']['ui_size']=size; self.w.apply_theme(preserve_layout=True); self.settle()
            listing.verticalScrollBar().setValue(0)
            self.assertEqual(listing.count(),9)
            self.assertTrue(listing.viewport().rect().contains(listing.visualItemRect(listing.item(6))))
            self.assertGreater(listing.visualItemRect(listing.item(7)).bottom(),listing.viewport().height())
    def test_canvas_settings_precedes_export(self):
        self.w.set_interface_mode('canvas'); self.settle(); header=self.w.canvas_header.layout()
        self.assertEqual(header.indexOf(self.w.canvas_settings)+1,header.indexOf(self.w.canvas_navigation['匯出']))
        self.w.canvas_settings.click(); self.settle(); self.assertTrue(self.p.isVisible())
    def test_cancel_text_matches_run_font_and_row_height_without_count_jumps(self):
        self.w.set_interface_mode('canvas'); self.settle(); bar=self.w.run_controls
        for size in (11,18,11):
            self.w.state['settings']['ui_size']=size; self.w.apply_theme(preserve_layout=True); self.settle()
            self.assertEqual(bar.stop.text(),'取消'); self.assertEqual(bar.stop.font().pixelSize(),bar.run_button.font().pixelSize())
            self.assertEqual(bar.stop.height(),bar.run_button.height()); self.assertEqual(bar.stop.height(),bar.count.height())
            before=(bar.run_button.width(),bar.run_button.height(),bar.stop.width(),bar.stop.height())
            for value in (1,2,10,99,100,1):
                bar.count.setValue(value); self.settle()
                self.assertEqual((bar.run_button.width(),bar.run_button.height(),bar.stop.width(),bar.stop.height()),before)
        self.w.set_interface_mode('list'); self.settle(); self.assertEqual(bar.stop.text(),'×'); self.assertEqual(bar.stop.font().pixelSize(),24)

if __name__=='__main__':unittest.main()
