"""Independent editor data review; isolated Qt and synthetic documents only."""
import copy
import unittest
from pathlib import Path

import test_material_editor as fixtures
from prompt_calculus_studio import materials, composition as comp


class MaterialReviewModel(unittest.TestCase):
    def test_content_history_restores_grouping_semantics_and_nested_metadata(self):
        from prompt_calculus_studio import content_history as history
        from prompt_calculus_studio.core import build_prompt, validate_state
        from prompt_calculus_studio.canvas_starter import guide
        value = fixtures.state()
        key = materials.add_to_canvas(value, value['items'][0]['id'], guide(value)['canvas'])
        before = copy.deepcopy(value)
        expected = build_prompt(value)
        value['uses'][key]['grouped'] = True
        value['uses'][key]['children'].append(comp.node('child', 'smile'))
        history.record(value, before, value, 'Group child')
        entry = history.entries(value, target=key)[0]
        value['uses'][key]['future'] = {'current': True}
        unrelated = copy.deepcopy(value['multi_output'])
        history.restore(value, entry['id'], key, previous=True)
        self.assertEqual(build_prompt(value), expected)
        self.assertEqual(value['uses'][key].get('grouped'), before['uses'][key].get('grouped'))
        self.assertEqual(value['uses'][key]['future'], {'current': True})
        self.assertEqual(value['multi_output'], unrelated)
        history.restore(value, entry['id'], key)
        self.assertTrue(value['uses'][key]['grouped'])
        self.assertEqual(value['uses'][key]['children'][0]['prompt'], 'smile')
        validate_state(value)

    def test_workspace_reprepare_keeps_distinct_none_and_empty_drafts(self):
        from prompt_calculus_studio.state_loading import prepare_state
        from prompt_calculus_studio.workspace_scene import create, switch, capture
        value = fixtures.state()
        first = value['workspace']
        other = create(value, 'Second', duplicate=True)
        switch(value, other)
        value['draft'] = ''
        value['multi_output']['outputs'][value['multi_output']['current_output']]['draft'] = ''
        capture(value)
        switch(value, first)
        original = copy.deepcopy(value)
        prepared = prepare_state(value, multi=True)
        self.assertEqual(value, original)
        self.assertEqual(prepare_state(prepared, multi=True), prepared)
        self.assertIsNone(prepared['workspace_scenes']['items'][first]['draft'])
        self.assertEqual(prepared['workspace_scenes']['items'][other]['draft'], '')

    def test_legacy_reverse_order_and_none_or_empty_draft_remain_exact(self):
        from prompt_calculus_studio.core import activate_selection_view, build_prompt
        from prompt_calculus_studio.multi_output import capture_current
        from prompt_calculus_studio.editor_legacy import documents
        for manual in (None, ''):
            with self.subTest(manual=manual):
                value = fixtures.state()
                activate_selection_view(value, 'list')
                assets = []
                for asset in value['items']:
                    if asset['module'] not in [v['module'] for v in assets]: assets.append(asset)
                    if len(assets) == 2: break
                self.assertEqual(len(assets), 2)
                for asset in assets:
                    value['selections'][asset['module']] = [asset['id']]
                    comp.instance(value, asset['id'])
                value['output_order'] = [v['module'] for v in reversed(assets)]
                value['draft'] = manual
                expected = build_prompt(value)
                self.assertLess(expected.index(assets[1]['prompt']), expected.index(assets[0]['prompt']))
                capture_current(value)
                for scope in ('list', 'canvas'):
                    activate_selection_view(value, scope)
                    before = copy.deepcopy(value)
                    restored = documents(value)[0]
                    self.assertEqual(restored['generated'], expected)
                    self.assertEqual(restored['manual'], manual)
                    self.assertEqual(restored['final'], expected if manual is None else '')
                    self.assertEqual(value, before)


class MaterialReviewUI(unittest.TestCase):
    setUpClass = classmethod(fixtures.MaterialEditorTests.setUpClass.__func__)
    setUp = fixtures.MaterialEditorTests.setUp
    tearDown = fixtures.MaterialEditorTests.tearDown

    def test_variant_copies_unsaved_form_without_modifying_original(self):
        source = self.w.state['items'][0]
        source.update(future={'keep': ['original']}, pinned=True, last_used=123)
        original = copy.deepcopy(source)
        self.editor.open_material(source['id'])
        self.editor.name.setText('Edited variant')
        self.editor.prompt.setPlainText('  # Edited\n\nBody,,  ')
        self.editor.kind.setCurrentIndex(self.editor.kind.findData('markdown'))
        self.editor.notes.setPlainText('edited notes')
        self.editor.aliases.setText('alias one、alias two')
        self.editor.excludes.setPlainText('excluded')
        self.editor.edit_category.setCurrentIndex(1)
        category = self.editor.edit_category.currentData()
        self.editor.preview_path = 'thumbnails/variant.png'
        self.editor.save_variant()
        self.assertEqual(self.editor.prompt.toPlainText(), '  # Edited\n\nBody,,  ')
        self.assertEqual(self.editor.name.text(), 'Edited variant')
        self.assertTrue(self.editor.dirty)
        self.assertEqual(materials.item(self.w.state, source['id']), original)
        self.editor.save_material()
        result = materials.item(self.w.state, self.editor.target[1])
        self.assertNotEqual(result['id'], source['id'])
        self.assertEqual(result['prompt'], '  # Edited\n\nBody,,  ')
        self.assertEqual(result['content_kind'], 'markdown')
        self.assertEqual(result['notes'], 'edited notes')
        self.assertEqual(result['aliases'], ['alias one', 'alias two'])
        self.assertEqual(result['excludes'], ['excluded'])
        self.assertEqual(result['module'], category)
        self.assertEqual(result['preview'], 'thumbnails/variant.png')
        self.assertFalse(result['pinned'])
        self.assertNotIn('last_used', result)
        self.assertEqual(result['future'], original['future'])
        self.assertEqual(materials.item(self.w.state, source['id']), original)

    def test_variant_updates_composition_root_and_preserves_children(self):
        source = self.w.state['items'][0]
        source['composition'] = comp.node(source['name'], 'original root')
        source['composition']['children'] = [comp.node('child', 'kept child')]
        original = copy.deepcopy(source)
        self.editor.open_material(source['id'])
        self.editor.prompt.setPlainText('edited root')
        self.editor.name.setText('Composition variant')
        self.editor.save_variant()
        self.assertEqual(self.editor.prompt.toPlainText(), 'edited root')
        self.editor.save_material()
        result = materials.item(self.w.state, self.editor.target[1])
        self.assertEqual(result['composition']['prompt'], 'edited root')
        self.assertEqual(result['composition']['children'], original['composition']['children'])
        self.assertIn('edited root', result['prompt'])
        self.assertIn('kept child', result['prompt'])
        self.assertEqual(materials.item(self.w.state, source['id']), original)

    def test_new_material_first_preview_and_metadata_survive_save_and_copy(self):
        from PySide6.QtGui import QImage, QColor
        path = Path(self.folder.name) / 'source.png'
        image = QImage(24, 24, QImage.Format.Format_RGB32)
        image.fill(QColor('#274766')); self.assertTrue(image.save(str(path)))
        self.editor.new_material()
        self.editor.name.setText('First preview')
        self.editor.prompt.setPlainText('preview text')
        self.editor.notes.setPlainText('new notes')
        self.editor.aliases.setText('new alias')
        self.editor.receive_image(str(path))
        preview = self.editor.preview_path
        self.assertTrue(preview)
        self.editor.save_material(True)
        result = materials.item(self.w.state, self.editor.target[1])
        self.assertEqual(result['preview'], preview)
        self.assertEqual(result['notes'], 'new notes')
        self.assertEqual(result['aliases'], ['new alias'])
        copied = next(v for v in self.w.state['uses'].values() if v.get('source_id') == result['id'])
        self.assertEqual(copied['preview'], preview)
        self.w.store.save(self.w.state)
        loaded = self.w.store.load()
        self.assertEqual(materials.item(loaded, result['id'])['preview'], preview)


if __name__ == '__main__': unittest.main()
