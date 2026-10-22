"""Synthetic, Qt-free Stage source snapshots and preserved reader semantics."""
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import weakref
import zlib

from prompt_calculus_studio import image_source, pnginfo, stage_context
from prompt_calculus_studio.composition import node
from prompt_calculus_studio.core import initial_state
from prompt_calculus_studio.flow_data import add_scheduler, endpoint
from prompt_calculus_studio.snapshots import make_snapshot
from prompt_calculus_studio.state_loading import prepare_state


def chunk(kind, data, *, corrupt=False):
    crc = zlib.crc32(kind + data) ^ (1 if corrupt else 0)
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', crc & 0xffffffff)


def png_bytes(metadata=None, *, compressed=False, corrupt=()):
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    for key, value in (metadata or {}).items():
        raw = (value if key == 'parameters' else json.dumps(value, ensure_ascii=False)).encode('utf-8')
        kind = b'zTXt' if compressed else b'tEXt'
        body = key.encode() + b'\0' + (b'\0' + zlib.compress(raw) if compressed else raw)
        data += chunk(kind, body, corrupt=key in corrupt)
    return data + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0')) + chunk(b'IEND', b'')


def graph(text='first', negative='negative'):
    return {'sampler': {'inputs': {'positive': ['p', 0], 'negative': ['n', 0]}},
            'p': {'inputs': {'text': text}}, 'n': {'inputs': {'text': negative}},
            'u': {'inputs': {'caption': 'unclassified'}}}


def document(readers):
    state = initial_state()
    state.update(version=3, selection_view='canvas', uses={'a': node('A', 'user original')})
    state = prepare_state(state, multi=True)
    first = next(iter(state['multi_output']['canvases']))
    state['multi_output']['canvases'][first]['members'] = ['a']
    state.setdefault('canvas_functions', {})['images'] = copy.deepcopy(readers)
    return state


def wire(state, source, destination):
    state['multi_output']['connections'].append(
        dict(id=source + ':' + destination, source=source, destination=destination, kind='image'))


@contextmanager
def trace_reads(paths, *, short_read=None):
    """Count returned bytes from actual synthetic files, never estimated sizes."""
    targets = {Path(path).resolve() for path in paths}
    original = Path.open
    records = []

    class Tracked:
        def __init__(self, stream, record):
            self.stream = stream
            self.record = record

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()
            self.record['closed'] = self.stream.closed

        def read(self, size=-1):
            self.record['requested'].append(size)
            block = self.stream.read(min(size, short_read) if short_read is not None else size)
            self.record['returned'].append(len(block))
            return block

    def opened(path, mode='r', *args, **kwargs):
        stream = original(path, mode, *args, **kwargs)
        if mode == 'rb' and Path(path).resolve() in targets:
            record = dict(path=Path(path).resolve(), requested=[], returned=[], closed=False)
            records.append(record)
            return Tracked(stream, record)
        return stream

    with patch.object(Path, 'open', opened):
        yield records


class StageImageSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def source(self, filename='image.png', *, metadata=None, data=None, **png_options):
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        data = png_bytes({'prompt': graph()} if metadata is None else metadata, **png_options) if data is None else data
        path.write_bytes(data)
        return dict(relative=filename, name=path.name, sha256=hashlib.sha256(data).hexdigest(), width=1, height=1)

    def images(self, state):
        return state['canvas_functions']['images']

    def test_same_source_aliases_have_one_real_read_hash_and_parse(self):
        source = self.source()
        (self.root / 'sub').mkdir()
        alias = dict(source, relative='sub/../image.png', name='other name', reference={'node': '9', 'index': 2})
        state = document({'__source_a': {}, '__source_b': {}})
        overrides = {'__source_a': source, '__source_b': alias}
        before = copy.deepcopy((state, overrides))
        path = self.root / source['relative']
        with trace_reads([path]) as reads, patch.object(stage_context, 'sha256', wraps=hashlib.sha256) as hashed, \
                patch.object(stage_context, 'png_metadata_stream', wraps=pnginfo.png_metadata_stream) as parsed:
            projected = stage_context.project(state, self.root, source_values=overrides)
        self.assertEqual((len(reads), hashed.call_count, parsed.call_count), (1, 1, 1))
        self.assertEqual(sum(reads[0]['returned']), len(path.read_bytes()))
        self.assertTrue(reads[0]['closed'])
        images = self.images(projected)
        self.assertEqual([images[k]['content']['text'] for k in overrides], ['first', 'first'])
        self.assertEqual(images['__source_b']['source'], alias)
        images['__source_b']['source']['reference']['index'] = 9
        images['__source_a']['content']['text'] = 'edited'
        self.assertEqual(images['__source_b']['content']['text'], 'first')
        self.assertEqual((state, overrides), before)

    def test_choices_saved_selection_and_missing_choice_are_per_reader(self):
        source = self.source(metadata={'prompt': graph('  positive,\n原文 ', '')})
        readers = {
            '__source_positive': {'prompt_choice': ['positive', 'p', 'text']},
            '__source_negative': {'prompt_choice': ['negative', 'n', 'text']},
            '__source_unknown': {'prompt_choice': ['unknown', 'u', 'caption']},
            '__source_legacy': {'prompt_choice': ['p', 'text']},
            '__source_missing': {'prompt_choice': ['positive', 'gone', 'text']},
            '__source_saved': {'prompt_choice': ['negative', 'n', 'text'], 'output_node': 'visible'},
        }
        state = document(readers)
        saved = {'__source_saved': {'prompt_choice': ['positive', 'p', 'text']}}
        before = copy.deepcopy((state, saved))
        with patch.object(stage_context, 'png_metadata_stream', wraps=pnginfo.png_metadata_stream) as parsed:
            projected = stage_context.project(state, self.root, source_values={k: source for k in readers}, readers=saved)
        self.assertEqual(parsed.call_count, 1)
        images = self.images(projected)
        for key in ('__source_positive', '__source_legacy', '__source_saved'):
            self.assertEqual(images[key]['content']['text'], '  positive,\n原文 ')
        self.assertEqual(images['__source_negative']['content'], dict(kind='raw', text=''))
        self.assertEqual(images['__source_unknown']['content']['text'], 'unclassified')
        self.assertIn('缺少指定', images['__source_missing']['content_error'])
        self.assertNotIn('content', images['__source_missing'])
        self.assertNotIn('output_node', images['__source_saved'])
        self.assertEqual((state, saved), before)

    def test_content_from_metadata_preserves_modules_manual_blank_and_independence(self):
        original = document({})
        out = original['multi_output']['current_output']
        original['multi_output']['outputs'][out]['draft'] = ''
        original['draft'] = ''
        marker = dict(schema_version=1, texts=[dict(node='p', field='text', output=out)],
                      bindings=[dict(node_id='p', field='text', snapshot=make_snapshot(original))])
        source = self.source(metadata={'prompt': graph(''), 'prompt_studio': marker})
        path = self.root / source['relative']
        metadata = pnginfo.png_metadata(path)
        before = copy.deepcopy(metadata)
        expected = image_source.read_content(path)
        self.assertEqual(image_source.content_from_metadata(metadata), expected)
        self.assertEqual(expected['kind'], 'modules')
        self.assertEqual(expected['manual_text'], '')
        state = document({'__source_a': {}, '__source_b': {}})
        projected = stage_context.project(state, self.root, source_values={k: source for k in self.images(state)})
        a, b = (self.images(projected)[k]['content'] for k in self.images(state))
        self.assertEqual(a, expected)
        self.assertEqual(b, expected)
        a['roots'][0]['prompt'] = 'changed copy'
        self.assertEqual(b['roots'][0]['prompt'], 'user original')
        self.assertEqual(metadata, before)
        self.assertEqual(state['uses']['a']['prompt'], 'user original')

    def test_ambiguous_positive_and_legacy_negative_do_not_fall_back(self):
        values = graph()
        values.update(second={'inputs': {'positive': ['q', 0]}}, q={'inputs': {'text': 'second'}})
        source = self.source(metadata={'prompt': values})
        state = document({'__source_automatic': {}, '__source_selected': {'prompt_choice': ['q', 'text']},
                          '__source_old_negative': {'prompt_choice': ['n', 'text']}})
        projected = stage_context.project(state, self.root, source_values={k: source for k in self.images(state)})
        images = self.images(projected)
        self.assertIn('多個正面', images['__source_automatic']['content_error'])
        self.assertIn('缺少指定的正面', images['__source_old_negative']['content_error'])
        self.assertEqual(images['__source_selected']['content'], dict(kind='raw', text='second'))

    def test_project_uses_hashed_bytes_if_path_changes_before_parse(self):
        source = self.source(metadata={'prompt': graph('first')})
        path = self.root / source['relative']
        replacement = png_bytes({'prompt': graph('other')})
        state = document({'__source_a': {}, '__source_b': {}})
        parser = pnginfo.png_metadata_stream

        def replace_then_parse(stream):
            path.write_bytes(replacement)
            return parser(stream)

        with trace_reads([path]) as reads, patch.object(stage_context, 'png_metadata_stream', replace_then_parse):
            projected = stage_context.project(state, self.root, source_values={k: source for k in self.images(state)})
        self.assertEqual(len(reads), 1)
        self.assertEqual([v['content']['text'] for v in self.images(projected).values()], ['first', 'first'])
        again = stage_context.project(state, self.root, source_values={'__source_a': source})
        self.assertIn('已變更', self.images(again)['__source_a']['content_error'])

    def test_next_project_rechecks_same_size_mtime_and_new_expected_digest(self):
        source = self.source(metadata={'prompt': graph('first')})
        path = self.root / source['relative']
        state = document({'__source_a': {}})
        first = stage_context.project(state, self.root, source_values={'__source_a': source})
        before = path.stat()
        replacement = png_bytes({'prompt': graph('other')})
        self.assertEqual(len(replacement), before.st_size)
        path.write_bytes(replacement); os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with trace_reads([path]) as reads:
            stale = stage_context.project(state, self.root, source_values={'__source_a': source})
            updated = dict(source, sha256=hashlib.sha256(replacement).hexdigest())
            fresh = stage_context.project(state, self.root, source_values={'__source_a': updated})
        self.assertEqual(len(reads), 2)
        self.assertEqual(self.images(first)['__source_a']['content']['text'], 'first')
        self.assertIn('已變更', self.images(stale)['__source_a']['content_error'])
        self.assertEqual(self.images(fresh)['__source_a']['content']['text'], 'other')

    def test_expected_digest_is_part_of_group_and_error_names_remain_independent(self):
        source = self.source()
        state = document({'__source_good': {}, '__source_bad_a': {}, '__source_bad_b': {}})
        bad_a = dict(source, name='alias A', sha256='0' * 64)
        bad_b = dict(bad_a, name='alias B')
        with trace_reads([self.root / source['relative']]) as reads, \
                patch.object(stage_context, 'png_metadata_stream', wraps=pnginfo.png_metadata_stream) as parsed:
            projected = stage_context.project(state, self.root, source_values={
                '__source_good': source, '__source_bad_a': bad_a, '__source_bad_b': bad_b})
        self.assertEqual((len(reads), parsed.call_count), (2, 1))
        self.assertEqual(self.images(projected)['__source_good']['content']['text'], 'first')
        self.assertEqual(self.images(projected)['__source_bad_a']['content_error'], '構圖圖片已變更：alias A')
        self.assertEqual(self.images(projected)['__source_bad_b']['content_error'], '構圖圖片已變更：alias B')

    def test_one_group_lifetime_for_interleaved_sources(self):
        a = self.source('a.png', metadata={'prompt': graph('A')})
        b = self.source('b.png', metadata={'prompt': graph('B')})
        state = document({'__source_a1': {}, '__source_b': {}, '__source_a2': {}})
        receipt_refs = []; metadata_refs = []
        receipt_type = stage_context.CapturedImage

        class Metadata(dict):
            pass

        def captured(*args):
            self.assertTrue(all(ref() is None for ref in receipt_refs + metadata_refs))
            value = receipt_type(*args); receipt_refs.append(weakref.ref(value))
            return value

        def parsed(stream):
            value = Metadata(pnginfo.png_metadata_stream(stream)); metadata_refs.append(weakref.ref(value))
            return value

        with trace_reads([self.root / 'a.png', self.root / 'b.png']) as reads, \
                patch.object(stage_context, 'CapturedImage', captured), \
                patch.object(stage_context, 'png_metadata_stream', parsed):
            result = stage_context.project(state, self.root, source_values={
                '__source_a1': a, '__source_b': b, '__source_a2': a})
        self.assertEqual([r['path'].name for r in reads], ['a.png', 'b.png'])
        self.assertEqual([v['content']['text'] for v in self.images(result).values()], ['A', 'B', 'A'])
        self.assertTrue(all(ref() is None for ref in receipt_refs + metadata_refs))

    def test_real_25_mib_limit_and_short_reads_are_bounded(self):
        self.assertEqual(stage_context.MAX_SOURCE_BYTES, 25 * 1024 * 1024)
        path = self.root / 'large.png'
        with path.open('wb') as stream:
            stream.seek(stage_context.MAX_SOURCE_BYTES + 1); stream.write(b'x')
        source = dict(relative='large.png', name='large', sha256='0' * 64, width=1, height=1)
        state = document({'__source_a': {}})
        with trace_reads([path]) as reads, patch.object(stage_context, 'png_metadata_stream') as parsed:
            result = stage_context.project(state, self.root, source_values={'__source_a': source})
        self.assertEqual(sum(reads[0]['returned']), stage_context.MAX_SOURCE_BYTES + 1)
        self.assertTrue(all(0 < n <= 1024 * 1024 for n in reads[0]['requested']))
        self.assertTrue(reads[0]['closed']); parsed.assert_not_called()
        self.assertIn('已變更', self.images(result)['__source_a']['content_error'])
        small = self.source('short.png')
        with trace_reads([self.root / 'short.png'], short_read=7) as reads:
            result = stage_context.project(state, self.root, source_values={'__source_a': small})
        self.assertEqual(sum(reads[0]['returned']), len((self.root / 'short.png').read_bytes()))
        self.assertGreater(len(reads[0]['returned']), 2)
        self.assertEqual(self.images(result)['__source_a']['content']['text'], 'first')
        # Exact-boundary acceptance uses the same production branch with a small
        # limit; the preceding sparse-file control used the actual 25 MiB limit.
        size = len((self.root / 'short.png').read_bytes())
        with patch.object(stage_context, 'MAX_SOURCE_BYTES', size), trace_reads([self.root / 'short.png']) as reads:
            result = stage_context.project(state, self.root, source_values={'__source_a': small})
        self.assertEqual(sum(reads[0]['returned']), size)
        self.assertEqual(self.images(result)['__source_a']['content']['text'], 'first')

    def test_root_boundary_missing_file_and_other_root_do_not_reuse_content(self):
        source = self.source()
        state = document({'__source_a': {}})
        child = self.root / 'child'; child.mkdir()
        traversal = dict(source, relative='../image.png', name='outside')
        with trace_reads([self.root / 'image.png']) as reads:
            outside = stage_context.project(state, child, source_values={'__source_a': traversal})
        self.assertEqual(reads, [])
        self.assertIn('遺失：outside', self.images(outside)['__source_a']['content_error'])
        missing = stage_context.project(state, child, source_values={'__source_a': source})
        self.assertIn('遺失', self.images(missing)['__source_a']['content_error'])
        (child / 'image.png').write_bytes(png_bytes({'prompt': graph('other')}))
        good = stage_context.project(state, self.root, source_values={'__source_a': source})
        wrong_root = stage_context.project(state, child, source_values={'__source_a': source})
        self.assertEqual(self.images(good)['__source_a']['content']['text'], 'first')
        self.assertIn('已變更', self.images(wrong_root)['__source_a']['content_error'])

    def test_reversed_reader_chain_keeps_stage_node_index_and_override_timing(self):
        a = self.source('a.png', metadata={'prompt': graph('A')})
        b = self.source('b.png', metadata={'prompt': graph('B')})
        c = self.source('c.png', metadata={'prompt': graph('C')})
        values = [dict(a, reference={'node': '8'}), dict(b, reference={'node': '9'}), dict(c, reference={'node': '9'})]
        state = document({'__source_child': {'input_index': 0},
                          '__source_parent': {'output_node': '9', 'input_index': 1,
                                              'stage_reference': 'stage', 'items': [b, a], 'index': 0}})
        state['multi_output']['stages']['stage'] = dict(name='Stage')
        wire(state, 'stage', '__source_parent'); wire(state, '__source_parent', '__source_child')
        results = {'stage': {'images': values}}
        before = copy.deepcopy((state, results))
        result = stage_context.project(state, self.root, results)
        self.assertEqual([v['content']['text'] for v in self.images(result).values()], ['C', 'C'])
        result = stage_context.project(state, self.root, results, {'__source_parent': a})
        self.assertEqual(self.images(result)['__source_parent']['content']['text'], 'A')
        self.assertEqual(self.images(result)['__source_child']['content']['text'], 'C')
        self.assertEqual(self.images(result)['__source_parent']['index'], 1)
        self.assertNotIn('stage_reference', self.images(result)['__source_parent'])
        self.assertFalse(any(c['destination'] == '__source_parent' and c['kind'] == 'image'
                             for c in result['multi_output']['connections']))
        self.assertTrue(any(c['destination'] == '__source_child' for c in result['multi_output']['connections']))
        self.assertEqual((state, results), before)

    def test_absent_stage_node_and_invalid_index_never_read_another_source(self):
        source = dict(self.source(), reference={'node': '9'})
        for settings in ({'output_node': 'missing'}, {'input_index': 1}, {'input_index': True}):
            with self.subTest(settings=settings):
                state = document({'__source_a': settings})
                state['multi_output']['stages']['stage'] = dict(name='Stage')
                wire(state, 'stage', '__source_a')
                with trace_reads([self.root / source['relative']]) as reads:
                    result = stage_context.project(state, self.root, {'stage': {'images': [source]}})
                self.assertEqual(reads, [])
                self.assertIsNone(self.images(result)['__source_a']['source'])
                self.assertIn('content_error', self.images(result)['__source_a'])
                self.assertNotIn('content', self.images(result)['__source_a'])

    def test_frozen_execution_input_wins_over_current_wire(self):
        old = self.source('old.png', metadata={'prompt': graph('saved')})
        live = self.source('live.png', metadata={'prompt': graph('live')})
        state = document({'__source_reader': {}, '__source_live': {'source': live, 'items': [live]}})
        port = endpoint(add_scheduler(state), 'image1')
        wire(state, '__source_live', port); wire(state, port, '__source_reader')
        state['_execution_inputs'] = {port: dict(type='image', value=old, origin={'frozen': True})}
        before = copy.deepcopy(state)
        result = stage_context.project(state, self.root)
        self.assertEqual(self.images(result)['__source_reader']['source'], old)
        self.assertEqual(self.images(result)['__source_reader']['content']['text'], 'saved')
        self.assertEqual(state, before)

    def test_cycle_missing_stage_and_unwired_reader_keep_existing_behavior(self):
        source = self.source()
        state = document({'__source_a': {'source': source, 'content': dict(kind='raw', text='kept')}})
        with trace_reads([self.root / source['relative']]) as reads:
            untouched = stage_context.project(state, self.root)
        self.assertEqual(reads, [])
        self.assertEqual(self.images(untouched), self.images(state))
        state['multi_output']['stages']['stage'] = dict(name='Stage')
        wire(state, 'stage', '__source_a')
        missing = stage_context.project(state, self.root)
        self.assertIn('尚未產生', self.images(missing)['__source_a']['content_error'])
        self.assertIsNone(self.images(missing)['__source_a']['source'])
        self.assertNotIn('content', self.images(missing)['__source_a'])
        cyclic = document({'__source_a': {}, '__source_b': {}})
        wire(cyclic, '__source_a', '__source_b'); wire(cyclic, '__source_b', '__source_a')
        with self.assertRaisesRegex(ValueError, '循環'):
            stage_context.project(cyclic, self.root)

    def test_crc_compression_non_png_and_invalid_snapshot_keep_parser_contract(self):
        cases = [
            ({'prompt': graph('compressed')}, {'compressed': True}, 'compressed'),
            ({'prompt': graph('corrupt'), 'parameters': 'fallback\nSteps: 20'}, {'corrupt': ('prompt',)}, 'fallback'),
            ({'prompt': graph('raw'), 'prompt_studio': {'schema_version': 1, 'bindings': [
                {'snapshot': {'schema_version': 7, 'state': {}}}]}}, {}, 'raw'),
        ]
        state = document({'__source_a': {}})
        for index, (metadata, options, expected) in enumerate(cases):
            with self.subTest(index=index):
                source = self.source(str(index) + '.png', metadata=metadata, **options)
                result = stage_context.project(state, self.root, source_values={'__source_a': source})
                self.assertEqual(self.images(result)['__source_a']['content'], dict(kind='raw', text=expected))
        for filename, data in [('not.png', b'not a PNG'), ('compressed-limit.png',
                               png_bytes({'parameters': 'x' * (2 * 1024 * 1024 + 1)}, compressed=True))]:
            with self.subTest(filename=filename):
                source = self.source(filename, data=data)
                with self.assertRaises(ValueError) as expected:
                    image_source.read_content(self.root / filename)
                result = stage_context.project(state, self.root, source_values={'__source_a': source})
                self.assertEqual(self.images(result)['__source_a']['content_error'], str(expected.exception))


if __name__ == '__main__':
    unittest.main()
