"""Real chat writer/owners and shared service, with a bounded fake engine."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from prompt_calculus_studio.local_agents.store import AgentStore
from prompt_calculus_studio.local_ai.chat_document import ChatDocument
from prompt_calculus_studio.local_ai.service import LocalAIService
from prompt_calculus_studio.local_documents import chat, identity, io
from prompt_calculus_studio.service_adapters import ServiceRequest
from test_local_chat_owner import FakeWorkspace, FakeService
from test_local_ai_auto_prepare import FakeRuntime


def register(data, root, model):
    skill = root / 'skill'
    skill.mkdir(exist_ok=True)
    (skill/'SKILL.md').write_text('---\nname: saving\ndescription: Find saving flow\n---\nRead reference.md.', encoding='utf8')
    (skill/'reference.md').write_text('Inspect the owner before saving.', encoding='utf8')
    project = root/'project'; project.mkdir(exist_ok=True)
    (project/'save.py').write_text('def save():\n    return "owner"\n', encoding='utf8')
    rule = root/'AGENTS.md'; rule.write_text('Inspect sources without changing them.', encoding='utf8')
    store = AgentStore(data)
    entry = store.add_skill(skill)
    project = store.save_project('PCS', project, [(str(rule), 'rule')])
    agent = store.save_agent('A', project['id'], model, [entry['id']])
    return store, entry, agent


class AgentService(FakeService):
    activity = Signal(object, object)
    review = Signal(object, object)
    approval_mode = 'auto'

    @property
    def can_accept(self):
        return self.can_submit


class AgentOwners(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.environment = patch.dict(os.environ, {'PROMPT_STUDIO_DATA': str(self.root/'runtime')})
        self.environment.start()
        self.workspace = FakeWorkspace(self.root)
        self.workspace.local_ai = AgentService(self.workspace)
        self.service = self.workspace.local_ai
        self.store, self.skill, self.agent = register(self.workspace.data, self.root, 'agent-model')
        self.owners = []
        for name in ('A', 'B'):
            result = io.create(self.root, name+'.pcs-chat', chat.empty())
            self.assertEqual(result.status, 'saved', result.error)
            owner = ChatDocument(self.root/(name+'.pcs-chat'), self.workspace)
            self.workspace.pages.append(SimpleNamespace(document=owner))
            self.owners.append(owner)
        self.a, self.b = self.owners
        self.service.read_document = lambda: io.load(self.a.path.parent, self.a.path.name).document
        self.assertTrue(self.a.set_agent(self.agent['id']))

    def tearDown(self):
        for owner in self.owners:
            owner.dispose()
        delete(self.workspace)
        self.environment.stop()
        self.tmp.cleanup()

    def send(self):
        self.a.set_draft('@saving find saving flow')
        self.assertTrue(self.a.send(self.a.payload['draft']), self.a.error or self.a.notice)
        return self.service.submitted[-1]

    def event(self, request, text='version-one'):
        self.service.activity.emit(request, dict(operation='read_skill', target='saving/SKILL.md',
            content=text, sha256='a'*64, status='read', skill=self.skill['id']))

    def test_binding_and_frozen_evidence_persist_without_changing_other_chat(self):
        global_model = self.service.selected_model
        untouched = self.b.path.read_bytes()
        request = self.send()
        self.assertEqual(request.operation, 'agent')
        self.assertEqual(request.input['model'], 'agent-model')
        accepted = self.service.persisted_at_submit[-1]['payload']['turns'][-1]['attempts'][-1]
        self.assertEqual(accepted['agent']['request'], request.input['agent'])
        self.assertFalse(self.a.set_agent(''))
        (self.root/'skill'/'SKILL.md').write_text('changed externally', encoding='utf8')
        self.event(request)
        self.service.complete(request, 'Answer with evidence')
        self.assertEqual(self.service.selected_model, global_model)
        self.assertEqual(self.b.path.read_bytes(), untouched)
        loaded = io.load(self.root, self.a.path.name).document
        self.assertEqual(loaded['payload']['agent_id'], self.agent['id'])
        evidence = loaded['payload']['turns'][-1]['attempts'][-1]['agent']
        self.assertEqual(evidence['events'][0]['content'], 'version-one')
        self.assertEqual(evidence['request']['skills'][0]['revision'], self.skill['revision'])
        prefix = chat.branch_payload(loaded['payload'], loaded['payload']['turns'][0]['id'])
        self.assertEqual(prefix['agent_id'], self.agent['id'])

    def test_cancel_retarget_close_and_late_read_events_keep_owner(self):
        request = self.send()
        self.event(request)
        self.a.stop()
        original = self.a.path.read_bytes()
        created = io.create(self.root, 'copy.pcs-chat', identity.duplicate(self.a.project()).document)
        self.a.retarget(created, self.root/'copy.pcs-chat')
        self.assertTrue(self.a.retry(), self.a.notice)
        current = self.service.submitted[-1]
        before = copy.deepcopy(self.a.payload)
        self.event(request, 'late foreign text')
        self.service.complete(request, 'late answer')
        self.assertEqual(self.a.payload, before)
        self.assertEqual((self.root/'A.pcs-chat').read_bytes(), original)
        self.event(current, 'current copy')
        self.a.dispose()
        after = self.a.path.read_bytes()
        self.event(current, 'late after close')
        self.service.complete(current, 'after close')
        self.assertEqual(self.a.path.read_bytes(), after)
        self.assertEqual(self.b.payload['turns'], [])

    def test_read_approval_only_accepts_current_owner(self):
        from threading import Event
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QPushButton
        from prompt_calculus_studio.local_ai.approval import ActionRequest
        request = self.send()
        action = ActionRequest('read_file', {'arguments': '{"path":"save.py"}'}, 'Read save.py', False, False)
        ticket = {'action': action, 'done': Event(), 'allow': False}
        self.service.review.emit(request, ticket)
        self.app.processEvents()
        dialog = self.a._approval_dialog
        control = next(b for b in dialog.findChildren(QPushButton) if b.text() == '允許本次')
        QTest.mouseClick(control, Qt.MouseButton.LeftButton)
        self.assertTrue(ticket['done'].is_set())
        self.assertTrue(ticket['allow'])
        later = {'action': action, 'done': Event(), 'allow': False}
        self.service.review.emit(request, later)
        self.a.stop()
        self.a._approval_dialog.accept()
        self.assertTrue(later['done'].is_set())
        self.assertFalse(later['allow'])


class ToolRuntime(FakeRuntime):
    def tool_turn(self, messages, params, cancel, tools):
        self.generate_entered.set()
        while not self.generate_release.wait(.01):
            cancel.check()
        cancel.check()
        prior = sum(m['role'] == 'tool' for m in messages)
        if prior < 2:
            path = 'SKILL.md' if prior == 0 else 'reference.md'
            return 'completed', dict(role='assistant', content='', tool_calls=[dict(id=str(prior), type='function',
                function=dict(name='read_skill', arguments=json.dumps(dict(skill='saving', path=path))))])
        return 'completed', dict(role='assistant', content='inspected owner')


class AgentSharedService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        engine = self.root/'llama-server.exe'; engine.write_bytes(b'fixture')
        self.runtime = ToolRuntime()
        self.service = LocalAIService(self.root/'data', runtime_factory=lambda: self.runtime)
        self.assertTrue(self.service.configure(dict(runtime_path=str(engine), idle_minutes=0)))
        model = self.root/'model.gguf'; model.write_bytes(b'GGUF'+(3).to_bytes(4, 'little'))
        model = self.service.register_model(model)
        self.store, self.skill, self.agent = register(self.root/'data', self.root, model['id'])
        self.results, self.events = [], []
        self.service.finished.connect(lambda *args: self.results.append(args))
        self.service.activity.connect(lambda *args: self.events.append(args))

    def wait(self, predicate):
        end = time.monotonic()+8
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail('service timed out: '+self.service.error)

    def tearDown(self):
        self.service.shutdown()
        self.runtime.generate_release.set()
        self.wait(lambda: self.service._worker is None)
        delete(self.service)
        self.tmp.cleanup()

    def request(self):
        return ServiceRequest('local_ai', 'agent', scope={'document_id': 'isolated'}, input=dict(
            model=self.agent['model'], params=self.service.settings,
            messages=[dict(role='user', content='@saving inspect owner')],
            agent=self.store.request(self.agent['id'], '@saving inspect owner', 'auto')))

    def test_agent_and_plain_chat_share_one_slot_and_engine(self):
        request = self.request()
        selected = self.service.selected_model
        self.runtime.generate_release.clear()
        self.assertTrue(self.service.submit(request), self.service.error)
        self.wait(self.runtime.generate_entered.is_set)
        self.assertFalse(self.service.submit(self.request()))
        self.runtime.generate_release.set()
        self.wait(lambda: len(self.results) == 1 and self.service.state == 'ready')
        self.assertEqual(self.results[-1][1:], ('completed', 'inspected owner'))
        self.assertEqual(len([e for _,e in self.events if e['operation']=='read_skill']), 2)
        self.assertEqual(self.service.selected_model, selected)
        plain = ServiceRequest('local_ai', 'chat', scope={'document_id':'other'}, input=dict(
            model=selected, params=self.service.settings, messages=[dict(role='user', content='hello')]))
        self.assertTrue(self.service.submit(plain), self.service.error)
        self.wait(lambda: len(self.results) == 2 and self.service.state == 'ready')
        self.assertEqual(len(self.runtime.starts), 1)
        self.assertEqual(self.results[-1][2], '合成回覆')

    def test_time_limit_interrupts_model_and_releases_slot(self):
        self.runtime.generate_release.clear()
        with patch('prompt_calculus_studio.local_agents.execution.MAX_SECONDS', .1):
            request = self.request()
            self.assertTrue(self.service.submit(request), self.service.error)
            self.wait(lambda: bool(self.results) and self.service.state == 'ready')
        self.assertEqual(self.results[0][1], 'error')
        self.assertIn('時間上限', self.results[0][2])
        self.assertIsNone(self.service._active)


if __name__ == '__main__':
    unittest.main()
