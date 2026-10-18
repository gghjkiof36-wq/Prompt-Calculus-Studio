"""Disposable local Qt projection; source snapshots are the only edit targets.

This A adapter covers Qt's basic Markdown/HTML presentation. B/C extend the
presentation for live preview and Obsidian syntax without changing save format.
"""
from PySide6.QtCore import QByteArray
from PySide6.QtGui import QTextDocument


class MarkdownProjection(QTextDocument):
    def __init__(self, snapshot, parent=None):
        super().__init__(parent)
        self.setUndoRedoEnabled(False)
        self.refresh(snapshot)

    def refresh(self, snapshot):
        self.source = snapshot
        self.setMarkdown(snapshot.text)
        self.setModified(False)

    def loadResource(self, resource_type, name):
        # A has no attachment resolver. Returning handled, empty bytes prevents
        # QTextDocument from falling through to local paths or a global loader.
        return QByteArray()
