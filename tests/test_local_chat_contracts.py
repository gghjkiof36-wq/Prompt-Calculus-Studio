"""Pure file/schema contracts for local chat; no Qt, engine or network startup.

These cases are authored for the first local-chat implementation. Run them only
when execution is authorized; writing or parsing this file is not a test pass.
"""
import copy
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from prompt_calculus_studio.local_documents import assets, chat, identity, index, io, schema


def uid():
    return str(uuid.uuid4())


def attempt(text='第一則回答。', status='complete', *, context=()):
    return dict(id=uid(), request=uid(), model='synthetic-test-model', model_name='測試模型', profile_epoch=uid(),
                parameters=dict(context_size=4096, max_tokens=512, temperature=0.7,
                                top_p=0.9, seed=42, gpu_layers=0),
                system_prompt='以繁體中文回答。', context=copy.deepcopy(list(context)),
                text=text, status=status, reason='')


def fixture():
    document = chat.empty()
    first = dict(id=uid(), text='第一個問題。', attempts=[attempt()])
    reference = dict(user_id=first['id'], attempt_id=first['attempts'][0]['id'])
    second = dict(id=uid(), text='繼續解釋。', attempts=[attempt('尚未收完', 'receiving', context=[reference])])
    document['payload'].update(draft='尚未送出的草稿\n第二行', system_prompt='以繁體中文回答。',
                               turns=[first, second])
    return document


class ChatSchemaContracts(unittest.TestCase):
    def test_unicode_code_and_empty_draft_survive_roundtrip(self):
        original = fixture()
        original['payload']['draft'] = ''
        original['payload']['turns'][0]['attempts'][0]['text'] = '說明🙂\n\n```python\nx = "\\n"\n```\n'
        restored = schema.decode(schema.encode(original))
        self.assertEqual(restored, original)
        self.assertEqual(restored['kind'], 'chat')
        self.assertIn('pcs.chat.v1', restored['required_features'])
        self.assertEqual(restored['payload']['draft'], '')

    def test_unknown_versions_and_features_are_rejected_without_mutation(self):
        for edit in (
            lambda d: d.update(file_schema_version=999),
            lambda d: d['payload'].update(version=999),
            lambda d: d['required_features'].append('pcs.chat.future'),
            lambda d: d['required_features'].remove('pcs.chat.v1'),
        ):
            with self.subTest(edit=edit):
                candidate = fixture()
                edit(candidate)
                before = copy.deepcopy(candidate)
                with self.assertRaises(schema.DocumentError):
                    schema.encode(candidate)
                self.assertEqual(candidate, before)

    def test_credentials_and_runtime_paths_are_not_payload_fields(self):
        for field, value in (('token', 'synthetic-secret'), ('model_path', 'C:/models/example.gguf'),
                             ('base_url', 'http://127.0.0.1:9999')):
            with self.subTest(field=field):
                document = fixture()
                document['payload'][field] = value
                with self.assertRaises(schema.DocumentError):
                    schema.encode(document)

    def test_duplicate_turn_and_attempt_ids_are_rejected(self):
        document = fixture()
        document['payload']['turns'][1]['id'] = document['payload']['turns'][0]['id']
        with self.assertRaises(schema.DocumentError):
            schema.encode(document)
        document = fixture()
        first, second = document['payload']['turns']
        second['attempts'][0]['id'] = first['attempts'][0]['id']
        with self.assertRaises(schema.DocumentError):
            schema.encode(document)

    def test_invalid_sampling_and_receiving_state_are_rejected(self):
        for field, value in (('temperature', float('nan')), ('top_p', 1.5),
                             ('max_tokens', 0), ('context_size', True)):
            with self.subTest(field=field):
                document = fixture()
                document['payload']['turns'][0]['attempts'][0]['parameters'][field] = value
                with self.assertRaises(schema.DocumentError):
                    schema.encode(document)
        document = fixture()
        document['payload']['turns'][0]['attempts'][0]['status'] = 'automatically-resume'
        with self.assertRaises(schema.DocumentError):
            schema.encode(document)

    def test_reopen_marks_only_inflight_attempts_interrupted(self):
        document = fixture()
        first, second = document['payload']['turns']
        first_before = copy.deepcopy(first)
        partial = second['attempts'][0]['text']
        self.assertTrue(chat.interrupt(document['payload']))
        self.assertEqual(first, first_before)
        self.assertEqual(second['attempts'][0]['status'], 'interrupted')
        self.assertEqual(second['attempts'][0]['text'], partial)
        self.assertFalse(chat.interrupt(document['payload']))
        schema.validate(document)

    def test_clone_is_independent_and_preserves_frozen_context_relationships(self):
        original = fixture()
        before = copy.deepcopy(original)
        duplicate = identity.duplicate(original).document
        self.assertEqual(original, before)
        self.assertNotEqual(duplicate['document_id'], original['document_id'])
        first, second = duplicate['payload']['turns']
        for old, new in zip(original['payload']['turns'], duplicate['payload']['turns']):
            self.assertNotEqual(new['id'], old['id'])
            self.assertNotEqual(new['attempts'][0]['id'], old['attempts'][0]['id'])
            self.assertEqual(new['text'], old['text'])
            self.assertEqual(new['attempts'][0]['text'], old['attempts'][0]['text'])
        self.assertEqual(second['attempts'][0]['status'], 'interrupted')
        self.assertEqual(second['attempts'][0]['context'], [dict(user_id=first['id'],
                                                              attempt_id=first['attempts'][0]['id'])])
        self.assertEqual(duplicate['payload']['draft'], original['payload']['draft'])
        schema.validate(duplicate)

    def test_retry_keeps_user_once_and_does_not_adopt_partial_response(self):
        document = fixture()
        before = copy.deepcopy(document)
        payload = document['payload']
        messages = chat.context_messages(payload, payload['turns'][-1]['text'], retry=True)
        self.assertEqual([m['role'] for m in messages], ['system', 'user', 'assistant', 'user'])
        self.assertEqual([m['content'] for m in messages],
                         ['以繁體中文回答。', '第一個問題。', '第一則回答。', '繼續解釋。'])
        self.assertEqual(document, before)
        # Editing a caller's input list cannot rewrite persisted messages.
        messages[-1]['content'] = 'changed caller snapshot'
        self.assertEqual(document, before)

    def test_unfinished_history_blocks_new_turn_instead_of_silent_omission(self):
        payload = fixture()['payload']
        before = copy.deepcopy(payload)
        with self.assertRaises(ValueError):
            chat.context_messages(payload, '下一題')
        self.assertEqual(payload, before)

    def test_context_references_select_latest_terminal_answer_and_are_independent(self):
        payload = fixture()['payload']
        first = payload['turns'][0]
        first['attempts'].append(attempt('較新的完整回答。'))
        first['attempts'].append(attempt('失敗重試的片段', 'failed'))
        before = copy.deepcopy(payload)
        references = chat.context_references(payload, retry=True)
        self.assertEqual(references, [dict(user_id=first['id'], attempt_id=first['attempts'][2]['id'])])
        messages = chat.context_messages(payload, payload['turns'][-1]['text'], retry=True)
        self.assertEqual(messages[-2], dict(role='assistant', content='失敗重試的片段'))
        references[0]['attempt_id'] = uid()
        self.assertEqual(payload, before)

    def test_context_cannot_reference_missing_self_or_incomplete_answer(self):
        for defect in ('missing', 'self', 'unknown', 'incomplete'):
            with self.subTest(defect=defect):
                document = fixture()
                first, second = document['payload']['turns']
                reference = second['attempts'][0]['context'][0]
                if defect == 'missing':
                    second['attempts'][0]['context'] = []
                elif defect == 'self':
                    reference.update(user_id=second['id'], attempt_id=second['attempts'][0]['id'])
                elif defect == 'unknown':
                    reference['attempt_id'] = uid()
                else:
                    first['attempts'][0]['status'] = 'failed'
                before = copy.deepcopy(document)
                with self.assertRaises(schema.DocumentError):
                    schema.encode(document)
                self.assertEqual(document, before)

    def test_receiving_answer_must_be_the_only_active_terminal_attempt(self):
        for defect in ('earlier_turn', 'earlier_attempt', 'two_active'):
            with self.subTest(defect=defect):
                document = fixture()
                first, second = document['payload']['turns']
                if defect == 'earlier_turn':
                    first['attempts'][0]['status'] = 'receiving'
                    second['attempts'][0]['status'] = 'failed'
                elif defect == 'earlier_attempt':
                    second['attempts'].append(attempt('新的完整回答。', context=second['attempts'][0]['context']))
                else:
                    second['attempts'].append(attempt('第二個同時接收', 'receiving', context=second['attempts'][0]['context']))
                with self.assertRaises(schema.DocumentError):
                    schema.encode(document)

    def test_new_system_prompt_does_not_rewrite_frozen_attempts(self):
        document = fixture()
        payload = document['payload']
        frozen = copy.deepcopy(payload['turns'])
        payload['system_prompt'] = '下一次改用簡短回答。'
        schema.validate(document)
        messages = chat.context_messages(payload, payload['turns'][-1]['text'], retry=True)
        self.assertEqual(messages[0], dict(role='system', content='下一次改用簡短回答。'))
        self.assertEqual(payload['turns'], frozen)

    def test_export_keeps_original_attempts_and_unsent_draft(self):
        document = fixture()
        payload = document['payload']
        previous = payload['turns'][1]['attempts'][0]
        previous['status'] = 'stopped'
        payload['turns'][1]['attempts'].append(attempt('重試完成的回答。', context=previous['context']))
        before = copy.deepcopy(document)
        output = chat.markdown(payload, '測試對話')
        for text in ('第一個問題。', '第一則回答。', '尚未收完', '重試完成的回答。',
                     '尚未送出的草稿\n第二行'):
            self.assertIn(text, output)
        self.assertEqual(document, before)

    def test_chat_has_no_canvas_assets_or_external_workflow_dependencies(self):
        document = fixture()
        self.assertEqual(list(assets.references(document)), [])
        self.assertEqual(assets.external_dependencies(document), [])
        self.assertEqual(identity.provenance(document), [])


class ChatFileContracts(unittest.TestCase):
    def test_exclusive_create_readback_and_index_use_chat_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = fixture()
            result = io.create(root, '同名對話.pcs-chat', original)
            self.assertEqual(result.status, 'saved', result.error)
            path = root / '同名對話.pcs-chat'
            before = path.read_bytes()
            loaded = io.load(root, path.name)
            self.assertEqual(loaded.document, original)
            self.assertEqual(loaded.receipt, result.receipt)
            second = io.create(root, path.name, chat.empty())
            self.assertNotEqual(second.status, 'saved')
            self.assertTrue(second.name_collision)
            self.assertEqual(path.read_bytes(), before)
            inventory = index.rebuild(root)
            self.assertEqual(inventory['errors'], [])
            entry = next(e for e in inventory['entries'] if e['relative'] == path.name)
            self.assertEqual(entry['kind'], 'chat')
            self.assertEqual(entry['title'], '同名對話')

    def test_chat_cannot_be_created_with_markdown_or_canvas_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for suffix in ('.md', '.pcs-workflow', '.pcs-category'):
                with self.subTest(suffix=suffix):
                    result = io.create(root, '錯誤格式' + suffix, chat.empty())
                    self.assertNotEqual(result.status, 'saved')
                    self.assertFalse((root / ('錯誤格式' + suffix)).exists())

    @unittest.skipUnless(os.name == 'nt', 'Existing protected replace writer is Windows-only')
    def test_save_reopen_and_external_conflict_preserve_both_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = io.create(root, '對話.pcs-chat', chat.empty())
            self.assertEqual(result.status, 'saved', result.error)
            candidate = copy.deepcopy(result.document)
            candidate['payload']['draft'] = '新草稿'
            saved = io.save(root, '對話.pcs-chat', candidate, result.receipt)
            self.assertEqual(saved.status, 'saved', saved.error)
            reopened = io.load(root, '對話.pcs-chat')
            self.assertEqual(reopened.document['payload']['draft'], '新草稿')
            self.assertNotEqual(reopened.receipt.revision, result.receipt.revision)
            external = copy.deepcopy(reopened.document)
            external['payload']['draft'] = '外部修改'
            path = root / '對話.pcs-chat'
            path.write_bytes(schema.encode(external))
            external_bytes = path.read_bytes()
            pending = copy.deepcopy(reopened.document)
            pending['payload']['draft'] = '本機尚未保存'
            conflict = io.save(root, path.name, pending, reopened.receipt)
            self.assertEqual(conflict.status, 'conflict')
            self.assertEqual(path.read_bytes(), external_bytes)
            self.assertEqual(pending['payload']['draft'], '本機尚未保存')
            self.assertTrue(conflict.recovery_paths)


if __name__ == '__main__':
    unittest.main()
