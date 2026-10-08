"""Startup route and display recovery preserve state without frame restyling."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.core import Storage, initial_state
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.editor_legacy import documents

APP = QApplication.instance() or QApplication([])


class RevisionThreeStartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'PROMPT_STUDIO_V08': '1', 'PROMPT_STUDIO_DATA': self.tmp.name})
        self.env.start()
        self.transport = patch('prompt_calculus_studio.comfy_client.ComfyClient.request', return_value=None)
        self.transport.start()
        state = prepare_state(initial_state(), multi=True)
        state['settings'].update(online=False, material='solid', interface_mode='list', separate_selections=True)
        state['selection_view'] = 'list'
        state['draft'] = '我的清單手動草稿'
        store = Storage(self.tmp.name)
        store.save(state)
        store.db.close()
        self.w = Window(self.tmp.name)

    def tearDown(self):
        self.w.close()
        APP.processEvents()
        self.transport.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_saved_list_and_export_start_on_canvas_with_legacy_draft_recoverable(self):
        before = copy.deepcopy(self.w.state['text_positions'])
        self.w.show_page(self.w.clean_export)
        self.w.start_interface()
        self.assertFalse(self.w.isVisible())
        self.assertIs(self.w.surface_stack.currentWidget(), self.w.canvas_shell)
        self.assertIs(self.w.canvas_content.currentWidget(), self.w.canvas)
        self.assertEqual(self.w.state['text_positions'], before)
        self.w.set_interface_mode('list')
        self.assertIs(self.w.surface_stack.currentWidget(), self.w.prompt_editor)
        self.assertEqual(self.w.state['selection_view'], 'canvas')
        # The Editor keeps the current Canvas output active. Historical list
        # text remains recoverable without becoming the generation draft.
        def check_saved_legacy(state):
            self.assertIsNone(state['draft'])
            current = state['multi_output']['current_output']
            saved = state['multi_output']['outputs'][current]['list_draft']
            self.assertEqual(saved['draft'], '我的清單手動草稿')
            recovered = next(value for value in documents(state) if value['id'] == current)
            self.assertEqual(recovered['manual'], '我的清單手動草稿')
            self.assertEqual(recovered['final'], '我的清單手動草稿')
        check_saved_legacy(self.w.state)
        self.assertTrue(self.w.persist())
        check_saved_legacy(self.w.store.load())
        self.w.close()
        APP.processEvents()
        self.w = Window(self.tmp.name)
        self.w.start_interface()
        self.w.set_interface_mode('list')
        self.assertIs(self.w.surface_stack.currentWidget(), self.w.prompt_editor)
        check_saved_legacy(self.w.state)
        check_saved_legacy(self.w.store.load())

    def test_recovery_does_not_clear_styles_or_repeat_native_backdrop(self):
        self.w.apply_theme()
        with patch.object(self.w, 'setStyleSheet', wraps=self.w.setStyleSheet) as style, \
                patch('prompt_calculus_studio.window.apply_backdrop', return_value=False) as backdrop:
            self.w.apply_theme(preserve_layout=True, refresh_fonts=True)
            style.assert_not_called()
            backdrop.assert_not_called()
            self.w.state['settings']['ui_size'] = 18
            self.w.apply_theme(preserve_layout=True, refresh_fonts=True)
            self.assertEqual(style.call_count, 1)
            self.assertTrue(style.call_args.args[0])
            backdrop.assert_not_called()

    def test_material_preview_invalidates_backdrop_cache_on_cancel(self):
        self.w.apply_theme()
        with patch('prompt_calculus_studio.window.apply_backdrop', return_value=True) as backdrop:
            self.w.appearance_preview = {'material': 'mica'}
            self.w.update_material_preview()
            self.w.appearance_preview = {}
            self.w.apply_theme(preserve_layout=True)
            self.assertEqual([call.args[1] for call in backdrop.call_args_list], ['mica', 'solid'])


if __name__ == '__main__':
    unittest.main()
