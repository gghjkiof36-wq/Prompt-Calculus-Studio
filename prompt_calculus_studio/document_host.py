"""Isolated document adapters for the local workspace development entry.

The existing material editor and Canvas own their transactions and native text
documents. File operations rebind their writer without rebuilding that model.
Live task/AppServices routing is a later integration boundary; this entry edits
locally and keeps its SQLite session in a separate directory.
"""
import copy
import shutil
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Qt, QTimer

from .core import Storage, separate_selections
from .comfy_client import ComfyClient
from .window import Window
from .material_editor_browser import PromptEditor
from .local_documents import assets, category, workflow, io, schema, paths


class LocalEditingClient(ComfyClient):
    """Use the existing offline engine state without enabling live dispatch."""
    def recheck_connection(self):
        self.connect_to(self.window.state['settings'].get('comfy_url', self.url))
        return False

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
        self.runtime_document = copy.deepcopy(self.document)
        self.identity_maps = None
        self.issues = loaded.issues
        self.kind = self.document['kind']
        self.key = self.document['document_id']
        self.data = Path(session_root) / (self.key + '-' + uuid.uuid4().hex[:8])
        self.host = None
        self.hosts = []
        self.state = None
        self.history = {}
        self.refreshing = False
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.refresh_views)
        self.last_editor = None
        self.execution = None
        self.retired_executions = []
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
        self.hosts.append(host)
        if self.host is not None:
            return
        self.host = host
        self.opening_settings = copy.deepcopy(host.state['settings'])
        self.baseline = self.project()['payload']

    def flush_views(self):
        # Forms keep their explicit confirmation boundary. Switching views or
        # saving never accepts an unconfirmed material dialog.
        if any(getattr(getattr(h, 'prompt_editor', None), 'active_dialog', None) is not None for h in self.hosts):
            return False
        return all(h.flush_editor_drafts() for h in self.hosts)

    def execution_client(self, host):
        if self.execution is None:
            from .document_execution import DocumentExecution
            self.execution = DocumentExecution(self, host, LocalEditingClient)
        return self.execution.client

    def retarget_execution(self):
        """Save As gives new submissions a new owner, preserving accepted work."""
        from .document_execution import DocumentExecution
        from .media import Catalog
        old = self.execution
        if old is None:return
        old.detach()
        self.retired_executions.append(old)
        data = self.data.with_name(self.key+'-'+uuid.uuid4().hex[:8])
        data.mkdir()
        for entry in self.document['assets']:
            for relative in {entry['reference'], entry['path']}:
                source = paths.checked(self.data, relative)
                target = paths.checked(data, relative)
                if source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
        store = Storage(data)
        store.save(self.state)
        self.data = data
        for host in self.hosts:
            host.save_timer.stop()
            if host.store is not old.store:host.store.close()
            host.store = store
            host.owns_store = False
            host.catalog = Catalog(store)
            from .image_drop import ImageDropLabel
            for loader in host.findChildren(ImageDropLabel):loader.store = store
        self.execution = DocumentExecution(self, self.host, LocalEditingClient)
        for host in self.hosts:
            old.client.stateChanged.disconnect(host.comfy_changed)
            host.comfy = self.execution.client
            host.comfy.stateChanged.connect(host.comfy_changed)
            host.comfy_changed()

    def view_changed(self, host):
        if self.refreshing:
            return
        self.last_editor = host
        self.refresh_timer.start(0)
        self.update_dirty()

    def refresh_views(self):
        self.refreshing = True
        try:
            for host in self.hosts:
                if host is self.last_editor or host.closing:
                    continue
                host.canvas.refresh()
                host.refresh_prompt_editors()
                host.comfy_changed()
        finally:
            self.refreshing = False

    def project(self, *, remap=True):
        state = self.host.state
        if self.kind == 'category':
            fresh = category.from_state(state, self.runtime_document['payload']['category']['id'])
            settings = fresh['payload']['settings']
            original = self.runtime_document['payload']['settings']
        else:
            fresh = workflow.from_state(state)
            settings = fresh['payload']['state']['settings']
            original = self.runtime_document['payload']['state']['settings']
        for key in list(settings):
            if key not in original and settings[key] == (self.opening_settings or state['settings']).get(key):
                settings.pop(key)
        result = copy.deepcopy(self.document)
        result['payload'] = fresh['payload']
        referenced = {ref for ref, _, _ in assets.references(result)}
        result['assets'] = [entry for entry in result['assets'] if entry['reference'] in referenced]
        if remap and self.identity_maps is not None:
            from .identity_projection import project_copy
            result = project_copy(result, self.identity_maps)
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
            if not self.flush_views():
                return False
            self.host.remember_canvas_view()
            if not self.path.is_file():
                self.error = '原始檔案已不存在，請按 Ctrl+Shift+S 另存目前內容。'
                self.dirty = True
                self.changed.emit()
                return False
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
        # Keep material editing local; the outer file tab/tree owns its F2.
        self.f2.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        # QScrollArea gives its content a filled Window background by default.
        # Use the same opaque content role as Home instead of the sidebar base.
        self.shell.setObjectName('ContentSurface')
        self.set_browser_category(host.document.document['payload']['category']['id'])
        self.workspace_header.hide()
        self.category_panel.hide()
        self.library_more.setText('')
        self.library_more.setToolTip('分類選單')
        self.library_more.setAccessibleName('分類選單')

    def _open_dialog(self, ident, text='', initial_value=None):
        for host in self.window.document.hosts:
            existing = getattr(getattr(host, 'prompt_editor', None), 'active_dialog', None)
            if existing is not None:
                existing.raise_()
                existing.activateWindow()
                return existing
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
        workspace = self.window.workspace_owner
        tab = workspace.tab_for_path(self.window.document.path) if workspace else None
        menu.addAction('保存文件', lambda: workspace.save_tab(tab) if tab else self.window.document.save())
        menu.addAction('復原', self.window.canvas.undo)
        menu.addAction('重做', self.window.canvas.redo)
        if tab:
            menu.addSeparator()
            workspace.commands.files.add_actions(menu, workspace.commands.context('page', tab=tab))
        menu.open_at(self.library_more.mapToGlobal(self.library_more.rect().bottomLeft()))


class EmbeddedHost(Window):
    """One existing editor/Canvas per canonical document; no global state swap."""
    def __init__(self, data, document=None, *, workspace=None):
        self.document = document
        self.workspace_owner = workspace
        self._document_ready = False
        self.owns_comfy = document is None
        self.owns_store = document is None or document.host is not None
        client_factory = document.execution_client if document else LocalEditingClient
        super().__init__(data, embedded=True, client_factory=client_factory, document_only=document is not None,
                         shared_state=document.state if document else None)
        self.studio_navigation.hide()
        self.context_rail.hide()
        self.canvas_header.hide()
        self.surface_stack.rounded = False
        self.surface_stack.clearMask()
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
            # Results have one document owner, not one receiver per view.
            self.comfy.resultsReceived.disconnect(self.recent.receive_results)
        self._document_ready = True

    @property
    def state(self):
        document = getattr(self, 'document', None)
        return document.state if document is not None else self._local_state

    @state.setter
    def state(self, value):
        document = getattr(self, 'document', None)
        if document is not None:
            document.state = value
        else:
            self._local_state = value

    def bind_workspace(self, workspace):
        """One outer navigation owner; keep the existing editors and models."""
        from PySide6.QtGui import QShortcut, QKeySequence
        self.workspace_owner = workspace
        for shortcut in self.findChildren(QShortcut):
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            if shortcut.key().toString(QKeySequence.SequenceFormat.PortableText) in ('Alt+Left', 'Alt+Right'):
                shortcut.setEnabled(False)
        # These are the same live sidebars used by the established pages.
        # Their aligned width replaces the file sidebar while a tool is open.
        if self.document is None:
            self.prepare_workspace_sidebar()
        else:
            # The appearance owner is the shell, never the saved file settings.
            self.apply_theme(preserve_layout=True)

    def prepare_workspace_sidebar(self):
        """Attach only the tool being visited; unopened tools stay unbuilt."""
        workspace = getattr(self, 'workspace_owner', None)
        if workspace is None or self.document is not None:
            return
        target = self.context_sidebar_target()
        if target is None or target.property('pcsWorkspaceSidebar'):
            return
        target.setProperty('pcsSidebarManaged', True)
        target.setProperty('pcsWorkspaceSidebar', True)
        target.setFixedWidth(workspace.sidebar_width)
        target.layout().setContentsMargins(12, 16, 12, 12)
        from .deferred_page import page_ready
        if page_ready(self.settings_page) and target is self.settings_page.navigation_area:
            header = target.sidebar_header
        else:
            if page_ready(self.gallery) and target is self.gallery.folder_panel:
                header = self.gallery.sidebar_header
                self.gallery.sidebar_split.setHandleWidth(0)
                from .ui_icons import icon
                self.gallery.new_folder.setIcon(icon('plus','navigation'))
                self.gallery.folder_more.setIcon(icon('more-horizontal','navigation'))
                for control in (self.gallery.new_folder, self.gallery.folder_more):
                    control.setFixedSize(32, 32)
                header.layout().setSpacing(0)
            else:
                header = self.clean_export.sidebar_header
        workspace.sidebar_headers.register(target, header)
        # This sidebar may already be polished before it joins the shell.
        # Dynamic ancestor selectors must also be refreshed on its children.
        from PySide6.QtWidgets import QWidget
        for widget in (target, *target.findChildren(QWidget)):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.updateGeometry()

    def apply_theme(self, *args, **kwargs):
        super().apply_theme(*args, **kwargs)
        self.sync_workspace_display()

    def remember_canvas_view(self):
        if self.document is None:return super().remember_canvas_view()
        from PySide6.QtCore import QPointF
        if hasattr(self, 'canvas'):
            view = self.canvas.view
            center = view.viewportTransform().inverted()[0].map(QPointF(view.viewport().rect().center()))
            self.local_canvas_view = [view.transform().m11(), center.x(), center.y()]

    def update_material_preview(self):
        if getattr(self,'workspace_owner',None) is not None and self.document is None:
            self.sync_workspace_display()
        else:
            super().update_material_preview()

    def sync_workspace_display(self):
        workspace = getattr(self, 'workspace_owner', None)
        if workspace is not None and self.document is None:
            workspace.sync_display_settings(self.state['settings'], getattr(self,'appearance_preview',{}))

    def context_sidebar_target(self):
        return None if self.document is not None else super().context_sidebar_target()

    def record_navigation(self, kind, value=None):
        super().record_navigation(kind, value)
        workspace = getattr(self, 'workspace_owner', None)
        if workspace is None or self.document is not None:
            return
        if kind == 'page' and value in ('gallery', 'clean_export'):
            workspace.utility_navigated('gallery' if value == 'gallery' else 'export')
        elif kind == 'settings':
            explore = ('explore', 'comfy', 'workflows', 'workflow_manager', 'models', 'nodes',
                       'civitai', 'downloads', 'civitai_settings')
            workspace.utility_navigated('explore' if value in explore else 'settings', value)

    def settings(self, section='interface'):
        workspace = getattr(self, 'workspace_owner', None)
        if workspace is not None and self.document is not None:
            return workspace.open_route('settings', section)
        if workspace is not None and section=='interface':
            section='appearance'
        return super().settings(section)

    def changed(self, scope='prompt', refresh=True):
        super().changed(scope, refresh)
        if self._document_ready and self.document is not None:
            self.document.view_changed(self)

    def sync_context_sidebar(self):
        if hasattr(self, 'context_rail'):
            self.context_rail.hide()
        workspace = getattr(self, 'workspace_owner', None)
        if workspace is not None and workspace.utility is self:
            self.prepare_workspace_sidebar()
            workspace.sync_sidebar()

    def return_to_prompt(self):
        if self.document is None:
            if getattr(self, 'workspace_owner', None) is not None:
                return self.workspace_owner.open_route('home')
            return super().return_to_prompt()
        self.surface_stack.setCurrentWidget(self.prompt_editor if self.document.kind == 'category' else self.canvas_shell)
        self.canvas_header.hide()

    def enter_canvas(self):
        super().enter_canvas()
        if hasattr(self, 'canvas_header'):
            self.canvas_header.hide()

    def notice(self, text):
        if getattr(self, '_material_transfer_reporting', False):
            self.workspace_owner.material_transfer.error(text)
            return
        super().notice(text)
        workspace = getattr(self, 'workspace_owner', None)
        if self._document_ready and workspace is not None:
            # Canvas already shows its own floating message. Neither route
            # should create a QMainWindow status bar below the workspace.
            if not self.canvas_shell.isVisible():
                workspace.show_notice(text)
        if self._document_ready and self.document is not None:
            self.document.changed.emit()

    def error(self, text):
        workspace = getattr(self, 'workspace_owner', None)
        if self._document_ready and workspace is not None:
            workspace.show_notice(text)
        else:
            super().error(text)
