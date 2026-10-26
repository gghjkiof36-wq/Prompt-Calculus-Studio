"""Same-file edits, terminal continuation and immutable retry snapshots.

Synthetic files/fake inference only. The normal owner uses the existing CAS
writer; the owner fixture below makes publication deterministic and observable.
"""
import copy
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from prompt_calculus_studio.local_ai.chat_document import ChatDocument
from prompt_calculus_studio.local_documents import chat, identity, io, schema
from test_local_chat_branch import conversation, RAW
from test_local_chat_contracts import attempt, uid
from test_local_chat_owner import FakeView, FakeWorkspace


class RevisionSchemaContracts(unittest.TestCase):
    def revision_document(self):
        value = conversation()
        chat.archive_revision(value['payload'], reason='edit', turn_id=value['payload']['turns'][1]['id'])
        chat.sync_features(value)
        return value

    def test_required_feature_roundtrip_and_closed_validation(self):
        value = self.revision_document()
        self.assertEqual(schema.decode(schema.encode(value)), value)
        self.assertIn(chat.REVISION_FEATURE, value['required_features'])
        for change in ('unmarked', 'unknown', 'receiving', 'bad_point', 'duplicate_revision'):
            with self.subTest(change=change):
                broken = copy.deepcopy(value)
                revision = broken['payload']['revisions'][0]
                if change == 'unmarked':
                    broken['required_features'].remove(chat.REVISION_FEATURE)
                elif change == 'unknown':
                    broken['required_features'].append('pcs.unknown.future')
                elif change == 'receiving':
                    revision['turns'][-1]['attempts'][-1]['status'] = 'receiving'
                elif change == 'bad_point':
                    revision['turn_id'] = uid()
                else:
                    broken['payload']['revisions'].append(copy.deepcopy(revision))
                with self.assertRaises(schema.DocumentError):
                    schema.encode(broken)

    def test_partial_and_empty_answer_refs_require_feature_and_keep_raw(self):
        value = chat.empty(); payload = value['payload']
        partial = dict(id=uid(), text='原問題', attempts=[attempt(RAW, 'stopped')])
        blank = dict(id=uid(), text='尚無回覆', attempts=[])
        payload['turns'] = [partial, blank]
        refs = chat.context_references(payload)
        self.assertEqual(refs, [dict(user_id=partial['id'], attempt_id=partial['attempts'][0]['id']),
                                dict(user_id=blank['id'], attempt_id='')])
        payload['turns'].append(dict(id=uid(), text='接著問', attempts=[attempt('回答', context=refs)]))
        with self.assertRaises(schema.DocumentError):
            schema.encode(value)
        payload['revisions'] = []; chat.sync_features(value)
        schema.encode(value)
        messages = chat.context_messages(dict(payload, turns=payload['turns'][:2]), '接著問')
        self.assertEqual([m['role'] for m in messages], ['user', 'assistant', 'user', 'user'])
        self.assertEqual(messages[1]['content'], RAW)
        copy_value = identity.duplicate(value).document
        schema.validate(copy_value)
        self.assertEqual(copy_value['payload']['turns'][-1]['attempts'][0]['context'][1]['attempt_id'], '')

    def test_clone_branch_export_preserve_every_archived_text_and_rekey(self):
        value = self.revision_document(); before = copy.deepcopy(value)
        cloned = identity.duplicate(value).document
        old, new = value['payload']['revisions'][0], cloned['payload']['revisions'][0]
        self.assertNotEqual(old['id'], new['id'])
        self.assertNotEqual(old['turn_id'], new['turn_id'])
        self.assertEqual(new['turn_id'], new['turns'][1]['id'])
        self.assertEqual(old['turns'][0]['attempts'][0]['text'], new['turns'][0]['attempts'][0]['text'])
        prefix = chat.branch_payload(value['payload'], value['payload']['turns'][0]['id'], value['payload']['turns'][0]['attempts'][0]['id'])
        self.assertEqual(prefix['revisions'], value['payload']['revisions'])
        exported = chat.markdown(cloned['payload'], '合成對話')
        self.assertIn('編輯紀錄 1', exported)
        self.assertIn(RAW, exported)
        self.assertEqual(value, before)

    def test_attachment_only_in_archived_path_keeps_required_feature_and_fresh_clone_ids(self):
        from test_local_chat_attachments import text_item, image_item
        value = conversation(); source = value['payload']['turns'][-1]
        source['attachments'] = [text_item(), image_item()]
        chat.archive_revision(value['payload'], reason='edit', turn_id=source['id'])
        value['payload']['turns'] = value['payload']['turns'][:1]
        chat.sync_features(value)
        self.assertIn('pcs.chat-attachments.v1', value['required_features'])
        cloned = identity.duplicate(schema.decode(schema.encode(value))).document
        old = value['payload']['revisions'][0]['turns'][-1]['attachments']
        new = cloned['payload']['revisions'][0]['turns'][-1]['attachments']
        for a, b in zip(old, new):
            self.assertNotEqual(a['id'], b['id'])
            self.assertEqual(a['sha256'], b['sha256'])
        self.assertIn(old[1]['data_uri'], chat.markdown(cloned['payload'], '附件紀錄'))


class RevisionOwnerContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name); self.path = self.root / 'edits.pcs-chat'
        self.path.write_bytes(schema.encode(conversation()))
        self.workspace = FakeWorkspace(self.root)
        self.owner = ChatDocument(self.path, self.workspace)
        self.service = self.workspace.local_ai
        self.service.read_document = lambda: io.load(self.path.parent, self.path.name).document
        self.workspace.pages = [SimpleNamespace(document=self.owner)]
        self.failure = None; self.writes = []

        def save(root, relative, candidate, expected):
            if self.failure:
                return io.SaveResult(self.failure, error='synthetic ' + self.failure, recovery_paths=['retained-candidate'])
            loaded = io.load(root, relative)
            if loaded.receipt != expected:
                return io.SaveResult('conflict', error='synthetic external conflict')
            committed = copy.deepcopy(candidate)
            committed['revision'] = dict(id=uid(), parent=expected.revision)
            (Path(root) / relative).write_bytes(schema.encode(committed))
            result = io.load(root, relative); self.writes.append(copy.deepcopy(committed))
            return io.SaveResult('saved', result.document, result.receipt)

        self.writer = patch('prompt_calculus_studio.local_ai.chat_document.io.save', side_effect=save)
        self.writer.start()

    def tearDown(self):
        self.owner.dispose(); delete(self.workspace)
        self.writer.stop(); self.temp.cleanup()

    def test_edit_middle_archives_following_turns_and_saves_before_submit(self):
        before = copy.deepcopy(self.owner.payload); key = self.owner.key
        turn_id = before['turns'][1]['id']
        self.assertTrue(self.owner.edit_and_resend(turn_id, '修改後的問題🙂'))
        self.assertEqual(self.owner.key, key)
        self.assertEqual(len(self.owner.payload['turns']), 2)
        self.assertEqual(self.owner.payload['turns'][-1]['text'], '修改後的問題🙂')
        self.assertNotEqual(self.owner.payload['turns'][-1]['id'], turn_id)
        self.assertEqual(self.owner.payload['revisions'][0]['turns'], before['turns'])
        self.assertEqual(self.owner.payload['draft'], before['draft'])
        self.assertEqual(self.service.persisted_at_submit[-1]['payload'], self.owner.payload)
        request = self.service.submitted[-1]
        self.assertEqual(request.input['messages'][-1]['content'], '修改後的問題🙂')
        self.assertEqual(len(request.input['messages']), 4)
        self.service.complete(request, '**新回覆**')
        reopened = io.load(self.root, self.path.name).document
        self.assertEqual(reopened['payload'], self.owner.payload)
        self.assertIn(RAW, chat.markdown(reopened['payload'], '測試'))

    def test_restore_roundtrip_archives_current_path_and_preserves_draft(self):
        before = copy.deepcopy(self.owner.payload)
        self.assertTrue(self.owner.edit_and_resend(before['turns'][0]['id'], '改第一問'))
        self.service.complete(self.service.submitted[-1], '新版回答')
        edited = copy.deepcopy(self.owner.payload['turns'])
        self.owner.set_draft('正在打的下一問，不可遺失')
        revision_id = self.owner.edit_revisions[0]['id']
        self.assertTrue(self.owner.restore_edit_revision(revision_id))
        self.assertEqual(self.owner.payload['turns'], before['turns'])
        self.assertEqual(self.owner.payload['draft'], '正在打的下一問，不可遺失')
        self.assertEqual(len(self.service.submitted), 1)
        self.assertEqual(self.owner.payload['revisions'][-1]['turns'], edited)
        redo = self.owner.edit_revisions[0]['id']
        self.assertTrue(self.owner.restore_edit_revision(redo))
        self.assertEqual(self.owner.payload['turns'], edited)
        self.assertEqual(len(self.owner.edit_revisions), 3)
        projected = self.owner.edit_revision_payload(revision_id)
        projected['turns'][0]['text'] = 'caller mutation'
        self.assertEqual(self.owner.payload['revisions'][0]['turns'], before['turns'])

    def test_edit_and_restore_save_failure_preserve_original_payload_and_file(self):
        for failure in ('failed', 'conflict'):
            self.failure = failure
            before = copy.deepcopy(self.owner.payload); raw = self.path.read_bytes()
            self.assertFalse(self.owner.edit_and_resend(before['turns'][1]['id'], '不可落盤'))
            self.assertEqual(self.owner.payload, before)
            self.assertEqual(self.path.read_bytes(), raw)
            self.assertFalse(self.owner.busy)
            self.assertEqual(self.service.submitted, [])
        self.failure = None; self.owner.error = ''
        self.assertTrue(self.owner.edit_and_resend(before['turns'][1]['id'], '可落盤'))
        self.service.complete(self.service.submitted[-1], '完成')
        before = copy.deepcopy(self.owner.payload); raw = self.path.read_bytes()
        self.failure = 'conflict'
        self.assertFalse(self.owner.restore_edit_revision(self.owner.edit_revisions[0]['id']))
        self.assertEqual(self.owner.payload, before)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_edit_rejects_busy_stale_limit_and_oversize_without_mutating(self):
        before = copy.deepcopy(self.owner.payload)
        self.assertFalse(self.owner.edit_and_resend(uid(), 'stale'))
        with patch.object(schema, 'MAX_BYTES', 100):
            self.assertFalse(self.owner.edit_and_resend(before['turns'][0]['id'], 'oversize'))
        self.assertEqual(self.owner.payload, before)
        self.assertEqual(self.service.submitted, [])
        self.assertTrue(self.owner.edit_and_resend(before['turns'][1]['id'], '接受'))
        active = copy.deepcopy(self.owner.payload)
        self.assertFalse(self.owner.edit_and_resend(active['turns'][0]['id'], '不接受'))
        self.assertFalse(self.owner.restore_edit_revision(self.owner.edit_revisions[0]['id']))
        self.assertEqual(self.owner.payload, active)

    def test_model_or_parameter_preflight_rejection_does_not_publish_edit(self):
        before = copy.deepcopy(self.owner.payload); raw = self.path.read_bytes()
        with patch.object(self.service, 'validate_submission', create=True, side_effect=ValueError('合成模型路徑失效')):
            self.assertFalse(self.owner.edit_and_resend(before['turns'][0]['id'], '不應替換原文'))
        self.assertEqual(self.owner.payload, before)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.service.submitted, [])
        self.assertFalse(self.owner.busy)

    def test_shared_views_lease_blocks_other_edits_send_restore_branch_and_dispose(self):
        first, second = FakeView(), FakeView()
        self.owner.views.update([first, second])
        selected = self.owner.payload['turns'][1]['id']
        before = copy.deepcopy(self.owner.payload)
        self.assertTrue(self.owner.acquire_edit(first, selected))
        self.assertTrue(self.owner.edit_locked)
        self.assertFalse(self.owner.acquire_edit(second, selected))
        self.assertFalse(self.owner.edit_and_resend(selected, '別頁修改', view=second))
        self.assertFalse(self.owner.edit_and_resend(selected, '未指定來源'))
        self.assertFalse(self.owner.send('別頁新訊息'))
        self.assertFalse(self.owner.retry())
        self.assertFalse(self.owner.restore_edit_revision(uid()))
        self.assertFalse(self.owner.dispose())
        with self.assertRaises(ValueError):
            self.owner.branch_document(selected)
        self.owner.release_edit(second)
        self.assertTrue(self.owner.edit_locked)
        self.assertEqual(self.owner.payload, before)
        self.failure = 'conflict'
        self.assertFalse(self.owner.edit_and_resend(selected, '同頁保存失敗', view=first))
        self.assertTrue(self.owner.edit_locked)
        self.assertEqual(self.owner.payload, before)
        self.failure = None
        self.assertTrue(self.owner.edit_and_resend(selected, '同頁修改', view=first))
        self.assertFalse(self.owner.edit_locked)

    def test_cancelled_edit_releases_only_its_view_without_mutating_history(self):
        view = FakeView(); self.owner.views.add(view)
        before = copy.deepcopy(self.owner.payload)
        self.assertTrue(self.owner.acquire_edit(view, before['turns'][0]['id']))
        self.owner.release_edit(view)
        self.assertFalse(self.owner.edit_locked)
        self.assertEqual(self.owner.payload, before)
        self.assertEqual(self.writes, [])

    def test_stopped_partial_new_send_uses_exact_latest_attempt_and_ignores_late(self):
        self.assertTrue(self.owner.send('先送出'))
        old = self.service.submitted[-1]
        self.service.delta.emit(old, RAW)
        self.assertTrue(self.owner.stop())
        self.assertEqual(self.owner.notice, '生成已停止')
        self.owner.set_draft('接著送出')
        self.assertTrue(self.owner.send('接著送出'), self.owner.notice)
        new = self.service.submitted[-1]
        self.assertEqual(new.input['messages'][-2], dict(role='assistant', content=RAW))
        self.assertEqual(self.owner.payload['turns'][-1]['attempts'][0]['context'][-1]['attempt_id'], old.scope['attempt_id'])
        before = copy.deepcopy(self.owner.payload)
        self.service.delta.emit(old, '遲到片段')
        self.service.finished.emit(old, 'completed', RAW + '遲到片段')
        self.assertEqual(self.owner.payload, before)
        schema.validate(self.owner.project())

    def test_empty_failed_answer_new_send_has_no_invented_assistant(self):
        self.assertTrue(self.owner.send('失敗的問題'))
        old = self.service.submitted[-1]
        self.service.state = 'ready'; self.service.finished.emit(old, 'error', '合成失敗')
        self.assertTrue(self.owner.send('直接問下一題'))
        messages = self.service.submitted[-1].input['messages']
        self.assertEqual(messages[-2:], [dict(role='user', content='失敗的問題'), dict(role='user', content='直接問下一題')])

    def test_retry_is_last_turn_only_retains_draft_and_adds_only_attempt(self):
        self.assertFalse(self.owner.retry(self.owner.payload['turns'][0]['id']))
        last_id = self.owner.payload['turns'][-1]['id']
        self.owner.set_draft('輸入中的另一個問題')
        before = copy.deepcopy(self.owner.payload)
        self.assertTrue(self.owner.retry(last_id))
        self.assertEqual(self.owner.payload['draft'], before['draft'])
        self.assertEqual(len(self.owner.payload['turns']), len(before['turns']))
        self.assertEqual(len(self.owner.payload['turns'][-1]['attempts']), len(before['turns'][-1]['attempts']) + 1)
        self.assertEqual(self.owner.payload['turns'][-1]['text'], before['turns'][-1]['text'])

    def test_checkpoint_timeout_prevents_edit_or_restore(self):
        before = copy.deepcopy(self.owner.payload)
        with patch.object(self.owner, 'finish_checkpoint', return_value=False):
            self.assertFalse(self.owner.edit_and_resend(before['turns'][0]['id'], '等待保存'))
            self.assertFalse(self.owner.restore_edit_revision(uid()))
        self.assertEqual(self.owner.payload, before)
        self.assertEqual(self.service.submitted, [])

    def test_edit_and_restore_keep_frozen_turn_attachments_and_pending_composer_attachments(self):
        from test_local_chat_attachments import text_item
        selected = self.owner.payload['turns'][1]
        selected['attachments'] = [text_item('已送出附件的原文')]
        self.owner.payload['draft_attachments'] = [text_item('準備下一題用的附件')]
        self.owner.update_dirty(); self.assertTrue(self.owner.save())
        before = copy.deepcopy(self.owner.payload)
        self.assertTrue(self.owner.edit_and_resend(selected['id'], '修改帶附件的問題'))
        new_turn = self.owner.payload['turns'][-1]
        self.assertEqual(new_turn['attachments'][0]['text'], selected['attachments'][0]['text'])
        self.assertNotEqual(new_turn['attachments'][0]['id'], selected['attachments'][0]['id'])
        self.assertEqual(self.owner.payload['draft_attachments'], before['draft_attachments'])
        self.assertIn('已送出附件的原文', self.service.submitted[-1].input['messages'][-1]['content'])
        self.assertNotIn('準備下一題用的附件', self.service.submitted[-1].input['messages'][-1]['content'])
        self.service.complete(self.service.submitted[-1], '帶附件的回覆')
        self.assertTrue(self.owner.restore_edit_revision(self.owner.edit_revisions[0]['id']))
        self.assertEqual(self.owner.payload['turns'], before['turns'])
        self.assertEqual(self.owner.payload['draft_attachments'], before['draft_attachments'])


if __name__ == '__main__':
    unittest.main()
