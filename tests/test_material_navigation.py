"""Pure-model folder compatibility, identity and output isolation."""
import copy
import tempfile
import unittest
from prompt_calculus_studio import material_navigation as nav
from prompt_calculus_studio.core import initial_state, validate_state, Storage, build_prompt


class MaterialNavigationTests(unittest.TestCase):
    def test_legacy_view_is_read_only_and_does_not_group_examples(self):
        state = initial_state(); before = copy.deepcopy(state)
        self.assertEqual(nav.view(state)['folders'], [])
        self.assertEqual(nav.view(state)['order'], [v['id'] for v in state['modules']])
        self.assertEqual(state, before)

    def test_moves_rename_remove_and_storage_keep_original_records(self):
        state = initial_state(); before = copy.deepcopy(state); prompt = build_prompt(state)
        fid = nav.add_folder(state, '人物'); other = nav.add_folder(state, '其他')
        a, b = [v['id'] for v in state['modules'][2:4]]
        nav.move(state, a, fid); nav.move(state, b, fid, a)
        self.assertEqual(nav.folder(state, fid)['categories'], [b, a])
        nav.move(state, a, other); self.assertEqual(nav.parent(state, a), other)
        self.assertEqual(nav.folder(state, fid)['categories'], [b])
        nav.move(state, a); nav.rename_folder(state, fid, '人物設定')
        state['settings']['material_folder_collapsed'] = [fid]
        validate_state(state)
        with tempfile.TemporaryDirectory() as root:
            store = Storage(root); store.save(state); loaded = store.load(); store.db.close()
        self.assertEqual(loaded, state)
        nav.remove_folder(state, fid)
        self.assertIsNone(nav.parent(state, b)); self.assertIn(b, nav.view(state)['order'])
        self.assertEqual({k: state[k] for k in before if k != 'settings'}, {k: v for k, v in before.items() if k != 'settings'})
        self.assertEqual(build_prompt(state), prompt)

    def test_invalid_names_destinations_duplicates_and_future_schema_are_rejected(self):
        state = initial_state(); fid = nav.add_folder(state, '人物')
        for name in ('', '  ', ' 人物 '):
            before = copy.deepcopy(state)
            with self.assertRaises(ValueError): nav.add_folder(state, name)
            self.assertEqual(state, before)
        with self.assertRaises(ValueError): nav.move(state, state['modules'][0]['id'], '@folder:missing')
        nav.move(state, state['modules'][0]['id'], fid)
        bad = copy.deepcopy(state); bad[nav.FIELD]['order'].append(state['modules'][0]['id'])
        with self.assertRaises(ValueError): validate_state(bad)
        bad = copy.deepcopy(state); bad[nav.FIELD]['version'] = 2
        with self.assertRaises(ValueError): validate_state(bad)

    def test_deleted_and_imported_categories_are_resolved_without_a_refresh_write(self):
        state = initial_state(); fid = nav.add_folder(state, '人物')
        cat = state['modules'][-1]['id']; nav.move(state, cat, fid)
        state['modules'] = [v for v in state['modules'] if v['id'] != cat]
        state['items'] = [v for v in state['items'] if v['module'] != cat]
        state['modules'].append(dict(id='new-import', name='匯入分類', mode='multiple'))
        before = copy.deepcopy(state); validate_state(state)
        self.assertEqual(nav.folder(state, fid)['categories'], [])
        self.assertIn('new-import', nav.view(state)['order']); self.assertEqual(state, before)

    def test_snapshot_restoration_and_workspace_projection_leave_library_navigation_shared(self):
        from prompt_calculus_studio.snapshots import make_snapshot, restore_snapshot
        from prompt_calculus_studio.workspace_scene import scene
        state = initial_state(); historical = make_snapshot(state)
        fid = nav.add_folder(state, '人物'); nav.move(state, state['modules'][2]['id'], fid)
        before = copy.deepcopy(state[nav.FIELD]); state['draft'] = ''
        restored = restore_snapshot(state, historical)
        self.assertEqual(restored[nav.FIELD], before); self.assertEqual(build_prompt(restored), historical['final_prompt'])
        self.assertNotIn(nav.FIELD, scene(state)); self.assertEqual(state['draft'], '')


if __name__ == '__main__': unittest.main()
