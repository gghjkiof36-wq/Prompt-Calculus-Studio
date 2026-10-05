import copy,os,tempfile,unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QLabel
from PySide6.QtTest import QTest
from prompt_calculus_studio.window import Window
from prompt_calculus_studio import multi_output as model
from test_multi_output import workflow

APP=QApplication.instance() or QApplication([])

class WorkflowWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1'}); self.env.start()
        self.w=Window(self.temp.name); self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.show(); self.w.set_interface_mode('canvas'); QTest.qWait(20)
    def tearDown(self):
        self.w.close(); APP.processEvents(); self.env.stop(); self.temp.cleanup()
        for widget in APP.topLevelWidgets():
            if isinstance(widget,QLabel): widget.close()
    def assert_main_window_only(self):
        floating=[w for w in APP.topLevelWidgets() if w.isVisible() and w is not self.w and w.graphicsProxyWidget() is None]
        self.assertEqual([(type(w).__name__,w.text() if isinstance(w,QLabel) else w.windowTitle()) for w in floating],[])
    def test_activate_bind_refresh_and_reopen_does_not_show_floating_hint(self):
        self.w.generation_panel.save_profile(workflow()); self.w.settings('workflows')
        oid=self.w.state['multi_output']['current_output']; manager=self.w.settings_page.workflow_manager
        manager.bind_output('flow',oid,('6','text')); QTest.qWait(20)
        for _ in range(3): self.w.generation_panel.refresh()
        self.assert_main_window_only()
        self.w.return_to_prompt(); self.w.set_interface_mode('list'); self.w.generation_panel.refresh(); self.assert_main_window_only()
        self.w.persist(); self.w.close(); APP.processEvents(); self.w=Window(self.temp.name); self.w.show(); QTest.qWait(20)
        self.w.settings('workflows'); self.assert_main_window_only()
    def test_denoise_stays_in_parameter_editor_and_summary_is_attached(self):
        from prompt_calculus_studio.generation_panel import ParametersDialog
        profile=workflow(); self.w.generation_panel.save_profile(profile)
        self.assertIn('Denoise',self.w.generation_panel.parameters.toolTip())
        self.assertIsNotNone(self.w.generation_panel.workflow.parentWidget())
        dialog=ParametersDialog(self.w,profile)
        try:
            self.assertIn('denoise',dialog.fields)
        finally: dialog.close(); dialog.deleteLater()
