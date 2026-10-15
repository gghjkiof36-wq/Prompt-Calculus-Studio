"""Real output-picker signals must not rewrite another output's Prompt order."""
import copy
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio import materials, material_navigation as nav
from prompt_calculus_studio.core import Storage, initial_state, validate_state, TEMPORARY_GROUP
from prompt_calculus_studio.multi_output import compiled_outputs
from prompt_calculus_studio.snapshots import make_snapshot, portable_state, restore_snapshot
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.window import Window
from test_multi_output import workspace


APP = QApplication.instance() or QApplication([])


def ordered_workspace():
    state, _, first = workspace()
    state = prepare_state(state, multi=True)
    state.update(selection_view='list', draft=None, draft_base='')
    state['settings'].update(separate_selections=True, online=False)
    unused = materials.add_category(state, 'SYNTHETIC_UNUSED_CATEGORY')
    red = materials.add_category(state, 'R needed')
    materials.add_category(state, 'SYNTHETIC_GAP')
    blue = materials.add_category(state, 'B needed')
    green = materials.add_category(state, 'G needed')
    chosen = {mid: materials.save(state, dict(name=text, prompt=text, module=mid))
              for mid, text in ((red, 'TAG_R'), (blue, 'TAG_B'), (green, 'TAG_G'))}
    folder = nav.add_folder(state, 'needed folder')
    for mid in (green, red, blue, unused):
        nav.move(state, mid, folder)
    nav.add_folder(state, 'SYNTHETIC_UNUSED_FOLDER')
    state['selections'] = {red: [chosen[red]], green: [chosen[green]], unused: []}
    state['temporary'] = ['TEMP_P']
    state['temporary_before'] = unused
    state['output_order'] = [green, red, TEMPORARY_GROUP, *state['uses']]
    output = state['multi_output']['outputs']['out2']
    output['list_state'] = dict(selections={mid: [item] for mid, item in chosen.items()},
                                temporary=['TEMP_N'], temporary_before=unused)
    state['multi_output']['outputs']['out3']['list_state'] = {}
    state.pop('workspace_scenes', None)
    return state, first


class OutputSwitchOrderTests(unittest.TestCase):
    @contextmanager
    def window(self, directory, state=None, view='list'):
        if state is not None:
            store = Storage(directory)
            try:
                store.save(state)
            finally:
                store.close()
        with patch('prompt_calculus_studio.comfy_client.ComfyClient.request',
                   side_effect=AssertionError('No external requests')):
            window = Window(directory)
            errors = []
            window.error = errors.append
            try:
                window.comfy.timer.stop()
                window.comfy.enabled = False
                window.show()
                window.enter_canvas()
                window.activate_prompt_view(view)
                window.refresh_builder()
                QTest.qWait(10)
                yield window
                self.assertEqual(errors, [])
            finally:
                window.close()
                APP.processEvents()

    def choose(self, window, key):
        picker = window.canvas.output_picker
        index = picker.findData(key)
        self.assertGreaterEqual(index, 0)
        picker.setCurrentIndex(index)
        QTest.qWait(10)
        self.assertEqual(window.state['multi_output']['current_output'], key)

    def assert_outputs(self, window, expected, bindings):
        self.assertEqual(compiled_outputs(window.state), expected)
        self.assertEqual(window.state['multi_output']['bindings'], bindings)
        validate_state(window.state)

    def test_picker_preserves_all_outputs_including_manual_empty(self):
        for restored in (False, True):
            for manual in (False, True):
                with self.subTest(restored=restored, manual=manual), tempfile.TemporaryDirectory() as directory:
                    state, first = ordered_workspace()
                    if manual:
                        state['multi_output']['outputs']['out2']['list_draft'] = dict(draft='', draft_base='original base')
                    expected = compiled_outputs(state)
                    self.assertEqual(expected['out2']['generated_prompt'], 'TAG_G,\nTAG_R,\nTEMP_N,\nTAG_B')
                    if restored:
                        state = restore_snapshot(initial_state(), make_snapshot(state))
                    bindings = copy.deepcopy(state['multi_output']['bindings'])
                    with self.window(directory, state) as window:
                        for key in ('out2', 'out3', first, 'out2', first):
                            self.choose(window, key)
                            self.assert_outputs(window, expected, bindings)
                            self.assertEqual(window.final.toPlainText(), expected[key]['final_prompt'])

    def test_save_reopen_while_empty_preserves_order_and_projected_privacy(self):
        state, first = ordered_workspace()
        state['multi_output']['outputs']['out2']['list_draft'] = dict(draft='', draft_base='preserved base')
        expected = compiled_outputs(state)
        state = restore_snapshot(initial_state(), make_snapshot(state))
        bindings = copy.deepcopy(state['multi_output']['bindings'])
        with tempfile.TemporaryDirectory() as directory:
            with self.window(directory, state) as window:
                self.choose(window, 'out2')
                self.choose(window, 'out3')
                self.assertTrue(window.persist())
            with self.window(directory) as window:
                self.assertEqual(window.state['multi_output']['current_output'], 'out3')
                for key in (first, 'out2', 'out3'):
                    self.choose(window, key)
                    self.assert_outputs(window, expected, bindings)
                snapshot = make_snapshot(window.state)
                self.assertNotIn('SYNTHETIC_', json.dumps(snapshot))
                self.assertEqual(snapshot['outputs'], expected)
                self.assertEqual(compiled_outputs(restore_snapshot(initial_state(), snapshot)), expected)
                full = portable_state(window.state)
                self.assertEqual(full['output_order'], window.state['output_order'])
                self.assertEqual(compiled_outputs(full), expected)

    def test_shared_list_and_canvas_switching_keep_existing_text(self):
        for separate, view in ((False, 'list'), (False, 'canvas'), (True, 'canvas')):
            with self.subTest(separate=separate, view=view), tempfile.TemporaryDirectory() as directory:
                state, first = ordered_workspace()
                state['settings']['separate_selections'] = separate
                state['selection_view'] = view
                expected = compiled_outputs(state)
                state = restore_snapshot(initial_state(), make_snapshot(state))
                bindings = copy.deepcopy(state['multi_output']['bindings'])
                with self.window(directory, state, view=view) as window:
                    for key in ('out2', 'out3', first, 'out2'):
                        self.choose(window, key)
                        self.assert_outputs(window, expected, bindings)
                    self.assertTrue(window.persist())
                with self.window(directory, view=view) as window:
                    self.assert_outputs(window, expected, bindings)

    def test_canvas_draft_edit_and_undo_redo_do_not_reorder_independent_lists(self):
        state, _ = ordered_workspace()
        state = restore_snapshot(initial_state(), make_snapshot(state))
        expected = compiled_outputs(state)
        bindings = copy.deepcopy(state['multi_output']['bindings'])
        with tempfile.TemporaryDirectory() as directory, self.window(directory, state) as window:
            self.choose(window, 'out3')
            panel = window.canvas.outputs['out2'].panel
            panel.editor.setPlainText('')
            QTest.qWait(10)
            self.assertEqual(window.state['multi_output']['outputs']['out2']['draft'], '')
            self.assert_outputs(window, expected, bindings)
            window.canvas.undo()
            self.assertIsNone(window.state['multi_output']['outputs']['out2']['draft'])
            self.assert_outputs(window, expected, bindings)
            window.canvas.redo()
            self.assertEqual(window.state['multi_output']['outputs']['out2']['draft'], '')
            self.assert_outputs(window, expected, bindings)

    def test_explicit_reorder_and_removal_still_update_the_prompt(self):
        state, _ = ordered_workspace()
        with tempfile.TemporaryDirectory() as directory, self.window(directory, state) as window:
            self.choose(window, 'out2')
            before = compiled_outputs(window.state)
            tree = window.selected
            temporary = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                             if tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == ('group', TEMPORARY_GROUP))
            tree.setCurrentItem(temporary)
            tree.move_current(-1)
            moved = compiled_outputs(window.state)
            self.assertEqual(moved['out2']['final_prompt'], 'TAG_G,\nTEMP_N,\nTAG_R,\nTAG_B')
            window.canvas.undo()
            self.assertEqual(compiled_outputs(window.state), before)
            window.canvas.redo()
            self.assertEqual(compiled_outputs(window.state), moved)
            window.remove_builder_entry(('temporary', 0))
            self.assertEqual(compiled_outputs(window.state)['out2']['final_prompt'], 'TAG_G,\nTAG_R,\nTAG_B')
            validate_state(window.state)


if __name__ == '__main__':
    unittest.main()
