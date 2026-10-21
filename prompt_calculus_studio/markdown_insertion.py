"""Bounded GUI insertion, one source transaction and one native Undo step."""
import time
from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QTextCursor
from shiboken6 import isValid
from .markdown_source import utf16_position


class SourceInsertion(QObject):
    def __init__(self, buffer, snapshot, edits, finished):
        super().__init__(buffer)
        self.buffer, self.snapshot, self.finished = buffer, snapshot, finished
        prepared = buffer.prepare_edit(snapshot, edits)
        self.result, self.edits, self.pieces = prepared
        self.views = [p.md_surface.editor for p in buffer.views
                      if isValid(p) and hasattr(p, 'md_surface') and isValid(p.md_surface.editor)]
        self.readonly = [view.isReadOnly() for view in self.views]
        self.layouts = [buffer.text, *[view.document() for view in self.views]]
        self.layout_enabled = [doc.isLayoutEnabled() for doc in self.layouts]
        for view in self.views:
            view.syncing = view.forwarding = True
            view.setReadOnly(True)
        for doc in self.layouts:doc.setLayoutEnabled(False)
        self.cursors = [QTextCursor(doc) for doc in self.layouts]
        for cursor in self.cursors:cursor.beginEditBlock()
        buffer.pending_pieces = self.pieces
        buffer.mutation = self
        self.steps = self.insert()
        self.timer = QTimer(self);self.timer.setInterval(1)
        self.timer.timeout.connect(self.advance)
        self.timer.start()

    def insert(self):
        for edit in reversed(self.edits):
            for cursor in self.cursors:
                cursor.setPosition(utf16_position(self.snapshot.text, edit.start))
                cursor.setPosition(utf16_position(self.snapshot.text, edit.end), QTextCursor.MoveMode.KeepAnchor)
            text = edit.replacement
            if not text:
                for cursor in self.cursors:cursor.removeSelectedText()
            start = 0
            while start < len(text):
                # Bound text length and block creation; short lines are costly.
                end = min(len(text), start+32768)
                at = start
                for _ in range(512):
                    at = text.find('\n', at, end)
                    if at < 0:break
                    at += 1
                else:end = at
                for cursor in self.cursors:cursor.insertText(text[start:end])
                start = end
                yield

    def advance(self):
        deadline = time.perf_counter()+.006
        while time.perf_counter() < deadline:
            if not self.step():return

    def step(self):
        try:next(self.steps);return True
        except StopIteration:
            self.complete()
            return False

    def finish(self):
        # Explicit save/close completes this same transaction before encoding.
        while self.buffer.mutation is self:
            if not self.step():break

    def complete(self):
        self.timer.stop()
        self.buffer.mutation = None
        for cursor in self.cursors:cursor.endEditBlock()
        self.buffer.pending_pieces = None
        for doc, enabled in zip(self.layouts, self.layout_enabled):
            if isValid(doc):doc.setLayoutEnabled(enabled)
        for view, readonly in zip(self.views, self.readonly):
            if not isValid(view):continue
            view.syncing = view.forwarding = False
            view.setReadOnly(readonly)
            view.update_metadata()
        finished, self.finished = self.finished, None
        # A completed transaction must not retain editors from closed tabs.
        # WeakSet membership alone cannot detect a deleted Qt wrapper retained
        # by another Python owner, so collect only live views above as well.
        self.views.clear()
        self.layouts.clear()
        self.cursors.clear()
        self.steps = None
        try:finished()
        finally:self.deleteLater()
