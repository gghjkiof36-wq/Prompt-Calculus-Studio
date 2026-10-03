"""Native navigation and small-window behavior with isolated data."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidgetItem
from prompt_studio.window import Window

APP=QApplication.instance() or QApplication([])
if APP.platformName()=='offscreen':
    for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)


class VisualNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1'});self.env.start()
        self.request=patch('prompt_studio.comfy_client.ComfyClient.request',side_effect=AssertionError('No service in UI test'));self.request.start()
        self.w=Window(self.temp.name);self.w.state['settings'].update(online=False,material='solid')
        self.w.show();self.w.apply_theme()

    def tearDown(self):
        self.w.close();APP.processEvents();self.request.stop();self.env.stop();self.temp.cleanup()

    def settle(self):QTest.qWait(40)

    def test_route_parent_and_icons_survive_download_selection(self):
        p=self.w.settings_page
        self.w.settings('civitai');self.settle()
        self.assertEqual(p.return_button.text(),'返回探索')
        self.assertFalse(p.navigation.item(1).icon().isNull())
        p.navigation.setCurrentRow(1);self.settle()
        self.assertEqual(p.section,'downloads')
        self.assertFalse(p.navigation.currentItem().icon().isNull())
        p.return_parent();self.settle();self.assertEqual(p.section,'explore')
        self.assertEqual(p.return_button.text(),'返回工作區')
        self.assertTrue(self.w.studio_navigation.entries['explore'][0].isChecked())

    def test_civitai_subpage_navigation_keeps_image_lifecycle(self):
        self.w.settings('civitai');self.settle();c=self.w.settings_page.civitai
        with patch.object(c,'stop_images') as stop:
            self.w.settings('downloads');stop.assert_called()
        with patch.object(c,'browse_on_entry') as browse,patch.object(c,'schedule_thumbnails') as thumbnails:
            self.w.settings('civitai');browse.assert_called();thumbnails.assert_called()

    def test_palette_save_preserves_manual_empty_draft(self):
        from prompt_studio.drafts import edit
        edit(self.w.state,'');before=copy.deepcopy(self.w.state['multi_output'])
        self.w.settings('appearance');self.settle();prefs=self.w.settings_page.preferences
        for name in ('mist','paper','graphite'):
            prefs.visual_palette.setCurrentIndex(prefs.visual_palette.findData(name));self.w.settings_page.flush()
            self.assertEqual(self.w.store.load()['settings']['visual_palette'],name)
            self.assertEqual(self.w.state['draft'],'')
            self.assertEqual(self.w.state['multi_output'],before)

    def test_list_mode_leaves_settings_for_media_and_export(self):
        self.w.set_interface_mode('list')
        for route,page in (('media',self.w.gallery),('export',self.w.clean_export)):
            self.w.settings('appearance');self.settle()
            self.w.studio_navigation.entries[route][0].click();self.settle()
            self.assertTrue(page.isVisible())
            self.assertIs(self.w.surface_stack.currentWidget(),self.w.canvas_shell)

    def test_module_selection_survives_compact_navigation(self):
        self.w.set_interface_mode('list');self.w.resize(1440,900);self.settle()
        self.w.module_list.setCurrentRow(1);selected=self.w.current_module
        self.w.resize(800,640);self.settle()
        self.assertEqual(self.w.compact_modules.currentData(),selected)

    def test_hidden_settings_flush_keeps_latest_interface_choice(self):
        self.w.set_interface_mode('canvas');self.w.settings('appearance');self.settle()
        self.w.settings_page.cancel();self.w.set_interface_mode('list');self.settle()
        self.w.settings_page.flush()
        self.assertEqual(self.w.state['selection_view'],'list')
        self.assertEqual(self.w.store.load()['selection_view'],'list')

    def test_export_feedback_changes_palette_without_losing_selection(self):
        from prompt_studio.theme import visual_tokens
        e=self.w.clean_export
        e.rows=[dict(source='synthetic.png',width=64,height=64,format='png',error='合成錯誤')]
        e.render();e.table.selectRow(0);e.set_feedback('合成錯誤')
        self.w.state['settings']['visual_palette']='paper';self.w.apply_theme()
        self.assertEqual(e.table.currentRow(),0)
        color=visual_tokens(self.w.state['settings'])['error']
        self.assertEqual(e.table.item(0,2).foreground().color().name(),color)
        self.assertIn(color,e.feedback.styleSheet())

    def test_narrow_detail_returns_to_same_query_and_selection(self):
        self.w.resize(800,640);self.w.settings('civitai');self.settle();c=self.w.settings_page.civitai
        c.initial_requested=True;c.query.setText('soft light')
        item=QListWidgetItem('Example');item.setData(Qt.ItemDataRole.UserRole,dict(id=1,name='Example',modelVersions=[]));c.list.addItem(item)
        c.list.setCurrentItem(item);self.settle()
        self.assertTrue(c.detail_pane.isVisible());self.assertFalse(c.results_pane.isVisible())
        self.assertFalse(c.preview.geometry().intersects(c.brief.geometry()))
        self.assertGreater(c.preview.width(),c.detail_body.width()*.9)
        c.hide_details();self.settle()
        self.assertTrue(c.results_pane.isVisible());self.assertEqual(c.query.text(),'soft light');self.assertIs(c.list.currentItem(),item)

    def test_small_windows_keep_navigation_and_export_controls_reachable(self):
        for width,height in ((1920,1080),(960,800),(800,600),(640,540)):
            self.w.resize(width,height);self.w.settings('appearance');self.settle()
            self.assertEqual(self.w.size().toTuple(),(width,height))
            self.assertLessEqual(self.w.settings_page.reading.geometry().right(),width)
            self.assertFalse(self.w.settings_page.compact_navigation.isVisible())
            self.assertTrue(self.w.studio_navigation.sidebar.isVisible())
            self.w.show_page(self.w.clean_export);self.settle()
            e=self.w.clean_export
            if e.compact_view.isVisible():e.compact_view.setCurrentIndex(1);self.settle()
            self.assertTrue(e.export_button.isVisible())
            center=e.export_button.mapTo(e,e.export_button.rect().center())
            self.assertTrue(e.rect().contains(center),(width,height,center))
        self.w.set_interface_mode('list');self.settle()
        self.assertTrue(self.w.list_compact.isVisible())
        self.w.compact_output.setCurrentIndex(1);self.settle()
        self.assertTrue(self.w.final.isVisible());self.assertTrue(self.w.builder_scroll.isVisible())


if __name__=='__main__':unittest.main()
