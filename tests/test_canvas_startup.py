"""Configured saved documents must reopen before the Comfy client exists."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.core import Storage, initial_state
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.canvas_starter import initialize, guide
from prompt_calculus_studio.composition import node
from prompt_calculus_studio.generation import store_profile
from prompt_calculus_studio.clip_flow import set_binding
from prompt_calculus_studio.stage_model import choose
from prompt_calculus_studio.drafts import edit
from test_multi_output import workflow


class CanvasStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def configured(self, draft):
        state=initialize(prepare_state(initial_state(),multi=True),fresh_install=True)
        refs=guide(state)
        state['settings'].update(online=False,comfy_enabled=False,material='solid')
        state['uses']['startup-text']=node('Saved text','retained prompt')
        state['multi_output']['canvases'][refs['canvas']]['members'].append('startup-text')
        store_profile(state,workflow())
        set_binding(state,'flow',refs['clip'],('6','text'))
        choose(state,refs['stage'],'flow')
        edit(state,draft,refs['output'])
        return state,refs

    def reopen(self,draft):
        from prompt_calculus_studio.window import Window
        state,refs=self.configured(draft)
        expected=copy.deepcopy(state)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder,patch.dict(os.environ,{
                'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_DATA':folder}):
            store=Storage(folder);store.save(state);store.db.close()
            for _ in range(2):
                window=None
                try:
                    window=Window(folder)
                    self.assertFalse(window.comfy.connected)
                    self.assertIn('連線 ComfyUI',window.canvas.onboarding.title.text())
                    for field in ('stages','clip_inputs','connections'):
                        self.assertEqual(window.state['multi_output'][field],expected['multi_output'][field])
                    self.assertEqual(window.state['generation']['profiles'],expected['generation']['profiles'])
                    self.assertEqual(window.state['multi_output']['outputs'][refs['output']]['draft'],draft)
                    with patch.object(window.comfy,'request',side_effect=AssertionError('Guide must not send requests')):
                        window.comfy.connected=True
                        window.canvas.onboarding.refresh()
                        self.assertIn('流程已設定',window.canvas.onboarding.title.text())
                        window.comfy.connected=False
                        window.canvas.onboarding.refresh()
                        self.assertIn('連線 ComfyUI',window.canvas.onboarding.title.text())
                finally:
                    if window is not None:
                        window.close();self.app.processEvents()

    def test_configured_workflow_reopens_without_resetting_manual_draft(self):
        self.reopen('saved manual prompt')

    def test_configured_workflow_reopens_with_valid_empty_manual_draft(self):
        self.reopen('')


if __name__=='__main__':unittest.main()
