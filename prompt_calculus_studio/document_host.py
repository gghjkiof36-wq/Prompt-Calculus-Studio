"""Isolated document adapters for the local workspace development entry.

The existing material editor and Canvas own their transactions and native text
documents. A host is never retargeted to another file while callbacks are alive.
Live task/AppServices routing is a later integration boundary; this entry edits
locally and keeps its SQLite session in a separate directory.
"""
import copy
import shutil
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .core import Storage, separate_selections
from .comfy_client import ComfyClient
from .window import Window
from .material_editor_browser import PromptEditor
from .local_documents import assets, category, workflow, io, schema, paths


class LocalEditingClient(ComfyClient):
    """Use the existing offline engine state without enabling live dispatch."""
    def connect_to(self, address):
        self.window.notice('此分頁檢視版提供本機編輯；連線與任務整合尚未開放。')

    def request(self, route, data=None, done=None, failed=None, raw=None,
                content_type='application/json'):
        if failed:
            failed('分頁檢視版尚未開放連線。')


class DesktopDocument(QObject):
    changed = Signal()
    editing = Signal()

    def __init__(self, path, session_root, parent=None):
        super().__init__(parent)
        self.path = Path(path).absolute()
        loaded = io.load(self.path.parent, self.path.name)
        self.document, self.receipt = loaded.document, loaded.receipt
        self.issues = loaded.issues
        self.kind = self.document['kind']
        self.key = self.document['document_id']
        self.data = Path(session_root) / (self.key + '-' + uuid.uuid4().hex[:8])
        self.host = None
        self.dirty = False
        self.draft_dirty = False
        self.last_result = None
        self.error = ''
        self.baseline = None
        self.opening_settings = None

    @property
    def title(self):
        return self.path.stem

    def prepare(self):
        state = (category if self.kind == 'category' else workflow).to_state(self.document)
        # The runtime loader fills defaults. Preserve the legacy missing-key
        # selection behavior, then omit unchanged runtime defaults on export.
        state['settings'].setdefault('separate_selections', separate_selections(state))
        state['settings'].update(online=False, comfy_enabled=False,
                                 interface_mode='canvas', material='solid')
        if self.kind == 'category':
            state['workspaces'][0]['name'] = self.document['payload']['category']['name']
        self.data.mkdir(parents=True, exist_ok=False)
        for entry in self.document['assets']:
            source = assets.resolve(self.document, self.path.parent, entry['reference'])
            # The legacy UI reads logical names; the portable writer verifies
            # existing manifests at their physical, hash-named paths.
            for relative in {entry['reference'], entry['path']}:
                target = paths.checked(self.data, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        store = Storage(self.data)
        try:
            store.save(state)
        finally:
            store.close()

    def attach(self, host):
        self.host = host
        self.opening_settings = copy.deepcopy(host.state['settings'])
        self.baseline = self.project()['payload']

    def project(self):
        state = self.host.state
        if self.kind == 'category':
            fresh = category.from_state(state, self.document['payload']['category']['id'])
            settings = fresh['payload']['settings']
            original = self.document['payload']['settings']
        else:
            fresh = workflow.from_state(state)
            settings = fresh['payload']['state']['settings']
            original = self.document['payload']['state']['settings']
        for key in list(settings):
            if key not in original and settings[key] == (self.opening_settings or state['settings']).get(key):
                settings.pop(key)
        result = copy.deepcopy(self.document)
        result['payload'] = fresh['payload']
        referenced = {ref for ref, _, _ in assets.references(result)}
        result['assets'] = [entry for entry in result['assets'] if entry['reference'] in referenced]
        schema.validate(result)
        return result

    def update_dirty(self):
        if self.host is None or self.baseline is None:
            return
        try:
            self.dirty = bool(self.error) or self.project()['payload'] != self.baseline
        except (ValueError, KeyError) as error:
            self.dirty = True
            self.error = str(error)
        self.changed.emit()

    def begin_edit(self):
        self.editing.emit()

    def save(self):
        try:
            dialog = getattr(getattr(self.host, 'prompt_editor', None), 'active_dialog', None)
            if dialog is not None and not dialog.save():
                return False
            if not self.host.flush_editor_drafts():
                return False
            self.host.remember_canvas_view()
            candidate = assets.prepare(self.project(), self.data, self.path.parent)
            result = io.save(self.path.parent, self.path.name, candidate, self.receipt)
            self.last_result = result
            if result.status != 'saved':
                self.error = result.error or '文件尚未保存，請檢視回復版本。'
                self.dirty = True
                self.changed.emit()
                return False
            self.document, self.receipt = result.document, result.receipt
            self.baseline = copy.deepcopy(candidate['payload'])
            self.dirty = False
            self.error = ''
            self.host.persist()
            self.changed.emit()
            return True
        except (ValueError, OSError, KeyError) as error:
            self.error = str(error)
            self.dirty = True
            self.changed.emit()
            return False


class DocumentBrowser(PromptEditor):
    document_mode = True

    def __init__(self, host):
        super().__init__(host)
        self.set_browser_category(host.document.document['payload']['category']['id'])
        self.workspace_header.hide()
        self.category_panel.hide()
        self.library_more.setText('')
        self.library_more.setToolTip('分類選單')
        self.library_more.setAccessibleName('分類選單')

    def _open_dialog(self, ident, text='', initial_value=None):
        self.window.document.begin_edit()
        dialog = super()._open_dialog(ident, text, initial_value)
        if not getattr(dialog, '_document_bound', False):
            dialog._document_bound = True
            def edited():
                self.window.document.draft_dirty = bool(dialog.form.dirty)
                self.window.document.changed.emit()
            dialog.form.draftChanged.connect(edited)
            dialog.finished.connect(self._document_dialog_finished)
        return dialog

    def _document_dialog_finished(self, _):
        self.window.document.draft_dirty = False
        self.window.document.update_dirty()

    def library_menu(self):
        # Creating another category is a file command, not a second writable
        # owner silently inserted into this single-category document.
        from .widgets import RoundMenu
        menu = RoundMenu(self)
        menu.addAction('保存文件', self.window.document.save)
        menu.addAction('復原', self.window.canvas.undo)
        menu.addAction('重做', self.window.canvas.redo)
        menu.open_at(self.library_more.mapToGlobal(self.library_more.rect().bottomLeft()))


class EmbeddedHost(Window):
    """One existing editor/Canvas per canonical document; no global state swap."""
    def __init__(self, data, document=None):
        self.document = document
        self._document_ready = False
        super().__init__(data, embedded=True, client_factory=LocalEditingClient)
        self.studio_navigation.hide()
        self.context_rail.hide()
        self.canvas_header.hide()
        self.display_recovery.stop()
        self.sidebar_peek.shutdown()
        if document is not None:
            if document.kind == 'category':
                self.prompt_editor = DocumentBrowser(self)
                self.surface_stack.addWidget(self.prompt_editor)
                self.surface_stack.setCurrentWidget(self.prompt_editor)
            else:
                self.surface_stack.setCurrentWidget(self.canvas_shell)
            self.changes.flush()
            self.save_timer.stop()
            document.attach(self)
        self._document_ready = True

    def changed(self, scope='prompt', refresh=True):
        super().changed(scope, refresh)
        if self._document_ready and self.document is not None:
            self.document.update_dirty()

    def sync_context_sidebar(self):
        if hasattr(self, 'context_rail'):
            self.context_rail.hide()

    def return_to_prompt(self):
        if self.document is None:
            return super().return_to_prompt()
        self.surface_stack.setCurrentWidget(self.prompt_editor if self.document.kind == 'category' else self.canvas_shell)
        self.canvas_header.hide()

    def enter_canvas(self):
        super().enter_canvas()
        if hasattr(self, 'canvas_header'):
            self.canvas_header.hide()

    def notice(self, text):
        super().notice(text)
        if self._document_ready and self.document is not None:
            self.document.changed.emit()
