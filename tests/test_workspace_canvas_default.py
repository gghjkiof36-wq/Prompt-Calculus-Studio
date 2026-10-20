"""New local Canvas files contain the clean six-module entry, without seeding old files."""
import copy
from pathlib import Path
import tempfile
import unittest

from prompt_calculus_studio.canvas_starter import guide
from prompt_calculus_studio.local_documents import io, workflow
from prompt_calculus_studio.multi_output import PREVIEW, compile_output, disconnect
from prompt_calculus_studio.workspace_files import create_file


class LocalCanvasDefaultTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-canvas-entry-')
        self.root = Path(self.area.name)

    def tearDown(self):
        self.area.cleanup()

    def test_create_and_reopen_six_empty_connected_modules_without_test_content(self):
        path = create_file(self.root, 'workflow', '我的畫布')
        document = io.load(self.root, path.name).document
        state = workflow.to_state(document)
        refs = guide(state)
        self.assertIsNotNone(refs)
        data = state['multi_output']
        for collection in ('canvases', 'outputs', 'clip_inputs', 'schedulers', 'stages'):
            self.assertEqual(len(data[collection]), 1, collection)
        self.assertTrue(state['canvas_functions']['preview'])
        self.assertEqual(
            {(edge['source'], edge['destination'], edge['kind']) for edge in data['connections']},
            {(refs['canvas'], refs['output'], 'text'),
             (refs['output'], refs['clip'], 'clip'),
             (refs['clip'], refs['scheduler']+'::bindings', 'control'),
             (refs['scheduler']+'::bindings', refs['stage'], 'control'),
             (refs['stage'], PREVIEW, 'image')})
        self.assertFalse(state['items'])
        self.assertFalse(state['modules'])
        self.assertFalse(state['uses'])
        self.assertFalse(data['bindings'])
        self.assertFalse(state.get('generation', {}).get('profiles', []))
        self.assertIsNone(data['stages'][refs['stage']]['workflow'])
        self.assertEqual(compile_output(state, refs['output'])['final_prompt'], '')
        self.assertEqual(io.load(self.root, path.name).document, document)

    def test_deliberately_cleared_layout_and_empty_manual_draft_survive_save_reopen(self):
        path = create_file(self.root, 'workflow', '保留修改')
        loaded = io.load(self.root, path.name)
        state = workflow.to_state(loaded.document)
        refs = guide(state)
        data = state['multi_output']
        for edge in list(data['connections']):
            disconnect(state, edge['id'])
        data['stages'].clear()
        data['schedulers'].clear()
        state['canvas_functions']['preview'] = False
        refs['dismissed'] = True
        state['draft'] = ''
        state['draft_base'] = '保留手動空稿'
        updated = copy.deepcopy(loaded.document)
        updated['payload'] = workflow.from_state(state)['payload']
        saved = io.save(self.root, path.name, updated, loaded.receipt)
        self.assertEqual(saved.status, 'saved')
        restored = workflow.to_state(io.load(self.root, path.name).document)
        self.assertFalse(restored['multi_output']['stages'])
        self.assertFalse(restored['multi_output']['schedulers'])
        self.assertFalse(restored['multi_output']['connections'])
        self.assertFalse(restored['canvas_functions']['preview'])
        self.assertTrue(guide(restored)['dismissed'])
        self.assertEqual(restored['draft'], '')
        self.assertEqual(restored['draft_base'], '保留手動空稿')

    def test_creating_another_canvas_leaves_existing_document_bytes_unchanged(self):
        first = create_file(self.root, 'workflow', '既有文件')
        original = first.read_bytes()
        second = create_file(self.root, 'workflow', '新的文件')
        self.assertEqual(first.read_bytes(), original)
        old = workflow.to_state(io.load(self.root, first.name).document)
        new = workflow.to_state(io.load(self.root, second.name).document)
        self.assertNotEqual(old['workspace'], new['workspace'])
        self.assertNotEqual(guide(old)['canvas'], guide(new)['canvas'])
