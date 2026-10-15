"""True pre-7 nested modules retain only definition-matched saved progress."""
import copy
import json
import random
import tempfile
import unittest
from pathlib import Path
from prompt_calculus_studio import core,composition as comp,prompt_materials as pm,prompt_sources as supply
from prompt_calculus_studio.state_loading import prepare_state

FIXTURE=json.loads((Path(__file__).parent/'fixtures/legacy_material_cursors.json').read_text(encoding='utf-8'))


def old_root(mode='increment'):
    root=copy.deepcopy(FIXTURE['roots'][mode])
    assert all('material_type' not in part for part in comp.walk(root))
    return root


class CursorUpgradeTests(unittest.TestCase):
    def assert_progress(self,before,after):
        previous=list(comp.walk(before));current=list(comp.walk(after))
        self.assertEqual([p['id'] for p in current],[p['id'] for p in previous])
        self.assertEqual(comp.render(after),comp.render(before))
        for first,last in zip(previous,current):
            if not first.get('supply_cursor'):continue
            expected=copy.deepcopy(first['supply_cursor']['position'])
            if first['sequence']['mode']=='random' and expected['repeat']>0:expected['ready']=True
            self.assertEqual(pm.position(last),expected)
            self.assertEqual(last['selection_cursor']['signature'],pm.signature(last))

    def test_version_five_and_six_storage_reopen_keep_nested_repeat_progress(self):
        for version in (5,6):
            with self.subTest(version=version),tempfile.TemporaryDirectory() as directory:
                state=core.initial_state();key=supply.add(state,root=old_root());state['version']=version
                core.validate_state(state);store=core.Storage(directory);store.save(state)
                before=store.load();self.assertEqual(before['version'],version)
                migrated=store.load_current(multi=True)
                self.assert_progress(supply.source(before,key)['root'],supply.source(migrated,key)['root'])
                self.assertEqual(migrated['version'],7);store.close()
                store=core.Storage(directory)
                try:
                    self.assertEqual(store.load_current(multi=True),migrated)
                    self.assertEqual(prepare_state(migrated,multi=True),migrated)
                finally:store.close()

    def test_legacy_random_partial_group_finishes_without_drawing_again(self):
        root=old_root('random');state=dict(version=6,uses={'one':root})
        before=copy.deepcopy(root);pm.migrate(state);self.assert_progress(before,root)
        class NoNewDraw:
            def choice(self,*_):raise AssertionError('The saved random group must finish first')
        used,following=pm.resolve(root,rng=NoNewDraw())
        self.assertEqual(used['sequence']['selected'],before['supply_cursor']['position']['group'])
        self.assertEqual(pm.position(following)['repeat'],2)
        self.assertEqual(pm.position(following)['draws'],before['supply_cursor']['position']['draws'])

    def test_definition_mismatch_is_not_revalidated_as_saved_progress(self):
        root=old_root();root['sequence']['groups'][0]['name']='edited after cursor'
        state=dict(version=6,uses={'one':root});pm.migrate(state)
        self.assertNotIn('selection_cursor',root)
        self.assertEqual(pm.position(root)['repeat'],0)

    def test_valid_newer_cursor_wins_over_legacy_cursor(self):
        root=old_root();position=copy.deepcopy(root['supply_cursor']['position']);position['repeat']=2
        root['selection_cursor']=dict(signature=pm.signature(root),position=position)
        state=dict(version=7,uses={'one':root});pm.migrate(state)
        self.assertEqual(pm.position(root),position)

    def test_all_saved_placements_and_output_instances_are_migrated_independently(self):
        owners=[dict(uses={'use':old_root()},instances={'instance':old_root('decrement')},
                     canvas_functions=dict(text_sources={'reader':dict(root=old_root('random'))}),
                     multi_output=dict(outputs={'output':dict(list_state={'instances':{'saved':old_root()}})}))
                for _ in range(3)]
        state=dict(version=6,**owners[0],workspaces=[owners[1]],workspace_scenes=dict(items={'other':owners[2]}))
        before=copy.deepcopy(owners);pm.migrate(state)
        for first,last in zip(before,owners):
            # The active owner's dictionaries are shared with state by construction.
            for key in ('uses','instances'):
                for ident,root in first[key].items():self.assert_progress(root,last[key][ident])
            self.assert_progress(first['canvas_functions']['text_sources']['reader']['root'],last['canvas_functions']['text_sources']['reader']['root'])
            self.assert_progress(first['multi_output']['outputs']['output']['list_state']['instances']['saved'],last['multi_output']['outputs']['output']['list_state']['instances']['saved'])
        unchanged=copy.deepcopy(state);pm.migrate(state);self.assertEqual(state,unchanged)

    def test_two_reader_branches_keep_their_own_consumed_positions(self):
        state=core.initial_state();first=supply.add(state,root=old_root());second=supply.add(state,root=old_root())
        second_root=supply.source(state,second)['root'];second_root['supply_cursor']['position']['repeat']=2
        state['version']=6;migrated=prepare_state(state,multi=True)
        other=copy.deepcopy(supply.source(migrated,second)['root'])
        plan=supply.build(migrated,[first],count=1);supply.reserve(migrated,plan)
        self.assertEqual(supply.source(migrated,second)['root'],other)
        self.assertEqual(pm.position(supply.source(migrated,first)['root'])['repeat'],2)
        self.assertEqual(pm.position(other)['repeat'],2)


if __name__=='__main__':unittest.main()
