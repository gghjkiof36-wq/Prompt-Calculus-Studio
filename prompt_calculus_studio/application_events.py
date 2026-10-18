"""Dispatch application-wide input hooks once, only for their event types.

Constructing/polishing a widget tree produces thousands of non-input events.
Separate QApplication filters made every one cross into every Python hook.
"""
import weakref
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid


class ApplicationEvents(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.listeners = {}
        app.installEventFilter(self)

    def add(self, listener, kinds):
        self.remove(listener)
        reference = weakref.ref(listener)
        for kind in kinds:
            self.listeners.setdefault(kind, []).append(reference)

    def remove(self, listener):
        for kind, entries in list(self.listeners.items()):
            alive = [ref for ref in entries if ref() is not None and ref() is not listener and isValid(ref())]
            if alive:self.listeners[kind] = alive
            else:self.listeners.pop(kind, None)

    def eventFilter(self, watched, event):
        # Qt calls the most recently installed application filter first.
        for reference in reversed(tuple(self.listeners.get(event.type(), ()))):
            listener = reference()
            if (reference in self.listeners.get(event.type(), ()) and
                    listener is not None and isValid(listener) and listener.eventFilter(watched, event)):
                return True
        return False


def listen(listener, *kinds):
    app = QApplication.instance()
    if not hasattr(app, '_pcs_application_events'):
        app._pcs_application_events = ApplicationEvents(app)
    app._pcs_application_events.add(listener, kinds)


def unlisten(listener):
    router = getattr(QApplication.instance(), '_pcs_application_events', None)
    if router is not None:router.remove(listener)
