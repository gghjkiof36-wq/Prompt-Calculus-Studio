"""Capture ingress, scope round trips and writer-operation persistence."""
import copy
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import uuid

from prompt_calculus_studio import connector_capture as capture
from prompt_calculus_studio import conversation_review as review, markdown_portable as portable
from prompt_calculus_studio.conversation_archive import canonical, conversation, Limits, load_archive
from prompt_calculus_studio.conversation_import import Destination, RECEIPT, import_selected


def sample():
    return dict(version=1, provider='chatgpt', conversation_id='chat-1', title='京都行程',
                url='https://chatgpt.com/c/chat-1', method='dom', scope='loaded',
                adapter_version='1', captured_at='2026-10-23T12:00:00Z', messages=[
                    dict(id='dom-1', role='user', content='第一天\n中文😀'),
                    dict(id='dom-2', role='assistant', content='安排清水寺。'),
                    dict(id='dom-3', role='user', content='UNSELECTED_BODY')])


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'workspace'; self.root.mkdir()
        self.out = self.base / 'export'; self.out.mkdir()
        self.path = self.base / 'capture.json'

    def archive(self, value=None):
        self.path.write_bytes(canonical(sample() if value is None else value))
        return load_archive(self.path)

    def imported(self, value=None, **kwargs):
        result = import_selected(self.archive(value), (0,), Destination.choose(self.root), Event(), **kwargs)
        self.assertFalse(result.failed)
        self.assertEqual(len(result.completed), 1)
        return result.completed[0]

    def test_observation_whitelist_order_and_timestamp_independence(self):
        value = sample(); value['conversation_id'] = None
        value['url'] += '?token=do-not-save#private'
        before = copy.deepcopy(value)
        raw = capture.validate_capture(value)
        item = conversation(raw, Limits())
        self.assertEqual(item.path(item.current), ('dom-1', 'dom-2', 'dom-3'))
        self.assertEqual(item.leaves, ('dom-3',))
        self.assertEqual(raw['pcs_capture']['graph'], 'observed-order')
        self.assertIs(raw['pcs_capture']['complete'], False)
        self.assertEqual(raw['pcs_capture']['message_identity'], 'capture-local')
        self.assertEqual(raw['mapping']['dom-1']['message']['content']['parts'], ['第一天\n中文😀'])
        self.assertNotIn(b'do-not-save', canonical(raw))
        self.assertNotIn('captured_at', raw['pcs_capture'])
        self.assertEqual(value, before)
        value['captured_at'] = '2026-10-24T03:00:00+08:00'
        self.assertEqual(raw, capture.validate_capture(value))
        value['scope'] = 'selection'
        self.assertNotEqual(item.digest, conversation(capture.validate_capture(value), Limits()).digest)

    def test_invalid_and_opaque_fields_are_rejected(self):
        invalid = [None, {}, dict(sample(), full_payload={'hidden': 'SECRET'}),
                   dict(sample(), version=True), dict(sample(), method=[]),
                   dict(sample(), scope='all_branches'), dict(sample(), captured_at='2026-10-23'),
                   dict(sample(), url='https://evil.example/c/chat-1'),
                   dict(sample(), url='https://user:pass@chatgpt.com/c/chat-1')]
        for field, bad in (('role', []), ('content', {'hidden': 'SECRET'}),
                           ('content', '\ud800'), ('id', '../hidden')):
            value = sample(); value['messages'][0][field] = bad; invalid.append(value)
        value = sample(); value['messages'][0]['metadata'] = {'full_payload': 'SECRET'}; invalid.append(value)
        value = sample(); value['messages'][1]['id'] = value['messages'][0]['id']; invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                capture.validate_capture(value)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_byte_and_message_budgets_do_not_truncate(self):
        value = sample(); value['messages'][0]['content'] = '😀' * 4
        with patch.object(capture, 'MAX_MESSAGE_BYTES', 15), self.assertRaises(ValueError):
            capture.validate_capture(value)
        with patch.object(capture, 'MAX_MESSAGE_BYTES', 32):
            self.assertEqual(capture.validate_capture(value)['mapping']['dom-1']['message']['content']['parts'], ['😀'*4])
        size = len(canonical(value))
        with patch.object(capture, 'MAX_CAPTURE_BYTES', size-1), self.assertRaises(ValueError):
            capture.validate_capture(value)
        value = sample(); value['messages'] = [dict(id='dom-'+str(i), role='user', content='') for i in range(5001)]
        with self.assertRaises(ValueError):capture.validate_capture(value)
        self.path.write_bytes(b' ' * 100 + canonical(sample()))
        with patch.object(capture, 'MAX_CAPTURE_BYTES', len(canonical(sample())) + 1), self.assertRaises(ValueError):
            load_archive(self.path)

    def test_download_json_and_direct_capture_have_identical_semantics(self):
        value = sample()
        archive = self.archive(value)
        self.assertEqual(archive.conversations[0].raw, capture.validate_capture(value))
        note = self.imported(value)
        source = review.Source.load(note.parent / '原始對話.json')
        self.assertEqual(source.item.raw['pcs_capture'], capture.validate_capture(value)['pcs_capture'])
        self.assertEqual(capture.capture_summary(value)['message_count'], 3)
        self.assertEqual(self.archive(source.item.raw).conversations[0].raw, source.item.raw)

    def test_narrowed_scope_survives_summary_portable_and_reopen(self):
        value = sample()
        value['messages'][1]['resources'] = [dict(kind='chart', label='行程時間軸', url=None)]
        value['messages'][2]['resources'] = [dict(kind='image', label='UNSELECTED_RESOURCE', url=None)]
        note = self.imported(value)
        source = review.Source.load(note.parent / '原始對話.json')
        narrowed = review.subset(source, ('dom-2',))
        self.assertNotIn(b'UNSELECTED_BODY', canonical(narrowed))
        self.assertNotIn(b'UNSELECTED_RESOURCE', canonical(narrowed))
        meta = narrowed['pcs_capture']
        self.assertEqual(meta['scope'], 'selection')
        self.assertEqual(meta['source_scope'], 'loaded')
        self.assertEqual(meta['message_ids'], ['dom-1', 'dom-2'])
        folder = self.root / 'selected'; folder.mkdir()
        (folder / '原始對話.json').write_bytes(canonical(narrowed))
        selected = review.Source.load(folder / '原始對話.json')
        request, _, _ = review.packets(selected, 'dom-2')
        record = review.candidate(request, [dict(category='待辦', text='保留行程', sources=['dom-2'])], {1})
        first = review.save_candidate(selected, record, review.continuation(record, selected))
        retained = review.Source.load(first / '原始對話.json')
        saved = json.loads((first / '整理.json').read_text(encoding='utf-8'))
        second = review.save_candidate(retained, saved, review.continuation(saved, retained))
        self.assertEqual(review.Source.load(second / '原始對話.json').item.raw['pcs_capture'], meta)
        package = portable.prepare(self.root, first / '整理.md', (first / '整理.md').read_text(encoding='utf-8'), retained, ('dom-2',))
        self.assertNotIn(b'UNSELECTED_BODY', b''.join(data for _, data in package.bundle.files))
        zipped = portable.export_zip(self.out, package)
        copied, entry = portable.import_zip(zipped, self.out)
        reopened = review.Source.load(entry.parent / '原始對話.json')
        self.assertEqual(reopened.item.raw['pcs_capture'], meta)
        self.assertEqual(reopened.item.raw['mapping'], retained.item.raw['mapping'])
        self.assertEqual(reopened.item.raw['mapping']['dom-2']['message']['metadata']['pcs_capture_resources'],
                         value['messages'][1]['resources'])

    def test_rich_markdown_and_resource_items_preserve_observed_content(self):
        value = sample()
        text = ('| 指標 | 數值 |\n| --- | --- |\n| CVSS | 8.1 |\n\n'
                '2026-10-01 → 公告\n2026-10-03 → 修正\n\n'
                '[資料來源](https://example.com/report#section)')
        value['messages'][1]['content'] = text
        resources = [dict(kind='chart', label='風險趨勢', url=None),
                     dict(kind='image', label='圖像 <img> [測試]', url='https://example.com/image(1).png'),
                     dict(kind='embed', label='互動時間軸', url=None)]
        value['messages'][1]['resources'] = resources
        original = copy.deepcopy(value)
        raw = capture.validate_capture(value)
        message = raw['mapping']['dom-2']['message']
        rendered = message['content']['parts'][0]
        self.assertTrue(rendered.startswith(text + '\n\n'))
        self.assertEqual(rendered.count('風險趨勢'), 1)
        self.assertIn('- 圖表：[風險趨勢](https://chatgpt.com/c/chat-1)', rendered)
        self.assertIn(r'圖像 \<img\> \[測試\]', rendered)
        self.assertIn('image%281%29.png', rendered)
        self.assertNotIn('![', rendered)
        self.assertEqual(message['metadata']['pcs_capture_resources'], resources)
        self.assertEqual(value, original)

    def test_single_volume_opens_at_content_and_export_preserves_the_edited_note(self):
        value = sample()
        table = '| 指標 | 數值 |\n| --- | --- |\n| CVSS | 8.1 |'
        value['messages'][1]['content'] = table
        value['messages'][1]['resources'] = [dict(kind='chart', label='風險趨勢', url=None)]
        archive = self.archive(value)
        input_bytes = self.path.read_bytes()
        result = import_selected(archive, (0,), Destination.choose(self.root), Event())
        self.assertFalse(result.failed)
        note = result.completed[0]
        text = note.read_text(encoding='utf-8')
        self.assertTrue(text.startswith('# 京都行程\n\n## 使用者\n'))
        self.assertIn(table, text)
        self.assertIn('"node": "dom-2"', text)
        self.assertIn('- 圖表：[風險趨勢](https://chatgpt.com/c/chat-1)', text)
        self.assertNotIn('返回對話索引', text)
        self.assertNotIn('原始完整紀錄', text)
        branch = note.parent / '分支-001-001.md'
        branch_bytes = branch.read_bytes()
        self.assertIn(table, branch_bytes.decode('utf-8'))
        source = review.Source.load(note.parent / '原始對話.json')
        raw_bytes = source.path.read_bytes()
        edited = text + '\n## 我的筆記\n\n保留手動修改。\n'
        note.write_text(edited, encoding='utf-8')
        package = portable.prepare(self.root, note, edited, source, ('dom-3',))
        zipped = portable.export_zip(self.out, package)
        _, entry = portable.import_zip(zipped, self.out)
        self.assertEqual(entry.read_text(encoding='utf-8'), edited)
        reopened = review.Source.load(entry.parent / '原始對話.json')
        self.assertEqual(reopened.item.raw['mapping'], source.item.raw['mapping'])
        self.assertEqual(reopened.item.raw['pcs_capture'], source.item.raw['pcs_capture'])
        self.assertEqual(self.path.read_bytes(), input_bytes)
        self.assertEqual(source.path.read_bytes(), raw_bytes)
        self.assertEqual(branch.read_bytes(), branch_bytes)
        self.assertEqual(note.read_text(encoding='utf-8'), edited)

    def test_multi_volume_capture_keeps_a_concise_scope_and_volume_index(self):
        value = sample()
        for index, message in enumerate(value['messages']):
            message['content'] = '第 '+str(index+1)+' 則內容 '+('資料' * 60)
        self.archive(value)
        archive = load_archive(self.path, limits=Limits(volume=5000))
        result = import_selected(archive, (0,), Destination.choose(self.root), Event())
        self.assertFalse(result.failed)
        note = result.completed[0]
        text = note.read_text(encoding='utf-8')
        branches = sorted(note.parent.glob('分支-*.md'))
        self.assertGreater(len(branches), 1)
        self.assertIn('來源範圍：已載入的對話', text)
        self.assertIn('[第 1 卷]', text)
        self.assertNotIn('完整', text)
        self.assertNotIn('第 1 則內容', text)
        body = '\n'.join(path.read_text(encoding='utf-8') for path in branches)
        for message in value['messages']:
            self.assertIn(message['content'], body)
        self.assertLess(len(text.encode('utf-8')), 1000)

    def test_image_only_message_saves_a_readable_reference_without_download(self):
        value = sample()
        value['messages'] = [dict(id='dom-image', role='assistant', content='', resources=[
            dict(kind='attachment', label='results.csv', url=None),
            dict(kind='image', label='結果圖', url='https://example.com/result.png')])]
        note = self.imported(value)
        branch = next(note.parent.glob('分支-*.md')).read_text(encoding='utf-8')
        self.assertIn('- 附件：[results', branch)
        self.assertIn('- 圖片：[結果圖](https://example.com/result.png)', branch)
        self.assertNotIn('來源未提供文字', branch)
        self.assertFalse((note.parent / 'assets').exists())
        receipt = json.loads((note.parent / RECEIPT).read_text(encoding='utf-8'))
        self.assertEqual(receipt['attachments'], {})
        self.assertEqual(receipt['warnings'], [])

    def test_resources_reject_opaque_fields_private_urls_and_excess_counts(self):
        good = dict(kind='chart', label='圖表', url=None)
        bad_items = [dict(good, pixels='opaque-payload'), dict(good, kind='html'),
                     dict(good, label='x' * 301), dict(good, label='😀' * 301),
                     dict(good, url='javascript:alert(1)'), dict(good, url='file:///C:/secret'),
                     dict(good, url='https://user:password@example.com/a'),
                     dict(good, url='https://example.com/image?X-Amz-Signature=private'),
                     dict(good, url='https://example.com/image?%74oken=private'),
                     dict(good, url='https://example.com/a\nb')]
        for item in bad_items:
            value = sample(); value['messages'][0]['resources'] = [item]
            with self.subTest(item=item), self.assertRaises(ValueError):
                capture.validate_capture(value)
        value = sample(); value['messages'][0]['resources'] = [good] * 33
        with self.assertRaises(ValueError):capture.validate_capture(value)
        value['messages'] = [dict(id='dom-' + str(i), role='user', content='', resources=[good] * 32)
                             for i in range(4)]
        self.assertEqual(len(capture.validate_capture(value)['mapping']), 4)
        value['messages'].append(dict(id='dom-last', role='user', content='', resources=[good]))
        with self.assertRaises(ValueError):capture.validate_capture(value)

    def test_resource_projection_counts_against_message_budget(self):
        value = sample()
        value['messages'] = [dict(id='dom-1', role='assistant', content='text', resources=[
            dict(kind='chart', label='chart', url=None)])]
        with patch.object(capture, 'MAX_MESSAGE_BYTES', 10), self.assertRaises(ValueError):
            capture.validate_capture(value)

    def test_scope_change_invalidates_existing_source_and_candidate(self):
        note = self.imported()
        source = review.Source.load(note.parent / '原始對話.json')
        request, _, _ = review.packets(source, 'dom-3')
        record = review.candidate(request, [dict(category='待辦', text='行程', sources=['dom-1'])], {1})
        raw = copy.deepcopy(source.item.raw); raw['pcs_capture']['scope'] = 'selection'
        source.path.write_bytes(canonical(raw))
        with self.assertRaises(ValueError):source.verify()
        latest = review.Source.load(source.path)
        self.assertTrue(review.freshness(record, latest, 'dom-3').startswith('來源已更新'))

    def test_subset_rejects_opaque_or_mismatched_capture_metadata(self):
        for mutate in (lambda v: v['pcs_capture'].update(opaque={'secret': 'SECRET'}),
                       lambda v: v['pcs_capture'].update(message_ids=['unrelated']),
                       lambda v: v['pcs_capture'].update(complete=True)):
            raw = capture.validate_capture(sample()); mutate(raw)
            self.path.write_bytes(canonical(raw))
            source = review.Source.load(self.path)
            with self.assertRaises(ValueError):review.subset(source, ('dom-2',))

    def test_writer_binds_operation_before_payload_and_preserves_user_edits(self):
        operation = str(uuid.uuid4()); started = []
        def on_started(ident, folder):
            self.assertTrue(all(path.name.startswith('.pcs-save-') and path.suffix == '.guard'
                                and path.stat().st_size == 0 for path in folder.iterdir()))
            started.append((ident, folder, folder.stat().st_ino))
        note = self.imported(operation_id=operation, on_started=on_started)
        self.assertEqual(started[0][:2], (uuid.UUID(operation).hex, note.parent))
        begin = json.loads((note.parent / '匯入開始.json').read_text(encoding='utf-8'))
        complete = json.loads((note.parent / RECEIPT).read_text(encoding='utf-8'))
        self.assertEqual(begin['operation'], complete['operation'])
        self.assertEqual(complete['operation'], uuid.UUID(operation).hex)
        note.write_text('user edit', encoding='utf-8')
        result = import_selected(self.archive(), (0,), Destination.choose(self.root), Event(), operation_id=operation, on_started=on_started)
        self.assertEqual(result.skipped, [note]); self.assertEqual(len(started), 1)
        self.assertEqual(note.read_text(), 'user edit')

    def test_writer_callback_failure_leaves_no_payload_or_completion(self):
        started = []
        def fail(ident, folder):
            started.append(folder)
            raise OSError('ledger unavailable')
        result = import_selected(self.archive(), (0,), Destination.choose(self.root), Event(),
                                 operation_id=uuid.uuid4().hex, on_started=fail)
        self.assertFalse(result.completed)
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(list(started[0].iterdir()), [])

    def test_operation_validation_precedes_writes_and_legacy_call_still_works(self):
        archive = self.archive()
        for operation in ('../owned', '', 1, 'not-a-uuid'):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                import_selected(archive, (0,), Destination.choose(self.root), Event(), operation_id=operation)
        self.assertEqual(list(self.root.iterdir()), [])
        result = import_selected(archive, (0,), Destination.choose(self.root), Event())
        self.assertEqual(len(result.completed), 1)
        folder = result.completed[0].parent
        before = json.loads((folder / '匯入開始.json').read_text(encoding='utf-8'))
        after = json.loads((folder / RECEIPT).read_text(encoding='utf-8'))
        self.assertEqual(before['operation'], after['operation'])


if __name__ == '__main__':
    unittest.main()
