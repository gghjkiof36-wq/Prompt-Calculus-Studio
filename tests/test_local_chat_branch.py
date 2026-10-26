"""Independent chat branch snapshots and owner isolation; no model or network.

New files in owner tests use explicit synthetic fixture writes. Workspace-level
exclusive create/name collision/open tests cover the actual branch publication.
"""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from prompt_calculus_studio.local_ai.chat_document import ChatDocument
from prompt_calculus_studio.local_documents import chat, io, schema
from test_local_chat_contracts import attempt, uid
from test_local_chat_owner import FakeWorkspace


RAW = '<think>\r\n條件🙂  \r\n</think>\n\n**回答**\n```py\nx = "\\n"\n```\n'


def conversation():
    document = chat.empty()
    first = dict(id=uid(), text='第一題\n保留格式', attempts=[attempt(RAW)])
    first_ref = dict(user_id=first['id'], attempt_id=first['attempts'][0]['id'])
    second = dict(id=uid(), text='第二題', attempts=[
        attempt('較早的完整回覆', context=[first_ref]),
        attempt('未完成的重試', 'failed', context=[first_ref]),
        attempt('最後的完整回覆', context=[first_ref])])
    second_ref = dict(user_id=second['id'], attempt_id=second['attempts'][-1]['id'])
    third = dict(id=uid(), text='第三題', attempts=[attempt('後面的內容', context=[first_ref, second_ref])])
    document['payload'].update(draft='只留在原對話的草稿', system_prompt='  原對話指示\n', turns=[first, second, third])
    document['extensions']['test.retained'] = dict(note='保留既有可選資料')
    return schema.validate(document)


class BranchPrefixContracts(unittest.TestCase):
    def test_user_cut_keeps_question_without_answers_or_source_draft(self):
        source = conversation()['payload']; before = copy.deepcopy(source)
        result = chat.branch_payload(source, source['turns'][1]['id'])
        self.assertEqual(source, before)
        self.assertEqual(len(result['turns']), 2)
        self.assertEqual(result['turns'][0], source['turns'][0])
        self.assertEqual(result['turns'][1]['text'], '第二題')
        self.assertEqual(result['turns'][1]['attempts'], [])
        self.assertEqual(result['draft'], '')
        self.assertEqual(result['system_prompt'], source['system_prompt'])
        self.assertEqual(chat.context_messages(result, '第二題', retry=True)[-1]['content'], '第二題')

    def test_assistant_cut_uses_exact_historical_attempt_and_excludes_later_retry(self):
        source = conversation()['payload']; selected = source['turns'][1]
        result = chat.branch_payload(source, selected['id'], selected['attempts'][0]['id'])
        self.assertEqual(len(result['turns']), 2)
        self.assertEqual(result['turns'][1]['attempts'], selected['attempts'][:1])
        messages = chat.context_messages(result, '另一個方向')
        self.assertEqual(messages[-2]['content'], '較早的完整回覆')
        self.assertEqual(result['turns'][0]['attempts'][0]['text'], RAW)

    def test_failure_cut_preserves_failure_as_failure(self):
        source = conversation()['payload']; selected = source['turns'][1]
        result = chat.branch_payload(source, selected['id'], selected['attempts'][1]['id'])
        self.assertEqual([a['status'] for a in result['turns'][-1]['attempts']], ['complete', 'failed'])
        self.assertEqual(result['turns'][-1]['attempts'][-1]['text'], '未完成的重試')

    def test_stale_or_cross_turn_message_identity_never_mutates_source(self):
        source = conversation()['payload']; before = copy.deepcopy(source)
        for turn, response in ((uid(), None), (source['turns'][0]['id'], uid()),
                               (source['turns'][0]['id'], source['turns'][1]['attempts'][0]['id']),
                               (source['turns'][0]['id'], '')):
            with self.subTest(turn=turn, response=response), self.assertRaises(ValueError):
                chat.branch_payload(source, turn, response)
            self.assertEqual(source, before)


class BranchOwnerContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / 'source.pcs-chat'
        self.source_path.write_bytes(schema.encode(conversation()))
        self.workspace = FakeWorkspace(self.root)
        self.source = ChatDocument(self.source_path, self.workspace)
        self.service = self.workspace.local_ai
        self.service.read_document = lambda: io.load(self.source.path.parent, self.source.path.name).document
        self.branches = []
        self.saved_paths = []

        def save(root, relative, candidate, expected):
            current = io.load(root, relative)
            if current.receipt != expected:
                return io.SaveResult('conflict', error='synthetic conflict')
            committed = copy.deepcopy(candidate)
            committed['revision'] = dict(id=uid(), parent=expected.revision)
            path = Path(root) / relative
            path.write_bytes(schema.encode(committed)); self.saved_paths.append(path)
            loaded = io.load(root, relative)
            return io.SaveResult('saved', loaded.document, loaded.receipt)

        self.writer = patch('prompt_calculus_studio.local_ai.chat_document.io.save', side_effect=save)
        self.writer.start()

    def tearDown(self):
        for branch in self.branches:
            branch.dispose()
        self.source.dispose()
        delete(self.workspace)
        self.writer.stop()
        self.temp.cleanup()

    def open_candidate_fixture(self, candidate):
        path = self.root / f'branch-{len(self.branches)}.pcs-chat'
        path.write_bytes(schema.encode(candidate))
        owner = ChatDocument(path, self.workspace)
        self.branches.append(owner)
        return owner

    def test_branch_has_independent_identity_context_maps_and_source_trace(self):
        snapshot = self.source.project(); before = self.source_path.read_bytes()
        selected = snapshot['payload']['turns'][1]
        candidate = self.source.branch_document(selected['id'], selected['attempts'][0]['id'])
        self.assertEqual(self.source_path.read_bytes(), before)
        self.assertEqual(self.source.project(), snapshot)
        self.assertEqual(self.saved_paths, [])
        self.assertEqual(self.service.submitted, [])
        self.assertNotEqual(candidate['document_id'], snapshot['document_id'])
        self.assertNotEqual(candidate['revision']['id'], snapshot['revision']['id'])
        self.assertIsNone(candidate['revision']['parent'])
        self.assertEqual(candidate['required_features'], snapshot['required_features'])
        self.assertEqual(candidate['extensions']['test.retained'], snapshot['extensions']['test.retained'])
        source_ids = {t['id'] for t in snapshot['payload']['turns']}
        source_ids.update(a[key] for t in snapshot['payload']['turns'] for a in t['attempts'] for key in ('id','request'))
        for original, copied in zip(snapshot['payload']['turns'], candidate['payload']['turns']):
            self.assertNotIn(copied['id'], source_ids)
            self.assertEqual(copied['text'], original['text'])
            for old, new in zip(original['attempts'], copied['attempts']):
                self.assertNotIn(new['id'], source_ids); self.assertNotIn(new['request'], source_ids)
                for key in ('model','model_name','profile_epoch','parameters','system_prompt','text','status','reason'):
                    self.assertEqual(new[key], old[key])
        first, second = candidate['payload']['turns']
        self.assertEqual(second['attempts'][0]['context'], [dict(user_id=first['id'], attempt_id=first['attempts'][0]['id'])])
        trace = candidate['extensions']['pcs.chat_branch']
        self.assertEqual(trace['source_document_id'], self.source.key)
        self.assertEqual(trace['source_saved_revision'], snapshot['revision']['id'])
        self.assertEqual((trace['turn_id'], trace['attempt_id']),
                         (selected['id'], selected['attempts'][0]['id']))
        branch = self.open_candidate_fixture(candidate)
        self.assertNotEqual(branch.owner_epoch, self.source.owner_epoch)
        self.assertFalse(branch.busy)
        self.assertEqual(branch.payload['turns'][0]['attempts'][0]['text'], RAW)
        self.assertEqual(io.load(branch.path.parent, branch.path.name).document, candidate)

    def test_source_stream_continues_but_branch_receives_no_late_source_events(self):
        self.assertTrue(self.source.send('正在產生的第四題'))
        request = self.service.submitted[-1]
        partial = '<think>尚未完成\n🙂'
        self.service.delta.emit(request, partial)
        active = self.source.payload['turns'][-1]
        source_file = self.source_path.read_bytes(); source_epoch = self.source.owner_epoch
        candidate = self.source.branch_document(active['id'], active['attempts'][-1]['id'])
        self.assertTrue(self.source.busy)
        self.assertEqual(self.source.owner_epoch, source_epoch)
        self.assertEqual(self.service.cancelled, [])
        self.assertEqual(self.source_path.read_bytes(), source_file)
        copied = candidate['payload']['turns'][-1]['attempts'][-1]
        self.assertEqual(copied['status'], 'interrupted'); self.assertEqual(copied['text'], partial)
        branch = self.open_candidate_fixture(candidate)
        branch_before = copy.deepcopy(branch.payload); branch_file = branch.path.read_bytes()
        self.service.delta.emit(request, '來源晚到內容')
        self.service.complete(request, partial + '來源晚到內容')
        self.assertEqual(branch.payload, branch_before)
        self.assertEqual(branch.path.read_bytes(), branch_file)
        self.assertEqual(self.source.payload['turns'][-1]['attempts'][-1]['text'], partial + '來源晚到內容')
        self.assertEqual(self.source.payload['turns'][-1]['attempts'][-1]['status'], 'complete')
        self.assertFalse(branch.busy)

    def test_user_branch_explicit_retry_has_its_own_request_owner(self):
        selected = self.source.payload['turns'][1]
        branch = self.open_candidate_fixture(self.source.branch_document(selected['id']))
        source_before = self.source_path.read_bytes()
        self.assertEqual(branch.payload['turns'][-1]['attempts'], [])
        self.assertEqual(self.service.submitted, [])
        self.assertTrue(branch.retry())
        request = self.service.submitted[-1]
        self.assertEqual(request.scope['document_id'], branch.key)
        self.assertEqual(request.scope['owner_epoch'], branch.owner_epoch)
        self.assertNotEqual(request.scope['document_id'], self.source.key)
        self.assertEqual(request.input['messages'][-1]['content'], selected['text'])
        self.assertEqual(request.input['model'], self.service.selected_model)
        self.assertEqual(request.input['params'], self.service.settings)
        self.service.complete(request, '分支的新回覆')
        self.assertEqual(self.source_path.read_bytes(), source_before)
        self.assertEqual(len(branch.payload['turns']), 2)
        self.assertEqual(len(branch.payload['turns'][-1]['attempts']), 1)
        self.assertEqual(io.load(branch.path.parent, branch.path.name).document['payload'], branch.payload)

    def test_two_branches_and_source_cannot_share_accepted_request(self):
        target = self.source.payload['turns'][0]
        first = self.open_candidate_fixture(self.source.branch_document(target['id'], target['attempts'][0]['id']))
        second = self.open_candidate_fixture(self.source.branch_document(target['id'], target['attempts'][0]['id']))
        self.assertNotEqual(first.key, second.key)
        self.assertNotEqual(first.payload['turns'][0]['id'], second.payload['turns'][0]['id'])
        second_before, source_before = copy.deepcopy(second.payload), copy.deepcopy(self.source.payload)
        self.assertTrue(first.send('只在第一分支的下一題'))
        request = self.service.submitted[-1]
        self.service.delta.emit(request, '新分支串流')
        self.service.complete(request, '新分支串流')
        self.assertEqual(second.payload, second_before)
        self.assertEqual(self.source.payload, source_before)

    def test_snapshot_uses_current_valid_owner_and_never_saves_source_draft(self):
        target = self.source.payload['turns'][0]
        before = self.source_path.read_bytes()
        self.source.set_draft('尚未落盤的個人草稿')
        self.source.set_options(dict(system_prompt='新的未保存文件指示'))
        snapshot = self.source.project()
        candidate = self.source.branch_document(target['id'], target['attempts'][0]['id'])
        self.assertEqual(candidate['payload']['draft'], '')
        self.assertEqual(candidate['payload']['system_prompt'], '新的未保存文件指示')
        self.assertEqual(candidate['extensions']['pcs.chat_branch']['source_saved_revision'],
                         snapshot['revision']['id'])
        self.assertEqual(self.source.payload['draft'], '尚未落盤的個人草稿')
        self.assertTrue(self.source.dirty)
        self.assertEqual(self.source_path.read_bytes(), before)
        self.assertEqual(self.saved_paths, [])

    def test_pending_input_closed_owner_and_stale_ids_fail_without_side_effects(self):
        target = self.source.payload['turns'][0]
        before = copy.deepcopy(self.source.payload)
        with patch.object(self.source, 'flush_views', return_value=False), self.assertRaises(ValueError):
            self.source.branch_document(target['id'])
        with self.assertRaises(ValueError):
            self.source.branch_document(uid())
        self.source.dispose()
        with self.assertRaises(ValueError):
            self.source.branch_document(target['id'])
        self.assertEqual(self.source.payload, before)
        self.assertEqual(self.saved_paths, [])
        self.assertEqual(self.service.submitted, [])


if __name__ == '__main__':
    unittest.main()
