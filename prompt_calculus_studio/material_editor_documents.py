"""Ephemeral plain-text document ownership; never stored in project/session data.

Both editor surfaces use QTextEdit. A document (and its native Undo/Redo stack)
has one editable view at a time, while inactive views retain plain snapshots.
"""
from PySide6.QtCore import QSignalBlocker
from PySide6.QtGui import QTextDocument, QTextCursor
from shiboken6 import isValid
from .material_editor_page import PagePromptText
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QTextBlockFormat


class ModalPromptText(PagePromptText):
    """Route every native editing entry point through the modal checkpoint."""
    history = None

    def undo(self):
        if self.history: self.history.undo()
        else: super().undo()

    def redo(self):
        if self.history: self.history.redo()
        else: super().redo()

    def keyPressEvent(self, event):
        if self.history and not self.composing:
            if event.matches(QKeySequence.StandardKey.Undo): self.undo(); return
            if (event.matches(QKeySequence.StandardKey.Redo) or
                    event.key() == Qt.Key.Key_Z and event.modifiers() ==
                    (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
                self.redo(); return
        super().keyPressEvent(event)

    def setPlainText(self, text):
        if not self.history: return super().setPlainText(text)
        # Programmatic plain-text replacements have the same native history as
        # paste/select-all typing, rather than silently clearing the Qt stack.
        if self.toPlainText() != text:
            cursor = self.textCursor(); cursor.beginEditBlock()
            cursor.select(QTextCursor.SelectionType.Document); cursor.insertText(text)
            cursor.endEditBlock(); self.setTextCursor(cursor)

    def createStandardContextMenu(self, *args):
        menu = super().createStandardContextMenu(*args)
        if self.history:
            # Qt gives these actions stable object names, independent of locale.
            for action in menu.actions():
                if action.objectName() not in ('edit-undo', 'edit-redo'): continue
                action.triggered.disconnect()
                undo = action.objectName() == 'edit-undo'
                action.setEnabled(self.history.can_undo() if undo else self.history.can_redo())
                action.triggered.connect(self.undo if undo else self.redo)
        return menu

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu(event.pos())
        menu.exec(event.globalPos()); menu.deleteLater()


def _position(prompt):
    cursor = prompt.textCursor()
    return (cursor.position(), cursor.anchor(), prompt.verticalScrollBar().value(),
            prompt.horizontalScrollBar().value())


def _restore_position(prompt, position):
    point, anchor, vertical, horizontal = position
    cursor = prompt.textCursor(); limit = prompt.document().characterCount()-1
    cursor.setPosition(min(anchor, limit)); cursor.setPosition(min(point, limit), QTextCursor.MoveMode.KeepAnchor)
    prompt.setTextCursor(cursor)
    prompt.verticalScrollBar().setValue(vertical); prompt.horizontalScrollBar().setValue(horizontal)


class ModalNativeHistory:
    """A reversible cursor into the saved native stack, plus native local edits.

    Cloning QTextDocument cannot clone its undo stack. We therefore traverse the
    actual source stack for inherited Undo/Redo, but keep new typing in separate
    documents until Save. Cancel restores the source cursor using its own native
    commands, so a local branch never destroys the source's pre-existing Redo.
    No text snapshots are used as substitute undo commands.
    """
    def __init__(self, form):
        self.form = form; self.window = form.window; self.prompt = form.prompt
        self.busy = False; self.closed = False; self.serial = 0; self.identity = None
        self.views = []; self.documents = {}; self.positions = {}; self.last_text = {}
        self.connections = []
        self.prompt.history = self
        self.bind()

    def _cache_private(self, document):
        self.serial += 1
        self.window._material_editor_documents[('dialog', self.form.document_scope, 'history', self.serial)] = document
        return document

    def bind(self):
        ident = self.form.base['id']
        if ident == self.identity: return
        if self.identity is not None: self.rollback()
        self.closed = False; self.identity = ident; self.offset = 0; self.redo_limit = None
        self.documents = {}; self.positions = {}; self.last_text = {}; self.views = []
        cache = self.window._material_editor_documents
        key = ('source', ident); text = self.prompt.toPlainText(); position = _position(self.prompt)
        self.source = cache.get(key)
        if self.source is None or not isValid(self.source):
            self.source = self._cache_private(_document(self.window, text))
        locks = getattr(self.window, '_material_native_transactions', None)
        if locks is None: locks = self.window._material_native_transactions = {}
        locks[key] = self
        # A hidden old view must not publish the temporary inherited-undo text.
        # Detach it for this modal, then restore the exact native document and
        # cursor/selection/scroll at the checkpoint on Cancel.
        for editor in list(getattr(self.window, '_material_editors', ())):
            if not isValid(editor) or not hasattr(editor, 'prompt'): continue
            if editor.prompt.document() is self.source:
                saved = _position(editor.prompt)
                self.views.append((editor, saved))
                with QSignalBlocker(editor.prompt):
                    _replace_view_document(editor, _document(self.window, self.source.toPlainText()), cache.values())
                    _restore_position(editor.prompt, saved)
        self.busy = True
        self._show_offset()
        if text != self.source.toPlainText():
            cursor = QTextCursor(self.prompt.document()); cursor.beginEditBlock()
            cursor.select(QTextCursor.SelectionType.Document); cursor.insertText(text); cursor.endEditBlock()
        _restore_position(self.prompt, position)
        self.busy = False

    def _working_document(self):
        document = self._cache_private(_document(self.window, self.source.toPlainText()))
        document.setDefaultFont(self.prompt.font())
        document.setUndoRedoEnabled(False)
        cursor = QTextCursor(document); cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextBlockFormat(); fmt.setLineHeight(165, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.mergeBlockFormat(fmt); document.setUndoRedoEnabled(True)
        self.last_text[document] = document.toPlainText()
        callback = lambda: self._changed(document)
        document.contentsChanged.connect(callback); self.connections.append((document, callback))
        return document

    def _changed(self, document):
        text = document.toPlainText(); previous = self.last_text.get(document)
        self.last_text[document] = text
        if self.busy or self.closed or document is not self.prompt.document() or text == previous: return
        # Qt already removes this local document's redo branch. The inherited
        # source redo is merely masked until Save, so Cancel can still restore it.
        self.redo_limit = self.offset
        for offset in list(self.documents):
            if offset > self.offset:
                self.documents.pop(offset); self.positions.pop(offset, None)

    def _show_offset(self):
        if self.offset not in self.documents: self.documents[self.offset] = self._working_document()
        document = self.documents[self.offset]
        cache = self.window._material_editor_documents; cache[_key(self.form)] = document
        with QSignalBlocker(self.prompt):
            _replace_view_document(self.form, document, cache.values())
            _restore_position(self.prompt, self.positions.get(self.offset, (document.characterCount()-1,)*2+(0,0)))

    def can_undo(self):
        return not self.closed and (self.prompt.document().isUndoAvailable() or self.source.isUndoAvailable())

    def can_redo(self):
        if self.closed: return False
        return self.prompt.document().isRedoAvailable() or (
            self.prompt.toPlainText() == self.source.toPlainText() and self.source.isRedoAvailable()
            and (self.redo_limit is None or self.offset < self.redo_limit))

    def _navigate(self, undo):
        if not (self.can_undo() if undo else self.can_redo()): return
        self.busy = True
        try:
            document = self.prompt.document()
            if document.isUndoAvailable() if undo else document.isRedoAvailable():
                cursor = self.prompt.textCursor()
                if undo: document.undo(cursor)
                else: document.redo(cursor)
                self.prompt.setTextCursor(cursor)
            else:
                self.positions[self.offset] = _position(self.prompt)
                cursor = QTextCursor(self.source)
                with QSignalBlocker(self.source):
                    if undo: self.source.undo(cursor); self.offset -= 1
                    else: self.source.redo(cursor); self.offset += 1
                self.positions.setdefault(self.offset, (cursor.position(), cursor.anchor(), 0, 0))
                self._show_offset()
                self.prompt.textChanged.emit()
        finally: self.busy = False

    def undo(self): self._navigate(True)
    def redo(self): self._navigate(False)

    def save_checkpoint(self):
        """Only explicit Save may branch the saved source's native history."""
        text = self.prompt.toPlainText(); position = _position(self.prompt)
        self.busy = True
        try:
            with QSignalBlocker(self.source):
                if self.source.toPlainText() != text:
                    cursor = QTextCursor(self.source); cursor.beginEditBlock()
                    cursor.select(QTextCursor.SelectionType.Document); cursor.insertText(text); cursor.endEditBlock()
            self.window._material_editor_documents[('source', self.identity)] = self.source
            self.offset = 0; self.redo_limit = None
            self.documents = {}; self.positions = {}; self.last_text = {}
            self._show_offset(); _restore_position(self.prompt, position)
        finally: self.busy = False

    def rollback(self):
        if self.closed: return
        self.busy = True
        try:
            with QSignalBlocker(self.source):
                while self.offset < 0: self.source.redo(); self.offset += 1
                while self.offset > 0: self.source.undo(); self.offset -= 1
            locks = getattr(self.window, '_material_native_transactions', {})
            if locks.get(('source', self.identity)) is self: locks.pop(('source', self.identity))
            cache = self.window._material_editor_documents
            for editor, position in self.views:
                if not isValid(editor) or not hasattr(editor, 'base') or _key(editor) != ('source', self.identity): continue
                with QSignalBlocker(editor.prompt):
                    _replace_view_document(editor, self.source, cache.values())
                    _restore_position(editor.prompt, position)
            for document, callback in self.connections:
                if isValid(document): document.contentsChanged.disconnect(callback)
            self.connections.clear(); self.views.clear()
            self.documents.clear(); self.positions.clear(); self.last_text.clear()
            self.closed = True
        finally: self.busy = False


def _document(window, text):
    # Parent to the window: changing an editor's document must not destroy it.
    document = QTextDocument(window)
    document.setPlainText(text)
    return document


def _key(editor):
    if getattr(editor, 'document_scope', None):
        return ('dialog', editor.document_scope, editor.base['id'])
    return (('canvas', editor.edit_workspace, editor.target[1])
            if editor.target and editor.target[0] == 'canvas'
            else ('source', editor.base['id']))


def _replace_view_document(editor, document, cached):
    old = editor.prompt.document()
    editor.prompt.setDocument(document)
    if old is not document and isValid(old) and old not in cached:
        old.deleteLater()


def activate_document(editor, text):
    window = editor.window
    if not hasattr(window, '_material_editor_documents'):
        window._material_editor_documents = {}
    cache = window._material_editor_documents
    key = _key(editor)
    if key in getattr(window, '_material_native_transactions', {}): return
    document = cache.get(key)
    if document is None or not isValid(document):
        document = cache[key] = _document(window, text)
    for other in list(getattr(window, '_material_editors', ())):
        if other is editor or not isValid(other) or not hasattr(other, 'prompt'): continue
        if other.prompt.document() is document:
            cursor = other.prompt.textCursor()
            position, anchor = cursor.position(), cursor.anchor()
            scroll = other.prompt.verticalScrollBar().value()
            with QSignalBlocker(other.prompt):
                _replace_view_document(other, _document(window, document.toPlainText()), cache.values())
                cursor = other.prompt.textCursor(); cursor.setPosition(anchor)
                cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
                other.prompt.setTextCursor(cursor)
                other.prompt.verticalScrollBar().setValue(scroll)
    with QSignalBlocker(editor.prompt):
        _replace_view_document(editor, document, cache.values())
        document.setDefaultFont(editor.prompt.font())
        if document.toPlainText() != text:
            # Only canonical external changes reach here. Session restoration
            # supplies pending text directly and never inserts an old base first.
            cursor = QTextCursor(document); cursor.beginEditBlock()
            cursor.select(QTextCursor.SelectionType.Document); cursor.insertText(text)
            cursor.endEditBlock()


def claim_document(editor):
    """The view receiving input owns the history, even after a hidden refresh."""
    if getattr(editor, 'base', None) is None or editor.loading: return
    if _key(editor) in getattr(editor.window, '_material_native_transactions', {}): return
    document = getattr(editor.window, '_material_editor_documents', {}).get(_key(editor))
    if document is None or not isValid(document) or editor.prompt.document() is document: return
    cursor = editor.prompt.textCursor(); position, anchor = cursor.position(), cursor.anchor()
    scroll = editor.prompt.verticalScrollBar().value()
    activate_document(editor, document.toPlainText())
    cursor = editor.prompt.textCursor(); limit = document.characterCount() - 1
    cursor.setPosition(min(anchor, limit)); cursor.setPosition(min(position, limit), QTextCursor.MoveMode.KeepAnchor)
    editor.prompt.setTextCursor(cursor); editor.prompt.verticalScrollBar().setValue(scroll)


def reset_documents(window):
    documents = list(getattr(window, '_material_editor_documents', {}).values())
    window._material_editor_documents = {}
    for editor in list(getattr(window, '_material_editors', ())):
        if isValid(editor) and hasattr(editor, 'prompt'):
            with QSignalBlocker(editor.prompt): editor.prompt.setDocument(_document(window, ''))
    for document in documents:
        if isValid(document): document.deleteLater()
