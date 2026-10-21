"""Synthetic export contracts; no account archive or live service is used."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo
from urllib.parse import unquote

from prompt_calculus_studio.conversation_archive import load_archive, Limits, Cancelled, conversation
from prompt_calculus_studio.conversation_import import Destination, import_selected, RECEIPT
from prompt_calculus_studio.conversation_markdown import attachment_plan, preview_text


def sample(key='chat-1', title='介面設計討論'):
    def node(key, parent, children, role=None, text=''):
        return dict(id=key, parent=parent, children=children, message=None if role is None else
                    dict(id='message-'+key, author=dict(role=role), create_time=1730000000,
                         content=dict(content_type='text', parts=[text]), metadata=dict(unknown='保留')))
    return dict(id=key, title=title, current_node='c', unknown={'source': 'kept'}, mapping={
        'root': node('root', None, ['a']), 'a': node('a', 'root', ['b', 'c'], 'user', '# 問題\n\n共同歷史'),
        'b': node('b', 'a', [], 'assistant', '回答 A\n\n```python\n  print(1)\n```'),
        'c': node('c', 'a', [], 'assistant', '重新生成回答 B')})


class ConversationImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.dest = self.base/'notes'
        self.dest.mkdir()
        self.source = self.base/'conversations.json'
        self.event = threading.Event()

    def load(self, records=None, **kwargs):
        self.source.write_text(json.dumps(records if records is not None else [sample()], ensure_ascii=False), encoding='utf-8')
        return load_archive(self.source, **kwargs)

    def run_import(self, archive, indices=(0,), **kwargs):
        return import_selected(archive, indices, Destination.choose(self.dest), self.event, **kwargs)

    def test_selected_raw_branches_and_original_are_preserved(self):
        source = self.load([sample(), sample('private', '未選取秘密')])
        before = self.source.read_bytes()
        result = self.run_import(source)
        self.assertEqual(len(result.completed), 1)
        folder = result.completed[0].parent
        self.assertEqual(json.loads((folder/'原始對話.json').read_text(encoding='utf-8')), sample())
        branches = list(folder.glob('分支-*.md'))
        self.assertEqual(len(branches), 2)
        for path in branches:
            text = path.read_text(encoding='utf-8')
            self.assertEqual(text.count('共同歷史'), 1)
            self.assertFalse('回答 A' in text and '回答 B' in text)
        index = result.completed[0].read_text(encoding='utf-8')
        self.assertIn('來源目前路徑', index)
        self.assertIn('分支-002-001.md', unquote(index))
        self.assertIn('`原始對話.json`', index)
        self.assertNotIn('](原始對話.json)', index)
        self.assertEqual(before, self.source.read_bytes())
        self.assertFalse(any('未選取秘密' in p.name for p in self.dest.iterdir()))
        self.assertNotIn('private', (folder/'原始對話.json').read_text(encoding='utf-8'))

    def test_nonstandard_role_is_preserved_without_breaking_preview(self):
        value = sample()
        value['mapping']['c']['message']['author']['role'] = {'future': 'role'}
        source = self.load([value])
        self.assertIn('未知角色', preview_text(source.conversations[0], 'c'))
        result = self.run_import(source)
        self.assertEqual(len(result.completed), 1)
        raw = json.loads((result.completed[0].parent/'原始對話.json').read_text(encoding='utf-8'))
        self.assertEqual(raw, value)

    def test_repeat_does_not_overwrite_edited_notes_and_revisions_are_separate(self):
        archive = self.load()
        first = self.run_import(archive).completed[0]
        first.write_text('user edit', encoding='utf-8')
        second = self.run_import(archive)
        self.assertEqual(second.skipped, [first])
        self.assertEqual(first.read_text(), 'user edit')
        other = sample('another-id')
        self.assertEqual(len(self.run_import(self.load([other])).completed), 1)
        changed = sample()
        changed['mapping']['c']['message']['content']['parts'] = ['new']
        third = self.run_import(self.load([changed]))
        self.assertEqual(len(third.completed), 1)
        self.assertNotEqual(first, third.completed[0])
        self.assertEqual(first.read_text(), 'user edit')

    def test_zip_shards_assets_missing_and_ambiguous_are_explicit(self):
        value = sample()
        value['mapping']['a']['message']['content']['parts'].extend([
            dict(content_type='image_asset_pointer', asset_pointer='file-service://file-picture'),
            dict(content_type='image_asset_pointer', asset_pointer='file-service://missing'),
            dict(content_type='image_asset_pointer', asset_pointer='file-service://file-ambiguous')])
        self.source = self.base/'export.zip'
        with ZipFile(self.source, 'w') as archive:
            archive.writestr('conversations-1.json', json.dumps([value]))
            archive.writestr('conversations-2.json', json.dumps([sample('second')]))
            archive.writestr('file-picture.png', b'fake image source')
            archive.writestr('one/file-ambiguous.png', b'a')
            archive.writestr('two/file-ambiguous.png', b'b')
            archive.writestr('account.json', '{"secret":"not selected"}')
        archive = load_archive(self.source)
        self.assertEqual(len(archive.conversations), 2)
        result = self.run_import(archive)
        folder = result.completed[0].parent
        assets = list((folder/'assets').iterdir())
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].read_bytes(), b'fake image source')
        index = result.completed[0].read_text(encoding='utf-8')
        self.assertIn('多個可能附件', index)
        self.assertIn('missing', index)
        self.assertFalse((folder/'account.json').exists())

    def test_conversation_carriers_cannot_be_imported_as_attachments(self):
        cases = (
            ('conversations.json', 'conversations.json'),
            ('nested/Conversations.JSON', 'nested/Conversations.JSON'),
            ('nested/conversations-1.json', 'conversations-1.json'),
            ('nested/deep/conversations_2.json', 'sandbox:/mnt/data/nested/deep/conversations_2.json'),
            ('nested/conversations-3.json', 'attachment://nested/conversations-3.json'),
            ('conversations_4.json', 'file-service://conversations_4.json'),
        )
        marker = 'SYNTHETIC_UNSELECTED_CARRIER_MESSAGE'
        for number, (member, reference) in enumerate(cases):
            with self.subTest(member=member, reference=reference):
                value = sample('selected-'+str(number))
                value['mapping']['a']['message']['metadata']['attachments'] = [
                    {'name': reference}, {'path': 'notes/settings.json'}, {'file_id': 'file-picture'}]
                private = sample('unselected', marker)
                source_path = self.base/f'carrier-{number}.zip'
                ordinary_json = b'{"setting":"selected attachment"}'
                media = b'synthetic selected image'
                with ZipFile(source_path, 'w') as zipped:
                    zipped.writestr(member, json.dumps([value, private]))
                    zipped.writestr('notes/settings.json', ordinary_json)
                    zipped.writestr('file-picture.png', media)
                before = source_path.read_bytes()
                archive = load_archive(source_path)
                result = self.run_import(archive)
                self.assertEqual(len(result.completed), 1)
                self.assertEqual(result.failed, [])
                folder = result.completed[0].parent
                self.assertFalse(any(marker.encode() in path.read_bytes()
                                     for path in folder.rglob('*') if path.is_file()))
                self.assertEqual(json.loads((folder/'原始對話.json').read_text(encoding='utf-8')), value)
                self.assertEqual(len(list(folder.glob('分支-*.md'))), 2)
                self.assertEqual({path.read_bytes() for path in (folder/'assets').iterdir()},
                                 {ordinary_json, media})
                receipt = json.loads((folder/RECEIPT).read_text(encoding='utf-8'))
                self.assertEqual(set(receipt['attachments']), {'notes/settings.json', 'file-picture.png'})
                self.assertTrue(any('對話來源檔' in warning for warning in receipt['warnings']))
                index = result.completed[0].read_text(encoding='utf-8')
                self.assertIn('對話來源檔', index)
                self.assertIn(reference, index.replace(r'\_', '_'))
                self.assertEqual(source_path.read_bytes(), before)

    def test_other_nested_shards_and_duplicate_carriers_stay_out_of_attachments(self):
        value = sample()
        names = ('a/conversations-1.json', 'b/deep/conversations_2.json',
                 'c/conversations.json', 'd/conversations.json')
        references = [*names, 'conversations_2.json', 'conversations.json']
        value['mapping']['c']['message']['metadata']['attachments'] = [{'name': ref} for ref in references]
        marker = 'SYNTHETIC_UNSELECTED_OTHER_SHARD'
        private = sample('unselected', marker)
        self.source = self.base/'shards.zip'
        with ZipFile(self.source, 'w') as zipped:
            zipped.writestr(names[0], json.dumps([value]))
            zipped.writestr(names[1], json.dumps([private]))
            zipped.writestr(names[2], json.dumps([value, private]))
            zipped.writestr(names[3], json.dumps([private]))
        before = self.source.read_bytes()
        archive = load_archive(self.source)
        self.assertEqual(len(archive.conversations), 2)
        result = self.run_import(archive)
        self.assertEqual(len(result.completed), 1)
        self.assertEqual(result.failed, [])
        folder = result.completed[0].parent
        self.assertFalse((folder/'assets').exists())
        self.assertFalse(any(marker.encode() in path.read_bytes()
                             for path in self.dest.rglob('*') if path.is_file()))
        self.assertEqual(json.loads((folder/'原始對話.json').read_text(encoding='utf-8')), value)
        receipt = json.loads((folder/RECEIPT).read_text(encoding='utf-8'))
        self.assertEqual(receipt['attachments'], {})
        self.assertEqual(len(receipt['warnings']), len(references))
        self.assertTrue(all('對話來源檔' in warning for warning in receipt['warnings']))
        self.assertEqual(self.source.read_bytes(), before)

    def test_reimport_after_carrier_fix_preserves_old_edits_and_creates_safe_copy(self):
        value = sample()
        value['mapping']['a']['message']['metadata']['attachments'] = [{'name': 'conversations.json'}]
        marker = 'SYNTHETIC_UNSELECTED_OLD_IMPORT'
        self.source = self.base/'previous-import.zip'
        with ZipFile(self.source, 'w') as zipped:
            zipped.writestr('conversations.json', json.dumps([value, sample('unselected', marker)]))
        before = self.source.read_bytes()
        archive = load_archive(self.source)
        # Reproduce the prior resolver in this fixture only, retaining its receipt
        # and edited note to exercise migration without rewriting old output.
        with patch.object(archive, 'attachment', return_value=('conversations.json', '')):
            old = self.run_import(archive).completed[0]
        self.assertTrue(any(marker.encode() in path.read_bytes() for path in old.parent.rglob('*') if path.is_file()))
        old.write_text('existing user edit', encoding='utf-8')
        old_files = {path.relative_to(old.parent): path.read_bytes()
                     for path in old.parent.rglob('*') if path.is_file()}

        result = self.run_import(archive)
        self.assertEqual(result.failed, [])
        self.assertEqual(result.skipped, [])
        self.assertEqual(len(result.completed), 1)
        fresh = result.completed[0]
        self.assertNotEqual(fresh, old)
        self.assertFalse(any(marker.encode() in path.read_bytes() for path in fresh.parent.rglob('*') if path.is_file()))
        self.assertEqual(json.loads((fresh.parent/'原始對話.json').read_text(encoding='utf-8')), value)
        self.assertEqual(len(list(fresh.parent.glob('分支-*.md'))), 2)
        self.assertEqual(self.run_import(archive).skipped, [fresh])
        self.assertEqual({path.relative_to(old.parent): path.read_bytes()
                          for path in old.parent.rglob('*') if path.is_file()}, old_files)
        self.assertEqual(self.source.read_bytes(), before)

    def test_unknown_parts_tool_messages_and_metadata_are_not_dropped(self):
        value = sample()
        value['mapping']['b']['message']['author']['role'] = 'tool'
        value['mapping']['b']['message']['content'] = dict(content_type='unknown', opaque={'key': 'value'})
        result = self.run_import(self.load([value]))
        folder = result.completed[0].parent
        branch = (folder/'分支-001-001.md').read_text(encoding='utf-8')
        self.assertIn('工具', branch)
        self.assertIn('原始內容保留', branch)
        self.assertEqual(json.loads((folder/'原始對話.json').read_text(encoding='utf-8')), value)

    def test_malformed_graphs_formats_and_limits_reject_without_output(self):
        for mutate in (lambda v:v['mapping']['a'].update(parent='missing'),
                       lambda v:v['mapping']['a'].update(children=['b']),
                       lambda v:v.update(current_node='missing'),
                       lambda v:v['mapping']['root'].update(parent='c')):
            value = sample()
            mutate(value)
            with self.assertRaises(ValueError):self.load([value])
        for raw in ({'messages': []}, [], {'mapping': {}}):
            with self.assertRaises(ValueError):self.load(raw)
        with self.assertRaises(ValueError):self.load(limits=replace(Limits(), nodes=2))
        with self.assertRaises(ValueError):self.load(limits=replace(Limits(), branches=1))
        self.assertEqual(list(self.dest.iterdir()), [])
        self.source.write_text('{"mapping":{},"mapping":{}}')
        with self.assertRaises(ValueError):load_archive(self.source)

    def test_dangerous_zip_paths_and_collisions_are_rejected(self):
        for name in ('../escape.txt', 'C:/outside', '/absolute', 'CON.txt', 'a\\b', 'a/../b'):
            path = self.base/'bad.zip'
            with ZipFile(path, 'w') as archive:
                archive.writestr('conversations.json', json.dumps([sample()]))
                entry = ZipInfo('placeholder')
                entry.filename = name
                archive.writestr(entry, 'evil')
            with self.subTest(name=name), self.assertRaises(ValueError):load_archive(path)
        with ZipFile(path, 'w') as archive:
            archive.writestr('conversations.json', json.dumps([sample()]))
            archive.writestr('A.txt', 'a')
            archive.writestr('a.txt', 'b')
        with self.assertRaises(ValueError):load_archive(path)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_cancel_before_confirm_and_after_first_complete(self):
        archive = self.load([sample(), sample('second')])
        self.assertEqual(list(self.dest.iterdir()), [])
        self.event.set()
        with self.assertRaises(Cancelled):self.run_import(archive)
        self.assertEqual(list(self.dest.iterdir()), [])
        self.event.clear()
        def progress(text):
            if '2 / 2' in text:self.event.set()
        result = self.run_import(archive, (0, 1), progress=progress)
        self.assertTrue(result.cancelled)
        self.assertEqual(len(result.completed), 1)
        self.assertTrue((result.completed[0].parent/RECEIPT).is_file())

    def test_partial_write_is_not_complete_and_retry_keeps_existing_files(self):
        archive = self.load()
        original = Path.open
        def open_file(path, *args, **kwargs):
            if path.name.startswith('分支-'):raise PermissionError('synthetic disk failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', open_file):
            result = self.run_import(archive)
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.completed, [])
        folder = Path(result.failed[0][1])
        self.assertTrue((folder/'匯入開始.json').exists())
        self.assertFalse((folder/RECEIPT).exists())
        raw = (folder/'原始對話.json').read_bytes()
        retry = self.run_import(archive)
        self.assertEqual(len(retry.completed), 1)
        self.assertNotEqual(retry.completed[0].parent, folder)
        self.assertEqual((folder/'原始對話.json').read_bytes(), raw)
        again = self.run_import(archive)
        self.assertEqual(again.skipped, retry.completed)

    def test_source_and_destination_replacements_are_rejected(self):
        archive = self.load()
        self.source.write_text('[]')
        with self.assertRaises(ValueError):self.run_import(archive)
        archive = self.load()
        destination = Destination.choose(self.dest)
        self.dest.rename(self.base/'previous')
        self.dest.mkdir()
        with self.assertRaises(ValueError):
            import_selected(archive, (0,), destination, self.event)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_large_message_volume_limits_no_truncation_and_preview_bounded(self):
        value = sample()
        value['mapping']['a']['message']['content']['parts'] = ['中文🙂'*4000]
        archive = self.load([value], limits=replace(Limits(), volume=8192))
        result = self.run_import(archive)
        self.assertFalse(result.failed, result.failed)
        folder = result.completed[0].parent
        files = list(folder.glob('分支-*.md'))
        self.assertGreater(len(files), 2)
        self.assertTrue(all(p.stat().st_size <= 8192 for p in files))
        self.assertEqual(json.loads((folder/'原始對話.json').read_text(encoding='utf-8')), value)
        joined = ''.join(p.read_text(encoding='utf-8') for p in files if p.name.startswith('分支-001'))
        self.assertEqual(joined.count('🙂'), 4000)
        self.assertIn('預覽至此', preview_text(archive.conversations[0], 'b', 100))
        limited = self.load(limits=replace(Limits(), output=100))
        result = self.run_import(limited)
        self.assertEqual(result.completed, [])
        self.assertTrue(result.failed)


if __name__ == '__main__':
    unittest.main()
