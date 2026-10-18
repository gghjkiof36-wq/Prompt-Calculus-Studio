"""Material preview must not repolish editors or lose saved settings."""
import json
import unittest
from unittest.mock import patch
from PySide6.QtCore import QEvent,QObject
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
import test_workspace_panes as fixture


class StyleEvents(QObject):
    def __init__(self,owner):
        super().__init__(owner);self.count=0;owner.installEventFilter(self)
    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.StyleChange:self.count+=1
        return False


class AppearancePreviewTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    open = fixture.WorkspacePaneTests.open
    events = fixture.WorkspacePaneTests.events
    def test_transparency_preview_preserves_source_geometry_and_persists(self):
        for font in ('msjh.ttc','msjhbd.ttc'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
        tab=self.open('Notes.md');editor=tab.widget.md_surface.editor
        editor.insertPlainText('Unfinished draft ');before=tab.widget.encoded()
        self.w.open_route('settings');self.events()
        page=self.w.utility.settings_page;p=page.preferences
        p.material.setCurrentIndex(p.material.findData('mica'));page.flush();self.events()
        watcher=StyleEvents(page);sheet=self.w.styleSheet();undo=tab.widget.document().availableUndoSteps()
        with patch('prompt_calculus_studio.workspace_shell.apply_backdrop') as backdrop:
            for value in range(0,101):p.transparency.setValue(value)
            for value in range(41):p.menu_transparency.setValue(value)
            self.assertEqual(p.transparency_value.value(),100)
            self.assertEqual(p.menu_transparency_value.value(),40)
            page.flush();self.events()
            backdrop.assert_not_called()
        self.assertEqual(watcher.count,0)
        self.assertEqual(self.w.styleSheet(),sheet)
        self.assertEqual(tab.widget.encoded(),before)
        self.assertEqual(tab.widget.document().availableUndoSteps(),undo)
        saved=json.loads((self.w.data/'workspace-ui.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['mica_transparency'],100);self.assertEqual(saved['menu_transparency'],40)
        scroll=page.preference_pages[0].verticalScrollBar()
        scroll.setValue(scroll.maximum());QTest.qWait(20)
        self.assertEqual(scroll.value(),scroll.maximum())
        self.w.open_route('home');self.w.open_route('settings')
        self.assertEqual(p.transparency.value(),100)

    def test_material_values_restore_and_real_theme_changes_still_apply(self):
        self.w.open_route('settings');self.events()
        page=self.w.utility.settings_page;p=page.preferences
        for material,value in (('mica',53),('acrylic',27)):
            p.material.setCurrentIndex(p.material.findData(material));p.transparency.setValue(value)
        p.material.setCurrentIndex(p.material.findData('mica'))
        self.assertEqual(p.transparency.value(),53)
        p.material.setCurrentIndex(p.material.findData('solid'))
        self.assertTrue(p.transparency_holder.isHidden())
        page.flush();saved=json.loads((self.w.data/'workspace-ui.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['mica_transparency'],53);self.assertEqual(saved['acrylic_transparency'],27)
        sheet=self.w.styleSheet()
        p.visual_palette.setCurrentIndex(p.visual_palette.findData('paper'));page.flush();self.events()
        self.assertNotEqual(self.w.styleSheet(),sheet)
        p.ui_size.setValue(16);page.flush();self.events()
        self.assertEqual(self.w.settings['ui_size'],16)
        self.assertEqual(self.w.settings['visual_palette'],'paper')


if __name__=='__main__':unittest.main()
