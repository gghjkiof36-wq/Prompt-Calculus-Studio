"""Conversation-to-file contracts, with synthetic native rollout and filesystem data."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from prompt_calculus_studio.conversation_archive import canonical, load_archive
from prompt_calculus_studio.conversation_import import Destination, import_selected, RECEIPT
from prompt_calculus_studio.work_resources.catalog import Catalog
from prompt_calculus_studio.work_resources import exports, navigation
from test_work_resources import example


def rollout(title='中文對話'):
    def event(key, role, text):
        return dict(type='event_msg', timestamp='2026-11-03T01:00:00Z', payload=dict(
            type='item_completed', started_at_ms=1793667600000,
            item=dict(type='UserMessage' if role == 'user' else 'AgentMessage', id=key,
                      content=[dict(type='text' if role == 'user' else 'Text', text=text)])))
    return [dict(type='session_meta', payload=dict(id='synthetic-session', cli_version='0.165.0-alpha.1.2', title=title)),
            event('human-1', 'user', '重複的真實問題🙂'),
            dict(type='response_item', payload=dict(type='message', id='model-projection', role='user',
                    content=[dict(type='input_text', text='重複的真實問題🙂\nHARNESS-DO-NOT-RENDER')])),
            event('answer-1', 'assistant', '```python\nprint("中文")\n```\n\n- 清單甲\n- 清單乙'),
            event('human-2', 'user', '重複的真實問題🙂'),
            dict(type='response_item', payload=dict(type='message', role='developer',
                 content=[dict(type='input_text', text='INTERNAL-DO-NOT-RENDER')])),
            dict(type='response_item', payload=dict(type='function_call_output', output='TOOL-DO-NOT-RENDER')),
            dict(type='compacted', payload=dict(message='SUMMARY-DO-NOT-RENDER'))]


def write_rollout(path, records=None):
    path.write_bytes(b'\n'.join(canonical(row) for row in (records or rollout())) + b'\n')
    return path


class MarkdownExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.output = self.base/'notes'; self.output.mkdir()
        self.data = self.base/'data'; self.data.mkdir()
        self.store = Catalog(self.data)
        self.source = write_rollout(self.base/'native.jsonl')
        self.destination = Destination.choose(self.output)

    def register(self, path):
        sid = self.store.register(path, 'archive')
        self.store.rebuild(sid)
        return self.store.browse(source=sid)[0]

    def test_native_projection_order_roles_repeats_original_and_collision(self):
        before = self.source.read_bytes()
        source = load_archive(self.source)
        item = source.conversations[0]
        self.assertEqual(list(item.raw['mapping']), ['human-1', 'answer-1', 'human-2'])
        first = import_selected(source, [0], self.destination, None, readable=True)
        self.assertFalse(first.failed)
        note = first.completed[0]
        text = note.read_text(encoding='utf8')
        self.assertIn('來源：Codex', text)
        self.assertEqual(text.count('重複的真實問題🙂'), 2)
        self.assertIn('```python\nprint("中文")\n```', text)
        self.assertIn('2026-11-03', text)
        self.assertNotIn('DO-NOT-RENDER', text)
        self.assertEqual((note.parent/'原始封存.jsonl').read_bytes(), before)
        self.assertEqual(len(list(note.parent.glob('*.md'))), 1)
        note.write_text('使用者編輯', encoding='utf8')
        second = import_selected(source, [0], self.destination, None, readable=True)
        self.assertFalse(second.failed)
        self.assertNotEqual(note, second.completed[0])
        self.assertEqual(note.read_text(encoding='utf8'), '使用者編輯')
        self.assertEqual(self.source.read_bytes(), before)

    def test_dedup_ids_preserve_distinct_equal_text_and_reject_conflicting_id(self):
        values = rollout()
        values.append(deepcopy(values[1]))
        write_rollout(self.source, values)
        self.assertEqual(len(load_archive(self.source).conversations[0].raw['mapping']), 3)
        values[-1]['payload']['item']['content'][0]['text'] = 'conflicting'
        write_rollout(self.source, values)
        with self.assertRaisesRegex(ValueError, '識別重複'):
            load_archive(self.source)
        self.source.write_bytes(b'{"type":"session_meta",')
        with self.assertRaisesRegex(ValueError, '完整 JSON'):
            load_archive(self.source)

    def test_response_only_filters_injected_records(self):
        values = [rollout()[0]]
        for role, kinds, text in [('user', ['agents_md.instructions'], 'INJECTED'),
                                  ('user', ['user.text'], '可見問題'), ('assistant', [], '可見回答')]:
            values.append(dict(type='response_item', timestamp='2026-11-03T01:00:00Z', payload=dict(
                type='message', role=role, content=[dict(type='input_text', text=text)],
                internal_chat_message_metadata_passthrough={'content_item_kinds': kinds})))
        write_rollout(self.source, values)
        source = load_archive(self.source)
        result = import_selected(source, [0], self.destination, None)
        text = result.completed[0].read_text(encoding='utf8')
        self.assertIn('可見問題', text)
        self.assertIn('可見回答', text)
        self.assertNotIn('INJECTED', text)

    def test_retained_catalog_export_relative_attachment_and_missing_absolute(self):
        import base64, struct, zlib
        def chunk(kind, data):
            return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
        png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR',struct.pack('>2I5B',1,1,8,2,0,0,0)) +
               chunk(b'IDAT',zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND',b''))
        (self.base/'image.png').write_bytes(png)
        values = rollout()
        values[1]['payload']['item']['content'] += [dict(type='local_image',path='image.png'),
            dict(type='image',image_url='data:image/png;base64,'+base64.b64encode(png).decode()),
            dict(type='local_image',path='C:/outside/private.png')]
        write_rollout(self.source, values)
        result = exports.export_selected(self.store, self.register(self.source), self.destination)
        self.assertFalse(result.failed)
        note = result.completed[0]
        self.assertEqual(len(list((note.parent/'assets').iterdir())),1)
        self.assertEqual(next((note.parent/'assets').iterdir()).read_bytes(),png)
        self.assertIn('未自動讀取',note.read_text(encoding='utf8'))
        receipt = json.loads((note.parent/RECEIPT).read_bytes())
        self.assertEqual(len(receipt['codex_images']),1)

    def test_batch_success_failure_cancel_and_registration_failure_are_individual(self):
        rows = self.register(self.source)
        bad = dict(rows[0], story='absent', title='壞的選取')
        result = exports.export_selected(self.store, [bad, rows[0]], self.destination)
        self.assertEqual(len(result.completed),1)
        self.assertEqual(result.exported,[1])
        self.assertEqual(len(result.failed),1)
        cancel = threading.Event(); cancel.set()
        stopped = exports.export_selected(self.store, rows, self.destination,cancel)
        self.assertTrue(stopped.cancelled)
        self.assertEqual(stopped.unprocessed,[rows[0]['title']])
        with patch.object(self.store,'register',side_effect=ValueError('catalog unavailable')):
            saved = exports.export_selected(self.store,rows,self.destination)
        self.assertEqual(len(saved.completed),1)
        self.assertTrue(saved.completed[0].exists())
        self.assertIn('文件已保存',saved.failed[0][2])

    def test_interrupted_publish_is_not_a_home_document(self):
        from prompt_calculus_studio.local_documents.paths import document_entries
        cancel = threading.Event()
        def stop(*_):cancel.set()
        result = import_selected(load_archive(self.source),[0],self.destination,cancel,on_started=stop,readable=True)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.completed)
        self.assertEqual(list(document_entries(self.output,{'.md'}, {'.pcs','assets'})),[])
        self.assertTrue((self.output/'.pcs/conversation-imports').exists())

    def test_folder_navigation_scope_and_definitions_are_not_home_copies(self):
        child = self.output/'子資料夾'; child.mkdir()
        (child/'筆記.md').write_text('# 內文\n可搜尋',encoding='utf8')
        (self.output/'AGENTS.md').write_text('# 指令\n不要執行',encoding='utf8')
        roots = navigation.roots(self.store,[self.output])
        rows,total = navigation.browse(self.store,roots,self.output,'','all',0,50,'name',None)
        self.assertEqual(total,2)
        self.assertEqual(rows[0]['kind'],'folder')
        found,_ = navigation.browse(self.store,roots,self.output,'可搜尋','all',0,50,'name',None)
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['title'],'子資料夾/筆記.md')
        rules,_ = self.store.browse(kind='definitions')
        self.assertEqual(len(rules),1)
        copy,sid = exports.copy_definition(self.store,rules[0])
        self.assertTrue(copy.is_relative_to(self.data))
        self.assertEqual(copy.read_text(encoding='utf8'),'# 指令\n不要執行')
        self.assertEqual(self.store.source(sid)['kind'],'definition')
        self.assertFalse((self.output/'definitions').exists())

    def test_zip_multiple_dialogues_have_working_branch_links(self):
        from zipfile import ZipFile
        from urllib.parse import unquote
        archive = self.base/'conversations.zip'
        with ZipFile(archive,'w') as zipped:
            zipped.writestr('conversations.json',canonical([example()]))
        rows = self.register(archive)
        result = exports.export_selected(self.store,rows,self.destination)
        self.assertFalse(result.failed)
        note = result.completed[0]
        import re
        for target in re.findall(r'\]\(([^)]+)\)',note.read_text(encoding='utf8')):
            branch = note.parent/unquote(target)
            self.assertTrue(branch.exists())
            self.assertIn('('+note.name+')',unquote(branch.read_text(encoding='utf8')))

    def test_pcs_context_routes_export_without_including_drafts_or_instructions(self):
        from prompt_calculus_studio.local_documents import schema
        from test_local_chat_contracts import fixture
        source=self.base/'本機對話.pcs-chat'
        source.write_bytes(schema.encode(fixture()))
        original=source.read_bytes()
        rows=self.register(source)
        result=exports.export_selected(self.store,rows,self.destination)
        self.assertFalse(result.failed)
        text=result.completed[0].read_text(encoding='utf8')
        self.assertIn('來源：PCS',text)
        self.assertIn('第一個問題',text)
        self.assertIn('尚未收完',text)
        self.assertNotIn('尚未送出的草稿',text)
        self.assertNotIn('以繁體中文回答',text)
        self.assertEqual(source.read_bytes(),original)

    def test_batch_reuses_parser_and_cancel_keeps_already_published_file(self):
        second=deepcopy(example()); second['id']='second'; second['title']='另一份'
        source=self.base/'two.json'; source.write_bytes(canonical([example(),second]))
        rows=self.register(source)
        with patch.object(exports,'parse_archive',wraps=exports.parse_archive) as parser:
            result=exports.export_selected(self.store,rows,self.destination)
        self.assertEqual(len(result.completed),2)
        self.assertEqual(parser.call_count,1)
        cancel=threading.Event()
        real_import=exports.import_selected
        def once(*args,**kwargs):
            current=real_import(*args,**kwargs); cancel.set(); return current
        with patch.object(exports,'import_selected',side_effect=once):
            stopped=exports.export_selected(self.store,rows,self.destination,cancel)
        self.assertTrue(stopped.cancelled)
        self.assertEqual(len(stopped.completed),1)
        self.assertTrue(stopped.completed[0].exists())
        self.assertEqual(stopped.unprocessed,[rows[1]['title']])


if __name__ == '__main__':
    unittest.main()
