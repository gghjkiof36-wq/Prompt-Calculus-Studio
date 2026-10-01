"""A16 storage/PNG/restart contracts using isolated synthetic documents.

The Qt cases use native receipt fixtures, never a real service or GPU. Reopening
or restoring must preserve intentions without manufacturing completed jobs.
"""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from prompt_studio import drafts, multi_output, stage_context, stage_model, workspace_scene
from prompt_studio.core import Storage, validate_state
from prompt_studio.flow_data import add_scheduler
from prompt_studio.pnginfo import png_metadata
from prompt_studio.snapshots import image_snapshots, make_snapshot, restore_snapshot
from prompt_studio.stage_parameters import effective
from prompt_studio.stage_store import StageStore
from prompt_studio.state_loading import prepare_state
from stage_parameter_fixture import add_samplers, intention
from test_multi_output import workspace


def document():
    state, canvas, output = workspace()
    state = prepare_state(state, multi=True)
    profile = state['generation']['profiles'][0]
    profile.update(frontend_id='native-flow', origin={'path': 'synthetic.json'})
    add_samplers(profile)
    profile['graph']['8']['inputs']['unrecognized_option'] = {'keep': [0, '', False]}
    stages = [stage_model.add(state, workflow=profile['id']) for _ in range(2)]
    clip = state['multi_output']['bindings'][0]['clip']
    multi_output.connect(state, clip, stages[0], 'control')
    multi_output.connect(state, stages[0], stages[1], 'done')
    for key, value in zip(stages, (3, 4)):
        state['multi_output']['stages'][key].update(
            parameters=intention(profile, value=value), retained_annotation={'custom': ''})
    drafts.edit(state, '', output=output)
    workspace_scene.capture(state)
    validate_state(state)
    return state, stages, output


class StageParameterPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def storage(self, name='document'):
        store = Storage(self.folder / name)
        self.addCleanup(store.close)
        return store

    def test_save_reopen_retains_v1_blank_unknown_fields_and_workspace_ownership(self):
        state, stages, output = document()
        first = state['workspace']
        other = workspace_scene.create(state, 'other synthetic workspace', duplicate=True)
        workspace_scene.switch(state, other)
        state['multi_output']['stages'][stages[0]]['parameters']['patches'][0]['value'] = 9
        workspace_scene.switch(state, first)
        store = self.storage()
        store.save(state)
        expected = copy.deepcopy(state)
        store.close()
        # Use a different SQLite connection, not an in-memory repeat load.
        reopened = self.storage()
        loaded = reopened.load_current(multi=True)
        self.assertEqual(loaded, expected)
        self.assertEqual(loaded['draft'], '')
        self.assertEqual(loaded['draft_base'], expected['draft_base'])
        self.assertEqual(loaded['multi_output']['outputs'][output]['draft'], '')
        self.assertEqual(set(loaded['multi_output']['stages']), set(stages))
        self.assertEqual([loaded['multi_output']['stages'][s]['parameters']['patches'][0]['value']
                          for s in stages], [3, 4])
        self.assertEqual(loaded['generation']['profiles'], expected['generation']['profiles'])
        workspace_scene.switch(loaded, other)
        self.assertEqual(loaded['multi_output']['stages'][stages[0]]['parameters']['patches'][0]['value'], 9)
        workspace_scene.switch(loaded, first)
        self.assertEqual(loaded['multi_output']['stages'], expected['multi_output']['stages'])

    def test_legacy_document_and_snapshot_do_not_inherit_live_stage_parameters(self):
        legacy, stages, _ = document()
        for stage in legacy['multi_output']['stages'].values():
            stage.pop('parameters')
        workspace_scene.capture(legacy)
        store = self.storage('legacy')
        store.save(legacy)
        before = store.db.execute('SELECT body FROM document WHERE id=1').fetchone()[0]
        snapshot = make_snapshot(legacy)
        loaded = store.load_current(multi=True)
        current = copy.deepcopy(legacy)
        profile = current['generation']['profiles'][0]
        for key in stages:
            current['multi_output']['stages'][key]['parameters'] = intention(profile, value=7)
        workspace_scene.capture(current)
        current_before = copy.deepcopy(current)
        restored = restore_snapshot(current, json.loads(json.dumps(snapshot)))
        for state in (loaded, restored):
            self.assertTrue(all('parameters' not in state['multi_output']['stages'][key]
                                for key in stages))
            self.assertEqual(state['draft'], '')
        self.assertEqual(current, current_before)
        self.assertEqual(store.db.execute('SELECT body FROM document WHERE id=1').fetchone()[0], before)
        self.assertEqual(list((self.folder / 'legacy').glob('before-import-*.sqlite3')), [])

    def test_unsupported_parameter_version_does_not_overwrite_stored_document(self):
        state, stages, _ = document()
        state['multi_output']['stages'][stages[0]]['parameters']['version'] = 999
        workspace_scene.capture(state)
        store = self.storage('unsupported')
        body = json.dumps(state, ensure_ascii=False)
        with store.db:
            store.db.execute('INSERT OR REPLACE INTO document VALUES (1,?)', (body,))
        with self.assertRaisesRegex(ValueError, 'Stage'):
            store.load_current(multi=True)
        self.assertEqual(store.db.execute('SELECT body FROM document WHERE id=1').fetchone()[0], body)
        self.assertEqual(list((self.folder / 'unsupported').glob('before-import-*.sqlite3')), [])

    def test_waiting_override_reopen_preserves_capture_other_item_and_stage_ownership(self):
        state, stages, _ = document()
        store = self.storage('waiting')
        journal = StageStore(store.db)
        plan = stage_model.compile_plan(state)
        saved = stage_context.capture(state, plan, stages, self.folder)
        captured = copy.deepcopy(saved)
        run = journal.add('run', state['workspace'], status='paused')
        entries = [journal.add('entry', state['workspace'], run['id'], run=run['id'],
                              scheduler='queue', stages=stages, status='waiting', saved=saved)
                   for _ in range(2)]
        profile = state['generation']['profiles'][0]
        override = intention(profile, value=5)
        journal.edit_parameters(entries[1]['id'], stages[0], override, entries[1]['saved'], profile)
        journal.set_scheduler_paused(state['workspace'], 'queue', True)
        expected_rows = journal.rows('entry')
        state['multi_output']['stages'][stages[0]]['parameters'] = intention(profile, value=7)
        # Returned/configuration objects must not alias the SQLite record.
        saved['parameters'][stages[0]]['patches'][0]['value'] = 90
        override['patches'][0]['value'] = 91
        store.save(state)
        store.close()
        reopened = self.storage('waiting')
        loaded = reopened.load_current(multi=True)
        journal = StageStore(reopened.db)
        self.assertEqual(journal.rows('entry'), expected_rows)
        self.assertEqual(journal.read(entries[0]['id'])['saved'], captured)
        second = journal.read(entries[1]['id'])
        self.assertEqual(second['saved']['parameters'], captured['parameters'])
        self.assertEqual(effective(second['saved'], stages[0])['patches'][0]['value'], 5)
        self.assertEqual(effective(second['saved'], stages[1])['patches'][0]['value'], 4)
        self.assertEqual(second['saved']['parameter_profiles'][stages[0]], profile)
        self.assertEqual(loaded['multi_output']['stages'][stages[0]]['parameters']['patches'][0]['value'], 7)
        with self.assertRaisesRegex(ValueError, '不屬於'):
            journal.edit_parameters(second['id'], 'foreign-stage', intention(profile), second['saved'])
        self.assertEqual(journal.read(second['id']), second)
        self.assertEqual(journal.read(run['id'])['status'], 'paused')
        self.assertTrue(journal.scheduler_paused(state['workspace'], 'queue'))
        self.assertEqual(journal.rows('attempt'), [])

    def test_old_waiting_item_without_parameters_stays_unoverridden_when_prepared(self):
        state, stages, _ = document()
        plan = stage_model.compile_plan(state)
        saved = stage_context.capture(state, plan, stages, self.folder)
        saved.pop('parameters')  # Historical queue format predates Stage controls.
        store = self.storage('old-waiting')
        journal = StageStore(store.db)
        item = journal.add('entry', state['workspace'], status='waiting',
                           scheduler='queue', stages=stages, saved=saved)
        store.save(state)
        store.close()
        reopened = self.storage('old-waiting')
        live = reopened.load_current(multi=True)
        live['multi_output']['stages'][stages[0]]['parameters'] = intention(
            live['generation']['profiles'][0], value=7)
        journal = StageStore(reopened.db)
        old = journal.read(item['id'])
        runner = SimpleNamespace(window=SimpleNamespace(state=live, store=reopened))
        prepared, images, foreign = stage_context.prepare_state(
            runner, {}, plan['stages'][stages[0]], {'saved': old['saved']})
        self.assertIsNone(prepared['multi_output']['stages'][stages[0]]['parameters'])
        self.assertIsNone(effective(old['saved'], stages[0]))
        self.assertEqual(prepared['generation']['profiles'][0]['graph']['35']['inputs']['cfg'], 8)
        self.assertEqual(prepared['draft'], '')
        self.assertEqual(journal.read(item['id']), old)
        self.assertEqual(live['multi_output']['stages'][stages[0]]['parameters']['patches'][0]['value'], 7)


class StageParameterRestartTests(unittest.TestCase):
    def setUp(self):
        # Importing the shared Qt fixture initializes the offscreen application.
        import test_084_stages as stage_tests
        stage_tests.StageTests.setUp(self)

    def tearDown(self):
        from stage_fixture import APP
        self.w.close()
        APP.processEvents()

    def stages(self):
        import test_084_stages as stage_tests
        return stage_tests.StageTests.stages(self)

    def click(self):
        from stage_fixture import StageFixture
        StageFixture.click(self)

    def configure(self):
        profile = self.w.state['generation']['profiles'][0]
        add_samplers(profile)
        profile['graph']['8']['inputs']['unrecognized_option'] = {'keep': ['', False, 0]}
        self.executor.graphs['flow'] = copy.deepcopy(profile['graph'])
        self.executor.live['parameters_protocol'] = 1
        self.executor.native.poll(self.executor.live)
        stage = self.stages()[0]
        self.c.commit(lambda state: (
            state['multi_output']['stages'][stage].update(parameters=intention(profile)),
            drafts.edit(state, '', output=self.out)))
        return profile, stage

    def test_full_window_reopen_keeps_paused_waiting_items_without_submitting(self):
        from PySide6.QtTest import QTest
        from stage_fixture import APP
        from prompt_studio.window import Window
        from test_084_stages import Executor
        profile, stage = self.configure()
        queue = add_scheduler(self.w.state)
        self.c.commit(lambda state: multi_output.connect(state, stage, queue + '::flow', 'flow'))
        self.runner.store.set_scheduler_paused(self.w.state['workspace'], queue, True)
        self.click()
        self.click()
        entries = self.runner.store.rows('entry')
        self.assertEqual(len(entries), 2, self.notices)
        self.runner.store.edit_parameters(entries[1]['id'], stage, intention(profile, value=4), entries[1]['saved'])
        self.c.commit(lambda state: state['multi_output']['stages'][stage].update(parameters=intention(profile, value=7)))
        expected = self.runner.store.rows('entry')
        run_ids = {row['id'] for row in self.runner.runs(True)}
        self.assertTrue(run_ids)
        self.assertEqual(self.executor.submissions, [])
        self.w.close()
        APP.processEvents()
        self.w = Window(self.tmp.name)
        self.w.comfy.timer.stop()
        self.w.comfy.enabled = False
        self.executor = Executor(self.w, Path(self.tmp.name) / 'reopened-executor')
        self.runner = self.w.comfy.input_flow.chain
        self.runner.later()
        QTest.qWait(50)
        self.runner.pump()
        self.assertEqual(self.runner.store.rows('entry'), expected)
        runs = self.runner.runs(True)
        self.assertEqual({row['id'] for row in runs}, run_ids)
        self.assertTrue(all(row['status'] == 'paused' for row in runs))
        self.assertTrue(self.runner.store.scheduler_paused(self.w.state['workspace'], queue))
        self.assertEqual(self.w.state['multi_output']['stages'][stage]['parameters']['patches'][0]['value'], 7)
        self.assertEqual([effective(row['saved'], stage)['patches'][0]['value'] for row in expected], [3, 4])
        self.assertEqual(self.executor.submissions, [])
        self.assertEqual(self.runner.store.rows('attempt'), [])
        self.assertEqual(self.w.comfy.generation.records(), [])

    def test_png_restore_preserves_parameters_and_blank_without_copying_jobs(self):
        from PySide6.QtTest import QTest
        from test_comfy_integration import png
        from prompt_studio.window import Window
        from test_084_stages import Executor
        profile, stage = self.configure()
        self.click()
        self.assertEqual(len(self.executor.submissions), 1, self.notices)
        self.assertEqual(len(self.runner.store.rows('attempt')), 1)
        self.assertEqual(len(self.w.comfy.generation.records()), 1)
        # Complete the source editor's ordinary delayed save before comparing
        # it with an independent restore; saving captures its workspace scene.
        self.w.changes.flush()
        self.w.store.save(self.w.state)
        snapshot = make_snapshot(self.w.state, 'synthetic-library')
        source_before = copy.deepcopy(self.w.state)
        source_rows = self.runner.store.rows('run') + self.runner.store.rows('attempt')
        path = Path(self.tmp.name) / 'snapshot.png'
        png(path, {'prompt_studio': {'schema_version': 1, 'bindings': [{'snapshot': snapshot}]}})
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        bindings = image_snapshots(png_metadata(path))
        self.assertEqual(len(bindings), 1)
        target = Window(str(Path(self.tmp.name) / 'png-target'))
        self.addCleanup(target.close)
        target.comfy.timer.stop()
        target.comfy.enabled = False
        target.state['settings']['online'] = False
        target.state = restore_snapshot(target.state, bindings[0]['snapshot'])
        target.store.save(target.state)
        loaded = target.store.load_current(multi=True)
        self.assertNotEqual(loaded['workspace'], source_before['workspace'])
        self.assertEqual(loaded['multi_output']['stages'][stage], source_before['multi_output']['stages'][stage])
        self.assertEqual(loaded['draft'], '')
        self.assertEqual(loaded['draft_base'], source_before['draft_base'])
        self.assertEqual(loaded['generation']['profiles'], source_before['generation']['profiles'])
        self.assertEqual(loaded['workspace_scenes']['items'][loaded['workspace']]['multi_output']['stages'][stage],
                         source_before['multi_output']['stages'][stage])
        target.state = loaded
        executor = Executor(target, Path(self.tmp.name) / 'png-target-executor')
        target.comfy.input_flow.chain.later()
        QTest.qWait(50)
        self.assertEqual(executor.submissions, [])
        for kind in ('run', 'entry', 'attempt', 'parameter_state'):
            self.assertEqual(target.comfy.input_flow.chain.store.rows(kind), [])
        self.assertEqual(target.comfy.generation.records(), [])
        self.assertEqual(self.runner.store.rows('run') + self.runner.store.rows('attempt'), source_rows)
        self.assertEqual(len(self.executor.submissions), 1)
        # The visible source window may finish its ordinary viewport-layout
        # timer; its editing document and runtime records must stay untouched.
        for key in ('uses', 'multi_output', 'generation', 'draft', 'draft_base'):
            self.assertEqual(self.w.state[key], source_before[key])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)


if __name__ == '__main__':
    unittest.main()
