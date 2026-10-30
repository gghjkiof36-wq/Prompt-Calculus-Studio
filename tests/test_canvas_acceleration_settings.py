"""Canvas renderer preference is local and takes effect only next session."""
import json
import unittest
from unittest.mock import patch

from prompt_calculus_studio.workspace_shell import WorkspaceShell
import test_workspace_shell as fixture


class CanvasAccelerationSettingsTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    setUp = fixture.WorkspaceShellTests.setUp
    tearDown = fixture.WorkspaceShellTests.tearDown
    events = fixture.WorkspaceShellTests.events

    def test_control_persists_without_replacing_current_or_new_tab_policy(self):
        tab = self.w.open_path(self.root/'Street.pcs-workflow')
        self.events()
        view = tab.host.canvas.view
        viewport = view.viewport()
        original = tab.path.read_bytes()
        self.w.open_route('settings', 'appearance')
        preferences = self.w.utility.settings_page.preferences
        self.assertEqual(preferences.canvas_hardware_acceleration.text(), '畫布硬體加速')
        self.assertIn('重新啟動', preferences.canvas_acceleration_hint.text())
        with patch.object(self.w, 'style_shell', side_effect=AssertionError('no renderer restyle')):
            preferences.canvas_hardware_acceleration.setChecked(False)
        self.w.utility.settings_page.flush()
        saved = json.loads((self.w.data/'workspace-ui.json').read_text(encoding='utf-8'))
        self.assertIs(saved['canvas_hardware_acceleration'], False)
        self.assertTrue(self.w.canvas_hardware_acceleration_active)
        self.assertIs(view.viewport(), viewport)
        self.assertEqual(tab.path.read_bytes(), original)
        other = self.w.open_path(self.root/'Night.pcs-workflow', new_tab=True)
        self.assertTrue(other.host.workspace_owner.canvas_hardware_acceleration_active)

    def test_restart_reads_preference_and_invalid_values_do_not_enable_it(self):
        self.w.set_canvas_hardware_acceleration(False)
        reopened = WorkspaceShell(self.root, self.w.data, defer_restore=True)
        try:
            self.assertFalse(reopened.canvas_hardware_acceleration)
            self.assertFalse(reopened.canvas_hardware_acceleration_active)
            for value in ('true', 1, None, []):
                (self.w.data/'workspace-ui.json').write_text(
                    json.dumps({'canvas_hardware_acceleration': value}), encoding='utf-8')
                reopened.load_ui_preferences()
                self.assertFalse(reopened.canvas_hardware_acceleration)
        finally:
            reopened.close()
            reopened.deleteLater()
            self.events()

    def test_fallback_is_reported_without_changing_saved_choice(self):
        self.w.open_route('settings', 'appearance')
        preferences = self.w.utility.settings_page.preferences
        self.assertNotIn('已使用軟體繪製', preferences.canvas_acceleration_hint.text())
        self.w.report_canvas_acceleration_fallback()
        self.assertTrue(preferences.canvas_hardware_acceleration.isChecked())
        self.assertIn('已使用軟體繪製', preferences.canvas_acceleration_hint.text())


if __name__ == '__main__':
    unittest.main()
