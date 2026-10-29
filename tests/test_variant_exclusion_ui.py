"""Scoped variant exclusions: native documents, draft ownership and placement."""
import copy
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QScreen
from PySide6.QtTest import QTest

import test_prompt_group_ui as prior
from prompt_calculus_studio import composition as comp, materials, prompt_materials as pm
from prompt_calculus_studio.window import Window


class VariantExclusionUITests(unittest.TestCase):
    setUpClass = classmethod(prior.GroupUITests.setUpClass.__func__)

    def setUp(self):
        self.screen = patch.object(QScreen, 'availableGeometry', return_value=QRect(0, 0, 1920, 1080))
        self.screen.start()
        prior.GroupUITests.setUp(self)

    def tearDown(self):
        try:
            prior.GroupUITests.tearDown(self)
        finally:
            self.screen.stop()

    def settle(self):
        QTest.qWait(250)

    def click(self, widget):
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        self.settle()

    def exclusions(self, dialog):
        pages = dialog.pages
        if not dialog.panels.opened:
            self.click(pages.details_toggle)
        pages.details.setCurrentWidget(pages.excludes_page)
        self.settle()

    def variants(self):
        dialog = self.page.new_material()
        dialog.name.setText('變體排除測試')
        dialog.prompt.setPlainText('smile, daylight')
        dialog.form.excludes.setPlainText('global tag')
        self.click(dialog.pages.add_variants)
        editor = dialog.pages.variant_editor
        editor.stack.currentWidget().setPlainText('laughing, daylight')
        ids = [group['id'] for group in editor.sequence['groups']]
        self.exclusions(dialog)
        return dialog, editor, ids

    def select(self, editor, ident):
        editor.choose.setCurrentIndex(editor.choose.findData(ident))
        self.app.processEvents()
        field = editor.exclusion_editors[ident]
        self.assertIs(editor.excludes_stack.currentWidget(), field)
        return field

    def type_text(self, field, text):
        field.setFocus()
        QTest.keyClicks(field, text)
        self.app.processEvents()

    def shortcut(self, field, key):
        field.setFocus()
        QTest.keyClick(field, key, Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()

    def save(self, dialog):
        ident = dialog.form.base['id']
        self.click(dialog.save_button)
        self.assertIsNone(self.page.active_dialog,
                          self.page.active_dialog.error.text() if self.page.active_dialog else '')
        return ident

    def test_plain_text_keeps_legacy_global_values_when_variants_are_created(self):
        ident = materials.save(self.w.state, dict(name='舊版排除', prompt='smile', module=self.cat,
                                                  excludes=['legacy first', 'legacy second']))
        dialog = self.page.edit_material(ident)
        self.exclusions(dialog)
        self.assertIsNone(dialog.pages.variant_editor)
        self.assertTrue(dialog.pages.variant_excludes.isHidden())
        self.assertEqual(dialog.form.excludes.toPlainText(), 'legacy first\nlegacy second')
        self.click(dialog.pages.add_variants)
        self.exclusions(dialog)
        editor = dialog.pages.variant_editor
        self.assertTrue(dialog.pages.variant_excludes.isVisible())
        for group in editor.sequence['groups']:
            self.assertEqual(self.select(editor, group['id']).toPlainText(), '')
            self.assertEqual(group.get('excludes', []), [])
        self.assertEqual(dialog.form.excludes.toPlainText(), 'legacy first\nlegacy second')
        self.save(dialog)
        saved = materials.item(self.w.state, ident)
        self.assertEqual(saved['composition']['excludes'], ['legacy first', 'legacy second'])
        self.assertTrue(all(not group.get('excludes') for group in saved['composition']['variants']['groups']))

    def test_native_undo_redo_is_independent_across_global_and_variant_documents(self):
        dialog, editor, ids = self.variants()
        global_field = dialog.form.excludes
        first = self.select(editor, ids[0]); first_doc = first.document()
        self.type_text(first, 'first local')
        second = self.select(editor, ids[1]); second_doc = second.document()
        self.type_text(second, 'second local')
        self.assertIs(self.select(editor, ids[0]), first)
        self.assertIs(first.document(), first_doc)
        self.shortcut(first, Qt.Key.Key_Z)
        self.assertEqual(first.toPlainText(), '')
        self.assertEqual(second.toPlainText(), 'second local')
        self.assertEqual(global_field.toPlainText(), 'global tag')
        self.shortcut(first, Qt.Key.Key_Y)
        self.assertEqual(first.toPlainText(), 'first local')
        self.assertIs(self.select(editor, ids[1]).document(), second_doc)
        self.shortcut(second, Qt.Key.Key_Z)
        self.assertEqual(second.toPlainText(), '')
        self.shortcut(second, Qt.Key.Key_Y)
        self.assertEqual(second.toPlainText(), 'second local')
        self.type_text(global_field, 'prefix ')
        self.shortcut(global_field, Qt.Key.Key_Z)
        self.assertEqual(global_field.toPlainText(), 'global tag')
        self.assertEqual(first.toPlainText(), 'first local')
        self.assertEqual(second.toPlainText(), 'second local')
        self.assertEqual({group['id']: group.get('excludes', []) for group in editor.sequence['groups']},
                         {ids[0]: ['first local'], ids[1]: ['second local']})

    def test_reorder_tracks_ids_and_preserves_document_undo_and_accessible_context(self):
        dialog, editor, ids = self.variants()
        fields = {}
        for index, ident in enumerate(ids):
            fields[ident] = self.select(editor, ident)
            self.type_text(fields[ident], f'local {index}')
        editor.name.setText('笑容'); editor.edit_metadata()
        self.assertIn('笑容', fields[ids[1]].accessibleDescription())
        self.assertEqual(fields[ids[1]].accessibleName(), '當前變體排除 Tag')
        self.assertEqual(dialog.form.excludes.accessibleName(), '全部變體排除 Tag')
        editor.move(-1)
        self.assertEqual([group['id'] for group in editor.sequence['groups']], list(reversed(ids)))
        self.assertEqual(editor.choose.currentData(), ids[1])
        self.assertIs(editor.excludes_stack.currentWidget(), fields[ids[1]])
        for index, ident in enumerate(ids):
            field = self.select(editor, ident)
            self.assertIs(field, fields[ident])
            self.assertEqual(field.toPlainText(), f'local {index}')
        self.shortcut(fields[ids[1]], Qt.Key.Key_Z)
        self.assertEqual(fields[ids[1]].toPlainText(), '')
        self.shortcut(fields[ids[1]], Qt.Key.Key_Y)
        self.assertEqual(fields[ids[1]].toPlainText(), 'local 1')
        ident = self.save(dialog)
        dialog = self.page.edit_material(ident)
        reopened = dialog.pages.variant_editor
        self.assertEqual([group['id'] for group in reopened.sequence['groups']], list(reversed(ids)))
        for index, group_id in enumerate(ids):
            self.assertEqual(self.select(reopened, group_id).toPlainText(), f'local {index}')

    def test_save_window_close_reopen_and_workspace_undo_redo_preserve_both_scopes(self):
        dialog, editor, ids = self.variants()
        for index, ident in enumerate(ids):
            self.type_text(self.select(editor, ident), f'local {index}')
        ident = self.save(dialog)
        saved = copy.deepcopy(materials.item(self.w.state, ident))
        self.w.canvas.undo()
        self.assertFalse(any(item['id'] == ident for item in self.w.state['items']))
        self.w.canvas.redo()
        self.assertEqual(materials.item(self.w.state, ident), saved)
        self.w.persist()
        self.assertEqual(materials.item(self.w.store.load(), ident), saved)
        self.w.completion.timer.stop()
        with patch('prompt_calculus_studio.prompt_editor.ask', return_value=True):
            self.w.close()
        self.w.deleteLater(); self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.w = Window(self.folder.name)
        self.w.resize(1440, 900); self.w.show(); self.w.leave_canvas()
        self.page = self.w.prompt_editor
        self.page.set_browser_category(self.cat); self.app.processEvents()
        self.assertEqual(materials.item(self.w.state, ident), saved)
        dialog = self.page.edit_material(ident)
        self.assertEqual(dialog.form.excludes.toPlainText(), 'global tag')
        for index, group_id in enumerate(ids):
            self.assertEqual(self.select(dialog.pages.variant_editor, group_id).toPlainText(), f'local {index}')

    def test_discard_does_not_publish_either_scope_or_restore_cancelled_draft(self):
        dialog, editor, ids = self.variants()
        self.type_text(self.select(editor, ids[0]), 'saved local')
        ident = self.save(dialog)
        saved = copy.deepcopy(materials.item(self.w.state, ident))
        self.w.persist(); stored = copy.deepcopy(materials.item(self.w.store.load(), ident))
        dialog = self.page.edit_material(ident); self.exclusions(dialog)
        dialog.form.excludes.setPlainText('cancelled global')
        self.select(dialog.pages.variant_editor, ids[0]).setPlainText('cancelled first')
        self.select(dialog.pages.variant_editor, ids[1]).setPlainText('cancelled second')
        self.click(dialog.cancel_button)
        self.assertEqual(materials.item(self.w.state, ident), saved)
        self.assertEqual(materials.item(self.w.store.load(), ident), stored)
        dialog = self.page.edit_material(ident)
        self.assertEqual(dialog.form.excludes.toPlainText(), 'global tag')
        self.assertEqual(self.select(dialog.pages.variant_editor, ids[0]).toPlainText(), 'saved local')
        self.assertEqual(self.select(dialog.pages.variant_editor, ids[1]).toPlainText(), '')

    def test_type_switches_hide_local_fields_without_losing_drafts_or_native_undo(self):
        dialog, editor, ids = self.variants()
        local = self.select(editor, ids[0]); document = local.document()
        self.type_text(local, 'kept local')
        for material_type in ('combination', 'flow', 'text'):
            dialog.pages.type_picker.setCurrentIndex(dialog.pages.type_picker.findData(material_type))
            self.settle()
            if material_type != 'text':
                self.assertTrue(dialog.pages.variant_excludes.isHidden())
                self.assertEqual(dialog.form.excludes.accessibleName(), '排除 Tag')
            else:
                self.exclusions(dialog)
                self.assertTrue(dialog.pages.variant_excludes.isVisible())
            self.assertIs(editor.exclusion_editors[ids[0]], local)
            self.assertIs(local.document(), document)
            self.assertEqual(local.toPlainText(), 'kept local')
        self.shortcut(local, Qt.Key.Key_Z)
        self.assertEqual(local.toPlainText(), '')
        self.shortcut(local, Qt.Key.Key_Y)
        self.assertEqual(local.toPlainText(), 'kept local')
        self.assertEqual(dialog.form.excludes.toPlainText(), 'global tag')

    def test_legacy_variant_records_keep_all_exclusions_global(self):
        root = comp.node('舊變體', prompt='first', excludes=['old global'])
        sequence = pm.variants(root)
        from prompt_calculus_studio.prompt_groups import new_group
        sequence['groups'].append(new_group('變體 2', 'second'))
        root['variants'] = sequence
        ident = materials.save(self.w.state, dict(name=root['name'], prompt='', composition=root, module=self.cat))
        dialog = self.page.edit_material(ident); self.exclusions(dialog)
        self.assertEqual(dialog.form.excludes.toPlainText(), 'old global')
        for group in sequence['groups']:
            self.assertEqual(self.select(dialog.pages.variant_editor, group['id']).toPlainText(), '')
        self.save(dialog)
        saved = materials.item(self.w.state, ident)['composition']
        self.assertEqual(saved['excludes'], ['old global'])
        self.assertTrue(all(not group.get('excludes') for group in saved['variants']['groups']))

    def test_both_fields_fit_typical_windows_and_long_values_scroll_inside_fields(self):
        for width, height in ((1700, 980), (980, 740)):
            with self.subTest(window=(width, height)):
                self.w.resize(width, height); self.app.processEvents()
                dialog, editor, ids = self.variants()
                local = self.select(editor, ids[0])
                self.settle()
                for field in (dialog.form.excludes, local):
                    self.assertTrue(field.isVisible())
                    bounds = QRect(field.mapTo(dialog, QPoint()), field.size())
                    self.assertTrue(dialog.rect().contains(bounds), (width, height, bounds, dialog.rect()))
                    self.assertTrue(field.visibleRegion().boundingRect().contains(field.rect()),
                                    (width, height, field.visibleRegion().boundingRect(), field.rect()))
                self.assertGreater(local.mapTo(dialog, QPoint()).y(),
                                   dialog.form.excludes.mapTo(dialog, QPoint()).y() + dialog.form.excludes.height())
                self.assertTrue(dialog.save_button.isVisible())
                self.assertEqual(dialog.form_scroll.verticalScrollBar().maximum(), 0)
                for field in (dialog.form.excludes, local):
                    field.setPlainText('\n'.join(f'excluded tag {i}' for i in range(100)))
                self.app.processEvents()
                for field in (dialog.form.excludes, local):
                    self.assertGreater(field.verticalScrollBar().maximum(), 0)
                self.assertEqual(dialog.form_scroll.verticalScrollBar().maximum(), 0)
                self.click(dialog.cancel_button)


if __name__ == '__main__':
    unittest.main()
