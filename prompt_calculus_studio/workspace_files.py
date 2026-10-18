"""File creation and plain Markdown editing for the local workspace."""
from pathlib import Path
from PySide6.QtCore import QSaveFile, QIODevice, QObject
from PySide6.QtWidgets import QPlainTextEdit, QFrame, QPlainTextDocumentLayout
from PySide6.QtGui import QTextDocument
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
    pass


class BufferField:
    def __set_name__(self, owner, name): self.name = name
    def __get__(self, page, owner=None):
        return self if page is None else getattr(page.buffer, self.name, False if self.name == 'operation_error' else '')
    def __set__(self, page, value): setattr(page.buffer, self.name, value)


class MarkdownPage(QPlainTextEdit):
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
            self.error = ''
            self.buffer.text = QTextDocument(self.buffer)
            self.buffer.text.setDocumentLayout(QPlainTextDocumentLayout(self.buffer.text))
            self.setDocument(self.buffer.text)
            self.setPlainText(self.baseline.decode('utf-8-sig'))
            self.document().setModified(False)
        else:
            self.setDocument(buffer.text)
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
        if not self.dirty:
            return True
        try:
            target = paths.checked(self.path.parent, self.path.name)
            if paths.read_bytes(target, 8*1024*1024)[0] != self.baseline:
                raise ValueError('檔案已被外部修改；編輯內容保留在此頁，尚未覆寫原檔。')
            raw = self.encoded()
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
            self.document().setModified(False)
            return True
        except (ValueError, OSError) as error:
            self.error = str(error)
            return False

    def encoded(self):
        text = self.toPlainText()
        if b'\r\n' in self.baseline:
            text = text.replace('\n', '\r\n')
        raw = (b'\xef\xbb\xbf' if self.baseline.startswith(b'\xef\xbb\xbf') else b'') + text.encode('utf-8')
        if len(raw) > 8*1024*1024:
            raise ValueError('Markdown 超過 8 MiB，請縮減內容後保存。')
        return raw
