"""Pinning changes catalog metadata without resetting the reading surface."""
import copy
import unittest
from PySide6.QtCore import Qt, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import test_editor_reorder as fixture
from prompt_calculus_studio import materials
from prompt_calculus_studio.ui_icons import icon


class MaterialPinTests(unittest.TestCase):
    setUpClass=classmethod(fixture.EditorReorderTests.setUpClass.__func__)
    setUp=fixture.EditorReorderTests.setUp
    tearDown=fixture.EditorReorderTests.tearDown

    def test_pin_preserves_row_objects_geometry_scroll_preview_and_canvas(self):
        for i in range(28):
            materials.save(self.w.state,dict(name=f'Text {i:02}',prompt='long prompt\n'*60,module=self.cat))
        self.w.state['settings']['material_library_order']='manual';self.page.refresh()
        self.page.preview_material(self.ids[0]);self.app.processEvents()
        listing=self.page.listing;listing.verticalScrollBar().setValue(180);self.app.processEvents()
        entries=[listing.item(i) for i in range(listing.count())]
        rectangles=[QRect(listing.visualItemRect(item)) for item in entries]
        reset=[];listing.model().modelReset.connect(lambda:reset.append(True))
        scroll=listing.verticalScrollBar().value();native=QRect(self.w.geometry())
        geometry=QRect(listing.geometry());canvas=copy.deepcopy(self.w.state['multi_output'])
        sources=copy.deepcopy(self.w.state['uses']);selected=self.page.selected_id()
        self.page.pin_material(self.ids[2]);self.app.processEvents()
        self.assertEqual(entries,[listing.item(i) for i in range(listing.count())]);self.assertEqual(reset,[])
        self.assertEqual(rectangles,[listing.visualItemRect(item) for item in entries])
        self.assertEqual(listing.geometry(),geometry);self.assertEqual(self.w.geometry(),native)
        self.assertEqual(listing.verticalScrollBar().value(),scroll)
        self.assertEqual(self.page.selected_id(),selected);self.assertEqual(self.page.preview_id,selected)
        self.assertEqual(self.w.state['multi_output'],canvas);self.assertEqual(self.w.state['uses'],sources)
        self.assertTrue(materials.item(self.w.state,self.ids[2])['pinned'])
        self.w.canvas.undo();self.assertFalse(materials.item(self.w.state,self.ids[2]).get('pinned',False))
        self.w.canvas.redo();self.assertTrue(materials.item(self.w.state,self.ids[2])['pinned'])
        self.w.persist();saved=self.w.store.load_current(multi=True)
        self.assertTrue(materials.item(saved,self.ids[2])['pinned'])

    def test_pinned_hover_reports_state_and_solid_symbol_and_delete_is_destructive(self):
        self.page.pin_material(self.ids[0]);self.app.processEvents()
        listing=self.page.listing
        entry=next(listing.item(i) for i in range(listing.count()) if listing.item(i).data(Qt.ItemDataRole.UserRole)==self.ids[0])
        QTest.mouseMove(listing.viewport(),listing.visualItemRect(entry).center());self.app.processEvents()
        actions=listing.row_actions;self.assertEqual(actions.pin.toolTip(),'已釘選')
        self.assertEqual(actions.pin.accessibleName(),'取消釘選')
        outline=icon('pin','#ffffff',24).pixmap(24,24).toImage()
        filled=icon('pin-filled','#ffffff',24).pixmap(24,24).toImage()
        self.assertLess(outline.pixelColor(12,7).alpha(),filled.pixelColor(12,7).alpha())
        self.page.material_menu_at(self.ids[0],listing.viewport().mapToGlobal(listing.visualItemRect(entry).center()))
        menu=QApplication.activePopupWidget();action=menu.actions()[-1]
        self.assertEqual(action.text(),'刪除');self.assertEqual(action.defaultWidget().objectName(),'DestructiveMenuAction')
        menu.hide()


if __name__=='__main__':unittest.main()
