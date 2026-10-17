import copy
import json

from local_document_fixtures import DocumentsCase, ROOT
from prompt_calculus_studio import core, composition, materials, prompt_materials, prompt_sources
from prompt_calculus_studio import multi_output, workspace_scene, state_loading
from prompt_calculus_studio.local_documents import category, workflow, schema
from prompt_calculus_studio.local_documents.policy import local_retention


class ContentTests(DocumentsCase):
    def test_category_retains_every_owned_field_and_nested_detached_content(self):
        doc = schema.decode(schema.encode(self.category()))
        owned = [i for i in self.state['items'] if i['module'] == self.ids['category_a']]
        self.assertEqual(doc['payload']['items'], owned)
        rendered = [composition.render(i['composition']) for i in owned]
        materials.item(self.state, self.ids['external'])['prompt'] = 'changed elsewhere'
        self.assertEqual([composition.render(i['composition']) for i in doc['payload']['items']], rendered)
        text = doc['payload']['items'][0]
        self.assertTrue(text['pinned'])
        self.assertEqual(text['notes'], 'P0 saved content history')
        self.assertEqual(len(text['composition']['variants']['groups']), 2)

    def test_workflow_whole_scene_with_inactive_output_and_empty_drafts(self):
        doc = self.workflow()
        result = workflow.to_state(schema.decode(schema.encode(doc)))
        for field in workspace_scene.FIELDS:
            if field in self.state:
                self.assertEqual(result[field], self.state[field], field)
        self.assertEqual(result['draft'], '')
        self.assertEqual(result['multi_output']['outputs']['output-b']['list_draft']['draft'], '')
        self.assertEqual(result['output_order'], ['use-b', 'use-a'])
        self.assertEqual(self.state, self.before)

    def test_only_real_catalog_dependencies_not_provenance_or_unused_profiles(self):
        unrelated = copy.deepcopy(self.state['generation']['profiles'][0])
        unrelated.update(id='unused-profile', name='UNUSED_PRIVATE_PROFILE')
        self.state['generation']['profiles'].append(unrelated)
        doc = self.workflow()
        data = schema.encode(doc)
        self.assertNotIn(b'SYNTHETIC_UNUSED', data)
        self.assertNotIn(b'SYNTHETIC_WORKFLOW_B', data)
        self.assertNotIn(b'UNUSED_PRIVATE_PROFILE', data)
        result = doc['payload']['state']
        self.assertEqual([i['id'] for i in result['items']], [self.ids['external']])
        self.assertEqual(result['items'][0], materials.item(self.state, self.ids['external']))
        self.assertEqual(doc['payload']['catalog_role'], 'pinned_dependency')
        self.assertEqual(result['generation']['profiles'][0], self.state['generation']['profiles'][0])

    def test_other_workspace_is_independent_despite_same_scene_ids(self):
        a = workflow.to_state(self.workflow())
        b = workflow.to_state(workflow.from_state(self.state, self.ids['workspace_b']))
        self.assertEqual(set(a['uses']), set(b['uses']))
        self.assertEqual(a['draft'], '')
        self.assertEqual(b['draft'], 'SYNTHETIC_WORKFLOW_B_DRAFT')
        b['uses']['use-b']['prompt'] = 'edit B only'
        self.assertNotEqual(a['uses']['use-b']['prompt'], b['uses']['use-b']['prompt'])
        self.assertEqual(self.state, self.before)

    def test_history_stays_in_local_source_and_not_misrepresented_as_portable(self):
        self.state['workspaces'][0]['history'] = [dict(parameters={'seed': '8'}, time=1.0, note='local history')]
        before = copy.deepcopy(self.state)
        retained = local_retention(self.state)
        doc = self.workflow()
        self.assertEqual(self.state, before)
        self.assertEqual(retained['fields']['content_history'], self.state['content_history'])
        self.assertEqual(retained['workspace_history'][self.ids['workspace_a']], self.state['workspaces'][0]['history'])
        self.assertNotIn('content_history', doc['payload']['state'])
        self.assertNotIn('history', doc['payload']['state']['workspaces'][0])

    def test_content_settings_preserved_private_settings_excluded_unknown_refused(self):
        self.state['settings'].update(formatter='original', artist_prefix=False, comfy_count=3,
                                     model_root='SYNTHETIC_PRIVATE_ROOT', comfy_url='http://127.0.0.1:8188')
        doc = self.workflow()
        self.assertEqual(doc['payload']['state']['settings']['comfy_count'], 3)
        self.assertNotIn(b'SYNTHETIC_PRIVATE_ROOT', schema.encode(doc))
        self.assertNotIn('comfy_url', doc['payload']['state']['settings'])
        self.state['settings']['unknown_content_policy'] = 'must not drop'
        with self.assertRaises(schema.DocumentError) as error:
            self.workflow()
        self.assertEqual(error.exception.code, 'unsupported')

    def test_unclassified_top_level_field_is_reported(self):
        self.state['future_owned_content'] = {'id': 'keep'}
        with self.assertRaises(schema.DocumentError):
            self.workflow()

    def test_starter_guide_is_local_while_the_graph_remains_portable(self):
        guide = dict(workspace=self.state['workspace'], dismissed=True)
        self.state['settings']['canvas_starter'] = guide
        document = self.workflow()
        self.assertNotIn('canvas_starter', document['payload']['state']['settings'])
        self.assertEqual(local_retention(self.state)['settings']['canvas_starter'], guide)
        self.assertEqual(document['payload']['state']['multi_output'], self.state['multi_output'])

    def test_absent_legacy_setting_keeps_its_old_fallback(self):
        self.state['settings'].pop('separate_selections')
        original = core.separate_selections(self.state)
        result = workflow.to_state(self.workflow())
        self.assertNotIn('separate_selections', result['settings'])
        self.assertEqual(core.separate_selections(result), original)

    def test_no_compile_reserve_dispatch_or_database_access(self):
        from unittest.mock import patch
        with patch.object(core, 'Storage', side_effect=AssertionError('database')), \
                patch.object(core, 'build_prompt', side_effect=AssertionError('compile')), \
                patch.object(prompt_sources, 'reserve', side_effect=AssertionError('consume')):
            doc = self.workflow()
            result = workflow.to_state(schema.decode(schema.encode(doc)))
        self.assertEqual(prompt_materials.position(prompt_sources.source(result, self.ids['reader'])['root'])['repeat'], 1)

    def test_missing_profile_or_material_dependency_is_rejected(self):
        doc = self.workflow()
        for field in ('items', 'profiles'):
            broken = copy.deepcopy(doc)
            if field == 'items':
                broken['payload']['state']['items'] = []
            else:
                broken['payload']['state']['generation']['profiles'] = []
            with self.subTest(field=field), self.assertRaises(schema.DocumentError):
                schema.encode(broken)

    def test_invalid_inactive_list_draft_and_cross_kind_node_collision(self):
        doc = self.workflow()
        doc['payload']['state']['multi_output']['outputs']['output-b']['list_draft']['draft'] = 4
        with self.assertRaises(schema.DocumentError):
            schema.encode(doc)
        doc = self.workflow()
        doc['payload']['state']['multi_output']['clip_inputs']['use-a'] = {'name': 'collision'}
        with self.assertRaises(schema.DocumentError):
            schema.encode(doc)

    def test_saved_legacy_material_cursor_roundtrip_and_explicit_load_migration(self):
        source = json.loads((ROOT / 'tests/fixtures/legacy_material_cursors.json').read_text('utf-8'))
        for name, root in source['roots'].items():
            state = copy.deepcopy(self.before)
            original = materials.item(state, self.ids['flow'])
            original['composition'] = root
            original.pop('material_type', None)
            document = category.from_state(state, self.ids['category_a'])
            loaded = category.to_state(schema.decode(schema.encode(document)))
            saved = materials.item(loaded, self.ids['flow'])['composition']
            self.assertEqual(saved, root, name)
            prepared = state_loading.prepare_state(loaded)
            migrated = materials.item(prepared, self.ids['flow'])['composition']
            expected = prompt_materials.position(root)
            if root['sequence']['mode'] == 'random' and expected['repeat'] > 0:
                # Existing load contract: this legacy random group was already drawn.
                expected['ready'] = True
            self.assertEqual(prompt_materials.position(migrated), expected, name)

    def test_fixed_stage_fixture_retains_own_validation_boundary(self):
        from prompt_calculus_studio.snapshots import validate_snapshot
        source = json.loads((ROOT / 'tests/fixtures/legacy_stage_snapshot_088.json').read_text('utf-8'))
        before = copy.deepcopy(source)
        checked = validate_snapshot(source)
        # Only its validated stored scene is exported; no missing journal is invented.
        doc = workflow.from_state(checked['state'])
        roundtrip = workflow.to_state(schema.decode(schema.encode(doc)))
        self.assertEqual(roundtrip['multi_output'], checked['state']['multi_output'])
        self.assertEqual(source, before)
