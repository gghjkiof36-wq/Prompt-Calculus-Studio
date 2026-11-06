"""Real GUI owners, worker and files; controlled model output, no GPU/network."""
import copy
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from prompt_calculus_studio.local_agents.delegation import delegation_for, chat_catalog
from prompt_calculus_studio.local_ai.chat_document import ChatDocument
from prompt_calculus_studio.local_ai.engine import Cancellation
from prompt_calculus_studio.local_ai.service import LocalAIService
from prompt_calculus_studio.local_documents import chat, io, schema
from test_local_ai_auto_prepare import FakeRuntime
from test_local_agent_owners import register
from test_local_chat_owner import FakeWorkspace


class NestedRuntime(FakeRuntime):
    def __init__(self):
        super().__init__()
        self.child_entered, self.child_release = Event(), Event()
        self.child_release.set()
        self.turns, self.returns = [], []
        self.child_status, self.child_answer = 'completed', '44'
        self.read_source, self.duplicate = False, False
        self.child_error = False
        self.fail_idle_once = False

    def ensure_idle(self, timeout=3):
        if self.fail_idle_once:
            self.fail_idle_once = False
            raise RuntimeError('synthetic cleanup failure')
        return super().ensure_idle(timeout)

    def tool_budget(self, messages, params, cancel, tools, *, final=False):
        cancel.check()
        return dict(input=200, output=512, capacity=8192)

    def tool_turn(self, messages, params, cancel, tools, *, final=False):
        cancel.check()
        names = [t['function']['name'] for t in tools]
        self.turns.append((self.model['id'], copy.deepcopy(messages), copy.deepcopy(params), names))
        prior = [m for m in messages if m['role'] == 'tool']
        if 'delegate_task' in names:
            if not prior:
                call = dict(id='child-1', type='function', function=dict(name='delegate_task',
                    arguments=json.dumps(dict(agent='A', task='Calculate 17 + 26.'))))
                calls = [call]
                if self.duplicate:
                    calls.append(dict(call, id='child-2'))
                return 'completed', dict(role='assistant', content='', tool_calls=calls)
            self.returns.append(copy.deepcopy(prior))
            return 'completed', dict(role='assistant', content='Checked independently: 17 + 26 = 43, not 44.')
        self.child_entered.set()
        while not self.child_release.wait(.01):
            cancel.check()
        cancel.check()
        if self.child_error:
            raise RuntimeError('synthetic child failure')
        if self.read_source and not prior:
            return 'completed', dict(role='assistant', content='', tool_calls=[dict(id='read-1', type='function',
                function=dict(name='read_file', arguments=json.dumps(dict(path='save.py', start=1, count=10))))])
        return self.child_status, dict(role='assistant', content=self.child_answer)


class ChatSubagents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'PROMPT_STUDIO_DATA': str(self.root/'runtime'),
                                          'PCS_AGENT_DISCOVERY': str(self.root/'discovery')})
        self.env.start()
        self.workspace = FakeWorkspace(self.root)
        self.workspace.root = self.root
        self.runtime = NestedRuntime()
        self.service = LocalAIService(self.workspace.data, runtime_factory=lambda: self.runtime)
        self.workspace.local_ai = self.service
        engine = self.root/'llama-server.exe'; engine.write_bytes(b'fixture only')
        self.assertTrue(self.service.configure(dict(runtime_path=str(engine), idle_minutes=0)))
        self.assertTrue(self.service.configure_approval_mode('auto'))
        model = self.root/'model.gguf'; model.write_bytes(b'GGUF'+(3).to_bytes(4, 'little'))
        self.model = self.service.register_model(model)
        self.assertTrue(self.service.select_model(self.model['id']))
        self.assertTrue(self.service.configure_model(self.model['id'], {},
            reasoning=dict(enable_thinking=True, reasoning_budget_tokens=128),
            expected_revision=self.service.model_preset(self.model['id'])['revision']), self.service.error)
        self.store, _, agent = register(self.workspace.data, self.root, self.model['id'])
        self.agent = self.store.save_agent('A', agent['project'], agent['model'], [], agent['id'])
        self.owners, self.finished, self.reviews = [], [], []
        self.service.finished.connect(lambda *a: self.finished.append(a))
        self.service.review.connect(lambda *a: self.reviews.append(a))
        self.workspace.open_path = self.open_path
        self.delegation = delegation_for(self.workspace)
        self.delegation.set_enabled(self.agent['project'], True)
        created = io.create(self.root, 'parent.pcs-chat', chat.empty())
        self.assertEqual(created.status, 'saved', created.error)
        self.parent = self.open_path(self.root/'parent.pcs-chat').document

    def open_path(self, path, **options):
        owner = ChatDocument(path, self.workspace)
        self.owners.append(owner)
        tab = SimpleNamespace(document=owner)
        self.workspace.pages.append(tab)
        return tab

    def wait(self, predicate):
        end = time.monotonic()+8
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate(): return
            time.sleep(.005)
        self.fail('worker did not settle: '+self.service.error)

    def tearDown(self):
        self.runtime.child_release.set()
        self.delegation.shutdown()
        for owner in self.owners:
            owner.dispose()
        self.service.shutdown()
        self.wait(lambda: self.service._worker is None)
        delete(self.workspace)
        delete(self.service)
        self.env.stop()
        self.tmp.cleanup()

    def send(self):
        self.assertTrue(self.parent.send('Delegate 17 + 26 to A, then independently check the result.'),
                        self.parent.error or self.parent.notice)
        return self.parent._request

    def complete(self):
        self.wait(lambda: not self.parent.busy and self.service.can_accept)
        self.app.processEvents()
        return io.load(self.root, 'parent.pcs-chat').document

    def receipt(self):
        row, = self.delegation.store.recent(self.agent['project'])
        return self.delegation.get(row['task_id'], local=True)

    def test_main_tool_child_saved_and_result_returns_to_original_main(self):
        request = self.send()
        saved = self.complete()
        record = self.receipt()
        self.assertEqual(record['parent_request_id'], request.request_id)
        self.assertEqual(record['state'], 'complete')
        self.assertTrue(record['result']['saved'])
        self.assertEqual(record['result']['draft'], '44')
        attempt = saved['payload']['turns'][0]['attempts'][0]
        self.assertEqual(attempt['text'], 'Checked independently: 17 + 26 = 43, not 44.')
        self.assertIn('pcs.chat-delegation.v1', saved['required_features'])
        self.assertEqual(len(self.runtime.starts), 1)
        self.assertNotIn('delegate_task', self.runtime.turns[1][3])
        self.assertTrue(all(t[2]['reasoning']['enable_thinking'] for t in self.runtime.turns))
        self.assertIn('"review_required": true', self.runtime.returns[0][0]['content'])
        self.assertIn('"draft": "44"', self.runtime.returns[0][0]['content'])
        self.assertTrue(any(e['operation'] == 'delegate_task' for e in attempt['delegation']['events']))
        broken = copy.deepcopy(saved); broken['required_features'].remove('pcs.chat-delegation.v1')
        with self.assertRaises(schema.DocumentError): schema.validate(broken)
        count = len(self.runtime.turns)
        self.open_path(self.parent.path)
        self.app.processEvents()
        self.assertEqual(len(self.runtime.turns), count)

    def test_duplicate_tool_call_returns_saved_child_without_running_twice(self):
        self.runtime.duplicate = True
        self.send(); self.complete()
        self.assertEqual(len(self.owners), 2)
        self.assertEqual(len(self.runtime.returns[0]), 2)
        self.assertIn(self.receipt()['task_id'], self.runtime.returns[0][1]['content'])
        self.assertIn('Identical result already shown', self.runtime.returns[0][1]['content'])

    def test_child_stop_keeps_parent_running_and_reports_stopped(self):
        self.runtime.child_release.clear()
        request = self.send()
        self.wait(self.runtime.child_entered.is_set)
        self.assertEqual(self.service._active, request)
        self.assertFalse(self.service.can_accept)
        self.assertFalse(self.service.submit_child(request, request))
        self.assertTrue(self.owners[-1].stop())
        self.complete()
        self.assertEqual(self.receipt()['state'], 'stopped')
        self.assertIn('"state": "stopped"', self.runtime.returns[0][0]['content'])

    def test_parent_stop_cascades_and_never_applies_late_child_to_parent(self):
        self.runtime.child_release.clear()
        self.send(); self.wait(self.runtime.child_entered.is_set)
        self.assertTrue(self.parent.stop())
        frozen = self.parent.path.read_bytes()
        self.complete()
        self.assertEqual(self.receipt()['state'], 'stopped')
        self.assertEqual(self.parent.path.read_bytes(), frozen)
        self.assertEqual(self.runtime.returns, [])

    def test_failed_limited_empty_children_are_returned_with_actual_state(self):
        for mode in ('failed', 'limited', 'empty'):
            with self.subTest(mode=mode):
                self.runtime.child_error = mode == 'failed'
                self.runtime.child_status = 'limited' if mode == 'limited' else 'completed'
                self.runtime.child_answer = '' if mode == 'empty' else '44'
                self.send(); self.complete()
                content = self.runtime.returns[-1][0]['content']
                self.assertIn('"state": "'+('complete' if mode == 'empty' else mode)+'"', content)
                if mode == 'empty': self.assertIn('"draft": ""', content)

    def test_child_model_switch_returns_to_original_main_model(self):
        path = self.root/'child.gguf'; path.write_bytes(b'GGUF'+(3).to_bytes(4, 'little')+b'child')
        child_model = self.service.register_model(path)
        self.assertTrue(self.service.select_model(self.model['id']))
        self.store.save_agent('A', self.agent['project'], child_model['id'], [], self.agent['id'])
        self.send(); self.complete()
        self.assertEqual([m['id'] for m, _ in self.runtime.starts],
                         [self.model['id'], child_model['id'], self.model['id']])
        self.assertEqual(self.service.selected_model, self.model['id'])

    def test_disabling_project_cancels_child_and_restores_direct_chat(self):
        self.runtime.child_release.clear()
        self.send(); self.wait(self.runtime.child_entered.is_set)
        self.delegation.set_enabled(self.agent['project'], False)
        self.complete()
        self.assertEqual(self.receipt()['state'], 'stopped')
        self.assertEqual(chat_catalog(self.workspace), [])
        self.assertTrue(self.parent.send('A direct question.'))
        saved = self.complete()
        self.assertNotIn('delegation', saved['payload']['turns'][-1]['attempts'][-1])
        self.assertEqual(len(self.runtime.generations), 1)

    def test_child_read_approval_belongs_to_child_and_parent_cancel_rejects(self):
        self.assertTrue(self.service.configure_approval_mode('ask'))
        self.runtime.read_source = True
        self.send(); self.wait(lambda: bool(self.reviews))
        request, ticket = self.reviews[0]
        self.assertEqual(request, self.owners[-1]._request)
        self.assertFalse(self.parent._owns(request))
        self.assertTrue(self.parent.stop())
        self.complete()
        self.assertTrue(ticket['done'].is_set())
        self.assertFalse(ticket['allow'])

    def test_chat_disclosure_opens_only_child_owned_by_this_attempt(self):
        from PySide6.QtWidgets import QWidget, QPushButton, QLabel
        from prompt_calculus_studio.local_ai.chat_page import ChatAttempt
        self.send(); saved = self.complete()
        container = QWidget()
        container.chat_document = self.parent
        view = ChatAttempt(container)
        attempt = saved['payload']['turns'][0]['attempts'][0]
        view.update_attempt(attempt, 0, 1, {})
        self.assertEqual(view.agent_evidence.text(), '子工作 · 1/1 已回傳')
        before = len(self.owners)
        view.agent_evidence.click()
        dialog = view.evidence_dialog
        next(b for b in dialog.findChildren(QPushButton) if b.text() == '開啟子對話').click()
        self.assertEqual(len(self.owners), before+1)
        view._delegation_request = 'foreign-attempt'
        view.agent_evidence.click()
        dialog = view.evidence_dialog
        next(b for b in dialog.findChildren(QPushButton) if b.text() == '開啟子對話').click()
        self.assertEqual(len(self.owners), before+1)
        self.assertTrue(any('只供檢視' in label.text() for label in dialog.findChildren(QLabel)))
        dialog.reject()
        delete(container)

    def test_child_cleanup_failure_settles_both_owners(self):
        self.runtime.fail_idle_once = True
        self.send()
        saved = self.complete()
        self.assertEqual(self.receipt()['state'], 'failed')
        self.assertEqual(saved['payload']['turns'][0]['attempts'][0]['status'], 'failed')
        self.assertIsNone(self.service._nested)

    def test_child_save_failure_is_not_returned_as_saved_success(self):
        original_open = self.workspace.open_path
        def opened(path, **options):
            tab = original_open(path, **options)
            original_save = tab.document.save
            def fail_final_save(*args, **kwargs):
                turns = tab.document.payload['turns']
                if turns and turns[-1]['attempts'][-1]['status'] != 'receiving':
                    tab.document.error = 'synthetic final save failure'
                    return False
                return original_save(*args, **kwargs)
            tab.document.save = fail_final_save
            return tab
        self.workspace.open_path = opened
        self.send(); self.complete()
        self.assertEqual(self.receipt()['state'], 'save_failed')
        self.assertIn('"saved": false', self.runtime.returns[0][0]['content'])

    def test_bound_main_only_sees_other_roles_in_its_project(self):
        other = self.store.save_project('Other project', self.root, [])
        self.store.save_agent('Other assistant', other['id'], self.model['id'], [])
        self.delegation.set_enabled(other['id'], True)
        main = self.store.save_agent('Main', self.agent['project'], self.model['id'], [])
        self.assertEqual([a['id'] for a in chat_catalog(self.workspace, main['id'])], [self.agent['id']])


class LinkedCancellation(unittest.TestCase):
    def test_child_only_and_parent_cancellation(self):
        parent = Cancellation()
        first, second = parent.child(), parent.child()
        first.cancel()
        self.assertFalse(parent.event.is_set())
        self.assertFalse(second.event.is_set())
        parent.release_child(first)
        parent.cancel()
        self.assertTrue(second.event.is_set())
        self.assertTrue(parent.child().event.is_set())


if __name__ == '__main__': unittest.main()
