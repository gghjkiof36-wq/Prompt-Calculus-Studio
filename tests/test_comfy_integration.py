import copy
import importlib
import json
import os
import struct
import sys
import tempfile
import types
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prompt_calculus_studio.core import initial_state, build_prompt, Storage, reorder_output, output_groups
from prompt_calculus_studio.snapshots import make_snapshot, validate_snapshot, restore_snapshot, image_snapshots
from prompt_calculus_studio.pnginfo import png_metadata

# Load the service without importing ComfyUI or mutating its user directory.
package = types.ModuleType('integration_test'); package.__path__ = [str(ROOT / 'comfyui_prompt_calculus_studio')]
sys.modules['integration_test'] = package
shared = types.ModuleType('integration_test.shared'); shared.__path__ = [str(ROOT / 'prompt_calculus_studio')]
sys.modules['integration_test.shared'] = shared
Service = importlib.import_module('integration_test.service').Service


def png(path, metadata):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    for key, value in metadata.items():
        data += chunk(b'tEXt', key.encode() + b'\0' + json.dumps(value, ensure_ascii=False).encode())
    data += chunk(b'IDAT', zlib.compress(b'\0\xff\0\0')) + chunk(b'IEND', b'')
    path.write_bytes(data)


class IntegrationTests(unittest.TestCase):
    def test_exact_task_results_are_not_truncated_by_recent_history_limit(self):
        service=object.__new__(Service)
        images=[dict(filename=f'image-{i}.png',type='temp',subfolder='') for i in range(137)]
        history={'task':dict(outputs={'9':dict(images=images)})}
        self.assertEqual(len(service.results(history)),120)
        result=service.results(history,limit=None)
        self.assertEqual([row['image'] for row in result],images)
        self.assertTrue(all(row['prompt_id']=='task' and row['node_id']=='9' for row in result))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'qa')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('output', 'temp', 'saved', 'library'): (self.root / name).mkdir()
        self.service = Service(self.root / 'service', self.root / 'output', self.root / 'temp')
        self.state = initial_state()
        for i in (self.state['items'][0], self.state['items'][2]):
            self.state['selections'][i['module']] = [i['id']]
        self.state['temporary'] = ['(soft light:1.2)', '<lora:example:0.5>']
        self.snap = make_snapshot(self.state, 'test-library', '1')
        self.service.configure(workspace=self.state['workspace'], destination=str(self.root / 'saved'))

    def payload(self, text=None):
        value = self.snap['final_prompt'] if text is None else text
        return dict(prompt={'7': {'class_type': 'CLIPTextEncode', 'inputs': {'text': value}}},
            extra_data={'extra_pnginfo': {'workflow': {'nodes': [dict(id=7, type='CLIPTextEncode',
                properties={'prompt_studio': dict(field='text', snapshot=copy.deepcopy(self.snap))})]}}})

    def save_result(self):
        data = self.service.prepare_prompt(self.payload())
        path = self.root / 'output' / 'test.png'
        png(path, dict(prompt=data['prompt'], **data['extra_data']['extra_pnginfo']))
        return path, data

    def collect(self, data):
        return self.service.collect(dict(filename='test.png', type='output'), self.state['workspace'], data['prompt_id'])

    def test_invalid_old_snapshot_is_reported_missing_but_png_still_collects(self):
        path = self.root / 'output' / 'test.png'
        png(path, dict(prompt={'1': {'inputs': {'text': 'old'}}}, prompt_studio='invalid old metadata'))
        result = self.service.collect(dict(filename=path.name), self.state['workspace'], 'old-job')
        self.assertTrue(result['has_generation'])
        self.assertFalse(result['has_snapshot'])
        self.assertEqual(path.read_bytes(), Path(result['path']).read_bytes())

    def test_malformed_workflow_does_not_keep_stale_submission(self):
        for workflow in (None, {'nodes': None}, {'nodes': [{'properties': None}]}):
            data = dict(prompt={}, extra_data={'extra_pnginfo': dict(workflow=workflow, prompt_studio={'submission_id': 'old'})})
            self.service.prepare_prompt(data)
            self.assertNotIn('prompt_studio', data['extra_data']['extra_pnginfo'])

    def test_read_only_library_never_creates_or_updates_database(self):
        path = self.root / 'library' / 'studio.sqlite3'
        with self.assertRaises(ValueError): self.service.read_library(path)
        self.assertFalse(path.exists())
        store = Storage(path.parent); store.save(self.state); store.close()
        before = path.read_bytes()
        result = self.service.read_library(path)
        self.assertEqual(build_prompt(result['state']), build_prompt(self.state))
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn('model_root', result['state']['settings'])

    def test_output_order_roundtrip_is_independent_of_modules(self):
        state = copy.deepcopy(self.state)
        order = output_groups(state)
        reorder_output(state, ('group', order[-1]), ('group', order[0]))
        snap = make_snapshot(state)
        self.assertEqual(snap['final_prompt'], build_prompt(state))
        self.assertEqual(snap['state']['modules'], self.snap['state']['modules'])
        restored = restore_snapshot(self.state, snap)
        self.assertEqual(build_prompt(restored), snap['final_prompt'])

    def test_manual_and_deleted_changed_items_restore_without_overwriting(self):
        self.state['draft'] = 'manual, (weight:1.4)'
        snap = make_snapshot(self.state)
        current = copy.deepcopy(self.state)
        current['items'][0]['prompt'] = 'new text'
        deleted = current['items'].pop(2)['id']
        current['selections'] = {}; current['draft'] = 'current draft'
        original = copy.deepcopy(current)
        restored = restore_snapshot(current, snap)
        self.assertEqual(current, original)
        self.assertEqual(restored['draft'], snap['final_prompt'])
        self.assertEqual(restored['items'][0]['prompt'], 'new text')
        self.assertTrue(any('歷史快照' in i['name'] for i in restored['items']))
        self.assertEqual(build_prompt(restored), snap['generated_prompt'])

    def test_every_submission_gets_own_snapshot_even_when_text_unchanged(self):
        first = self.service.prepare_prompt(self.payload())
        second = self.service.prepare_prompt(self.payload())
        self.assertNotEqual(first['prompt_id'], second['prompt_id'])
        self.assertEqual(first['extra_data']['extra_pnginfo']['prompt_studio']['bindings'],
                         second['extra_data']['extra_pnginfo']['prompt_studio']['bindings'])

    def test_submitted_text_wins_over_stale_widget_snapshot(self):
        data = self.service.prepare_prompt(self.payload('typed after selection'))
        snap = data['extra_data']['extra_pnginfo']['prompt_studio']['bindings'][0]['snapshot']
        self.assertEqual(snap['final_prompt'], 'typed after selection')
        self.assertTrue(snap['manual_draft'])
        validate_snapshot(snap)

    def test_linked_or_invalid_inputs_do_not_claim_snapshot(self):
        data = self.payload(); data['prompt']['7']['inputs']['text'] = ['4', 0]
        envelope = self.service.prepare_prompt(data)['extra_data']['extra_pnginfo']['prompt_studio']
        self.assertEqual(envelope['bindings'], [])
        self.assertTrue(envelope['problems'])
        bad = copy.deepcopy(self.snap); bad['final_prompt'] = 'false'
        with self.assertRaises(ValueError): validate_snapshot(bad)

    def test_copy_preserves_all_bytes_and_old_prompt_then_deduplicates(self):
        original, data = self.save_result()
        self.state['draft'] = 'later edit'
        result = self.collect(data)
        self.assertEqual(original.read_bytes(), Path(result['path']).read_bytes())
        self.assertTrue(result['has_generation']); self.assertTrue(result['has_snapshot'])
        self.assertFalse(result['already'])
        second = self.collect(data)
        self.assertTrue(second['already']); self.assertEqual(result['path'], second['path'])
        metadata = png_metadata(result['path'])
        self.assertEqual(image_snapshots(metadata)[0]['snapshot']['final_prompt'], self.snap['final_prompt'])

    def test_conflict_does_not_overwrite_and_changed_source_is_distinct(self):
        original, data = self.save_result()
        existing = self.root / 'saved' / 'test.png'; existing.write_bytes(b'keep')
        result = self.collect(data)
        self.assertEqual(existing.read_bytes(), b'keep')
        self.assertNotEqual(result['path'], str(existing))
        png(original, {'prompt': {'different': 2}})
        next_result = self.collect(data)
        self.assertNotEqual(result['path'], next_result['path'])
        self.assertFalse(next_result['has_snapshot'])

    def test_rejects_replaced_image_with_different_submission(self):
        path, data = self.save_result()
        other = self.service.prepare_prompt(self.payload())
        png(path, dict(prompt=other['prompt'], **other['extra_data']['extra_pnginfo']))
        with self.assertRaisesRegex(ValueError, '覆寫'): self.collect(data)

    def test_missing_destination_and_path_escape_fail_without_fallback(self):
        _, data = self.save_result()
        (self.root / 'saved').rmdir()
        with self.assertRaises(ValueError): self.collect(data)
        for image in ({'filename': '../private.png'}, {'filename': 'test.png', 'subfolder': '../'},
                      {'filename': 'test.png', 'type': 'input'}):
            with self.assertRaises(ValueError): self.service.source(image)

    def test_copy_failure_removes_partial_and_keeps_source(self):
        path, data = self.save_result()
        before = path.read_bytes()
        with patch('integration_test.service.os.fsync', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.collect(data)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list((self.root / 'saved').iterdir()), [])

    def test_settings_and_duplicate_history_survive_restart(self):
        _, data = self.save_result(); result = self.collect(data)
        self.service = Service(self.root / 'service', self.root / 'output', self.root / 'temp')
        self.assertEqual(self.collect(data)['path'], result['path'])
        self.assertTrue(self.collect(data)['already'])

    def test_multiple_bindings_keep_independent_sources(self):
        data = self.payload()
        node = copy.deepcopy(data['extra_data']['extra_pnginfo']['workflow']['nodes'][0]); node['id'] = 8
        data['extra_data']['extra_pnginfo']['workflow']['nodes'].append(node)
        data['prompt']['8'] = dict(class_type='CLIPTextEncode', inputs={'text': 'other text'})
        result = self.service.prepare_prompt(data)['extra_data']['extra_pnginfo']['prompt_studio']['bindings']
        self.assertEqual(len(result), 2)
        self.assertNotEqual(result[0]['snapshot']['final_prompt'], result[1]['snapshot']['final_prompt'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
