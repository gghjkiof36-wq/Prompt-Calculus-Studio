"""Accent persistence, menu selection, custom input and readable controls."""
import copy
import unittest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from prompt_calculus_studio.accent_colors import ACCENT_PRESETS, valid_hex
from prompt_calculus_studio.color_roles import contrast
from prompt_calculus_studio.theme import visual_tokens
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from prompt_calculus_studio.core import initial_state, validate_state as validate
import test_workspace_panes as fixture


class AccentColorTests(unittest.TestCase):
    def test_presets_and_extreme_custom_colors_keep_controls_readable(self):
        for palette in ('graphite','mist','paper'):
            for accent,custom in [(key,None) for _,key,_ in ACCENT_PRESETS]+[
                    ('custom',v) for v in ('#000000','#ffffff','#f077af','#ff0000','#7f7f7f')]:
                with self.subTest(palette=palette,accent=accent,custom=custom):
                    settings={'visual_palette':palette,'accent':accent,'accent_custom':custom}
                    roles=visual_tokens(settings)
                    if custom:self.assertEqual(roles['accent'],custom)
                    for background in ('accent','accent_hover','accent_pressed'):
                        self.assertGreaterEqual(contrast(roles['on_accent'],roles[background]),4.5)
                    for background in ('base','surface','field','raised'):
                        self.assertGreaterEqual(contrast(roles['accent_outline'],roles[background]),3)

    def test_custom_settings_validation_keeps_old_documents_valid(self):
        state=initial_state();state['settings'].pop('accent_custom',None);validate(state)
        state['settings'].update(accent='custom',accent_custom='#F077AF');validate(state)
        for value in ('#123','red','#gg0000',None,123):
            self.assertFalse(valid_hex(value));state['settings']['accent_custom']=value
            with self.assertRaises(ValueError):validate(state)


class AccentPickerTests(unittest.TestCase):
    setUpClass=classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp=fixture.WorkspacePaneTests.setUp
    tearDown=fixture.WorkspacePaneTests.tearDown
    events=fixture.WorkspacePaneTests.events
    open=fixture.WorkspacePaneTests.open

    def controls(self):
        self.w.open_route('settings','appearance');self.events()
        return self.w.utility.settings_page.preferences

    def flush(self):
        self.w.utility.settings_page.flush();self.events()

    def test_custom_color_and_preset_survive_restart_without_changing_content(self):
        tab=self.open('Street.pcs-workflow');before=tab.path.read_bytes();state=copy.deepcopy(tab.host.state)
        controls=self.controls()
        self.assertEqual(controls.accent.count(),10)
        for i in range(9):self.assertFalse(controls.accent.itemIcon(i).isNull())
        controls.accent.setCurrentIndex(controls.accent.findData('custom'))
        controls.accent_hex.setText('#F077AF');self.flush()
        self.assertTrue(controls.accent_hex.isVisible())
        self.assertEqual(self.w.settings['accent_custom'],'#f077af')
        self.assertEqual(tab.path.read_bytes(),before);self.assertEqual(tab.host.state,state)
        controls.accent.setCurrentIndex(controls.accent.findData('purple'));self.flush()
        self.assertFalse(controls.accent_hex.isVisible())
        data=self.w.data;self.w.close();self.w.deleteLater();self.events()
        self.w=WorkspaceShell(self.root,data);self.w.report_error=self.errors.append;self.w.show();self.events()
        self.assertEqual(self.w.settings['accent'],'purple')
        self.assertEqual(self.w.settings['accent_custom'],'#f077af')
        controls=self.controls();controls.accent.setCurrentIndex(controls.accent.findData('custom'));self.flush()
        self.assertEqual(controls.accent_hex.text(),'#f077af')
        self.assertEqual(visual_tokens(self.w.settings)['accent'],'#f077af')

    def test_partial_hex_preserves_color_and_picker_applies_live_on_escape(self):
        controls=self.controls();controls.accent.setCurrentIndex(controls.accent.findData('custom'))
        controls.accent_hex.setText('#124567');self.flush()
        for value in ('','#12','#GGGGGG'):
            controls.accent_hex.setText(value);self.flush()
            self.assertEqual(self.w.settings['accent_custom'],'#124567')
            self.assertTrue(controls.accent_hex.toolTip())
        controls.accent_hex.setText('#124567')
        controls.accent_row.open_picker();self.events();popup=controls.accent_row.popover
        QTest.mouseClick(popup.plane,Qt.MouseButton.LeftButton,pos=QPoint(100,80))
        QTest.keyClick(popup.strip,Qt.Key.Key_Right)
        QTest.qWait(80);self.events()
        changed=controls.accent_hex.text()
        self.assertNotEqual(changed,'#124567')
        self.assertTrue(popup.isVisible())
        self.assertEqual(self.w.settings['accent_custom'],changed)
        self.assertEqual(visual_tokens(self.w.settings)['accent'],changed)
        QTest.keyClick(popup.plane,Qt.Key.Key_Escape);self.events()
        self.assertEqual(self.w.settings['accent_custom'],changed)
        controls.accent_row.open_picker();self.events();popup=controls.accent_row.popover
        QTest.mouseClick(popup.plane,Qt.MouseButton.LeftButton,pos=QPoint(180,40))
        popup.close();self.events()
        self.assertNotEqual(controls.accent_hex.text(),changed)
        self.assertEqual(controls.accent_hex.text(),self.w.settings['accent_custom'])

    def test_reset_only_accent_retains_custom_color_and_persists(self):
        controls=self.controls();controls.accent.setCurrentIndex(controls.accent.findData('custom'))
        controls.accent_hex.setText('#e17a29');self.flush()
        before=copy.deepcopy(self.w.settings)
        QTest.mouseClick(controls.accent_row.reset_button,Qt.MouseButton.LeftButton);self.events()
        self.assertEqual(self.w.settings,{**before,'accent':'neutral'})
        self.assertFalse(controls.accent_row.reset_button.isEnabled())
        data=self.w.data;self.w.close();self.w.deleteLater();self.events()
        self.w=WorkspaceShell(self.root,data);self.w.report_error=self.errors.append;self.w.show();self.events()
        self.assertEqual(self.w.settings['accent'],'neutral')
        self.assertEqual(self.w.settings['accent_custom'],'#e17a29')

    def test_color_menu_keyboard_selects_and_escape_preserves_choice(self):
        controls=self.controls();combo=controls.accent
        combo.showPopup();self.events()
        QTest.keyClick(combo.view(),Qt.Key.Key_Down);QTest.keyClick(combo.view(),Qt.Key.Key_Escape)
        self.assertEqual(combo.currentData(),'neutral')
        combo.showPopup();self.events()
        index=combo.model().index(combo.findData('pink'),0)
        QTest.mouseClick(combo.view().viewport(),Qt.MouseButton.LeftButton,pos=combo.view().visualRect(index).center())
        self.flush();self.assertEqual(self.w.settings['accent'],'pink')

    def test_custom_hex_stays_readable_at_largest_text_size(self):
        controls=self.controls();self.w.resize(800,700)
        controls.accent.setCurrentIndex(controls.accent.findData('custom'))
        controls.ui_size.setValue(22);self.flush()
        field=controls.accent_hex
        self.assertGreaterEqual(field.width()-24,field.fontMetrics().horizontalAdvance('#FFFFFF'))
        row=controls.accent_row
        self.assertLessEqual(row.custom.geometry().right(),row.width())
        self.assertFalse(row.combo.geometry().intersects(row.custom.geometry()))
