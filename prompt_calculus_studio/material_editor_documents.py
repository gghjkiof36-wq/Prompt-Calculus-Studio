"""Ephemeral plain-text document ownership; never stored in project/session data.

Both editor surfaces use QTextEdit. A document (and its native Undo/Redo stack)
has one editable view at a time, while inactive views retain plain snapshots.
"""
from PySide6.QtCore import QSignalBlocker
from PySide6.QtGui import QTextDocument, QTextCursor
from shiboken6 import isValid


def _document(window, text):
    # Parent to the window: changing an editor's document must not destroy it.
    document = QTextDocument(window)
    document.setPlainText(text)
    return document


def _key(editor):
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
