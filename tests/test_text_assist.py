"""Ephemeral Markdown candidates with synthetic services and an isolated worker.

No real engine, model, network service, user document or foreground UI is used.
"""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.local_ai.settings import DEFAULT_SETTINGS
from prompt_calculus_studio.local_ai.text_assist import TextAssistController
from prompt_calculus_studio.service_adapters import ServiceRequest
import test_local_ai_auto_prepare as prepare_fixtures


APP = QApplication.instance() or QApplication([])


class FakeService(QObject):
    changed = Signal()
    delta = Signal(object, str)
    finished = Signal(object, str, str)

    def __init__(self):
        super().__init__()
        self.models = [dict(id='a', name='Alpha'), dict(id='b', name='Beta')]
        self.selected_model = 'a'
        self.settings = dict(DEFAULT_SETTINGS)
        self.preset = dict(system_prompt='請簡潔回答', reasoning=dict(enable_thinking=None, reasoning_budget_tokens=None))
        self.state, self.error = 'unloaded', ''
        self.can_accept = True
        self.requests, self.cancelled = [], []
        self.accepted = True
        self.synchronous = None

    def settings_for_model(self, _model):
        return dict(self.settings)

    def model_preset(self, _model):
        return copy.deepcopy(self.preset)

    def validate_submission(self, request):
        if not self.can_accept:
            raise ValueError('busy')
        if request.operation != 'text_assist':
            raise ValueError('wrong operation')

    def submit(self, request):
        if not self.accepted:
            self.error = '拒絕合成請求'
            return False
        self.requests.append(request)
        self.can_accept, self.state = False, 'loading'
        self.changed.emit()
        if self.synchronous is not None:
            self.complete(request, self.synchronous)
        return True

    def cancel(self, request_id):
        self.cancelled.append(request_id)
        self.can_accept, self.state = True, 'ready'
        self.changed.emit()
        return True

    def complete(self, request, text, status='completed'):
        self.can_accept, self.state = True, 'ready'
        self.finished.emit(request, status, text)
        self.changed.emit()


class TextAssistContracts(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.scope = dict(path='synthetic.md', document_id='owner', revision=3, start=2, end=5, kind='selection')
        self.controller = TextAssistController(self.service, source_text='原文🙂\n', source_scope=self.scope)

    def tearDown(self):
        self.controller.dispose()

    def start(self):
        self.assertTrue(self.controller.start('改寫得清楚一些'))
        return self.service.requests[-1]

    def test_request_freezes_exact_source_instruction_scope_model_and_settings(self):
        self.assertTrue(self.controller.start('  保留空白\n', model_id='b'))
        request = self.service.requests[-1]
        self.scope['revision'] = 100
        self.service.settings['temperature'] = 1.5
        self.service.preset['reasoning']['enable_thinking'] = False
        self.controller.source_scope['revision'] = 200
        self.controller.models.clear()
        self.assertEqual(request.operation, 'text_assist')
        self.assertEqual(request.scope['source']['revision'], 3)
        self.assertEqual(request.scope['source_sha256'], hashlib.sha256('原文🙂\n'.encode()).hexdigest())
        self.assertEqual(request.input['model'], 'b')
        self.assertEqual(self.service.selected_model, 'a')
        self.assertEqual(self.controller.model_name, 'Beta')
        self.assertEqual(request.input['params']['temperature'], DEFAULT_SETTINGS['temperature'])
        self.assertIsNone(request.input['reasoning']['enable_thinking'])
        messages = request.input['messages']
        self.assertEqual([m['role'] for m in messages], ['system', 'user'])
        self.assertEqual(json.loads(messages[-1]['content']), dict(instruction='  保留空白\n', source_text='原文🙂\n'))
        self.assertNotIn('synthetic.md', messages[-1]['content'])
        self.assertIn('只輸出', messages[0]['content'])

    def test_stream_hides_split_thinking_and_only_completed_body_can_apply(self):
        request = self.start()
        for chunk in ('<th', 'ink>不應採用的思考', '</thi', 'nk>\n# 新', '正文\n'):
            self.service.delta.emit(request, chunk)
            self.assertFalse(self.controller.can_apply)
            self.assertNotIn('不應採用', self.controller.candidate)
        self.service.complete(request, '<think>不應採用的思考</think>\n# 新正文\n')
        self.assertEqual(self.controller.state, 'completed')
        self.assertEqual(self.controller.candidate_for_apply(), '# 新正文')

    def test_literal_tags_in_code_and_prose_are_preserved(self):
        for text in ('正文 `<think>字面</think>`', '    <think>code</think>', '```xml\n<think>code</think>\n```'):
            request = self.start()
            self.service.complete(request, text)
            self.assertEqual(self.controller.candidate_for_apply(), text)

    def test_empty_unclosed_and_partial_tag_completed_results_are_not_applicable(self):
        for text in ('', '  \n', '<think>未結束', '<think>想法</thi', '<think>想法</think>', '<thi'):
            with self.subTest(text=text):
                request = self.start()
                self.service.complete(request, text)
                self.assertEqual(self.controller.state, 'failed')
                with self.assertRaises(ValueError):
                    self.controller.candidate_for_apply()

    def test_failed_cancelled_limited_and_stream_mismatch_never_apply(self):
        for status, detail in (('error', '合成錯誤'), ('cancelled', '部分'), ('limited', '部分'), ('completed', '不同文字')):
            with self.subTest(status=status):
                request = self.start()
                self.service.delta.emit(request, '部分')
                self.service.complete(request, detail, status)
                self.assertFalse(self.controller.can_apply)
                self.assertEqual(self.controller.raw_text, '部分')

    def test_stop_revokes_request_before_cancel_and_late_result_cannot_replace_retry(self):
        first = self.start()
        self.service.delta.emit(first, '部分')
        self.assertTrue(self.controller.stop())
        second = self.start()
        self.service.delta.emit(first, '舊串流')
        self.service.finished.emit(first, 'completed', '舊結果')
        self.assertEqual(self.controller.raw_text, '')
        self.assertTrue(self.controller.busy)
        self.service.complete(second, '新結果')
        self.assertEqual(self.controller.candidate_for_apply(), '新結果')
        self.assertEqual(self.service.cancelled, [first.request_id])

    def test_same_request_id_different_frozen_scope_is_rejected(self):
        request = self.start()
        changed = ServiceRequest('local_ai', 'text_assist', request_id=request.request_id,
                                 scope=dict(request.scope, serial=999), input=request.input)
        self.service.delta.emit(changed, '別的來源')
        self.service.finished.emit(changed, 'completed', '別的來源')
        self.assertEqual(self.controller.raw_text, '')
        self.assertTrue(self.controller.busy)

    def test_invalidate_keeps_preview_but_blocks_apply_new_request_and_late_events(self):
        request = self.start()
        self.service.delta.emit(request, '預覽')
        self.controller.invalidate('原文已改')
        self.controller.invalidate('重複')
        self.service.finished.emit(request, 'completed', '晚到結果')
        self.assertEqual(self.controller.candidate, '預覽')
        self.assertEqual(self.controller.error, '原文已改')
        self.assertFalse(self.controller.start('新要求'))
        self.assertFalse(self.controller.can_apply)
        self.assertEqual(self.service.cancelled, [request.request_id])

    def test_dispose_is_idempotent_and_disconnects_its_service_callbacks(self):
        request = self.start()
        self.controller.dispose()
        self.controller.dispose()
        self.service.delta.emit(request, '晚到')
        self.service.complete(request, '晚到')
        self.assertEqual(self.controller.state, 'disposed')
        self.assertEqual(self.controller.raw_text, '')
        self.assertFalse(self.controller.start('新要求'))
        self.assertEqual(self.service.cancelled, [request.request_id])

    def test_busy_shared_service_rejects_without_cancelling_any_other_owner(self):
        self.service.can_accept = False
        self.assertFalse(self.controller.start('改寫'))
        self.assertEqual(self.service.requests, [])
        self.assertEqual(self.service.cancelled, [])

    def test_busy_controller_does_not_replace_its_existing_request(self):
        request = self.start()
        self.assertFalse(self.controller.start('第二次'))
        self.service.complete(request, '第一次的結果')
        self.assertEqual(self.controller.candidate_for_apply(), '第一次的結果')
        self.assertEqual(len(self.service.requests), 1)

    def test_synchronous_submit_result_and_rejection_leave_no_pending_owner(self):
        self.service.synchronous = '立即結果'
        self.assertTrue(self.controller.start('改寫'))
        self.assertEqual(self.controller.candidate_for_apply(), '立即結果')
        self.assertFalse(self.controller.busy)
        self.service.accepted = False
        self.assertFalse(self.controller.start('再次'))
        self.assertFalse(self.controller.busy)
        self.assertFalse(self.controller.can_apply)
        self.assertEqual(self.controller.error, '拒絕合成請求')

    def test_reentrant_retry_at_terminal_changed_keeps_new_owner(self):
        first = self.start()
        restarted = []
        def retry():
            if self.controller.state == 'completed' and not restarted:
                restarted.append(True)
                self.assertTrue(self.controller.start('第二次'))
        self.controller.changed.connect(retry)
        self.service.complete(first, '第一次')
        self.assertTrue(self.controller.busy)
        self.assertEqual(self.controller.raw_text, '')
        self.assertEqual(len(self.service.requests), 2)

    def test_response_limit_revokes_before_late_callbacks(self):
        request = self.start()
        with patch('prompt_calculus_studio.local_ai.text_assist.MAX_CANDIDATE_CHARS', 3):
            self.service.delta.emit(request, '1234')
        self.service.finished.emit(request, 'completed', '1234')
        self.assertEqual(self.controller.state, 'limited')
        self.assertFalse(self.controller.can_apply)
        self.assertEqual(self.service.cancelled, [request.request_id])

    def test_invalid_input_does_not_submit_and_scope_is_json_only(self):
        for instruction in ('', '   ', None, '\ud800'):
            self.assertFalse(self.controller.start(instruction))
        self.assertFalse(self.controller.start('改寫', model_id='missing'))
        self.assertEqual(self.service.requests, [])
        for scope in ({'object': self.service}, {'number': float('nan')}, {1: 'key'}):
            with self.assertRaises((TypeError, ValueError)):
                TextAssistController(self.service, source_text='原文', source_scope=scope)

    def test_service_changes_are_forwarded_without_a_controller_request(self):
        changes = []
        self.controller.changed.connect(lambda: changes.append(self.controller.state))
        self.service.changed.emit()
        self.assertEqual(changes, ['idle'])


class TextAssistServiceContracts(unittest.TestCase):
    """Real LocalAIService queue with the existing fake engine fixture."""
    add_model = prepare_fixtures.AutoPrepareContracts.add_model
    wait_for = prepare_fixtures.AutoPrepareContracts.wait_for
    request = prepare_fixtures.AutoPrepareContracts.request
    app = APP

    def setUp(self):
        prepare_fixtures.AutoPrepareContracts.setUp(self)
        self._controllers = []

    def tearDown(self):
        for controller in self._controllers:
            controller.dispose()
        prepare_fixtures.AutoPrepareContracts.tearDown(self)

    def test_per_request_model_preserves_registry_selection_and_chat_rules(self):
        second = self.add_model('second')
        self.assertTrue(self.service.select_model(self.first['id']))
        before = (self.root / 'data' / 'local_ai' / 'registry.json').read_bytes()
        controller = TextAssistController(self.service, source_text='原文', source_scope={'document_id': 'owner'})
        self._controllers.append(controller)
        with self.assertRaises(ValueError):
            self.service.validate_submission(self.request(model=second['id']))
        self.assertTrue(controller.start('改寫', model_id=second['id']))
        self.wait_for(lambda: controller.state == 'completed' and self.service.state == 'ready')
        self.assertEqual(controller.candidate_for_apply(), '合成回覆')
        self.assertEqual(self.runtime.generations[-1][0], second['id'])
        self.assertEqual(self.service.selected_model, self.first['id'])
        self.assertEqual((self.root / 'data' / 'local_ai' / 'registry.json').read_bytes(), before)
        self.assertEqual(self.service.validate_submission(self.request())[0]['id'], self.first['id'])

    def test_assist_with_no_global_selection_still_uses_explicit_registered_model(self):
        self.service._registry.update(selected_model='')
        self.assertTrue(self.service.can_accept)
        self.assertFalse(self.service.can_submit)
        controller = TextAssistController(self.service, source_text='原文', source_scope={})
        self._controllers.append(controller)
        self.assertTrue(controller.start('改寫', model_id=self.first['id']))
        self.wait_for(lambda: controller.state == 'completed' and self.service.state == 'ready')
        self.assertEqual(self.service.selected_model, '')
        self.assertFalse(self.service.can_submit)
        self.assertTrue(self.service.can_accept)

    def test_other_owner_busy_and_unknown_model_are_rejected_without_cancellation(self):
        self.runtime.generate_release.clear()
        chat_request = self.request()
        self.assertTrue(self.service.submit(chat_request))
        self.wait_for(self.runtime.generate_entered.is_set)
        controller = TextAssistController(self.service, source_text='原文', source_scope={})
        self._controllers.append(controller)
        self.assertFalse(controller.start('改寫'))
        self.assertFalse(controller.stop())
        self.assertIs(self.service._active, chat_request)
        self.runtime.generate_release.set()
        self.wait_for(lambda: self.service.state == 'ready')
        bad = ServiceRequest('local_ai', 'text_assist', input=dict(self.request().input, model='unknown'))
        with self.assertRaises(ValueError):
            self.service.validate_submission(bad)
        self.service._closing = True
        self.assertFalse(self.service.can_accept)


if __name__ == '__main__':
    unittest.main()
