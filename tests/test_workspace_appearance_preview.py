"""Material preview must not repolish editors or lose saved settings."""
import json
import unittest
from unittest.mock import patch
from pathlib import Path
from PySide6.QtCore import QEvent,QObject
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
import test_workspace_panes as fixture
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from prompt_calculus_studio.theme import shell_color


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
            for value in range(51):p.menu_transparency.setValue(value)
            self.assertEqual(p.transparency_value.value(),100)
            self.assertEqual(p.menu_transparency_value.value(),50)
            page.flush();self.events()
            backdrop.assert_not_called()
        self.assertEqual(watcher.count,0)
        self.assertEqual(self.w.styleSheet(),sheet)
        self.assertEqual(tab.widget.encoded(),before)
        self.assertEqual(tab.widget.document().availableUndoSteps(),undo)
        saved=json.loads((self.w.data/'workspace-ui.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['mica_transparency'],100);self.assertEqual(saved['menu_transparency'],50)
        scroll=page.preference_pages[0].verticalScrollBar()
        scroll.setValue(scroll.maximum());QTest.qWait(20)
        self.assertEqual(scroll.value(),scroll.maximum())
        self.w.open_route('home');self.w.open_route('settings')
        self.assertEqual(p.transparency.value(),100)

    def test_material_values_restore_and_real_theme_changes_still_apply(self):
        self.w.open_route('settings');self.events()
        page=self.w.utility.settings_page;p=page.preferences
        for material,value in (('mica',53),('acrylic',27),('clear',84)):
            p.material.setCurrentIndex(p.material.findData(material));p.transparency.setValue(value)
        p.material.setCurrentIndex(p.material.findData('mica'))
        self.assertEqual(p.transparency.value(),53)
        p.material.setCurrentIndex(p.material.findData('solid'))
        self.assertTrue(p.transparency_holder.isHidden())
        page.flush();saved=json.loads((self.w.data/'workspace-ui.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['mica_transparency'],53);self.assertEqual(saved['acrylic_transparency'],27)
        self.assertEqual(saved['clear_transparency'],84)
        sheet=self.w.styleSheet()
        p.visual_palette.setCurrentIndex(p.visual_palette.findData('paper'));page.flush();self.events()
        self.assertNotEqual(self.w.styleSheet(),sheet)
        p.ui_size.setValue(16);page.flush();self.events()
        self.assertEqual(self.w.settings['ui_size'],16)
        self.assertEqual(self.w.settings['visual_palette'],'paper')

    def test_first_show_reapplies_saved_material_after_surface_is_ready(self):
        data=Path(self.area.name)/'first-show';data.mkdir()
        (data/'workspace-ui.json').write_text(json.dumps(dict(material='mica',mica_transparency=53)),encoding='utf-8')
        calls=[]
        def native(window,material,settings):
            calls.append((window.isVisible(),material,settings['mica_transparency']))
            return True
        with patch('prompt_calculus_studio.workspace_shell.apply_backdrop',side_effect=native):
            window=WorkspaceShell(self.root,data)
            try:
                self.assertEqual(calls,[])
                self.assertFalse(window.native_material)
                window.show();self.events()
                self.assertEqual(calls,[(True,'mica',53)])
                self.assertTrue(window.native_material)
                # A recreated native surface must invalidate even if Windows
                # reuses the same numerical handle. Coalesce duplicate events.
                self.app.sendEvent(window,QEvent(QEvent.Type.WinIdChange))
                self.app.sendEvent(window,QEvent(QEvent.Type.WinIdChange))
                self.assertFalse(window.native_material)
                self.events();self.assertEqual(len(calls),2)
                window.hide();window.show();self.events()
                self.assertEqual(len(calls),3)
                before=window.styleSheet()
                for value in range(101):
                    window.sync_display_settings(window.settings,{'mica_transparency':value})
                self.assertEqual(len(calls),3)
                self.assertEqual(window.styleSheet(),before)
            finally:window.close();window.deleteLater();self.events()

    def test_clear_material_has_independent_opacity_and_reopens_its_controls(self):
        self.w.open_route('settings');self.events()
        page=self.w.utility.settings_page;p=page.preferences
        p.material.setCurrentIndex(p.material.findData('clear'));p.transparency.setValue(84)
        self.assertFalse(p.transparency_holder.isHidden());page.flush();self.events()
        settings=self.w.display_settings()
        self.assertEqual(settings['material'],'clear')
        self.assertEqual(shell_color(dict(settings,clear_transparency=100)).rsplit(',',1)[-1],'178)')
        self.assertEqual(shell_color(dict(settings,clear_transparency=50)).rsplit(',',1)[-1],'217)')
        self.assertEqual(shell_color(dict(settings,clear_transparency=0)).rsplit(',',1)[-1],'255)')
        # The new choice also survives the ordinary settings storage validator.
        from prompt_calculus_studio.core import initial_state,validate_state
        state=initial_state();state['settings'].update(material='clear',clear_transparency=84)
        validate_state(state)
        with patch('prompt_calculus_studio.workspace_shell.apply_backdrop',return_value=True):
            reopened=WorkspaceShell(self.root,self.w.data)
            try:
                reopened.show();self.events();reopened.open_route('settings');self.events()
                controls=reopened.utility.settings_page.preferences
                self.assertEqual(controls.material.currentData(),'clear')
                self.assertEqual(controls.transparency.value(),84)
                self.assertFalse(controls.transparency_holder.isHidden())
                controls.material.setCurrentIndex(controls.material.findData('mica'))
                self.assertEqual(controls.transparency.value(),61)
                controls.material.setCurrentIndex(controls.material.findData('clear'))
                self.assertEqual(controls.transparency.value(),84)
            finally:reopened.close();reopened.deleteLater();self.events()


if __name__=='__main__':unittest.main()
