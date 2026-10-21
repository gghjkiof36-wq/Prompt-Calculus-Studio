"""Prepare large HTML away from the UI, applying only to its captured source."""
from PySide6.QtCore import QObject, QThreadPool, Slot
from .jobs import Job
from .markdown_html import from_html
from .markdown_source import SourceEdit, utf16_position
from .markdown_syntax import line_info

LARGE_HTML = 128 * 1024


class PreparedPaste(QObject):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.job = None
        self.insertion = None

    def start(self, html):
        surface = self.editor.surface
        if self.job:
            surface.report('上一段貼上內容還在整理，請稍候。')
            return
        self.snapshot, self.start_at, self.end_at = surface.selection()
        self.mode = surface.mode
        workspace = surface.page.file_title.workspace
        if workspace:
            tab = next((tab for tab in workspace.all_pages() if tab.widget is surface.page), None)
            if tab is not None:workspace.retain(tab)
        snapshot, start, end = self.snapshot, self.start_at, self.end_at
        def prepare(cancel):
            text = from_html(html)
            if cancel.is_set():return None
            merged = snapshot.text[:start]+text+snapshot.text[end:]
            return text, merged, line_info(merged)
        self.job = Job(prepare)
        self.job.signals.finished.connect(self.finished)
        self.destroyed.connect(lambda _=None, flag=self.job.cancel: flag.set())
        surface.message = '正在整理貼上內容…'
        surface.update_statistics()
        QThreadPool.globalInstance().start(self.job)

    @Slot(object, str)
    def finished(self, result, error):
        editor = self.editor
        surface = editor.surface
        if error:
            self.job = None
            return surface.report(error+' 可使用 Ctrl+Shift+V 貼上純文字。')
        if result is None:
            self.job = None
            return
        if (editor.isReadOnly() or surface.mode != self.mode or editor.page.buffer.composing
                or editor.page.buffer.snapshot() != self.snapshot):
            self.job = None
            return surface.report('整理期間文件或編輯模式已變更，沒有覆蓋內容；請重新貼上。')
        try:
            text, merged, lines = result
            def complete():
                self.job = None
                self.insertion = None
                cursor = editor.textCursor()
                cursor.setPosition(utf16_position(editor.page.buffer.snapshot().text, self.start_at+len(text)))
                editor.setTextCursor(cursor)
                surface.message = ''
                surface.refresh()  # Never move keyboard focus from a newer active tab.
            if text and merged != self.snapshot.text:
                from .markdown_insertion import SourceInsertion
                editor.page.buffer.remember_syntax(merged, lines)
                self.insertion = SourceInsertion(editor.page.buffer, self.snapshot,
                    [SourceEdit(self.start_at, self.end_at, text)], complete)
            else:complete()
        except (ValueError, OSError) as error:
            self.job = None
            surface.report(str(error))
