import copy
import os

from local_document_fixtures import DocumentsCase
from prompt_calculus_studio import composition, prompt_materials, prompt_sources, core
from prompt_calculus_studio import flow_data, multi_output, stage_model, preview_nodes
from prompt_calculus_studio.local_documents import identity, workflow, category, io, schema


class IdentityTests(DocumentsCase):
    def test_category_owned_ids_change_provenance_and_content_survive(self):
        original = self.category()
        before = copy.deepcopy(original)
        result = identity.duplicate(original)
        payload = result.document['payload']
        self.assertNotEqual(result.document['document_id'], original['document_id'])
        self.assertNotEqual(payload['category']['id'], self.ids['category_a'])
        for old, new in zip(original['payload']['items'], payload['items']):
            self.assertNotEqual(old['id'], new['id'])
            self.assertEqual(new['module'], payload['category']['id'])
            self.assertEqual(composition.render(old['composition']), composition.render(new['composition']))
            for previous, following in zip(composition.walk(old['composition']), composition.walk(new['composition'])):
                self.assertNotEqual(previous['id'], following['id'])
                for field in ('source_id', 'source_module', 'source_revision'):
                    self.assertEqual(previous.get(field), following.get(field))
        self.assertEqual(original, before)

    def test_workspace_duplicate_remaps_connections_bindings_and_order(self):
        original = self.workflow()
        result = identity.duplicate(original)
        old = original['payload']['state']
        new = workflow.to_state(result.document)
        mapping = result.id_maps['scene']
        self.assertNotEqual(old['workspace'], new['workspace'])
        self.assertEqual(set(new['uses']), {mapping[k] for k in old['uses']})
        self.assertEqual(new['output_order'], [mapping[k] for k in old['output_order']])
        for prior, after in zip(old['multi_output']['bindings'], new['multi_output']['bindings']):
            self.assertEqual((after['workflow'], after['node'], after['field']),
                             (prior['workflow'], prior['node'], prior['field']))
            self.assertEqual(after['clip'], mapping[prior['clip']])
        self.assertEqual(new['generation']['profiles'], old['generation']['profiles'])
        self.assertEqual(new['items'], old['items'])  # pinned dependencies, not new originals
        self.assertEqual(new['draft'], '')
        self.assertEqual(new['multi_output']['outputs'][mapping['output-b']]['list_draft']['draft'], '')
        core.validate_state(new)

    def test_same_spelling_external_graph_target_is_never_remapped(self):
        profile = self.state['generation']['profiles'][0]
        profile['graph']['use-a'] = profile['graph'].pop('6')
        profile['graph']['3']['inputs']['positive'][0] = 'use-a'
        profile['prompt'][0] = 'use-a'
        self.state['multi_output']['bindings'][0]['node'] = 'use-a'
        document = self.workflow()
        result = identity.duplicate(document)
        state = result.document['payload']['state']
        self.assertNotIn('use-a', state['uses'])
        self.assertIn('use-a', state['generation']['profiles'][0]['graph'])
        self.assertEqual(state['multi_output']['bindings'][0]['node'], 'use-a')
        self.assertEqual(state['generation']['profiles'], document['payload']['state']['generation']['profiles'])

    def test_valid_nested_reader_cursors_resigned_without_consuming_repeat(self):
        document = self.workflow()
        result = identity.duplicate(document)
        original = prompt_sources.source(document['payload']['state'], self.ids['reader'])['root']
        new = prompt_sources.source(result.document['payload']['state'], result.id_maps['scene'][self.ids['reader']])['root']
        for old, after in zip(composition.walk(original), composition.walk(new)):
            if old.get('selection_cursor'):
                self.assertEqual(after['selection_cursor']['signature'], prompt_materials.signature(after))
                a, b = prompt_materials.position(old), prompt_materials.position(after)
                self.assertEqual(a['repeat'], b['repeat'])
                self.assertEqual(a['draws'], b['draws'])
                self.assertNotEqual(a['group'], b['group'])

    def test_stale_cursor_is_not_made_valid_by_duplicate(self):
        document = self.workflow()
        reader = prompt_sources.source(document['payload']['state'], self.ids['reader'])['root']
        reader['selection_cursor']['signature'] = 'stale'
        result = identity.duplicate(document)
        new = prompt_sources.source(result.document['payload']['state'], result.id_maps['scene'][self.ids['reader']])['root']
        self.assertEqual(new['selection_cursor']['signature'], 'stale')

    def test_unavailable_provenance_is_explicit_and_embedded_copy_stays_readable(self):
        doc = self.category()
        sources = identity.provenance(doc)
        external = [s for s in sources if s['source_id'] == self.ids['external']]
        self.assertTrue(external)
        self.assertTrue(all(s['embedded'] and s['resolution'] == 'unresolved-provenance' for s in external))
        self.assertTrue(any(s['resolution'] == 'owned-record' for s in sources))

    def test_legacy_supply_cursor_duplicate_keeps_saved_repeat_and_valid_signature(self):
        import json
        from local_document_fixtures import ROOT
        from prompt_calculus_studio.prompt_sources import legacy_signature
        fixture = json.loads((ROOT / 'tests/fixtures/legacy_material_cursors.json').read_text('utf-8'))
        for name, root in fixture['roots'].items():
            document = self.category()
            document['payload']['items'][0]['composition'] = copy.deepcopy(root)
            result = identity.duplicate(document)
            new = result.document['payload']['items'][0]['composition']
            self.assertEqual(new['supply_cursor']['signature'], legacy_signature(new), name)
            self.assertEqual(new['supply_cursor']['position']['repeat'], root['supply_cursor']['position']['repeat'], name)
            self.assertEqual(new['supply_cursor']['position']['draws'], root['supply_cursor']['position']['draws'], name)

    def test_legacy_chain_ids_and_pcs_sources_change_external_target_fields_do_not(self):
        from prompt_calculus_studio import chain_model
        plan = chain_model.new_definition()
        first = chain_model.stage('profile-p0', 'first')
        second = chain_model.stage('profile-p0', 'second')
        first['texts'] = [dict(mode='pcs', source='output-b', node='6', field='text')]
        second['upstream'] = dict(stage=first['id'], node='external-image', mode='all')
        second['texts'] = [dict(mode='upstream', source=dict(stage=first['id'], node='7', field='text'), node='6', field='text')]
        plan['stages'] = [first, second]
        self.state['multi_output']['workflow_order']['chain'] = plan
        result = identity.duplicate(self.workflow())
        copied = result.document['payload']['state']['multi_output']['workflow_order']['chain']
        self.assertNotEqual(copied['id'], plan['id'])
        self.assertNotEqual(copied['stages'][0]['id'], first['id'])
        self.assertEqual(copied['stages'][1]['upstream']['stage'], copied['stages'][0]['id'])
        self.assertEqual(copied['stages'][1]['upstream']['node'], 'external-image')
        self.assertEqual(copied['stages'][0]['texts'][0]['source'], result.id_maps['scene']['output-b'])
        self.assertEqual(copied['stages'][0]['texts'][0]['node'], '6')

    def test_scheduler_ports_stage_targets_previews_and_unconnected_nodes(self):
        scheduler = flow_data.add_scheduler(self.state)
        stage = stage_model.add(self.state, workflow='profile-p0')
        preview = preview_nodes.add(self.state)
        clip = next(iter(self.state['multi_output']['clip_inputs']))
        multi_output.connect(self.state, clip, stage, 'control')
        self.state['multi_output']['stages'][stage]['output'] = '6'  # external target
        multi_output.connect(self.state, 'output-b', scheduler + '::clip1', 'clip')
        self.state['multi_output']['schedulers'][scheduler]['policies'] = {clip: 'saved'}
        document = self.workflow()
        result = identity.duplicate(document)
        new = result.document['payload']['state']
        mapping = result.id_maps['scene']
        self.assertIn(mapping[preview], new['canvas_functions']['previews'])
        self.assertEqual(new['multi_output']['stages'][mapping[stage]]['output'], '6')
        self.assertEqual(new['multi_output']['schedulers'][mapping[scheduler]]['policies'], {mapping[clip]: 'saved'})
        self.assertIn(mapping[scheduler + '::clip1'], {c['destination'] for c in new['multi_output']['connections']})
        core.validate_state(new)

    def test_rename_preserves_identity_and_duplicate_physical_path_is_blocked(self):
        result = self.saved()
        original = self.destination / '分類.pcs-category'
        renamed = self.destination / '改名.pcs-category'
        original.rename(renamed)
        loaded = io.load(self.destination, renamed.name)
        self.assertEqual(result.document, loaded.document)
        registry = identity.Registry()
        self.assertEqual(registry.register(loaded.receipt), 'registered')
        copied = self.destination / '外部複製.pcs-category'
        copied.write_bytes(renamed.read_bytes())
        self.assertEqual(registry.register(io.load(self.destination, copied.name).receipt), 'duplicate-id')
        linked = self.destination / '同檔別名.pcs-category'
        os.link(renamed, linked)
        self.assertEqual(registry.register(io.load(self.destination, linked.name).receipt), 'same-file')
        self.assertEqual(len(registry.by_id), 1)
