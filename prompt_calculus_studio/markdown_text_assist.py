"""Local text assistance adopts one candidate into one captured source range."""
import weakref

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QTextCursor
from shiboken6 import isValid

from .markdown_source import SourceEdit, utf16_position


class MarkdownTextTarget(QObject):
    """Keep original source, view and location authoritative until adoption."""
    def __init__(self, surface, snapshot, start, end, *, kind='selection'):
        super().__init__(surface)
        page = surface.page
        workspace = page.file_title.workspace
        if surface.mode == 'read' or page.buffer.opening or page.buffer.composing:
            raise ValueError('請先回到編寫模式，並完成目前的組字。')
        if not 0 <= start < end <= len(snapshot.text) or not snapshot.text[start:end].strip():
            raise ValueError('請選取要協作的文字，或在有文字的段落按右鍵。')
        if page.buffer.snapshot() != snapshot:
            raise ValueError('原文已變更，請重新選取文字。')
        tab = next((tab for tab in workspace.tabs_by_key.values() if tab.widget is page), None) if workspace else None
        if tab is None:
            raise ValueError('請在工作區中開啟這份 Markdown 文件。')
        self._surface = weakref.ref(surface)
        self._workspace = weakref.ref(workspace)
        self._buffer = weakref.ref(page.buffer)
        self.tab_key, self.path = tab.key, page.path
        self.save_key = getattr(page.buffer, 'save_key', None)
        self.snapshot, self.start, self.end, self.kind = snapshot, start, end, kind
        self.used = self._applying = False
        self.controller = None
        page.buffer.text.contentsChanged.connect(self._source_changed)

    @property
    def source_text(self):
        return self.snapshot.text[self.start:self.end]

    @property
    def scope(self):
        return dict(document_id=self.snapshot.document_id, revision=self.snapshot.revision,
                    path=str(self.path), owner=self.tab_key, start=self.start, end=self.end, kind=self.kind)

    def bind(self, controller):
        self.controller = controller
        surface = self._surface()
        if surface is not None:
            surface.destroyed.connect(controller.dispose)

    def _source_changed(self):
        if self._applying or self.used or self.controller is None:
            return
        buffer = self._buffer()
        if buffer is None or buffer.snapshot() != self.snapshot:
            self.controller.invalidate('原文已變更。請重新選取文字，再開啟文字協作。')

    def check(self):
        surface, workspace, buffer = self._surface(), self._workspace(), self._buffer()
        if self.used:
            raise ValueError('這份候選已套用。')
        if surface is None or not isValid(surface) or workspace is None or not isValid(workspace):
            raise ValueError('原文件已關閉。')
        tab = workspace.tabs_by_key.get(self.tab_key)
        if tab is None or tab.widget is not surface.page or surface.page.buffer is not buffer:
            raise ValueError('原文件已關閉，請重新開啟並選取文字。')
        if (surface.page.path != self.path or getattr(buffer, 'save_key', None) != self.save_key
                or buffer.snapshot() != self.snapshot):
            raise ValueError('原文或文件位置已變更。請重新選取文字，再開啟文字協作。')
        if surface.mode == 'read' or surface.editor.isReadOnly() or buffer.composing:
            raise ValueError('請先回到編寫模式，並完成目前的組字。')
        if surface.page.operation_error or buffer.mapping_error:
            raise ValueError('請先處理這份文件的保存或原文錯誤。')
        return surface, buffer

    def apply(self, mode, text):
        surface, buffer = self.check()
        if mode not in ('replace', 'insert') or not isinstance(text, str) or not text.strip():
            raise ValueError('尚無可套用的候選文字。')
        text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\u2029', '\n')
        if mode == 'replace':
            edit = SourceEdit(self.start, self.end, text)
        else:
            # Append as a separate Markdown paragraph, preserving every source byte.
            before, after = self.snapshot.text[:self.end], self.snapshot.text[self.end:]
            prefix = '\n' * max(0, 2 - (len(before) - len(before.rstrip('\n'))))
            suffix = '\n' * max(0, 2 - (len(after) - len(after.lstrip('\n')))) if after else ''
            edit = SourceEdit(self.end, self.end, prefix + text + suffix)
        self._applying = True
        try:
            buffer.apply(self.snapshot, [edit])
            self.used = True
        finally:
            self._applying = False
        surface.message = ''
        surface.refresh()
        cursor = surface.editor.textCursor()
        current = buffer.snapshot().text
        cursor.setPosition(utf16_position(current, edit.start))
        cursor.setPosition(utf16_position(current, edit.start + len(edit.replacement)), QTextCursor.MoveMode.KeepAnchor)
        surface.editor.setTextCursor(cursor)
        surface.editor.ensureCursorVisible()
        return True


def add_text_assist_action(menu, surface, position):
    """Freeze a selection, or the right-clicked source paragraph, before opening."""
    snapshot, start, end = surface.selection()
    kind = 'selection'
    if start == end or not start <= position <= end:
        start = snapshot.text.rfind('\n', 0, position) + 1
        end = snapshot.text.find('\n', position)
        if end < 0:
            end = len(snapshot.text)
        kind = 'paragraph'
    action = menu.addAction('文字協作…', lambda: open_text_assist(surface, snapshot, start, end, kind=kind))
    action.setEnabled(surface.mode != 'read' and not surface.page.buffer.opening
                      and not surface.page.buffer.composing and bool(snapshot.text[start:end].strip()))
    menu.addSeparator()
    return action


def open_text_assist(surface, snapshot=None, start=None, end=None, *, kind='selection'):
    from .local_ai.service import for_workspace
    from .local_ai.text_assist import TextAssistController
    from .local_ai.text_assist_dialog import TextAssistDialog
    if snapshot is None:
        snapshot, start, end = surface.selection()
    try:
        target = MarkdownTextTarget(surface, snapshot, start, end, kind=kind)
    except ValueError as error:
        surface.report(str(error))
        return None
    workspace = surface.page.file_title.workspace
    service = for_workspace(workspace)
    if not hasattr(service, 'can_accept'):
        target.deleteLater()
        surface.report('請重新開啟 PCS，以使用文字協作。')
        return None
    previous = getattr(surface, 'text_assist_dialog', None)
    if previous is not None and isValid(previous):
        previous.close()
    workspace.retain(workspace.tabs_by_key[target.tab_key])
    cursor = surface.editor.textCursor()
    cursor.setPosition(utf16_position(snapshot.text, start))
    cursor.setPosition(utf16_position(snapshot.text, end), QTextCursor.MoveMode.KeepAnchor)
    surface.editor.setTextCursor(cursor)
    controller = TextAssistController(service,
                                      source_text=target.source_text, source_scope=target.scope)
    dialog = TextAssistDialog(surface, controller, target.apply)
    controller.setParent(dialog)
    target.setParent(dialog)
    target.bind(controller)
    dialog.target = target
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    surface.text_assist_dialog = dialog
    dialog.show()
    return dialog
