"""Global input hooks retain Qt ordering without processing layout events."""
import unittest
from PySide6.QtCore import QObject, QEvent, QCoreApplication
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.application_events import ApplicationEvents


class Listener(QObject):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def eventFilter(self, watched, event):
        return self.callback(watched, event)


class ApplicationEventsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.router = ApplicationEvents(self.app)
        self.target = QObject()
        self.calls = []
        self.listeners = []

    def tearDown(self):
        self.app.removeEventFilter(self.router)
        self.router.deleteLater()

    def add(self, name, result=False):
        listener = Listener(lambda *_: self.calls.append(name) or result)
        self.listeners.append(listener)
        self.router.add(listener, (QEvent.Type.User,))
        return listener

    def send(self, kind=QEvent.Type.User):
        QCoreApplication.sendEvent(self.target, QEvent(kind))

    def test_only_subscribed_events_in_reverse_order_and_consumption(self):
        self.add('first')
        self.add('second')
        self.send(QEvent.Type.LayoutRequest)
        self.assertEqual(self.calls, [])
        self.send()
        self.assertEqual(self.calls, ['second', 'first'])
        self.calls.clear()
        self.add('consume', True)
        self.send()
        self.assertEqual(self.calls, ['consume'])

    def test_unregister_during_dispatch_and_deleted_qobject(self):
        first = self.add('first')
        removed = Listener(lambda *_: self.router.remove(first) or False)
        self.listeners.append(removed)
        self.router.add(removed, (QEvent.Type.User,))
        self.send()
        self.assertEqual(self.calls, [])
        deleted = self.add('deleted')
        deleted.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.send()
        self.assertEqual(self.calls, [])
        self.router.remove(removed)
        self.assertEqual(self.router.listeners, {})
