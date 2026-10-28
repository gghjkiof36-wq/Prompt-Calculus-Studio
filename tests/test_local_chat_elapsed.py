"""Owner clock is ephemeral and request-scoped; no engine or real service."""
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

import test_local_chat_owner as owner_fixture


class ChatElapsedContracts(unittest.TestCase):
    setUp = owner_fixture.LocalChatOwnerContracts.setUp
    tearDown = owner_fixture.LocalChatOwnerContracts.tearDown
    _save = owner_fixture.LocalChatOwnerContracts._save
    _send = owner_fixture.LocalChatOwnerContracts._send

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def clock(self):
        return patch('prompt_calculus_studio.local_ai.chat_document.monotonic')

    def test_accepted_clock_does_not_modify_file_schema(self):
        self.assertIsNone(self.owner.generation_elapsed_seconds)
        with self.clock() as clock:
            clock.return_value = 100.
            self._send()
            clock.return_value = 162.4
            self.assertAlmostEqual(self.owner.generation_elapsed_seconds, 62.4)
        self.assertNotIn('generation_started', self.path.read_text(encoding='utf-8'))
        self.assertNotIn('pcs.chat-metrics.v1', self.path.read_text(encoding='utf-8'))

    def test_terminal_statuses_freeze_elapsed_without_continuing_clock(self):
        for status, detail in [('completed', ''), ('limited', ''), ('error', 'synthetic failure')]:
            with self.subTest(status=status), self.clock() as clock:
                self.service.state = 'ready'; clock.return_value = 100.
                request = self._send()
                clock.return_value = 105.
                self.assertEqual(self.owner.generation_elapsed_seconds, 5.)
                self.service.finished.emit(request, status, detail)
                clock.return_value = 1000.
                self.assertEqual(self.owner.generation_elapsed_seconds, 5.)
                self.assertIsNone(self.owner._generation_started)

    def test_stop_and_retry_reset_without_late_terminal_clearing_new_clock(self):
        with self.clock() as clock:
            clock.return_value = 100.; old = self._send()
            clock.return_value = 107.
            self.owner.stop()
            clock.return_value = 150.
            self.assertEqual(self.owner.generation_elapsed_seconds, 7.)
            clock.return_value = 200.; self.assertTrue(self.owner.retry())
            self.assertEqual(self.owner.generation_elapsed_seconds, 0.)
            clock.return_value = 203.
            self.service.finished.emit(old, 'completed', '')
            self.assertEqual(self.owner.generation_elapsed_seconds, 3.)
            current = self.owner._request
            self.service.finished.emit(current, 'completed', '')
            clock.return_value = 400.
            self.service.finished.emit(old, 'error', 'late error')
            self.assertEqual(self.owner.generation_elapsed_seconds, 3.)

    def test_save_or_service_rejection_never_starts_clock(self):
        self.fail_saves = True
        self.assertFalse(self.owner.send('保存失敗'))
        self.assertIsNone(self.owner.generation_elapsed_seconds)
        self.fail_saves = False
        with patch.object(self.service, 'submit', return_value=False):
            self.assertFalse(self.owner.send('拒絕請求'))
        self.assertIsNone(self.owner.generation_elapsed_seconds)

    def test_synchronous_terminal_freezes_clock_before_submit_returns(self):
        with self.clock() as clock:
            def finish(request):
                clock.return_value = 102.
                self.service.finished.emit(request, 'completed', '')
                return True
            clock.return_value = 100.
            with patch.object(self.service, 'submit', side_effect=finish):
                self.assertTrue(self.owner.send('立即結束'))
            clock.return_value = 500.
            self.assertEqual(self.owner.generation_elapsed_seconds, 2.)
            self.assertIsNone(self.owner._generation_started)

    def test_rejected_new_attempt_clears_previous_elapsed(self):
        with self.clock() as clock:
            clock.return_value = 100.; request = self._send()
            clock.return_value = 107.; self.service.complete(request, '')
            self.assertEqual(self.owner.generation_elapsed_seconds, 7.)
            with patch.object(self.service, 'submit', return_value=False):
                self.assertFalse(self.owner.send('拒絕下一輪'))
            self.assertIsNone(self.owner.generation_elapsed_seconds)

    def test_frozen_reasoning_and_retry_elapsed_belong_to_the_same_attempt(self):
        first = dict(enable_thinking=True, reasoning_budget_tokens=64)
        second = dict(enable_thinking=False, reasoning_budget_tokens=None)
        with self.clock() as clock, patch.object(self.service, 'model_preset') as preset:
            preset.return_value = dict(system_prompt='', reasoning=first)
            clock.return_value = 100.; old = self._send()
            self.assertEqual(old.input['reasoning'], first)
            clock.return_value = 107.; self.owner.stop()
            preset.return_value = dict(system_prompt='', reasoning=second)
            clock.return_value = 200.; self.assertTrue(self.owner.retry())
            new = self.owner._request
            self.assertEqual(new.input['reasoning'], second)
            clock.return_value = 203.; self.service.finished.emit(old, 'completed', '')
            self.assertEqual(self.owner.generation_elapsed_seconds, 3.)
            self.service.finished.emit(new, 'completed', '')
            attempts = self.owner.payload['turns'][-1]['attempts']
            self.assertEqual([a['reasoning'] for a in attempts], [first, second])
            clock.return_value = 500.
            self.assertEqual(self.owner.generation_elapsed_seconds, 3.)

    def test_reopened_or_retargeted_document_does_not_inherit_ephemeral_elapsed(self):
        from prompt_calculus_studio.local_documents import identity, io
        with self.clock() as clock:
            clock.return_value = 100.; request = self._send()
            clock.return_value = 107.; self.service.complete(request, '')
        reopened = type(self.owner)(self.path, self.workspace)
        try:
            self.assertIsNone(reopened.generation_elapsed_seconds)
        finally:
            reopened.dispose()
        target = self.root / 'new-chat.pcs-chat'
        created = io.create(self.root, target.name, identity.duplicate(self.owner.project()).document)
        self.assertEqual(created.status, 'saved', created.error)
        self.assertTrue(self.owner.retarget(created, target))
        self.assertIsNone(self.owner.generation_elapsed_seconds)

    def test_edit_and_resend_gets_its_own_clock(self):
        request = self._send()
        self.service.complete(request, '')
        turn_id = self.owner.payload['turns'][0]['id']
        with self.clock() as clock:
            clock.return_value = 400.
            self.assertTrue(self.owner.edit_and_resend(turn_id, '編輯後問題'))
            clock.return_value = 403.5
            self.assertEqual(self.owner.generation_elapsed_seconds, 3.5)


if __name__ == '__main__':
    unittest.main()
