"""Visible settings edges, material inheritance and canvas action sizing.

All transport is stubbed; screenshots and widget events use Qt offscreen.
"""
import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QPoint,Qt
from PySide6.QtGui import QFont,QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QListWidgetItem,QScrollArea,QPushButton,QStyle,QStyleOptionComboBox
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.theme import visual_tokens

APP=QApplication.instance() or QApplication([])
if APP.platformName()=='offscreen':
    for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)

def offline_request(client,route,data=None,done=None,**kwargs):
    if done and route.startswith('/userdata?'):
        done([f'Workflow {i}.json' for i in range(1,10)])

class SettingsEdgesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_V081':'1'}); self.env.start()
        self.transport=patch('prompt_calculus_studio.comfy_client.ComfyClient.request',offline_request); self.transport.start()
        self.w=Window(self.temp.name); self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.resize(1440,920); self.w.apply_theme(); self.w.show(); self.p=self.w.settings_page
    def tearDown(self):
        self.w.close(); APP.processEvents(); self.transport.stop(); self.env.stop(); self.temp.cleanup()
    def settle(self):QTest.qWait(60)
    def point(self,widget):return widget.mapTo(self.p,QPoint(0,0))
    def test_every_settings_section_uses_palette_surface_and_rounded_edges(self):
        for section in ('interface','appearance','completion','dictionary','civitai','workflows','models','nodes','data'):
            with self.subTest(section=section):
                self.w.settings(section); self.settle()
                r=self.p.reading.geometry(); self.assertEqual(r.right(),self.p.width()-1); self.assertEqual(r.bottom(),self.p.height()-1)
                image=self.p.grab().toImage()
                dpr=image.devicePixelRatio()
                # Corners expose the parent material. Sample the solid surface
                # beyond the 20px radius and inside the right-hand scrollbar.
                for x,y in ((image.width()-round(24*dpr),image.height()-round(2*dpr)),
                            (image.width()-round(14*dpr),round((r.top()+24)*dpr)),
                            (round((r.left()+24)*dpr),image.height()-round(2*dpr))):
                    color=image.pixelColor(x,y); self.assertEqual((color.name(),color.alpha()),(visual_tokens(self.w.state['settings'])['base'],255))
                self.assertLess(image.pixelColor(image.width()-1,image.height()-1).alpha(),255)
    def test_collapsing_navigation_removes_its_entire_material_gutter(self):
        self.w.settings('civitai'); self.settle(); original=self.p.reading.x()
        self.assertGreater(original,200)
        for _ in range(3):
            self.p.sidebar_toggle.click(); self.settle()
            self.assertFalse(self.p.navigation.isVisible()); self.assertEqual(self.p.reading.x(),0)
            self.assertFalse(self.p.return_button.isVisible())
            image=self.p.grab().toImage(); dpr=image.devicePixelRatio()
            self.assertEqual(image.pixelColor(round(24*dpr),image.height()-round(2*dpr)).name(),visual_tokens(self.w.state['settings'])['base'])
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
                self.assertEqual(bottom,self.p.height()-c.tabs.widget(0).layout().contentsMargins().bottom())
                self.assertGreaterEqual(c.counter.width(),c.counter.fontMetrics().horizontalAdvance(c.counter.text()))
                self.assertLess(self.point(c.more).y()+c.more.height(),self.point(viewport).y())
                c.list.verticalScrollBar().setValue(c.list.verticalScrollBar().maximum()); self.settle()
                self.assertEqual(self.point(viewport).y()+viewport.height(),self.p.height()-c.tabs.widget(0).layout().contentsMargins().bottom())
    def test_sidebar_return_has_bold_text_arrow_and_keeps_draft(self):
        self.w.set_interface_mode('canvas'); self.w.final.setPlainText('keep this draft')
        self.w.settings('civitai'); self.settle(); b=self.p.return_button
        self.assertTrue(self.p.navigation_area.isAncestorOf(b))
        self.assertLess(self.point(b).x()+b.width(),self.p.reading.x())
        self.assertLess(self.point(b).y()+b.height(),self.point(self.p.navigation).y())
        self.assertGreaterEqual(b.font().weight(),QFont.Weight.DemiBold); self.assertFalse(b.icon().isNull())
        self.assertEqual(b.text(),'返回探索');b.click();self.settle()
        self.assertEqual(self.p.section,'explore');self.assertEqual(b.text(),'返回工作區')
        self.assertEqual(self.w.final.toPlainText(),'keep this draft')
        b.click(); self.settle(); self.assertTrue(self.w.canvas.isVisible()); self.assertEqual(self.w.final.toPlainText(),'keep this draft')

    def test_download_navigation_keeps_icons_and_selection_when_window_changes(self):
        self.w.settings('civitai'); self.w.resize(640,740); self.settle()
        self.assertFalse(self.p.navigation_area.isVisible())
        # Narrow pages use the shared sidebar control; there is no second
        # navigation row taking space away from the content.
        self.assertFalse(self.p.compact_navigation.isVisible())
        self.w.studio_navigation.sidebar.click(); self.settle()
        self.assertTrue(self.p.navigation_area.isVisible())
        downloads=next(i for i,route in enumerate(self.p.nav_routes) if route[0]=='downloads')
        self.p.navigation.setCurrentRow(downloads); self.settle()
        self.assertEqual(self.p.section,'downloads'); self.assertEqual(self.p.civitai.tabs.currentIndex(),1)
        self.assertEqual(self.p.navigation.currentItem().text(),'下載記錄')
        self.assertFalse(self.p.navigation.currentItem().icon().isNull())
        self.assertTrue(self.w.studio_navigation.entries['explore'][0].isChecked())
        self.w.resize(1440,920); self.settle()
        self.assertTrue(self.p.navigation_area.isVisible()); self.assertFalse(self.p.compact_navigation.isVisible())
        self.assertEqual(self.p.navigation.currentItem().text(),'下載記錄')
        self.assertFalse(self.p.navigation.currentItem().icon().isNull())
        self.assertTrue(all(not self.p.navigation.item(i).icon().isNull() for i in range(self.p.navigation.count())))

    def test_model_detail_and_manager_use_bottom_and_actions_remain_reachable(self):
        self.w.settings('models'); self.settle()
        root=Path(self.temp.name)/'models'; root.mkdir()
        source=root/'example.safetensors'; source.write_bytes(b'fixture')
        record=dict(id='model',name='Example',path=str(source),root=str(root.resolve()),kind='LoRA',
                    size=7,mtime=source.stat().st_mtime_ns,thumb='',trigger='',notes='',url='')
        self.w.catalog.put('model',record,str(root.resolve()))
        self.w.models.root.setText(str(root)); self.w.models.refresh(); self.w.models.list.setCurrentRow(0)
        # This fixture is already catalogued. No background scan is required
        # to test the selected record's editor and its actions.
        self.w.first_models=False
        for width,height,size in ((1440,920,11),(1040,700,18),(1440,1000,14)):
            self.w.state['settings']['ui_size']=size; self.w.apply_theme(preserve_layout=True)
            self.w.resize(width,height)
            for section in ('models','nodes'):
                self.w.settings(section); self.settle()
                page=self.p.comfy_tabs.currentWidget()
                if section=='models':
                    if self.w.models.compact_view.isVisible():self.w.models.compact_view.setCurrentIndex(1)
                    scroll=self.w.models.detail_scroll
                else:
                    self.p.manager.show_view('tools');scroll=self.p.manager.tools_page
                self.settle()
                self.assertIsNotNone(scroll)
                self.assertTrue(scroll.isVisible())
                self.assertEqual(self.point(scroll).y()+scroll.height(),self.p.height()-(page.layout().contentsMargins().bottom() if section=='nodes' else 0))
                action_text='將模型檔案移至資源回收筒…' if section=='models' else '開啟 ComfyUI'
                action=next(b for b in scroll.findChildren(QPushButton) if b.text()==action_text)
                scroll.ensureWidgetVisible(action); self.settle()
                scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum()); self.settle()
                self.assertTrue(scroll.viewport().rect().contains(action.mapTo(scroll.viewport(),action.rect().center())),
                                (section,width,height,size,scroll.viewport().size(),action.geometry()))
                if section=='nodes':self.assertEqual(self.p.manager.view,'tools')
    def test_model_filters_follow_hidden_font_changes_and_display_recovery(self):
        models=self.w.models
        for width,size in ((1440,18),(1440,11),(1040,18),(1440,14),(1440,11)):
            self.w.settings('appearance'); self.w.resize(width,920)
            self.w.state['settings']['ui_size']=size
            self.w.apply_theme(preserve_layout=True,refresh_fonts=True)
            self.w.settings('models'); self.settle()
            for recover in (False,True):
                if recover: self.w.display_recovery.refresh(); self.settle()
                for combo in (models.kind,models.base_filter,models.category_filter):
                    with self.subTest(width=width,font=size,recovery=recover,text=combo.currentText()):
                        option=QStyleOptionComboBox(); combo.initStyleOption(option)
                        text=combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,combo)
                        self.assertGreaterEqual(text.height(),combo.fontMetrics().height())
                        self.assertGreaterEqual(text.width(),combo.fontMetrics().horizontalAdvance(combo.currentText()))
                        self.assertTrue(combo.parentWidget().rect().contains(combo.geometry()))
                row=models.kind.parentWidget()
                self.assertEqual(row.height(),len(row.rows)*models.kind.height()+(len(row.rows)-1)*row.box.spacing())
    def test_material_transparency_changes_header_while_sidebar_stays_opaque(self):
        self.w.settings('appearance'); self.settle(); prefs=self.p.preferences
        with patch('prompt_calculus_studio.window.apply_backdrop',return_value=True) as backdrop:
            observed=[]
            for material,opacity in (('mica',20),('mica',80),('acrylic',35),('acrylic',90),('solid',0)):
                prefs.material.setCurrentIndex(prefs.material.findData(material))
                if material!='solid':prefs.transparency.setValue(opacity)
                # Material belongs to the top shell. Navigation and reading
                # surfaces stay opaque so desktop content cannot bleed through.
                self.settle(); image=self.w.grab().toImage()
                header=image.pixelColor(self.p.width()//2,8); nav=image.pixelColor(3,image.height()-10)
                self.assertEqual(nav.alpha(),255)
                self.assertEqual(image.pixelColor(image.width()-24,image.height()-2).alpha(),255)
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
    def test_workflow_maintenance_menu_keeps_availability_and_existing_action(self):
        self.w.settings('workflows'); self.settle(); manager=self.p.workflow_manager
        self.assertIsNotNone(manager.entry())
        self.assertFalse(manager.rename_button.isVisible()); self.assertFalse(manager.delete_button.isVisible())
        with patch('prompt_calculus_studio.workflow_manager.RoundMenu.open_at',autospec=True) as opened:
            manager.more_button.click()
        menu=opened.call_args.args[0]
        actions={action.text():action for action in menu.actions() if not action.isSeparator()}
        for control in (manager.rename_button,manager.reimport_button,manager.delete_button,manager.undo_button):
            self.assertEqual(actions[control.text()].isEnabled(),control.isEnabled())
        # The menu dispatches the established deletion signal and confirmation;
        # declining confirmation must leave the catalog and application intact.
        before=self.w.catalog.count('workflow'); selected=manager.entry().copy(); clicks=[]
        manager.delete_button.clicked.connect(lambda:clicks.append(True))
        with patch.object(manager,'confirm_delete',return_value=False):actions[manager.delete_button.text()].trigger()
        self.assertEqual(clicks,[True]); self.assertEqual(manager.entry(),selected)
        self.assertEqual(self.w.catalog.count('workflow'),before)
        menu.deleteLater()
    def test_shared_navigation_orders_export_before_settings_and_opens_preferences(self):
        self.w.set_interface_mode('canvas'); self.settle(); navigation=self.w.studio_navigation
        self.assertEqual(list(navigation.entries),['canvas','media','explore','export','settings'])
        export=navigation.entries['export'][0];settings=navigation.entries['settings'][0]
        self.assertEqual(navigation.layout_box.indexOf(export)+1,navigation.layout_box.indexOf(settings))
        self.assertFalse(settings.icon().isNull());self.assertEqual(settings.accessibleName(),'設定')
        settings.click(); self.settle(); self.assertTrue(self.p.isVisible());self.assertEqual(self.p.section,'appearance')
    def test_cancel_icon_has_accessible_name_and_stable_control_height(self):
        self.w.set_interface_mode('canvas'); self.settle(); bar=self.w.run_controls
        for size in (11,18,11):
            self.w.state['settings']['ui_size']=size; self.w.apply_theme(preserve_layout=True); self.settle()
            self.assertEqual(bar.stop.text(),'取消'); self.assertEqual(bar.stop.accessibleName(),'取消執行'); self.assertFalse(bar.stop.icon().isNull())
            self.assertEqual(bar.stop.height(),bar.run_button.height()); self.assertEqual(bar.stop.height(),bar.count.height())
            before=(bar.run_button.width(),bar.run_button.height(),bar.stop.width(),bar.stop.height())
            for value in (1,2,10,99,100,1):
                bar.count.setValue(value); self.settle()
                self.assertEqual((bar.run_button.width(),bar.run_button.height(),bar.stop.width(),bar.stop.height()),before)
        self.w.set_interface_mode('list'); self.settle(); self.assertEqual(bar.stop.text(),'取消'); self.assertEqual(bar.stop.accessibleName(),'取消執行')

if __name__=='__main__':unittest.main()
