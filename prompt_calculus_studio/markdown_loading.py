"""Load large sources in bounded GUI turns; retain original bytes until ready."""
import time
import weakref
from PySide6.QtCore import QObject, QTimer, QRunnable, QThreadPool, Signal, Slot, Qt
from PySide6.QtGui import QTextCursor
from shiboken6 import isValid
from .markdown_syntax import line_info


class MetadataResult(QObject):
    ready = Signal(object, str)


class MetadataJob(QRunnable):
    def __init__(self, text):
        super().__init__()
        self.text = text
        self.result = MetadataResult()

    def run(self):
        try:
            lines = line_info(self.text)
            self.result.ready.emit((lines, [item['start'] for item in lines]), '')
        except Exception as error:
            self.result.ready.emit(None, str(error))


class SourceLoading(QObject):
    """No partial document may be edited, saved or treated as a complete view."""
    def __init__(self, buffer, text):
        super().__init__(buffer)
        self.buffer, self.text = buffer, text
        self.position = 0
        self.positions = weakref.WeakKeyDictionary()
        self.metadata = None
        self.timer = QTimer(self)
        self.timer.setInterval(0)
        self.timer.timeout.connect(self.advance)
        self.job = MetadataJob(text)
        self.job.result.ready.connect(self.prepared, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(self.job)
        self.timer.start()

    @Slot(object, str)
    def prepared(self, metadata, error):
        if error:
            self.timer.stop()
            self.buffer.error = '文件載入未完成；原檔保持不變，請關閉後重新開啟。'
            for view in self.buffer.views:
                if isValid(view):view.md_surface.report(self.buffer.error)
        self.metadata = metadata
        self.job = None

    def append(self, document, position):
        end = min(len(self.text), position + 131_072)
        # Bound dense documents by blocks as well as characters.
        boundary = position
        for _ in range(2048):
            boundary = self.text.find('\n', boundary, end)
            if boundary < 0:
                break
            boundary += 1
        else:
            end = boundary
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(self.text[position:end])
        return end

    def advance(self):
        views = [view.md_surface for view in self.buffer.views
                 if isValid(view) and hasattr(view, 'md_surface')]
        if not views:
            self.timer.stop()
            return
        deadline = time.perf_counter() + .006
        while time.perf_counter() < deadline:
            changed = False
            if self.position < len(self.text):
                self.position = self.append(self.buffer.text, self.position)
                changed = True
            for surface in views:
                editor = surface.editor
                position = self.positions.get(editor, 0)
                if position < len(self.text):
                    self.positions[editor] = self.append(editor.document(), position)
                    changed = True
            if not changed:
                break
        if self.position < len(self.text) or self.metadata is None:
            return
        if any(self.positions.get(surface.editor, 0) < len(self.text) for surface in views):
            return
        self.timer.stop()
        self.buffer.text.setLayoutEnabled(True)
        self.buffer.opening = False
        self.buffer.loading = False
        from .markdown_source import SourceSnapshot
        self.buffer.snapshot_key = (self.buffer.text.revision(), id(self.buffer.text))
        self.buffer.cached_snapshot = SourceSnapshot(self.text, self.buffer.text.revision(), self.buffer.document_id)
        self.buffer.text.setUndoRedoEnabled(True)
        self.buffer.text.setModified(False)
        snapshot = self.buffer.snapshot()
        for surface in views:
            editor = surface.editor
            editor.lines, editor._line_starts = self.metadata
            editor._metadata_snapshot = snapshot
            editor._presentation_key = None
            editor.update_metadata()
            editor.document().setLayoutEnabled(True)
            editor.document().setModified(False)
            editor.setReadOnly(False)
            surface.refresh()
        self.buffer.opened.emit()
        del self.buffer.opening_snapshot
        self.text = ''
        self.metadata = None
