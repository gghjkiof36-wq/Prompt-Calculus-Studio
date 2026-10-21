"""File creation and plain Markdown editing for the local workspace."""
from pathlib import Path
from PySide6.QtCore import QSaveFile, QObject, Signal, QEvent
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
            from .canvas_starter import initialize, guide
            # Only explicit new files receive the starter. Opening a saved or
            # imported Canvas must retain its layout, including an empty one.
            state = initialize(prepare_state(state, multi=True), fresh_install=True)
            starter = guide(state)
            if starter:
                # Local documents exclude application settings; retain the
                # guide with its owning workspace so reopen keeps the entry.
                state['workspaces'][0]['canvas_starter'] = dict(starter)
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
    opened = Signal()

    def __init__(self):
        super().__init__()
        self.views = weakref.WeakSet()
        self.composing = set()
        self.external_stamp = None
        self.external_error = ''
        self.document_id = uuid.uuid4().hex
        self.loading = False
        self.opening = False
        self.mapping_error = ''
        self.pending_pieces = None
        self.last_result = None
        self.recovery_paths = []
        self.syntax_cache = []
        self.mutation = None

    def flush_mutation(self):
        if self.mutation is not None:self.mutation.finish()

    def remember_syntax(self, text, lines):
        # A few whole revisions cover shared views and native Undo/Redo without
        # retaining an unbounded history of large manuscripts.
        self.syntax_cache = [(text, lines)] + [(source, value) for source, value in self.syntax_cache if source != text][:2]

    def cached_syntax(self, text):
        return next((lines for source, lines in self.syntax_cache if source == text), None)

    def changed(self, position, removed, added):
        previous=getattr(self,'cached_snapshot',None)
        self.snapshot_key=None
        if self.loading:
            return
        try:
            from .markdown_source import changed_source
            text = changed_source(self.text,previous.text,position,removed,added) if previous is not None else source_text(self.text)
            self.snapshot_key=(self.text.revision(),id(self.text))
            self.cached_snapshot=SourceSnapshot(text,self.snapshot_key[0],self.document_id)
            if self.pending_pieces is not None:
                if self.ledger.text_of(self.pending_pieces) != text:
                    raise ValueError('編輯交易尚未完整套用，內容尚未保存。')
                self.ledger.accept(self.pending_pieces, self.text.availableUndoSteps())
            else:
                self.ledger.changed(position, removed, added, text, self.text.availableUndoSteps())
        except (ValueError, UnicodeError) as error:
            self.mapping_error = self.error = str(error)

    def snapshot(self):
        if self.mutation is not None:return self.mutation.snapshot
        if self.opening:
            return self.opening_snapshot
        key=(self.text.revision(),id(self.text))
        if getattr(self,'snapshot_key',None)!=key:
            self.snapshot_key=key;self.cached_snapshot=SourceSnapshot(source_text(self.text),key[0],self.document_id)
        return self.cached_snapshot

    def prepare_edit(self, snapshot, edits):
        self.flush_mutation()
        if self.opening:
            raise ValueError('文件正在載入，請稍候。')
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
            return None
        return result, ordered, prospective

    def apply(self, snapshot, edits, *, join_previous=False):
        prepared = self.prepare_edit(snapshot, edits)
        if prepared is None:return False
        result, ordered, prospective = prepared
        cursor = QTextCursor(self.text)
        self.pending_pieces = prospective
        cursor.joinPreviousEditBlock() if join_previous else cursor.beginEditBlock()
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
    last_result = BufferField()
    recovery_paths = BufferField()

    def __init__(self, path, buffer=None, *, workspace=None):
        super().__init__(workspace.workspace_stack if workspace else None)
        self.buffer = buffer or MarkdownBuffer()
        if buffer is None:
            self.path = Path(path)
            self.baseline = paths.read_bytes(self.path, 8*1024*1024)[0]
            self.buffer.original = self.baseline
            self.error = ''
            self.buffer.text = QTextDocument(self.buffer)
            self.buffer.text.setDocumentLayout(QPlainTextDocumentLayout(self.buffer.text))
            self.setDocument(self.buffer.text)
            self.buffer.ledger = SourceLedger(self.baseline)
            text = self.buffer.ledger.text_of(self.buffer.ledger.pieces)
            self.buffer.opening = len(text) > 1_000_000 or text.count('\n') > 20_000
            if self.buffer.opening:
                self.buffer.loading = True
                self.buffer.opening_snapshot = SourceSnapshot(text, 0, self.buffer.document_id)
                self.buffer.text.setLayoutEnabled(False)
                self.buffer.text.setUndoRedoEnabled(False)
            else:
                self.setPlainText(text)
            self.document().setModified(False)
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
        self.setStyleSheet('QPlainTextEdit { border:0; padding:28px 36px; background:transparent; }')
        from .workspace_file_title import FileTitle
        self.file_title = FileTitle(self.path.stem, self)
        self.file_title.workspace = workspace
        from .markdown_surface import MarkdownSurface
        self.md_surface = MarkdownSurface(self)
        self._prepare_workspace_show = self.md_surface.prepare_workspace_show
        self.setViewportMargins(0, 0, 0, 0)
        super().viewport().hide()
        super().horizontalScrollBar().hide()
        super().verticalScrollBar().hide()
        self.setFocusProxy(self.md_surface.editor)
        self.md_surface.show()
        if self.buffer.opening and not hasattr(self.buffer, 'source_loading'):
            from .markdown_loading import SourceLoading
            self.buffer.source_loading = SourceLoading(self.buffer, text)

    def event(self,event):
        handled=super().event(event)
        # QAbstractScrollArea.resizeEvent belongs to its hidden viewport. Track
        # the outer editor as well, including the first single-pane show.
        if event.type() in (QEvent.Type.Resize,QEvent.Type.Show) and hasattr(self,'md_surface'):
            self.md_surface.setGeometry(self.rect())
        return handled

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'md_surface'):
            self.md_surface.setGeometry(self.rect())

    def textCursor(self):
        if not hasattr(self, 'md_surface'):return super().textCursor()
        local = self.md_surface.editor.textCursor()
        cursor = QTextCursor(self.document())
        maximum = self.document().characterCount()-1
        cursor.setPosition(min(local.anchor(), maximum))
        cursor.setPosition(min(local.position(), maximum), QTextCursor.MoveMode.KeepAnchor)
        return cursor

    def setTextCursor(self, cursor):
        if not hasattr(self, 'md_surface'):return super().setTextCursor(cursor)
        local = self.md_surface.editor.textCursor()
        maximum = self.md_surface.editor.document().characterCount()-1
        local.setPosition(min(cursor.anchor(), maximum))
        local.setPosition(min(cursor.position(), maximum), QTextCursor.MoveMode.KeepAnchor)
        self.md_surface.editor.setTextCursor(local)

    def insertPlainText(self, text):
        if not hasattr(self, 'md_surface'):return super().insertPlainText(text)
        if self.md_surface.mode == 'read':return
        self.md_surface.editor.insertPlainText(text)

    def moveCursor(self, operation, mode=QTextCursor.MoveMode.MoveAnchor):
        if not hasattr(self, 'md_surface'):return super().moveCursor(operation, mode)
        self.md_surface.editor.moveCursor(operation, mode)

    def selectAll(self):
        if not hasattr(self, 'md_surface'):return super().selectAll()
        self.md_surface.editor.selectAll()

    def undo(self):
        self.buffer.flush_mutation()
        if self.buffer.composing:return
        if not hasattr(self, 'md_surface') or self.md_surface.mode != 'read':self.document().undo()

    def redo(self):
        self.buffer.flush_mutation()
        if self.buffer.composing:return
        if not hasattr(self, 'md_surface') or self.md_surface.mode != 'read':self.document().redo()

    def verticalScrollBar(self):
        return self.md_surface.editor.verticalScrollBar() if hasattr(self, 'md_surface') else super().verticalScrollBar()

    def horizontalScrollBar(self):
        return self.md_surface.editor.horizontalScrollBar() if hasattr(self, 'md_surface') else super().horizontalScrollBar()

    def viewport(self):
        return self.md_surface.editor.viewport() if hasattr(self, 'md_surface') else super().viewport()

    def keyPressEvent(self, event):
        if hasattr(self, 'md_surface') and self.md_surface.mode == 'read':event.ignore()
        elif hasattr(self, 'md_surface'):self.md_surface.editor.keyPressEvent(event)
        else:super().keyPressEvent(event)

    @property
    def dirty(self):
        return not self.buffer.opening and self.document().isModified()

    def save(self):
        self.buffer.flush_mutation()
        from .file_publication import checked_replace, PublicationError
        if self.buffer.opening or self.buffer.composing or self.operation_error:
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
            checked_replace(target, self.baseline, raw, QSaveFile, max_bytes=MAX_BYTES)
            self.baseline = raw
            self.buffer.recovery_source_sha = None
            self.last_result = None
            self.recovery_paths = []
            self.error = ''
            self.buffer.external_error = ''
            self.document().setModified(False)
            return True
        except (ValueError, OSError) as error:
            if isinstance(error, PublicationError):
                self.last_result = error.result
                self.recovery_paths = list(dict.fromkeys([*self.recovery_paths, *error.result.recovery_paths]))
            self.error = str(error)
            self.buffer.external_error = ''  # Explicit save failure needs explicit retry.
            return False

    def encoded(self):
        self.buffer.flush_mutation()
        if self.buffer.opening:
            return self.baseline
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
        if hasattr(self, 'md_surface'):
            if self.md_surface.mode == 'read':event.ignore();return
            return self.md_surface.editor.inputMethodEvent(event)
        token = id(self)
        self.buffer.composing.add(token)  # Also guard synchronous commit signals.
        try:
            super().inputMethodEvent(event)
        finally:
            if not event.preeditString():
                self.buffer.composing.discard(token)
            self.buffer.compositionChanged.emit()

    def refresh_external(self, probe=None):
        """Reload only a clean shared document; a conflict never replaces a draft."""
        if self.buffer.opening or self.buffer.composing or self.operation_error:
            return False
        try:
            if probe is None:
                target = paths.checked(self.path.parent, self.path.name)
                info = target.stat()
                stamp = (str(target), info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
                if stamp == self.buffer.external_stamp and not self.buffer.external_error:
                    return False
                raw = paths.read_bytes(target, MAX_BYTES)[0]
            else:
                stamp,raw,error=probe
                if error:raise ValueError(error)
                if raw is None:return False
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
