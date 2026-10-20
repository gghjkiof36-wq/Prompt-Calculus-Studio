"""Prepare large HTML away from the UI, applying only to its captured source."""
from PySide6.QtCore import QObject, QThreadPool, Slot
from .jobs import Job
from .markdown_html import from_html
from .markdown_source import SourceEdit, utf16_position

LARGE_HTML = 128 * 1024


class PreparedPaste(QObject):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.job = None

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
        self.job = Job(lambda cancel, source=html: from_html(source))
        self.job.signals.finished.connect(self.finished)
        self.destroyed.connect(lambda _=None, flag=self.job.cancel: flag.set())
        surface.message = '正在整理貼上內容…'
        surface.update_statistics()
        QThreadPool.globalInstance().start(self.job)

    @Slot(object, str)
    def finished(self, text, error):
        self.job = None
        editor = self.editor
        surface = editor.surface
        if error:return surface.report(error+' 可使用 Ctrl+Shift+V 貼上純文字。')
        if (editor.isReadOnly() or surface.mode != self.mode or editor.page.buffer.composing
                or editor.page.buffer.snapshot() != self.snapshot):
            return surface.report('整理期間文件或編輯模式已變更，沒有覆蓋內容；請重新貼上。')
        try:
            if text:
                editor.page.buffer.apply(self.snapshot, [SourceEdit(self.start_at, self.end_at, text)])
                cursor = editor.textCursor()
                cursor.setPosition(utf16_position(editor.page.buffer.snapshot().text, self.start_at+len(text)))
                editor.setTextCursor(cursor)
            surface.message = ''
            surface.refresh()  # Never move keyboard focus from a newer active tab.
        except (ValueError, OSError) as error:surface.report(str(error))
