"""Observed thinking durations: isolated owners, files, clocks and fake service."""
import copy
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.local_documents import chat, identity, io, schema
import test_local_chat_owner as owner_fixture
import test_local_chat_contracts as chat_fixture


class ThinkingElapsedOwnerContracts(unittest.TestCase):
    setUp = owner_fixture.LocalChatOwnerContracts.setUp
    tearDown = owner_fixture.LocalChatOwnerContracts.tearDown
    _save = owner_fixture.LocalChatOwnerContracts._save
    _send = owner_fixture.LocalChatOwnerContracts._send

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def clock(self):
        return patch('prompt_calculus_studio.local_ai.chat_document.monotonic')

    def emit(self, request, text):
        self.service.delta.emit(request, text)

    def test_loading_and_body_are_excluded_and_active_reads_never_save(self):
        with self.clock() as clock:
            clock.return_value = 100.; request = self._send()
            attempt = self.owner._attempt; ident = attempt['id']
            clock.return_value = 120.
            self.assertIsNone(self.owner.thinking_elapsed_seconds(ident))
            self.emit(request, '<th')
            self.assertIsNone(self.owner.thinking_elapsed_seconds(ident))
            clock.return_value = 123.; self.emit(request, 'ink>work')
            self.assertEqual(self.owner.thinking_elapsed_seconds(ident), 3.)
            saved_count = len(self.saved); before = copy.deepcopy(self.owner.payload)
            clock.return_value = 125.
            self.assertEqual(self.owner.thinking_elapsed_seconds(ident), 5.)
            self.assertEqual(self.owner.payload, before)
            self.assertEqual(len(self.saved), saved_count)
            self.assertNotIn('thinking_seconds', attempt)
            self.assertNotIn(chat.TIMING_FEATURE, self.owner.project()['required_features'])
            clock.return_value = 127.; self.emit(request, '</thi')
            clock.return_value = 130.; self.emit(request, 'nk>answer')
            self.assertEqual(attempt['thinking_seconds'], 10.)
            clock.return_value = 145.; self.emit(request, ' continued')
            self.assertEqual(self.owner.thinking_elapsed_seconds(ident), 10.)
            self.service.complete(request, attempt['text'])
            clock.return_value = 200.
            self.assertEqual(self.owner.thinking_elapsed_seconds(ident), 10.)
        saved = io.load(self.path.parent, self.path.name).document
        self.assertIn(chat.TIMING_FEATURE, saved['required_features'])
        self.assertEqual(saved['payload']['turns'][0]['attempts'][0]['thinking_seconds'], 10.)
        reopened = type(self.owner)(self.path, self.workspace)
        try:
            self.assertEqual(reopened.thinking_elapsed_seconds(ident), 10.)
            self.assertIsNone(reopened._thinking_started)
        finally:
            reopened.dispose()

    def test_all_opening_and_closing_tag_splits_freeze_once(self):
        for opening_split in range(1, len('<think>')):
            for closing_split in range(1, len('</think>')):
                with self.subTest(opening=opening_split, closing=closing_split), self.clock() as clock:
                    self.service.state = 'ready'; clock.return_value = 100.
                    request = self._send(); attempt = self.owner._attempt
                    clock.return_value = 110.; self.emit(request, '<think>'[:opening_split])
                    clock.return_value = 113.; self.emit(request, '<think>'[opening_split:] + 'work')
                    self.assertEqual(self.owner.thinking_elapsed_seconds(attempt['id']), 3.)
                    clock.return_value = 116.; self.emit(request, '</think>'[:closing_split])
                    clock.return_value = 119.; self.emit(request, '</think>'[closing_split:] + 'answer')
                    self.assertEqual(attempt['thinking_seconds'], 9.)
                    clock.return_value = 130.; self.service.complete(request, attempt['text'])
                    self.assertEqual(attempt['thinking_seconds'], 9.)

    def test_single_chunk_records_zero_observed_duration(self):
        with self.clock() as clock:
            clock.return_value = 100.; request = self._send()
            attempt = self.owner._attempt
            clock.return_value = 120.; self.emit(request, '<think>work</think>answer')
            self.assertEqual(attempt['thinking_seconds'], 0.)
            clock.return_value = 150.; self.service.complete(request, attempt['text'])
            self.assertEqual(attempt['thinking_seconds'], 0.)

    def test_stop_error_limit_and_complete_freeze_unclosed_thinking(self):
        for terminal in ('stop', 'error', 'limited', 'completed'):
            with self.subTest(terminal=terminal), self.clock() as clock:
                self.service.state = 'ready'; clock.return_value = 100.
                request = self._send(); attempt = self.owner._attempt
                clock.return_value = 110.; self.emit(request, '<think>work')
                clock.return_value = 117.
                if terminal == 'stop': self.owner.stop()
                else: self.service.finished.emit(request, terminal, 'synthetic error' if terminal == 'error' else attempt['text'])
                clock.return_value = 200.
                self.assertEqual(self.owner.thinking_elapsed_seconds(attempt['id']), 7.)
                self.assertEqual(attempt['thinking_seconds'], 7.)
                self.assertIsNone(self.owner._thinking_started)

    def test_literal_disabled_incomplete_prefix_and_final_only_get_no_invented_timing(self):
        for text, disabled, final_only in (
            ('ordinary answer', False, False), ('```xml\n<think>literal', False, False),
            ('    <think>literal code', False, False), ('<thistle', False, False),
            ('<thi', False, False), ('<think>literal', True, False),
            ('<think>work</think>answer', False, True)):
            with self.subTest(text=text, disabled=disabled, final_only=final_only), self.clock() as clock:
                self.service.state = 'ready'; clock.return_value = 100.
                request = self._send(); attempt = self.owner._attempt
                if disabled:
                    attempt['reasoning'] = dict(enable_thinking=False, reasoning_budget_tokens=None)
                clock.return_value = 110.
                if not final_only: self.emit(request, text)
                clock.return_value = 120.; self.service.complete(request, text)
                self.assertIsNone(self.owner.thinking_elapsed_seconds(attempt['id']))
                self.assertNotIn('thinking_seconds', attempt)
        self.assertNotIn(chat.TIMING_FEATURE, self.owner.project()['required_features'])

    def test_terminal_detail_can_finish_a_previously_observed_split_marker(self):
        with self.clock() as clock:
            clock.return_value = 100.; request = self._send(); attempt = self.owner._attempt
            clock.return_value = 110.; self.emit(request, '<thi')
            clock.return_value = 119.; self.service.complete(request, '<think>work</think>answer')
            self.assertEqual(attempt['thinking_seconds'], 9.)

    def test_retry_and_late_events_keep_attempt_clocks_separate(self):
        with self.clock() as clock:
            clock.return_value = 100.; old = self._send(); first = self.owner._attempt
            clock.return_value = 110.; self.emit(old, '<think>first')
            clock.return_value = 117.; self.owner.stop()
            clock.return_value = 200.; self.assertTrue(self.owner.retry())
            new = self.owner._request; second = self.owner._attempt
            self.assertIsNone(self.owner.thinking_elapsed_seconds(second['id']))
            clock.return_value = 210.; self.emit(new, '<think>second')
            clock.return_value = 215.
            self.emit(old, '</think>late'); self.service.finished.emit(old, 'completed', '<think>first</think>late')
            self.assertEqual(self.owner.thinking_elapsed_seconds(first['id']), 7.)
            self.assertEqual(self.owner.thinking_elapsed_seconds(second['id']), 5.)
            self.emit(new, '</think>answer')
            self.service.complete(new, second['text'])
            self.assertEqual(second['thinking_seconds'], 5.)

    def test_failed_save_retains_frozen_value_for_retry(self):
        with self.clock() as clock:
            clock.return_value = 100.; request = self._send(); attempt = self.owner._attempt
            clock.return_value = 110.; self.emit(request, '<think>work')
            clock.return_value = 114.; self.emit(request, '</think>answer')
            self.fail_saves = True; self.service.complete(request, attempt['text'])
            self.assertTrue(self.owner.dirty); self.assertTrue(self.owner.error)
            self.assertEqual(attempt['thinking_seconds'], 4.)
            self.fail_saves = False; self.assertTrue(self.owner.save())
            saved = io.load(self.path.parent, self.path.name).document
            self.assertEqual(saved['payload']['turns'][0]['attempts'][0]['thinking_seconds'], 4.)

    def test_reopen_unfinished_without_value_and_retarget_do_not_start_or_leak_clock(self):
        with self.clock() as clock:
            clock.return_value = 100.; request = self._send(); attempt = self.owner._attempt
            clock.return_value = 110.; self.emit(request, '<think>work')
            self.assertTrue(self.owner.save())
            reopened = type(self.owner)(self.path, self.workspace)
            try:
                self.assertIsNone(reopened.thinking_elapsed_seconds(attempt['id']))
                self.assertEqual(reopened.payload['turns'][0]['attempts'][0]['status'], 'interrupted')
            finally:
                reopened.dispose()
            target = self.root / 'other-chat.pcs-chat'
            created = io.create(self.root, target.name, chat.empty())
            clock.return_value = 115.; self.assertTrue(self.owner.retarget(created, target))
            self.emit(request, '</think>late')
            self.service.finished.emit(request, 'completed', '<think>work</think>late')
            self.assertEqual(self.owner.payload['turns'], [])
            self.assertIsNone(self.owner.thinking_elapsed_seconds(attempt['id']))


class ThinkingElapsedSchemaContracts(unittest.TestCase):
    def document(self, value=1.25):
        document = chat.empty()
        item = chat_fixture.attempt('<think>work</think>answer')
        item['thinking_seconds'] = value
        item['reasoning'] = dict(enable_thinking=True, reasoning_budget_tokens=64)
        document['payload']['turns'] = [dict(id=chat_fixture.uid(), text='question', attempts=[item])]
        return chat.sync_features(document)

    def test_only_finite_nonnegative_real_numbers_are_accepted(self):
        for value in (0, 0., 1, 1.25):
            with self.subTest(valid=value):
                document = self.document(value)
                self.assertEqual(schema.decode(schema.encode(document)), document)
        for value in (None, True, False, -1, -.01, float('nan'), float('inf'), -float('inf'), '1', [], 10**400):
            with self.subTest(invalid=type(value).__name__), self.assertRaises(schema.DocumentError):
                schema.validate(self.document(value))

    def test_feature_covers_current_and_archived_attempts_and_rejects_mismatch(self):
        document = self.document()
        self.assertIn(chat.TIMING_FEATURE, document['required_features'])
        self.assertIn(chat.REASONING_FEATURE, document['required_features'])
        missing = copy.deepcopy(document); missing['required_features'].remove(chat.TIMING_FEATURE)
        with self.assertRaises(schema.DocumentError): schema.validate(missing)
        extra = chat.empty(); extra['required_features'].append(chat.TIMING_FEATURE)
        with self.assertRaises(schema.DocumentError): schema.validate(extra)
        chat.archive_revision(document['payload'], reason='restore')
        document['payload']['turns'] = []
        chat.sync_features(document); schema.validate(document)
        self.assertIn(chat.TIMING_FEATURE, document['required_features'])
        document['payload']['revisions'][0]['turns'][0]['attempts'][0].pop('thinking_seconds')
        with self.assertRaises(schema.DocumentError): schema.validate(document)
        chat.sync_features(document); schema.validate(document)
        self.assertNotIn(chat.TIMING_FEATURE, document['required_features'])

    def test_clone_branch_archive_keep_exact_duration_and_legacy_stays_legacy(self):
        document = self.document(7.125)
        chat.archive_revision(document['payload'], reason='restore'); chat.sync_features(document)
        cloned = identity.duplicate(document).document
        self.assertEqual(cloned['payload']['turns'][0]['attempts'][0]['thinking_seconds'], 7.125)
        self.assertEqual(cloned['payload']['revisions'][0]['turns'][0]['attempts'][0]['thinking_seconds'], 7.125)
        turn = cloned['payload']['turns'][0]
        branch = chat.branch_payload(cloned['payload'], turn['id'], turn['attempts'][0]['id'])
        self.assertEqual(branch['turns'][0]['attempts'][0]['thinking_seconds'], 7.125)
        legacy = chat.empty(); legacy['payload']['turns'] = [dict(id=chat_fixture.uid(), text='old', attempts=[chat_fixture.attempt()])]
        self.assertEqual(schema.decode(schema.encode(legacy)), legacy)
        self.assertNotIn(chat.TIMING_FEATURE, legacy['required_features'])


if __name__ == '__main__':
    unittest.main()
