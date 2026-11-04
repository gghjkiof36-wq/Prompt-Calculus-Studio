"""Two real document owners and Qt worker; no model, executable or GPU runs."""
import copy
import os
from pathlib import Path
import tempfile
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from shiboken6 import delete

from prompt_calculus_studio.local_ai.chat_document import ChatDocument
from prompt_calculus_studio.local_ai.service import LocalAIService
from prompt_calculus_studio.local_documents import chat, io
from test_local_agent_owners import ToolRuntime, register
from test_local_chat_owner import FakeWorkspace


class AgentReviewOwnership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.environment = patch.dict(os.environ, {'PROMPT_STUDIO_DATA': str(self.root/'data')})
        self.environment.start()
        self.workspace = FakeWorkspace(self.root)
        self.workspace.data = self.root/'data'
        self.runtime = ToolRuntime()
        self.service = LocalAIService(self.workspace.data, runtime_factory=lambda: self.runtime)
        self.workspace.local_ai = self.service
        engine = self.root/'llama-server.exe'; engine.write_bytes(b'fixture only')
        self.assertTrue(self.service.configure(dict(runtime_path=str(engine), idle_minutes=0)))
        self.assertTrue(self.service.configure_approval_mode('ask'))
        model = self.root/'model.gguf'; model.write_bytes(b'GGUF'+(3).to_bytes(4, 'little'))
        model = self.service.register_model(model)
        _, _, self.agent = register(self.workspace.data, self.root, model['id'])
        self.reviews, self.results, self.events = [], [], []
        self.before_delivery = None
        self.service.review.connect(self.observe_review)
        self.service.finished.connect(lambda *args: self.results.append(args))
        self.service.activity.connect(lambda *args: self.events.append(args))
        self.owners = []
        for name in ('A', 'B'):
            result = io.create(self.root, name+'.pcs-chat', chat.empty())
            self.assertEqual(result.status, 'saved', result.error)
            owner = ChatDocument(self.root/(name+'.pcs-chat'), self.workspace)
            self.workspace.pages.append(SimpleNamespace(document=owner))
            self.owners.append(owner)
        self.a, self.b = self.owners
        self.assertTrue(self.a.set_agent(self.agent['id']))
        self.other_bytes = self.b.path.read_bytes()

    def observe_review(self, request, ticket):
        self.reviews.append((request, ticket))
        if self.before_delivery is not None:
            self.before_delivery()

    def wait(self, predicate):
        end = time.monotonic()+5
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail('approval did not finish within 5 seconds: '+self.service.error)

    def tearDown(self):
        self.before_delivery = None
        for owner in self.owners:
            owner.dispose()
            dialog = getattr(owner, '_approval_dialog', None)
            if dialog is not None:
                dialog.reject()
        self.runtime.generate_release.set()
        self.service.shutdown()
        self.wait(lambda: self.service._worker is None)
        delete(self.workspace)
        delete(self.service)
        self.environment.stop()
        self.tmp.cleanup()

    def send(self):
        self.a.set_draft('@saving find saving flow')
        self.assertTrue(self.a.send(self.a.payload['draft']), self.a.error or self.a.notice)
        return self.a._request

    def pending(self, index):
        self.wait(lambda: len(self.reviews) > index)
        request, ticket = self.reviews[index]
        # Cross the dialog's 200 ms check: a foreign receiver must not cause it
        # to close, nor let the worker continue before the actual decision.
        QTest.qWait(230)
        self.assertFalse(ticket['done'].is_set(), 'another document ended the approval')
        self.assertFalse(ticket['allow'])
        self.assertTrue(self.a._owns(request))
        self.assertTrue(self.a._approval_dialog.isVisible())
        self.assertFalse(hasattr(self.b, '_approval_dialog'))
        return ticket

    def answer(self, allow):
        text = '允許本次' if allow else '拒絕'
        button = next(b for b in self.a._approval_dialog.findChildren(QPushButton) if b.text() == text)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)

    def reads(self):
        # Registered role rules are loaded at acceptance; only tool reads wait
        # for these tickets. Keep those two existing scopes distinct.
        return [event for _, event in self.events
                if event['status'] == 'read' and event['operation'] == 'read_skill']

    def check_broadcast(self, owner_first):
        for owner in self.owners:
            self.service.review.disconnect(owner._agent_review)
        for owner in (self.a, self.b) if owner_first else (self.b, self.a):
            self.service.review.connect(owner._agent_review)
        request = self.send()
        first = self.pending(0)
        self.assertEqual(self.reads(), [])
        self.answer(True)
        self.assertTrue(first['done'].is_set())
        self.assertTrue(first['allow'])
        second = self.pending(1)
        self.answer(False)
        self.assertTrue(second['done'].is_set())
        self.assertFalse(second['allow'])
        self.wait(lambda: self.results and self.service.can_accept)
        self.assertEqual(self.results[-1], (request, 'completed', 'inspected owner'))
        self.assertEqual([event['target'] for event in self.reads()], ['saving/SKILL.md'])
        saved = io.load(self.root, self.a.path.name).document['payload']
        self.assertEqual(saved, self.a.payload)
        self.assertEqual(self.b.path.read_bytes(), self.other_bytes)
        self.assertEqual(self.service.approval_mode, 'ask')

    def test_owner_then_other_document_allow_and_deny(self):
        self.check_broadcast(True)

    def test_other_document_then_owner_allow_and_deny(self):
        self.check_broadcast(False)

    def check_cancel(self, dispose):
        request = self.send()
        ticket = self.pending(0)
        self.assertTrue(self.a.dispose() if dispose else self.a.stop())
        self.wait(lambda: ticket['done'].is_set() and self.results and self.service.can_accept
                  and not self.a._approval_dialog.isVisible())
        self.assertFalse(ticket['allow'])
        self.assertFalse(self.a._approval_dialog.isVisible())
        self.assertEqual(self.results[-1][:2], (request, 'cancelled'))
        self.assertEqual(self.reads(), [])
        self.assertEqual(self.b.path.read_bytes(), self.other_bytes)

    def test_stop_during_approval_releases_worker(self):
        self.check_cancel(False)

    def test_dispose_during_approval_releases_worker(self):
        self.check_cancel(True)

    def test_cancel_before_document_delivery_settles_ticket(self):
        # The queued service notification can arrive just as its owner closes.
        # No document will create a dialog, so the worker must settle its ticket.
        self.before_delivery = self.a.stop
        request = self.send()
        self.wait(lambda: self.reviews and self.results and self.service.can_accept)
        ticket = self.reviews[0][1]
        self.assertTrue(ticket['done'].is_set())
        self.assertFalse(ticket['allow'])
        self.assertFalse(hasattr(self.a, '_approval_dialog'))
        self.assertEqual(self.results[-1][:2], (request, 'cancelled'))
        self.assertEqual(self.reads(), [])

    def test_old_broadcast_is_ignored_and_service_rejects_old_ticket(self):
        old = self.send()
        first = self.pending(0)
        self.assertTrue(self.a.stop())
        self.wait(lambda: first['done'].is_set() and self.service.can_accept)
        current = self.send()
        pending = self.pending(1)
        current_dialog = self.a._approval_dialog
        before = copy.deepcopy(self.a.payload)
        late = dict(action=pending['action'], done=Event(), allow=False)
        self.service.review.emit(old, late)
        self.assertFalse(late['done'].is_set())
        self.assertFalse(late['allow'])
        self.assertEqual(self.a.payload, before)
        self.assertIs(self.a._approval_dialog, current_dialog)
        self.assertFalse(pending['done'].is_set())
        self.assertTrue(self.a._owns(current))
        self.service._on_review(old, late)
        self.assertTrue(late['done'].is_set())
        self.assertFalse(late['allow'])
        self.assertFalse(pending['done'].is_set())
        self.assertTrue(self.a.stop())
        self.wait(lambda: pending['done'].is_set() and self.service.can_accept)
        self.assertEqual(self.b.path.read_bytes(), self.other_bytes)

    def test_expired_ticket_cannot_be_approved_late(self):
        # Advance only the service's clock; the real Qt worker and 120 s branch
        # run normally without waiting two minutes or changing product limits.
        offset = [0.]
        clock = SimpleNamespace(monotonic=lambda: time.monotonic()+offset[0], sleep=time.sleep)
        with patch('prompt_calculus_studio.local_ai.service.time', clock):
            self.send()
            first = self.pending(0)
            old_dialog = self.a._approval_dialog
            offset[0] = 121.
            self.wait(lambda: first['done'].is_set() and len(self.reviews) == 2)
            second = self.reviews[1][1]
            # A delayed accept callback must not overwrite the terminal denial,
            # even while the same request still owns its next approval.
            old_dialog.accept()
            self.assertFalse(first['allow'])
            self.assertFalse(second['done'].is_set())
            self.answer(False)
            self.wait(lambda: self.results and self.service.can_accept)
        self.assertEqual(self.reads(), [])
        self.assertEqual(self.b.path.read_bytes(), self.other_bytes)


if __name__ == '__main__':
    unittest.main()
