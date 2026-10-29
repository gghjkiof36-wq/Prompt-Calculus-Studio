"""Variant exclusions survive real Manager saves and detached workflow copies."""
import copy
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from prompt_calculus_studio import materials, prompt_materials as pm
from prompt_calculus_studio.local_documents import io
import test_workspace_material_transfer as transfer_fixture
import test_workspace_shell as shell_fixture


class VariantExclusionWorkspaceTests(unittest.TestCase):
    # Borrow setup only: do not collect the fixture's unrelated transfer tests.
    setUpClass = classmethod(transfer_fixture.WorkspaceMaterialTransferTests.setUpClass.__func__)
    setUp = transfer_fixture.WorkspaceMaterialTransferTests.setUp
    tearDown = transfer_fixture.WorkspaceMaterialTransferTests.tearDown
    events = shell_fixture.WorkspaceShellTests.events
    editor = shell_fixture.WorkspaceShellTests.editor
    capture = transfer_fixture.WorkspaceMaterialTransferTests.capture
    target_tab = transfer_fixture.WorkspaceMaterialTransferTests.target_tab

    def write_field(self, field, text):
        field.setFocus()
        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        for index, line in enumerate(text.split('\n')):
            if index:
                QTest.keyClick(field, Qt.Key.Key_Return)
            QTest.keyClicks(field, line)
        self.events()
        self.assertEqual(field.toPlainText(), text)

    def show_exclusions(self, dialog):
        pages = dialog.pages
        if not pages.details_toggle.isChecked():
            QTest.mouseClick(pages.details_toggle, Qt.MouseButton.LeftButton)
        index = pages.details.indexOf(pages.excludes_page)
        QTest.mouseClick(pages.details.tabBar(), Qt.MouseButton.LeftButton,
                         pos=pages.details.tabBar().tabRect(index).center())
        self.events()
        self.assertIs(pages.details.currentWidget(), pages.excludes_page)
        return pages.variant_editor

    def assert_rules(self, root, local_a=('variant_a',)):
        self.assertEqual(root['excludes'], ['shared_tag', 'shared_second'])
        self.assertEqual(root['variants']['version'], 2)
        self.assertEqual([g.get('excludes', []) for g in root['variants']['groups']],
                         [list(local_a), ['variant_b', 'variant_b_two']])
        pm.validate_variants(root['variants'])

    def test_canvas_accept_selected_v2_variant_uses_its_exclusions(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QAbstractButton
        from prompt_calculus_studio import core, multi_output, prompt_groups as groups
        from prompt_calculus_studio.text_canvas import NodeDialog

        self.assertEqual(self.app.platformName(), 'offscreen')
        target = self.target_tab()
        canvas = target.host.canvas
        canvas_id = next(iter(target.host.state['multi_output']['canvases']))
        root = pm.new('text', 'Synthetic v2 variant source')
        root['excludes'] = ['pcs_shared_exclusion']
        root['variants'] = pm.variants(root)
        first = groups.new_group('First', 'pcs_first_variant')
        second = groups.new_group('Second', 'pcs_second_variant')
        root['variants'].update(groups=[first, second], start=first['id'], selected=first['id'])
        pm.set_variant_excludes(root['variants'], first['id'], ['pcs_a_only'])
        pm.set_variant_excludes(root['variants'], second['id'], ['pcs_b_only'])
        added = {}

        def seed(state):
            category = materials.add_category(state, 'Synthetic variant test')
            added['original'] = materials.save(state, dict(name=root['name'], prompt='',
                composition=root, module=category, material_type='text', content_kind='tags',
                excludes=list(root['excludes'])))
            added['use'] = materials.add_to_canvas(state, added['original'], canvas_id)
            candidate = materials.save(state, dict(name='Synthetic exclusion target',
                prompt='pcs_shared_exclusion, pcs_a_only, pcs_b_only, pcs_keep',
                module=category, content_kind='tags'))
            added['target'] = materials.add_to_canvas(state, candidate, canvas_id)

        self.assertTrue(canvas.commit(seed, 'Prepare synthetic v2 material'))
        self.events()
        original = copy.deepcopy(materials.item(target.host.state, added['original']))
        projection = multi_output.canvas_projection(target.host.state, canvas_id)
        before = core.build_prompt(projection)
        self.assertIn('pcs_first_variant', before)
        self.assertIn('pcs_b_only', before)
        self.assertNotIn('pcs_a_only', before)
        self.assertNotIn('pcs_shared_exclusion', before)
        key = next(key for key, (ident, node) in canvas.entries.items()
                   if ident == added['use'] and node == target.host.state['uses'][added['use']]['id'])
        callback_errors = []
        observed = []

        def select_and_accept():
            dialog = canvas.active_node_dialog
            try:
                self.assertIsInstance(dialog, NodeDialog)
                self.assertEqual(dialog.sequence['version'], 2)
                QTest.mouseClick(dialog.next, Qt.MouseButton.LeftButton)
                self.assertEqual(dialog.index.value(), 2)
                self.assertEqual(dialog.prompt.toPlainText(), 'pcs_second_variant')
                observed.append(dialog.sequence['selected'])
                apply_button = next(button for button in dialog.findChildren(QAbstractButton)
                                    if button.text() == '套用')
                QTest.mouseClick(apply_button, Qt.MouseButton.LeftButton)
            except Exception as error:
                callback_errors.append(repr(error))
                if dialog is not None:
                    dialog.reject()

        guard = QTimer(self.w)
        guard.setSingleShot(True)
        def expire():
            callback_errors.append('Node editor did not finish within 5 seconds')
            if canvas.active_node_dialog is not None:
                canvas.active_node_dialog.reject()
        guard.timeout.connect(expire)
        guard.start(5000)
        QTimer.singleShot(0, select_and_accept)
        try:
            accepted = canvas.edit(key)
        finally:
            guard.stop()
            guard.deleteLater()
        self.events()
        self.assertEqual(callback_errors, [])
        self.assertEqual(self.errors, [])
        self.assertTrue(accepted)
        self.assertEqual(observed, [second['id']])
        saved = target.host.state['uses'][added['use']]
        self.assertEqual(saved['variants']['selected'], second['id'])
        self.assertEqual(saved['selection_cursor']['position']['group'], second['id'])
        self.assertEqual(materials.item(target.host.state, added['original']), original)
        projection = multi_output.canvas_projection(target.host.state, canvas_id)
        after, affected = core.compose_details(projection)
        self.assertIn('pcs_second_variant', after)
        self.assertIn('pcs_a_only', after)
        self.assertIn('pcs_keep', after)
        self.assertNotIn('pcs_first_variant', after)
        self.assertNotIn('pcs_b_only', after)
        self.assertNotIn('pcs_shared_exclusion', after)
        self.assertEqual(affected[added['target']]['tags'], ['pcs_shared_exclusion', 'pcs_b_only'])

    def test_manager_reopen_and_workflow_copies_keep_each_rule_owner(self):
        self.assertEqual(self.app.platformName(), 'offscreen')
        original = self.source_tab.path.read_bytes()
        original_items = copy.deepcopy(self.source_tab.host.state['items'])
        _, dialog = self.editor(self.source_tab)
        self.write_field(dialog.prompt, 'alpha, shared_tag, variant_a')
        QTest.mouseClick(dialog.pages.add_variants, Qt.MouseButton.LeftButton)
        self.events()
        variant = dialog.pages.variant_editor
        self.assertEqual(len(variant.sequence['groups']), 2)
        self.write_field(variant.stack.currentWidget(), 'beta, shared_tag, variant_b')
        variant = self.show_exclusions(dialog)
        self.write_field(dialog.form.excludes, 'shared_tag\nshared_second')
        variant.choose.setCurrentIndex(0)
        self.events()
        self.write_field(variant.excludes_stack.currentWidget(), 'variant_a')
        variant.choose.setCurrentIndex(1)
        self.events()
        self.write_field(variant.excludes_stack.currentWidget(), 'variant_b\nvariant_b_two')
        self.assertEqual(self.source_tab.path.read_bytes(), original)
        self.assertEqual(self.source_tab.host.state['items'], original_items)
        QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        self.events()
        self.assertIsNone(self.page.active_dialog)
        self.assert_rules(materials.item(self.source_tab.host.state, self.ident)['composition'])
        self.w.autosave.flush(self.source_tab)
        self.events()
        self.assertFalse(self.source_tab.dirty, self.source_tab.document.error)
        saved = io.load(self.root, self.source_tab.path.name).document
        saved_item = next(item for item in saved['payload']['items'] if item['id'] == self.ident)
        self.assert_rules(saved_item['composition'])

        path = self.source_tab.path
        self.assertTrue(self.w.close_tab(self.w.index_of(self.source_tab.key)))
        self.source_tab = self.w.open_path(path, True, new_tab=True)
        self.events()
        self.page = self.source_tab.host.prompt_editor
        self.assert_rules(materials.item(self.source_tab.host.state, self.ident)['composition'])
        _, dialog = self.editor(self.source_tab)
        variant = self.show_exclusions(dialog)
        self.assertEqual(dialog.form.excludes.toPlainText(), 'shared_tag\nshared_second')
        for index, expected in enumerate(('variant_a', 'variant_b\nvariant_b_two')):
            variant.choose.setCurrentIndex(index)
            self.events()
            self.assertEqual(variant.excludes_stack.currentWidget().toPlainText(), expected)
        QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
        self.events()
        source_bytes = path.read_bytes()
        source_items = copy.deepcopy(self.source_tab.host.state['items'])

        target = self.target_tab()
        keys = [self.transfer.add(self.capture(), self.transfer.target(target.path), host=target.host)
                for _ in range(2)]
        self.assertTrue(all(keys), self.errors)
        self.assertNotEqual(*keys)
        self.events()
        first, second = (target.host.state['uses'][key] for key in keys)
        self.assertNotEqual(first['id'], second['id'])
        for root in (first, second):
            self.assert_rules(root)
        self.assertIsNot(first['variants']['groups'][0]['excludes'],
                         second['variants']['groups'][0]['excludes'])

        # Edit one detached placement through its normal content transaction.
        # This is an ownership check; Manager/Canvas Undo has separate coverage.
        def edit_one(state):
            sequence = state['uses'][keys[0]]['variants']
            pm.set_variant_excludes(sequence, sequence['groups'][0]['id'],
                                    ['variant_a', 'copy_only'])
        self.assertTrue(target.host.canvas.commit(edit_one, 'Edit copied variant exclusions'))
        self.events()
        self.w.autosave.flush(target)
        self.events()
        self.assertFalse(target.dirty, target.document.error)
        saved_workflow = io.load(self.root, target.path.name).document['payload']['state']
        self.assert_rules(saved_workflow['uses'][keys[0]], ('variant_a', 'copy_only'))
        self.assert_rules(saved_workflow['uses'][keys[1]])
        self.assertEqual(path.read_bytes(), source_bytes)
        self.assertEqual(self.source_tab.host.state['items'], source_items)

        target_path = target.path
        self.assertTrue(self.w.close_tab(self.w.index_of(target.key)))
        target = self.w.open_path(target_path, True, new_tab=True)
        self.events()
        self.assert_rules(target.host.state['uses'][keys[0]], ('variant_a', 'copy_only'))
        self.assert_rules(target.host.state['uses'][keys[1]])
        self.assertEqual(path.read_bytes(), source_bytes)
        self.assertEqual(self.source_tab.host.state['items'], source_items)


if __name__ == '__main__':
    unittest.main()
