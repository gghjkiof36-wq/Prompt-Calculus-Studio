"""Canvas text editing keeps reusable combinations and sibling copies intact."""
import copy
import unittest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import test_editor_reorder as fixture
from prompt_calculus_studio import composition as comp, materials, prompt_materials as pm


class CanvasCombinationCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = fixture.EditorReorderTests.setUp
    tearDown = fixture.EditorReorderTests.tearDown

    def place_combination(self):
        root = pm.new('combination', '可重用組合')
        root['deduplicate'] = False
        root['combination']['modules'] = [
            comp.clone_node(comp.prototype(materials.item(self.w.state, ident)))
            for ident in (self.ids[0], self.ids[2])
        ]
        root['combination']['text'] = 'retained direct draft'
        refs = {}

        def add(state):
            refs['source'] = materials.save(state, dict(name=root['name'], prompt='',
                composition=root, material_type='combination', module=self.cat))
            target = next(iter(state['multi_output']['canvases']))
            refs['first'] = materials.add_to_canvas(state, refs['source'], target)
            refs['second'] = materials.add_to_canvas(state, refs['source'], target)

        self.assertTrue(self.w.canvas.commit(add))
        self.w.enter_canvas(); self.app.processEvents()
        self.refs = refs
        self.catalog = copy.deepcopy(self.w.state['items'])
        self.first = copy.deepcopy(self.w.state['uses'][refs['first']])
        self.second = copy.deepcopy(self.w.state['uses'][refs['second']])
        source = materials.item(self.w.state, refs['source'])['composition']
        self.assertTrue({n['id'] for n in comp.walk(source)}.isdisjoint(
            n['id'] for n in comp.walk(self.first)))
        self.assertTrue({n['id'] for n in comp.walk(self.first)}.isdisjoint(
            n['id'] for n in comp.walk(self.second)))

    def edit(self, change=None, *, cancel=False, expected=None):
        canvas = self.w.canvas
        key = next(key for key, (ident, node) in canvas.entries.items()
                   if ident == self.refs['first'] and node == self.first['id'])

        failures = []

        def interact():
            dialog = canvas.active_node_dialog
            try:
                if expected is not None:
                    self.assertEqual(dialog.prompt.toPlainText(), expected)
                if change is not None:
                    dialog.prompt.setPlainText(change)
                if cancel:
                    dialog.reject()
                else:
                    dialog.save()
            except Exception as exc:
                failures.append(exc)
                dialog.reject()

        # Drive the actual modal editor and its save/history transaction.
        QTimer.singleShot(0, interact)
        result = canvas.edit(key)
        if failures:
            raise failures[0]
        self.app.processEvents()
        return result

    def assert_isolated(self):
        self.assertEqual(self.w.state['items'], self.catalog)
        self.assertEqual(self.w.state['uses'][self.refs['second']], self.second)
        current = self.w.state['uses'][self.refs['first']]
        self.assertEqual(current['combination']['modules'], self.first['combination']['modules'])

    def test_unchanged_apply_preserves_combination_structure_and_source(self):
        self.place_combination()
        self.edit(expected=comp.render(self.first))
        self.assertEqual(self.w.state['uses'][self.refs['first']], self.first)
        self.assert_isolated()

    def test_text_override_undo_redo_and_reopen_only_affect_canvas_copy(self):
        self.place_combination()
        self.assertTrue(self.edit('local canvas text', expected=comp.render(self.first)))
        changed = copy.deepcopy(self.w.state['uses'][self.refs['first']])
        self.assertEqual(comp.render(changed), 'local canvas text')
        self.assertEqual(changed['combination']['input'], 'text')
        self.assert_isolated()
        self.w.canvas.undo(); self.app.processEvents()
        self.assertEqual(self.w.state['uses'][self.refs['first']], self.first)
        self.assert_isolated()
        self.w.canvas.redo(); self.app.processEvents()
        self.assertEqual(self.w.state['uses'][self.refs['first']], changed)
        self.assert_isolated()
        self.edit(expected='local canvas text')
        self.assertEqual(self.w.state['uses'][self.refs['first']], changed)
        self.assert_isolated()

    def test_cancel_and_empty_text_remain_local_and_undoable(self):
        self.place_combination()
        self.assertFalse(self.edit('discarded local text', cancel=True))
        self.assertEqual(self.w.state['uses'][self.refs['first']], self.first)
        self.assert_isolated()
        self.assertTrue(self.edit(''))
        self.assertEqual(comp.render(self.w.state['uses'][self.refs['first']]), '')
        self.assert_isolated()
        self.edit(expected='')
        self.w.canvas.undo(); self.app.processEvents()
        self.assertEqual(self.w.state['uses'][self.refs['first']], self.first)
        self.assert_isolated()
        self.w.canvas.redo(); self.app.processEvents()
        self.assertEqual(comp.render(self.w.state['uses'][self.refs['first']]), '')
        self.assert_isolated()


if __name__ == '__main__':
    unittest.main()
