"""Affected Canvas ownership and copy-editor contracts, isolated Qt only."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox
from prompt_calculus_studio import composition as comp, materials, multi_output as model
from prompt_calculus_studio.core import DEFAULT_SETTINGS
from prompt_calculus_studio.extension_page import ExtensionPage
from prompt_calculus_studio.window import Window

APP = QApplication.instance() or QApplication([])


class CanvasRev9Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for context in (patch.dict(os.environ, PROMPT_STUDIO_V08='1'),
                        patch.dict(DEFAULT_SETTINGS, online=False, comfy_enabled=False, reduce_motion=True),
                        patch.object(ExtensionPage, 'startup')):
            context.start(); self.addCleanup(context.stop)
        self.w = Window(self.temp.name)
        self.w.comfy.timer.stop(); self.w.comfy.enabled = False
        self.w.state['settings'].update(online=False, comfy_enabled=False, material='solid', reduce_motion=True)
        self.notices = []; self.w.notice = self.notices.append
        self.w.resize(1360, 900); self.w.show(); self.w.set_interface_mode('canvas')
        QTest.qWait(10)
        self.c = self.w.canvas
        self.cid = next(iter(self.c.data()['canvases']))
        self.other = self.c.add_canvas(QPointF(2200, 200))
        self.asset_id = None
        def create(state):
            self.asset_id = materials.save(state, dict(name='Clothing', prompt='red dress', module=None))
        self.c.commit(create)

    def tearDown(self):
        self.w.close(); APP.processEvents()

    def asset(self):
        return materials.item(self.w.state, self.asset_id)

    def add_copy(self):
        old = set(self.w.state['uses'])
        self.assertTrue(self.c.insert_asset_with_destination(self.asset(), QPointF(2240, 340), self.cid))
        return (set(self.w.state['uses']) - old).pop()

    def edit_dialog(self, canvas, key, *, save):
        errors = []
        def input_and_close():
            try:
                dialog = canvas.active_node_dialog
                self.assertEqual(dialog.use_id, key.split(':')[0])
                dialog.name.setText('Local copy')
                dialog.prompt.setFocus(); dialog.prompt.selectAll(); QTest.keyClicks(dialog.prompt, 'blue dress')
                dialog.weight.setValue(1.7); dialog.enabled.setChecked(False)
                dialog.excludes.setPlainText('red dress\nsmile')
                buttons = dialog.findChild(QDialogButtonBox)
                choice = QDialogButtonBox.StandardButton.Save if save else QDialogButtonBox.StandardButton.Cancel
                QTest.mouseClick(buttons.button(choice), Qt.MouseButton.LeftButton)
            except BaseException as error:
                errors.append(error)
                canvas.active_node_dialog.reject()
        QTimer.singleShot(0, input_and_close)
        result = canvas.edit(key)
        if errors: raise errors[0]
        return result

    def test_fixed_destination_wins_over_position_and_copy_is_independent(self):
        first = self.add_copy(); second = self.add_copy()
        self.assertEqual(model.owner(self.w.state, first), self.cid)
        self.assertEqual(model.owner(self.w.state, second), self.cid)
        self.assertNotEqual(self.w.state['uses'][first]['id'], self.w.state['uses'][second]['id'])
        self.assertEqual(self.c.insertion_canvas, None)
        self.assertFalse(getattr(self.c, '_fixed_insertion_canvas', False))

    def test_deleted_destination_does_not_insert_or_create_history(self):
        before = copy.deepcopy(self.w.state); undo = len(self.c.undo_stack)
        self.assertFalse(self.c.insert_asset_with_destination(self.asset(), None, 'removed'))
        self.assertEqual(self.w.state, before); self.assertEqual(len(self.c.undo_stack), undo)

    def test_fixed_nested_target_preserves_path_and_rejects_foreign_owner(self):
        root = self.add_copy(); self.c.enter(root)
        self.assertTrue(self.c.add_tag_with_destination('smile', None, self.cid))
        parent = self.w.state['uses'][root]
        child = parent['children'][0]; key = root + ':' + child['id']
        self.c.enter(key); path = list(self.c.path)
        self.assertTrue(self.c.add_tag_with_destination('eyes', None, self.cid))
        self.assertEqual(self.c.root_id, root); self.assertEqual(self.c.path, path)
        self.assertEqual(comp.find(self.w.state['uses'][root], child['id'])['children'][0]['prompt'], 'eyes')
        before = copy.deepcopy(self.w.state)
        self.assertFalse(self.c.add_tag_with_destination('wrong', None, self.other))
        self.assertEqual(self.w.state, before)

    def test_root_dialog_cancel_then_save_is_one_undo_and_preserves_original(self):
        root = self.add_copy(); other = self.add_copy(); old = copy.deepcopy(self.w.state['uses'][root])
        before = len(self.c.undo_stack)
        self.assertFalse(self.edit_dialog(self.c, root, save=False))
        self.assertEqual(self.w.state['uses'][root], old); self.assertEqual(len(self.c.undo_stack), before)
        self.assertTrue(self.edit_dialog(self.c, root, save=True))
        saved = self.w.state['uses'][root]
        self.assertEqual((saved['prompt'], saved['weight'], saved['enabled'], saved['excludes']),
                         ('blue dress', 17, False, ['red dress', 'smile']))
        self.assertEqual(len(self.c.undo_stack), before + 1)
        self.assertEqual(self.asset()['prompt'], 'red dress')
        self.assertEqual(self.w.state['uses'][other]['prompt'], 'red dress')
        self.c.undo(); self.assertEqual(self.w.state['uses'][root], old)
        self.c.redo(); self.assertEqual(self.w.state['uses'][root], saved)

    def test_nested_dialog_save_and_undo_keeps_parent_and_original(self):
        root = self.add_copy(); self.c.enter(root)
        self.c.add_tag_with_destination('smile', None, self.cid)
        child = self.w.state['uses'][root]['children'][0]; key = root + ':' + child['id']
        old = copy.deepcopy(self.w.state['uses'][root]); before = len(self.c.undo_stack)
        self.assertTrue(self.edit_dialog(self.c, key, save=True))
        self.assertEqual(self.w.state['uses'][root]['prompt'], 'red dress')
        self.assertEqual(comp.find(self.w.state['uses'][root], child['id'])['prompt'], 'blue dress')
        self.assertEqual(len(self.c.undo_stack), before + 1)
        self.c.undo(); self.assertEqual(self.w.state['uses'][root], old)
        self.assertEqual(self.asset()['prompt'], 'red dress')

    def test_expanded_copy_editor_has_no_text_host_and_limits_destination(self):
        root = self.add_copy(); self.c.open_editor(self.cid); page = self.c.editor_page
        APP.processEvents()
        canvas = page.text_canvas
        self.assertFalse(canvas.add_tag_with_destination('wrong owner', None, self.other))
        self.assertTrue(self.edit_dialog(canvas, root, save=True))
        self.assertIsNone(page.prompt_editor); self.assertIsNone(page.text_sidebar)
        self.assertTrue(page.layer_toggle.isChecked())
        self.assertEqual(self.asset()['prompt'], 'red dress')
        page.close_editor(); APP.processEvents()
        self.assertIsNone(self.c.editor_page)


if __name__ == '__main__': unittest.main()
