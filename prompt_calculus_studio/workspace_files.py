"""File creation and plain Markdown editing for the local workspace."""
from pathlib import Path
from PySide6.QtCore import QSaveFile, QIODevice, QObject, Signal
from PySide6.QtWidgets import QPlainTextEdit, QFrame, QPlainTextDocumentLayout
from PySide6.QtGui import QTextDocument, QTextCursor
import weakref
import uuid
from shiboken6 import isValid
from .markdown_source import (MAX_BYTES, SourceSnapshot, SourceLedger, source_text,
                              edited_text, utf16_position, text_style_edit)
from .core import initial_state, uid
from .local_documents import category, workflow, io, paths


def create_file(root, kind, name):
    if not name or '/' in name or '\\' in name:
        raise ValueError('請輸入檔案名稱，不含路徑。')
    # Validate the entered stem before adding a suffix: otherwise trailing dots
    # and spaces can silently become apparently valid document names.
    paths.relative_parts(name)
    if name.casefold() in ('.pcs', 'assets'):
        raise ValueError('這個名稱保留給程式內部資料，請使用其他名稱。')
    if kind == 'folder':
        target = paths.checked(root, name, new=True)
        target.mkdir()  # Exclusive; a concurrent directory is not our success.
        return target
    suffix = {'category': '.pcs-category', 'workflow': '.pcs-workflow', 'markdown': '.md'}[kind]
    stem = name[:-len(suffix)] if name.casefold().endswith(suffix) else name
    if not stem.strip():
        raise ValueError('請輸入副檔名之前的名稱。')
    relative = name if name.casefold().endswith(suffix) else name+suffix
    target = paths.checked(root, relative, new=True)
    if kind == 'markdown':
        with target.open('xb'):
            pass
    else:
        state = initial_state()
        state['items'] = []
        state['modules'] = []
        state['dictionary'] = {}
        state['workspaces'][0].update(name=target.stem, fixed=[], picks={})
        if kind == 'category':
            ident = uid()
            state['modules'] = [dict(id=ident, name=target.stem, mode='multiple')]
            document = category.from_state(state, ident)
        else:
            from .state_loading import prepare_state
            state = prepare_state(state, multi=True)
            document = workflow.from_state(state)
        result = io.create(root, relative, document)
        if result.status != 'saved':
            message = result.error or '無法建立文件。'
            if target.exists():
                message += '\n目的位置已有檔案，未覆寫或移除：\n' + str(target)
            if result.recovery_paths:
                message += '\n保留的回復版本：\n' + '\n'.join(result.recovery_paths)
            raise ValueError(message)
    return target


class MarkdownBuffer(QObject):
    """One source/undo owner per file; projections never write back HTML."""
    compositionChanged = Signal()

    def __init__(self):
        super().__init__()
        self.views = weakref.WeakSet()
        self.composing = set()
        self.external_stamp = None
        self.external_error = ''
        self.document_id = uuid.uuid4().hex
        self.loading = False
        self.mapping_error = ''
        self.pending_pieces = None

    def changed(self, position, removed, added):
        if self.loading:
            return
        try:
            text = source_text(self.text)
            if self.pending_pieces is not None:
                if self.ledger.text_of(self.pending_pieces) != text:
                    raise ValueError('編輯交易尚未完整套用，內容尚未保存。')
                self.ledger.accept(self.pending_pieces, self.text.availableUndoSteps())
            else:
                self.ledger.changed(position, removed, added, text, self.text.availableUndoSteps())
        except (ValueError, UnicodeError) as error:
            self.mapping_error = self.error = str(error)

    def snapshot(self):
        return SourceSnapshot(source_text(self.text), self.text.revision(), self.document_id)

    def apply(self, snapshot, edits):
        if self.composing:
            raise ValueError('請先完成目前的中文組字。')
        if snapshot != self.snapshot():
            raise ValueError('內容已變更，請重新選取後操作。')
        result, ordered = edited_text(snapshot, edits)
        if self.mapping_error:
            raise ValueError(self.mapping_error)
        pieces = self.ledger.pieces
        try:
            for edit in reversed(ordered):
                self.ledger.pieces = self.ledger.replacement(edit.start, edit.end, edit.replacement)
            self.ledger.encoded()  # Validate exact prospective bytes before mutating Qt.
            prospective = self.ledger.pieces
        finally:
            self.ledger.pieces = pieces
        if result == snapshot.text:
            return False
        cursor = QTextCursor(self.text)
        self.pending_pieces = prospective
        cursor.beginEditBlock()
        try:
            for edit in reversed(ordered):
                cursor.setPosition(utf16_position(snapshot.text, edit.start))
                cursor.setPosition(utf16_position(snapshot.text, edit.end), QTextCursor.MoveMode.KeepAnchor)
                cursor.insertText(edit.replacement)
        finally:
            cursor.endEditBlock()
            self.pending_pieces = None
        return True


class BufferField:
    def __set_name__(self, owner, name): self.name = name
    def __get__(self, page, owner=None):
        return self if page is None else getattr(page.buffer, self.name, False if self.name == 'operation_error' else '')
    def __set__(self, page, value): setattr(page.buffer, self.name, value)


class MarkdownPage(QPlainTextEdit):
    compositionChanged = Signal()
    path = BufferField()
    baseline = BufferField()
    error = BufferField()
    operation_error = BufferField()

    def __init__(self, path, buffer=None):
        super().__init__()
        self.buffer = buffer or MarkdownBuffer()
        if buffer is None:
            self.path = Path(path)
            self.baseline = paths.read_bytes(self.path, 8*1024*1024)[0]
            self.buffer.original = self.baseline
            self.error = ''
            self.buffer.text = QTextDocument(self.buffer)
            self.buffer.text.setDocumentLayout(QPlainTextDocumentLayout(self.buffer.text))
            self.setDocument(self.buffer.text)
            self.setPlainText(self.baseline.decode('utf-8-sig'))
            self.document().setModified(False)
            self.buffer.ledger = SourceLedger(self.baseline)
            self.buffer.text.contentsChange.connect(self.buffer.changed)
        else:
            self.setDocument(buffer.text)
        self.buffer.views.add(self)
        self.buffer.compositionChanged.connect(self.compositionChanged)
        # destroyed can arrive after the QWidget wrapper becomes invalid.
        composing, token = self.buffer.composing, id(self)
        self.destroyed.connect(lambda: composing.discard(token))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAccessibleName(self.path.name + '，Markdown 編輯')
        self.setPlaceholderText('開始撰寫…')
        self.setStyleSheet('QPlainTextEdit { border:0; padding:28px 36px; background:transparent; }')
        from .workspace_file_title import FileTitle
        self.file_title = FileTitle(self.path.stem, self)
        self.setViewportMargins(0, 60, 0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'file_title'):
            self.file_title.setGeometry(36, 16, max(80, self.width()-72), 40)

    @property
    def dirty(self):
        return self.document().isModified()

    def save(self):
        if self.buffer.composing:
            return False
        if not self.dirty and not self.error and not self.buffer.mapping_error:
            return True
        try:
            target = paths.checked(self.path.parent, self.path.name)
            if paths.read_bytes(target, 8*1024*1024)[0] != self.baseline:
                raise ValueError('檔案已被外部修改；編輯內容保留在此頁，尚未覆寫原檔。')
            raw = self.encoded()
            if not self.dirty:
                if raw != self.baseline:
                    raise ValueError('原文與保存狀態不同步，內容尚未保存。')
                self.error = ''
                self.buffer.external_error = ''
                return True
            output = QSaveFile(str(target))
            output.setDirectWriteFallback(False)
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            if output.write(raw) != len(raw) or paths.read_bytes(target, 8*1024*1024)[0] != self.baseline:
                output.cancelWriting()
                raise OSError('保存前檔案已變更或無法完整寫入；編輯內容保留。')
            if not output.commit():
                raise OSError(output.errorString())
            self.baseline = raw
            self.error = ''
            self.buffer.external_error = ''
            self.document().setModified(False)
            return True
        except (ValueError, OSError) as error:
            self.error = str(error)
            self.buffer.external_error = ''  # Explicit save failure needs explicit retry.
            return False

    def encoded(self):
        if self.buffer.mapping_error:
            raise ValueError(self.buffer.mapping_error)
        if self.buffer.ledger.text_of(self.buffer.ledger.pieces) != source_text(self.document()):
            raise ValueError('原文映射與編輯器不同步，內容尚未保存。')
        return self.buffer.ledger.encoded()

    def apply_text_style(self, start, end, **style):
        if self.buffer.composing:
            raise ValueError('請先完成目前的中文組字。')
        snapshot = self.buffer.snapshot()
        return self.buffer.apply(snapshot, [text_style_edit(snapshot, start, end, **style)])

    def inputMethodEvent(self, event):
        token = id(self)
        self.buffer.composing.add(token)  # Also guard synchronous commit signals.
        try:
            super().inputMethodEvent(event)
        finally:
            if not event.preeditString():
                self.buffer.composing.discard(token)
            self.buffer.compositionChanged.emit()

    def refresh_external(self):
        """Reload only a clean shared document; a conflict never replaces a draft."""
        if self.buffer.composing or self.operation_error:
            return False
        try:
            target = paths.checked(self.path.parent, self.path.name)
            info = target.stat()
            stamp = (str(target), info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
            if stamp == self.buffer.external_stamp and not self.buffer.external_error:
                return False
            raw = paths.read_bytes(target, MAX_BYTES)[0]
            if raw == self.baseline:
                self.buffer.external_stamp = stamp
                if self.error == self.buffer.external_error:
                    self.error = ''
                self.buffer.external_error = ''
                return False
            if self.dirty:
                raise ValueError('檔案已被外部修改；本機稿已保留。請另存本機稿後再開啟外部版本。')
            text = raw.decode('utf-8-sig')  # Validate before touching shared content.
            positions = [(view, view.textCursor().position(), view.textCursor().anchor(),
                          view.verticalScrollBar().value()) for view in self.buffer.views if isValid(view)]
            self.buffer.loading = True
            try:
                self.setPlainText(text)
            finally:
                self.buffer.loading = False
            self.buffer.ledger = SourceLedger(raw)
            self.buffer.mapping_error = ''
            self.baseline = self.buffer.original = raw
            self.error = ''
            self.buffer.external_error = ''
            self.buffer.external_stamp = stamp
            self.document().setModified(False)
            for view, position, anchor, scroll in positions:
                cursor = view.textCursor()
                last = self.document().characterCount()-1
                cursor.setPosition(min(anchor, last))
                cursor.setPosition(min(position, last), QTextCursor.MoveMode.KeepAnchor)
                view.setTextCursor(cursor)
                view.verticalScrollBar().setValue(scroll)
            return True
        except (ValueError, OSError) as error:
            if not self.error or self.error == self.buffer.external_error:
                self.error = self.buffer.external_error = str(error)
            return False
