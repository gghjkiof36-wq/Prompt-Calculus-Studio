"""Context-only icon navigation and attached borrowed sidebars."""
import copy
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from prompt_studio.theme import visual_tokens
import test_media_layout as fixture
APP=fixture.APP


class ContextRailTests(unittest.TestCase):
    setUp=fixture.MediaLayoutTests.setUp
    tearDown=fixture.MediaLayoutTests.tearDown
    settle=fixture.MediaLayoutTests.settle

    def collapse(self,section='appearance'):
        self.w.sidebar_pinned_preference=False
        self.w.settings(section);self.w.sync_context_sidebar();self.settle()

    def test_five_destinations_use_context_icons_without_duplicating_main_navigation(self):
        self.collapse()
        for destination,labels in (
                ('canvas',[]),('media',['資料夾','新增資料夾','資料夾操作']),('export',['圖片來源']),
                ('explore',['返回工作區','全部入口']),
                ('settings',['返回工作區','使用介面','介面個人化','候選與翻譯','個人字典','資料與備份'])):
            with self.subTest(destination=destination):
                self.w.studio_navigation.entries[destination][0].click();self.settle()
                rail=self.w.context_rail
                if destination=='canvas':
                    self.assertIsNone(self.w.context_sidebar_target())
                    self.assertFalse(rail.isVisible());self.assertFalse(self.w.studio_navigation.sidebar.isEnabled())
                    self.assertEqual(self.w.surface_stack.mapTo(self.w,QPoint()).x(),0)
                    self.assertTrue(self.w.canvas_workspace_bar.isVisible())
                    continue
                self.assertTrue(rail.isVisible());self.assertEqual(rail.width(),56)
                self.assertTrue(self.w.context_sidebar_target().isHidden())
                self.assertEqual(self.w.surface_stack.mapTo(self.w,QPoint()).x(),56)
                self.assertEqual([b.accessibleName() for b in rail.buttons.values()],labels)
                self.assertTrue(all(not b.text() and b.toolTip() for b in rail.buttons.values()))

    def test_current_area_heading_and_back_region_stay_separate_from_destinations(self):
        for section,title,parent in (('appearance','設定','返回工作區'),('explore','探索','返回工作區'),
                                     ('nodes','ComfyUI','返回探索'),('downloads','CivitAI','返回探索')):
            with self.subTest(section=section):
                self.collapse(section);page=self.w.settings_page
                self.assertEqual(page.navigation_area.heading.text(),title)
                self.assertEqual(page.return_button.text(),parent)
                self.assertTrue(page.navigation_area.back_area.isAncestorOf(page.return_button))
                self.assertFalse(page.navigation.isAncestorOf(page.return_button))
                self.assertGreater(page.navigation.y(),page.navigation_area.heading.y())
                self.assertEqual(self.w.context_rail.buttons['parent'].accessibleName(),parent)
        QTest.mouseClick(self.w.context_rail.buttons['parent'],Qt.MouseButton.LeftButton);self.settle()
        self.assertEqual(self.w.settings_page.section,'explore')

    def test_switch_to_export_hides_previous_context_buttons_before_deferred_delete(self):
        self.collapse('appearance')
        rail=self.w.context_rail;previous=list(rail.buttons.values())
        self.assertEqual(len(previous),6)
        self.assertTrue(all(entry.isVisible() for entry in previous))
        self.w.studio_navigation.entries['export'][0].click()
        # No event-loop turn: deferred deletion must not leave old icons painted.
        self.assertTrue(all(entry.isHidden() for entry in previous))
        self.assertEqual([entry.accessibleName() for entry in rail.buttons.values()],['圖片來源'])
        APP.processEvents()
        self.assertTrue(rail.buttons['sidebar'].isVisible())

    def test_recent_overlay_route_change_preserves_canvas_geometry_and_draft(self):
        self.w.set_interface_mode('canvas');self.w.sidebar_pinned_preference=False;self.w.sync_context_sidebar();self.settle()
        self.w.display_recovery.stop()
        canvas=self.w.canvas;view=canvas.view
        from prompt_studio.drafts import edit
        key=next(iter(canvas.data()['outputs']));canvas.commit(lambda state:edit(state,'',key));self.settle()
        view.resetTransform();view.scale(.9,.9);view.centerOn(800,300)
        geometry=self.w.canvas_content.geometry();transform=view.transform()
        center=view.mapToScene(view.viewport().rect().center())
        data=copy.deepcopy(canvas.data());history=copy.deepcopy(canvas.undo_stack)
        self.w.canvas_workspace_bar.history.click();self.settle()
        sheet=self.w.recent_sheet;self.assertIs(sheet.parentWidget(),self.w.surface_stack)
        self.assertFalse(sheet.isWindow());self.assertEqual(sheet.geometry(),self.w.surface_stack.rect())
        self.assertGreater(sheet.surface.x(),0);self.assertGreater(sheet.surface.y(),0)
        self.assertFalse(self.w.sidebar_peek.is_open);self.assertFalse(self.w.context_rail.isVisible())
        self.assertEqual(self.w.canvas_content.geometry(),geometry)
        self.assertEqual(view.transform(),transform);self.assertEqual(view.mapToScene(view.viewport().rect().center()),center)
        self.assertEqual(canvas.data(),data);self.assertEqual(canvas.undo_stack,history)
        self.assertEqual(canvas.data()['outputs'][key]['draft'],'')
        QTest.mouseClick(self.w.studio_navigation.entries['settings'][0],Qt.MouseButton.LeftButton);self.settle()
        self.assertIsNone(self.w.recent_sheet);self.assertGreaterEqual(self.w.tabs.indexOf(self.w.recent),0)
        self.assertEqual(canvas.data(),data);self.assertEqual(canvas.undo_stack,history)

    def test_drawer_is_opaque_and_pinning_restores_shared_workspace_boundary(self):
        self.collapse();peek=self.w.sidebar_peek
        for palette in ('graphite','paper'):
            self.w.state['settings']['visual_palette']=palette;self.w.apply_theme(preserve_layout=True);self.settle()
            colors=visual_tokens(self.w.state['settings'])
            selected=self.w.context_rail.buttons['appearance']
            selected.clearFocus();APP.processEvents()
            rail_pixel=selected.grab().toImage().pixelColor(5,selected.height()//2)
            self.assertEqual(rail_pixel.name(),colors.get('rail_selected',colors['selected']))
            self.assertTrue(peek._borrow(self.w.context_sidebar_target()));self.settle()
            pixel=peek.overlay.grab().toImage().pixelColor(4,peek.overlay.height()//2)
            self.assertEqual(pixel.alpha(),255);self.assertEqual(pixel.name(),visual_tokens(self.w.state['settings'])['sidebar'])
            self.assertFalse(peek.overlay.mask().contains(QPoint()))
            self.assertTrue(peek.overlay.mask().contains(QPoint(peek.overlay.width()-1,0)))
            peek.close();self.settle()
        self.w.sidebar_pinned_preference=True;self.w.sync_context_sidebar();self.settle()
        self.assertFalse(self.w.context_rail.isVisible())
        self.assertEqual(self.w.surface_stack.mapTo(self.w,QPoint()).x(),0)
        sidebar=self.w.context_sidebar_target()
        self.assertEqual(sidebar.mapTo(self.w.surface_stack,QPoint()),QPoint())
        self.assertEqual(self.w.settings_page.reading.mapTo(self.w.surface_stack,QPoint()).x(),sidebar.width())


if __name__=='__main__':unittest.main()
