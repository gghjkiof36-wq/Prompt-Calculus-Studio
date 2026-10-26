"""Bounded owner regression cases with a fake service and temporary files.

These tests do not start an engine, worker, network service, or model. The fake
writer exercises owner acceptance/readback behavior, not Windows writer races;
the real writer contracts live in test_local_chat_contracts.py. Authored only:
AST parsing this file is not evidence that the cases have run or passed.
"""
import copy
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from prompt_calculus_studio.local_ai.chat_document import ChatDocument
from prompt_calculus_studio.local_documents import chat, identity, io, schema
from prompt_calculus_studio.service_adapters import ServiceRequest


class FakeService(QObject):
    changed = Signal()
    delta = Signal(object, str)
    finished = Signal(object, str, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.state, self.error = 'ready', ''
        self.selected_model = str(uuid.uuid4())
        self.settings = dict(context_size=4096, max_tokens=512, temperature=.7,
                             top_p=.9, seed=42, gpu_layers=0, idle_minutes=10,
                             runtime_path='synthetic-unused-runtime.exe')
        self.submitted, self.cancelled, self.persisted_at_submit = [], [], []
        self.read_document = None
        self.prepare_on_submit = False

    @property
    def can_submit(self):
        return bool(self.selected_model and self.state not in
                    ('loading', 'generating', 'stopping', 'unloading'))

    def settings_for_model(self, model_id):
        return dict(self.settings)

    def model_preset(self, model_id):
        return dict(system_prompt='')

    def submit(self, request):
        # Observation at acceptance proves that the frozen attempt was already
        # published, rather than only present in the in-memory document.
        self.persisted_at_submit.append(self.read_document())
        self.submitted.append(request)
        self.state = 'loading' if self.prepare_on_submit else 'generating'
        return True

    def cancel(self, request_id):
        self.cancelled.append(request_id)
        self.state = 'ready'
        return True

    def complete(self, request, text):
        self.state = 'ready'
        self.finished.emit(request, 'completed', text)


class FakeWorkspace(QObject):
    def __init__(self, root):
        super().__init__()
        self.data = root / 'runtime'
        self.data.mkdir()
        self.local_ai = FakeService(self)
        self.autosave = SimpleNamespace(paused=set())
        self.pages, self.notices = [], []

    def all_pages(self):
        return list(self.pages)

    def show_notice(self, message):
        self.notices.append(message)


class FakeView:
    composing = False
    has_uncommitted_draft = False


class LocalChatOwnerContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {'PROMPT_STUDIO_DATA': str(self.root / 'runtime')})
        self.environment.start()
        self.path = self.root / '合成對話.pcs-chat'
        created = io.create(self.root, self.path.name, chat.empty())
        self.assertEqual(created.status, 'saved', created.error)
        self.workspace = FakeWorkspace(self.root)
        self.owner = ChatDocument(self.path, self.workspace)
        self.service = self.workspace.local_ai
        self.service.read_document = lambda: copy.deepcopy(io.load(self.owner.path.parent, self.owner.path.name).document)
        self.page = SimpleNamespace(document=self.owner)
        self.workspace.pages.append(self.page)
        self.saved, self.fail_saves = [], False
        self.writer = patch('prompt_calculus_studio.local_ai.chat_document.io.save', side_effect=self._save)
        self.writer.start()

    def tearDown(self):
        try:
            self.owner.dispose()
            delete(self.workspace)
        finally:
            self.writer.stop()
            self.environment.stop()
            self.temporary.cleanup()

    def _save(self, root, relative, candidate, expected):
        if self.fail_saves:
            return io.SaveResult('failed', error='synthetic write failure')
        path = Path(root) / relative
        before = io.load(root, relative)
        if before.receipt != expected:
            return io.SaveResult('conflict', error='synthetic external conflict')
        committed = copy.deepcopy(candidate)
        committed['revision'] = dict(id=str(uuid.uuid4()), parent=expected.revision)
        path.write_bytes(schema.encode(committed))
        loaded = io.load(root, relative)
        self.saved.append(copy.deepcopy(loaded.document))
        return io.SaveResult('saved', loaded.document, loaded.receipt)

    def _send(self, text='問題一'):
        self.owner.set_draft(text)
        self.assertTrue(self.owner.send(text), self.owner.notice or self.owner.error)
        return self.service.submitted[-1]

    def test_send_persists_frozen_attempt_before_service_accepts(self):
        self.owner.set_options({'system_prompt': '先說結論。'})
        request = self._send()
        persisted = self.service.persisted_at_submit[-1]
        turn = persisted['payload']['turns'][-1]
        attempt = turn['attempts'][-1]
        self.assertEqual(persisted['document_id'], self.owner.key)
        self.assertEqual(attempt['request'], request.request_id)
        self.assertEqual(attempt['status'], 'receiving')
        self.assertEqual(attempt['system_prompt'], '先說結論。')
        self.assertEqual(attempt['parameters']['temperature'], .7)
        self.assertEqual(persisted['payload']['draft'], '')
        self.assertEqual(request.scope['attempt_id'], attempt['id'])
        self.assertEqual(request.scope['user_message_id'], turn['id'])

    def test_unloaded_send_reserves_owner_and_stop_revokes_preparation(self):
        self.service.state = 'unloaded'
        self.service.prepare_on_submit = True
        request = self._send()
        self.assertTrue(self.owner.busy)
        self.assertEqual(self.service.state, 'loading')
        self.assertFalse(self.owner.send('重複點擊'))
        self.assertEqual(len(self.service.submitted), 1)
        self.assertEqual(self.service.persisted_at_submit[0]['payload']['turns'][0]['attempts'][0]['status'],
                         'receiving')
        self.assertTrue(self.owner.stop())
        before = self.path.read_bytes()
        self.service.delta.emit(request, '準備取消後的晚到文字')
        self.service.finished.emit(request, 'completed', '準備取消後的晚到文字')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.owner.payload['turns'][0]['attempts'][0]['status'], 'stopped')

    def test_preparation_failure_is_saved_on_its_original_attempt(self):
        self.service.state = 'unloaded'
        self.service.prepare_on_submit = True
        request = self._send()
        self.service.state = 'error'
        self.service.finished.emit(request, 'error', '合成模型準備失敗')
        self.assertFalse(self.owner.busy)
        payload = io.load(self.root, self.path.name).document['payload']
        attempt = payload['turns'][0]['attempts'][0]
        self.assertEqual(attempt['request'], request.request_id)
        self.assertEqual((attempt['status'], attempt['reason']), ('failed', '合成模型準備失敗'))
        self.assertEqual(attempt['text'], '')

    def test_dispose_during_preparation_cancels_and_disconnects_late_output(self):
        self.service.state = 'unloaded'
        self.service.prepare_on_submit = True
        request = self._send()
        self.owner.dispose()
        before = self.path.read_bytes()
        self.assertEqual(self.service.cancelled, [request.request_id])
        self.service.delta.emit(request, '關閉後的文字')
        self.service.finished.emit(request, 'completed', '關閉後的文字')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.owner.busy)

    def test_reentrant_send_during_save_cannot_duplicate_a_turn(self):
        attempts = []
        self.owner.editing.connect(lambda: attempts.append(self.owner.send('重入訊息')) if self.owner.busy else None)
        self._send()
        self.assertEqual(attempts, [False])
        self.assertEqual(len(self.owner.payload['turns']), 1)
        self.assertEqual(len(self.service.submitted), 1)

    def test_stop_during_save_signal_prevents_later_service_submission(self):
        revoked = []

        def revoke():
            if self.owner.busy and not revoked:
                revoked.append(True)
                self.owner.stop()

        self.owner.changed.connect(revoke)
        self.assertFalse(self.owner.send('保存期間取消'))
        self.assertEqual(self.service.submitted, [])
        self.assertFalse(self.owner.busy)
        self.assertEqual(io.load(self.root, self.path.name).document['payload']['turns'][0]['attempts'][0]['status'],
                         'stopped')

    def test_wrong_full_request_is_ignored_even_when_request_id_matches(self):
        request = self._send()
        for defect in ('document', 'epoch', 'input', 'request_id'):
            with self.subTest(defect=defect):
                scope, inputs = request.scope, request.input
                request_id = request.request_id
                if defect == 'document':
                    scope['document_id'] = str(uuid.uuid4())
                elif defect == 'epoch':
                    scope['owner_epoch'] = str(uuid.uuid4())
                elif defect == 'input':
                    inputs['messages'][-1]['content'] = '另一份輸入'
                else:
                    request_id = str(uuid.uuid4())
                wrong = ServiceRequest('local_ai', 'chat', request_id=request_id, scope=scope, input=inputs)
                before = copy.deepcopy(self.owner.payload)
                self.service.delta.emit(wrong, '錯誤回覆')
                self.service.finished.emit(wrong, 'completed', '錯誤回覆')
                self.assertEqual(self.owner.payload, before)
                self.assertTrue(self.owner.busy)
        self.service.delta.emit(request, '正確片段')
        self.assertEqual(self.owner.payload['turns'][-1]['attempts'][-1]['text'], '正確片段')

    def test_stop_persists_partial_and_ignores_late_delta_and_terminal(self):
        request = self._send()
        self.service.delta.emit(request, '已收到的內容')
        self.assertTrue(self.owner.stop())
        attempt = self.owner.payload['turns'][-1]['attempts'][-1]
        self.assertEqual((attempt['status'], attempt['text']), ('stopped', '已收到的內容'))
        self.assertEqual(self.service.cancelled, [request.request_id])
        self.assertFalse(self.owner.busy)
        before = self.path.read_bytes()
        self.service.delta.emit(request, '晚到片段')
        self.service.finished.emit(request, 'completed', '已收到的內容晚到片段')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(io.load(self.root, self.path.name).document['payload'], self.owner.payload)
        self.assertFalse(self.owner.stop())

    def test_retry_keeps_user_old_attempt_and_frozen_request_snapshots(self):
        self.owner.set_options({'system_prompt': '原指示'})
        first_request = self._send('第一問')
        self.service.complete(first_request, '第一答')
        previous_request = self._send('第二問')
        self.service.complete(previous_request, '舊的第二答')
        old_turn = copy.deepcopy(self.owner.payload['turns'][-1])
        previous_input = previous_request.input
        self.owner.set_draft('尚未送出的下一問')
        self.owner.set_options({'system_prompt': '新指示'})
        self.service.settings['temperature'] = .2
        self.assertTrue(self.owner.retry())
        request = self.service.submitted[-1]
        turn = self.owner.payload['turns'][-1]
        self.assertEqual(len(self.owner.payload['turns']), 2)
        self.assertEqual(turn['id'], old_turn['id'])
        self.assertEqual(turn['text'], old_turn['text'])
        self.assertEqual(turn['attempts'][0], old_turn['attempts'][0])
        self.assertEqual(len(turn['attempts']), 2)
        self.assertEqual(self.owner.payload['draft'], '尚未送出的下一問')
        self.assertEqual(previous_request.input, previous_input)
        self.assertEqual(request.input['messages'], [dict(role='system', content='新指示'),
            dict(role='user', content='第一問'), dict(role='assistant', content='第一答'),
            dict(role='user', content='第二問')])
        self.assertEqual(turn['attempts'][0]['parameters']['temperature'], .7)
        self.assertEqual(turn['attempts'][1]['parameters']['temperature'], .2)
        self.service.delta.emit(previous_request, '舊請求晚到')
        self.assertEqual(turn['attempts'][1]['text'], '')
        self.service.complete(request, '新的第二答')
        self.assertEqual(io.load(self.root, self.path.name).document['payload'], self.owner.payload)

    def test_failed_save_does_not_submit_or_erase_user_turn(self):
        self.owner.set_draft('不能丟失的問題')
        before = self.path.read_bytes()
        self.fail_saves = True
        self.assertFalse(self.owner.send('不能丟失的問題'))
        self.assertEqual(self.service.submitted, [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.owner.dirty)
        self.assertFalse(self.owner.busy)
        turn = self.owner.payload['turns'][-1]
        self.assertEqual(turn['text'], '不能丟失的問題')
        self.assertEqual(turn['attempts'][-1]['status'], 'failed')
        self.assertIn('synthetic write failure', self.owner.error)

    def test_retarget_rejects_old_request_while_new_attempt_is_active(self):
        self.service.state = 'unloaded'
        self.service.prepare_on_submit = True
        request = self._send()
        self.service.delta.emit(request, '舊文件片段')
        self.owner.stop()
        old_key, old_epoch = self.owner.key, self.owner.owner_epoch
        original = self.path.read_bytes()
        target = self.root / '獨立副本.pcs-chat'
        result = io.create(self.root, target.name, identity.duplicate(self.owner.project()).document)
        self.assertEqual(result.status, 'saved', result.error)
        self.owner.retarget(result, target)
        self.assertNotEqual(self.owner.key, old_key)
        self.assertNotEqual(self.owner.owner_epoch, old_epoch)
        self.assertTrue(self.owner.retry())
        current = self.service.submitted[-1]
        before = copy.deepcopy(self.owner.payload)
        self.service.delta.emit(request, '晚到舊片段')
        self.service.finished.emit(request, 'completed', '舊文件片段晚到舊片段')
        self.assertEqual(self.owner.payload, before)
        self.assertEqual(self.path.read_bytes(), original)
        self.service.complete(current, '新文件回覆')
        self.assertEqual(io.load(self.root, target.name).document['payload'], self.owner.payload)

    def test_preedit_and_unaccepted_draft_block_save_and_send(self):
        view = FakeView()
        self.owner.views.add(view)
        self.owner.set_draft('已提交的草稿')
        before = self.path.read_bytes()
        for flag in ('composing', 'has_uncommitted_draft'):
            with self.subTest(flag=flag):
                setattr(view, flag, True)
                self.assertFalse(self.owner.flush_views())
                self.assertFalse(self.owner.save())
                self.assertFalse(self.owner.send('不應送出'))
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.service.submitted, [])
                setattr(view, flag, False)
        self.assertTrue(self.owner.save())
        self.assertEqual(io.load(self.root, self.path.name).document['payload']['draft'], '已提交的草稿')

    def test_paused_file_operation_retains_terminal_text_until_writer_resumes(self):
        request = self._send()
        before = self.path.read_bytes()
        self.workspace.autosave.paused.add(id(self.page))
        self.service.complete(request, '暫停保存期間完成的回覆')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.owner.dirty)
        self.assertFalse(self.owner.busy)
        self.assertEqual(self.owner.payload['turns'][-1]['attempts'][-1]['status'], 'complete')
        self.workspace.autosave.paused.clear()
        self.assertTrue(self.owner.save())
        self.assertEqual(io.load(self.root, self.path.name).document['payload'], self.owner.payload)

    def test_terminal_save_failure_keeps_complete_candidate_for_recovery(self):
        request = self._send()
        self.service.delta.emit(request, '已收到')
        self.fail_saves = True
        self.service.complete(request, '已收到完整回覆')
        self.assertFalse(self.owner.busy)
        self.assertTrue(self.owner.dirty)
        self.assertIn('synthetic write failure', self.owner.error)
        recovered = self.owner.project()
        attempt = recovered['payload']['turns'][-1]['attempts'][-1]
        self.assertEqual((attempt['status'], attempt['text']), ('complete', '已收到完整回覆'))
        self.assertEqual(io.load(self.root, self.path.name).document['payload']['turns'][-1]['attempts'][-1]['status'],
                         'receiving')
        self.fail_saves = False
        self.assertTrue(self.owner.save())
        self.assertEqual(io.load(self.root, self.path.name).document['payload'], recovered['payload'])

    def test_recovery_checks_both_document_identity_and_source_readback(self):
        self.owner.set_draft('目前編輯')
        before = copy.deepcopy(self.owner.payload)
        candidate = self.owner.project()
        candidate['payload']['draft'] = '恢復副本的編輯'
        foreign = copy.deepcopy(candidate)
        foreign['document_id'] = str(uuid.uuid4())
        self.assertFalse(self.owner.restore_recovery(dict(document=foreign, sha=self.owner.receipt.sha256)))
        self.assertEqual(self.owner.payload, before)
        self.assertFalse(self.owner.restore_recovery(dict(document=candidate, sha='0' * 64)))
        self.assertEqual(self.owner.payload, before)
        self.assertTrue(self.owner.restore_recovery(dict(document=candidate, sha=self.owner.receipt.sha256)))
        self.assertEqual(self.owner.payload['draft'], '恢復副本的編輯')
        self.assertTrue(self.owner.dirty)


if __name__ == '__main__':
    unittest.main()
