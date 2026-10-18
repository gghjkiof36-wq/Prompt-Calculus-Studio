"""View lifetimes and two pane navigation, independent of document writers."""
from dataclasses import dataclass, replace
from pathlib import Path
import uuid

from PySide6.QtCore import QObject, QEvent, Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QStackedWidget, QSplitter, QApplication

from .document_tabs import DocumentTabs
from .document_host import DesktopDocument, EmbeddedHost
from .workspace_files import MarkdownPage
from .workspace_home import WorkspaceHome
from .workspace_locations import path_key
from .widgets import RoundMenu


@dataclass
class Pane:
    key: str
    container: QWidget
    strip: DocumentTabs
    pages: QStackedWidget


class PaneFocus(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.w = workspace
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn):
            widget = obj if isinstance(obj, QWidget) else None
            if widget is not None:
                ancestor = widget
                while ancestor is not None:
                    proxy = ancestor.graphicsProxyWidget()
                    if proxy is not None and proxy.scene() is not None:
                        views = proxy.scene().views()
                        widget = views[0] if views else widget
                        break
                    ancestor = ancestor.parentWidget()
            owner = widget
            while owner is not None and getattr(owner, '_workspace_pages', None) is None:
                owner = owner.parentWidget()
            for pane in self.w.panes:
                if widget is not None and (getattr(owner, '_workspace_pages', None) is pane.pages
                        or widget is pane.container or pane.container.isAncestorOf(widget)
                        or widget is pane.strip or pane.strip.isAncestorOf(widget)):
                    self.w.use_pane(pane)
                    break
        return False


class WorkspacePanes:
    """Methods mixed into the shell; legacy public single-pane entry names remain."""
    def init_panes(self):
        from .workspace_pane_chrome import PaneSplitter, PaneTabRow, PaneResizeGrip
        self.documents = []
        self.panes = []
        self.split_ratio = .5
        self.pane_split = PaneSplitter()
        self.pane_split.setObjectName('DocumentPaneSplit')
        self.home_split.replaceWidget(1, self.pane_split)
        self._make_pane(self.tab_strip, self.pages)
        self.active_pane = self.panes[0]
        self.pages = self.active_pane.pages
        self.pane_tab_row = PaneTabRow(self)
        self.pane_grip = PaneResizeGrip(self)
        self.pane_split.splitterMoved.connect(self.remember_ratio)
        self.pane_focus = PaneFocus(self)
        from .workspace_tab_drag import TabDrag
        self.tab_drag = TabDrag(self)

    def _make_pane(self, strip=None, pages=None):
        container = QWidget()
        container.setObjectName('DocumentPane')
        container.setMinimumWidth(250)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        existing_strip = strip is not None
        strip = strip or DocumentTabs()
        from .workspace_pages import WorkspacePages
        if pages is not None:
            pages.deleteLater()  # Empty bootstrap stack; editors use stable owners.
        pages = WorkspacePages(self)
        pages.setObjectName('DocumentPages')
        layout.addWidget(pages, 1)
        pane = Pane(uuid.uuid4().hex, container, strip, pages)
        self.panes.append(pane)
        self.pane_split.addWidget(container)
        container.installEventFilter(pages)
        # The first strip was connected by the original shell builder.
        if existing_strip:
            for signal, callback in ((strip.newRequested, self.new_tab), (strip.bar.currentChanged, self.activate),
                    (strip.bar.tabBarClicked, self.activate), (strip.bar.tabCloseRequested, self.close_tab),
                    (strip.bar.customContextMenuRequested, self.tab_menu)):
                signal.disconnect(callback)
        strip.newRequested.connect(lambda: self.new_tab(pane=pane))
        strip.bar.currentChanged.connect(lambda at: self.activate_in(pane, at))
        strip.bar.tabBarClicked.connect(lambda at: self.activate_in(pane, at))
        strip.bar.tabCloseRequested.connect(lambda at: self.close_tab(at, pane=pane))
        strip.bar.tabMoved.connect(lambda *_: self.layout_changed())
        strip.bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        strip.bar.customContextMenuRequested.connect(lambda point: self.tab_menu(point, pane))
        strip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        strip.customContextMenuRequested.connect(lambda point: self.pane_menu(pane, strip.mapToGlobal(point)))
        strip.pane_menu = lambda menu: self.add_pane_actions(menu, pane)
        if hasattr(self, 'tab_drag'):
            self.tab_drag.register(pane)
        return pane

    def remember_ratio(self, *_):
        sizes = self.pane_split.sizes()
        if len(sizes) == 2 and sum(sizes):
            self.split_ratio = sizes[0]/sum(sizes)
        self.layout_changed()

    def layout_changed(self):
        if hasattr(self, 'layout_store'):
            self.layout_store.schedule()

    def pane_for(self, tab):
        return next((p for p in self.panes if p.key == tab.pane), self.active_pane)

    def use_pane(self, pane):
        if pane not in self.panes:
            return
        previous = getattr(self, 'active_pane', None)
        if previous is pane:
            return
        if previous is not pane:
            old = self.tabs_by_key.get(self.active_key)
            if old and old.document and not old.document.flush_views():
                # A modal form remains owned by its originating view.
                return
        self.active_pane = pane
        self.tab_strip, self.pages = pane.strip, pane.pages
        key = pane.strip.bar.tabData(pane.strip.bar.currentIndex())
        if key in self.tabs_by_key and self.active_route == 'home' and previous is not pane:
            self.activate(pane.strip.bar.currentIndex())
        for candidate in self.panes:
            candidate.strip.bar.pane_active = candidate is pane
            candidate.strip.bar.workspace_active = self.active_route == 'home'
            candidate.strip.bar.update()
        self.layout_changed()

    def activate_in(self, pane, at):
        self.use_pane(pane)
        self.activate(at)

    def ensure_split(self, *, empty=False):
        if len(self.panes) == 2:
            return self.panes[1]
        right = self._make_pane()
        self.arrange_navigation()
        self.style_tab_strip(right.strip)
        if not empty:
            self.new_tab(pane=right)
        total = max(500, self.pane_split.width())
        self.pane_split.setSizes([int(total*self.split_ratio), int(total*(1-self.split_ratio))])
        self.layout_changed()
        return right

    def tab_for_path(self, path, include_history=False):
        if path is None:return None
        pages = self.all_pages() if include_history else list(self.tabs_by_key.values())
        pages.sort(key=lambda t: t.pane != self.active_pane.key)
        return next((t for t in pages if t.path and path_key(t.path) == path_key(path)), None)

    def index_of(self, key):
        tab = self.tabs_by_key.get(key)
        bar = self.pane_for(tab).strip.bar if tab else self.tab_strip.bar
        return next((i for i in range(bar.count()) if bar.tabData(i) == key), -1)

    def add_tab(self, tab, replace_current=False, position=None, pane=None, activate=True, background=False):
        pane = pane or self.active_pane
        bar = pane.strip.bar
        previous_index = bar.currentIndex()
        at = bar.currentIndex() if position is None else position
        current = self.tabs_by_key.get(bar.tabData(at)) if replace_current else None
        if current:
            if not self.prepare_navigation(current):
                if tab.key not in self.saved_pages:self.dispose_page(tab)
                return None
            self.saved_pages[current.key] = self.tabs_by_key.pop(current.key)
            tab.slot = current.slot
        elif not tab.slot:
            tab.slot = uuid.uuid4().hex
        self.saved_pages.pop(tab.key, None)
        tab.pane = pane.key
        self.tabs_by_key[tab.key] = tab
        if pane.pages.indexOf(tab.widget) < 0:pane.pages.addWidget(tab.widget)
        bar.blockSignals(True)
        if current:bar.setTabText(at, tab.title)
        else:at = bar.addTab(tab.title)
        bar.setTabData(at, tab.key)
        bar.blockSignals(False)
        self.update_tab(tab)
        if background:
            bar.blockSignals(True)
            bar.setCurrentIndex(previous_index)
            bar.blockSignals(False)
        elif activate:
            bar.setCurrentIndex(at)
            self.activate_in(pane, at)
        else:
            bar.blockSignals(True)
            bar.setCurrentIndex(at)
            bar.blockSignals(False)
            pane.pages.setCurrentWidget(tab.widget)
        self.layout_changed()
        return tab

    def update_tab(self, tab):
        at = self.index_of(tab.key)
        if at < 0:return
        strip = self.pane_for(tab).strip
        if tab.dirty:tab.retained = True
        strip.decorate(at, tab.title, True, tab.dirty, str(tab.path or ''))
        error = getattr(tab.document or tab.widget, 'error', '')
        if error:strip.bar.setTabToolTip(at, strip.bar.tabToolTip(at)+'\n'+error)

    def open_path(self, path, retained=False, *, new_tab=False, pane=None, quiet=False, background=False):
        from .workspace_shell import Tab
        from .workspace_layout import MissingDocument
        pane = pane or self.active_pane
        try:
            path = self.locations.file(path)
            matching = [t for t in self.all_pages() if t.path and not isinstance(t.widget, MissingDocument) and (path_key(t.path) == path_key(path)
                        or (t.path.exists() and path.exists() and t.path.samefile(path)))]
            if not new_tab:
                current = next((t for t in matching if t.pane == pane.key and t.key in self.tabs_by_key), None)
                if current:
                    if not background:self.focus_tab(current)
                    return current
                cached = next((t for t in matching if t.pane == pane.key and t.key in self.saved_pages), None)
                if cached:return self.add_tab(cached, replace_current=not background, pane=pane, background=background)
            if path.suffix.casefold() == '.md':
                page = MarkdownPage(path, matching[0].widget.buffer if matching else None)
                tab = Tab('md:'+uuid.uuid4().hex, path.stem, page, retained, path=path)
                if not hasattr(page.buffer, 'save_key'):page.buffer.save_key = tab.key
                page.textChanged.connect(lambda: self.document_changed(tab))
                page.document().modificationChanged.connect(lambda _: self.update_tab(tab))
            else:
                document = matching[0].document if matching else next((d for d in self.documents if d.path == path), None)
                if document is None:
                    document = DesktopDocument(path, self.session, self)
                    if any(d.key == document.key for d in self.documents):
                        document.deleteLater()
                        raise ValueError('另一個位置的同身分文件已開啟，請先建立獨立副本。')
                    document.prepare()
                    self.documents.append(document)
                host = EmbeddedHost(document.data, document, workspace=self)
                host.bind_workspace(self)
                host.comfy.stateChanged.connect(self.refresh_connection)
                page = QWidget()
                layout = QVBoxLayout(page)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.addWidget(host)
                if document.kind == 'category':
                    page._workspace_minimum_width = host.prompt_editor.workspace_minimum_width
                page._prepare_workspace_show = host.canvas.present if document.kind == 'workflow' else None
                # A view survives Save As. Reopening its former source must
                # therefore never reuse the former document id as a view key.
                key = 'view:'+uuid.uuid4().hex
                tab = Tab(key, path.stem, page, retained, document, path, host=host)
                document.changed.connect(lambda: self.document_changed(tab))
                document.editing.connect(lambda: self.retain(tab))
            return self.add_tab(tab, replace_current=not new_tab and not background, pane=pane, background=background)
        except (ValueError, OSError, KeyError) as error:
            self.last_open_error = str(error)
            if not quiet:self.report_error(str(error))

    def new_tab(self, *, pane=None, activate=True):
        from .workspace_shell import Tab
        pane = pane or self.active_pane
        page = WorkspaceHome(self.root, self.recent_files, self.settings,
                             lambda: self.choose_file_in(self.pane_for(tab)),
                             lambda path: self.open_path(path, True, pane=self.pane_for(tab)),
                             lambda kind: self.create_in_home(kind, tab))
        tab = Tab('new:'+uuid.uuid4().hex, '新分頁', page, True)
        return self.add_tab(tab, pane=pane, activate=activate)

    def create_in_home(self, kind, tab):
        # Bind the action to its home, including keyboard/queued activation
        # while the other pane has focus. Mouse focus is not the destination.
        context = replace(self.commands.context('home'),
                          pane_id=self.pane_for(tab).key, view_key=tab.key)
        return self.commands.new(kind, context)

    def choose_file_in(self, pane):
        self.use_pane(pane)
        return self.choose_file()

    def focus_tab(self, tab):
        pane = self.pane_for(tab)
        self.use_pane(pane)
        pane.strip.bar.setCurrentIndex(self.index_of(tab.key))
        self.activate(self.index_of(tab.key))

    def open_context(self, path, context):
        pane = next((p for p in self.panes if p.key == context.pane_id), self.active_pane)
        tab = self.tabs_by_key.get(context.view_key)
        if tab:
            self.focus_tab(tab)
            pane = self.pane_for(tab)
        return self.open_path(path, True, new_tab=tab is None, pane=pane)

    def move_view(self, tab, target=None, position=None):
        source = self.pane_for(tab)
        target = target or (self.ensure_split(empty=True) if len(self.panes) == 1 else next(p for p in self.panes if p is not source))
        if source is target:
            if position is not None:source.strip.bar.moveTab(self.index_of(tab.key), max(0, min(position, source.strip.bar.count()-1)))
            self.layout_changed()
            return
        self._detach_view(tab)
        placeholders = [t for t in self.tabs_by_key.values() if t.pane == target.key and isinstance(t.widget, WorkspaceHome)]
        if target.strip.bar.count() == 1 and placeholders:
            self.close_tab(0, pane=target, make_home=False)
        self.add_tab(tab, pane=target)
        if position is not None:target.strip.bar.moveTab(self.index_of(tab.key), min(position, target.strip.bar.count()-1))
        if source.strip.bar.count() == 0:
            if source is self.panes[-1] and len(self.panes) == 2:
                self.collapse_empty_pane(source)
            else:
                self.new_tab(pane=source, activate=False)
        self.focus_tab(tab)
        self.layout_changed()

    def duplicate_view(self, tab, other=False):
        if not tab.path:return
        pane = self.pane_for(tab)
        if other:
            fresh = len(self.panes) == 1
            pane = self.ensure_split(empty=True) if fresh else next(p for p in self.panes if p is not pane)
        return self.open_path(tab.path, True, new_tab=True, pane=pane)

    def _detach_view(self, tab):
        pane = self.pane_for(tab)
        bar = pane.strip.bar
        at = self.index_of(tab.key)
        bar.blockSignals(True)
        bar.removeTab(at)
        bar.blockSignals(False)
        self.tabs_by_key.pop(tab.key, None)
        pane.pages.removeWidget(tab.widget)
        if bar.count():pane.pages.setCurrentWidget(self.tabs_by_key[bar.tabData(bar.currentIndex())].widget)
        pane.strip.fit()

    def dispose_page(self, tab):
        self.autosave.forget(tab)
        if tab.document:
            # Keep the document's execution owner alive after its last view.
            # No focus change or view close cancels accepted work.
            tab.host.hide()
            tab.host.setParent(self)
        self.pane_for(tab).pages.removeWidget(tab.widget)
        tab.widget.deleteLater()
        return True

    def close_tab(self, position, decision=None, make_home=True, *, pane=None):
        pane = pane or self.active_pane
        tab = self.tabs_by_key.get(pane.strip.bar.tabData(position))
        if tab is None or not self.prepare_close(tab, decision):return False
        self._detach_view(tab)
        self.dispose_page(tab)
        self.prune_saved_pages()
        if pane.strip.bar.count():self.activate_in(pane, pane.strip.bar.currentIndex())
        elif make_home:
            if len(self.panes) == 2:self.collapse_empty_pane(pane)
            else:self.new_tab(pane=pane)
        self.layout_changed()
        return True

    def collapse_empty_pane(self, pane):
        """Close an empty split, including the last right tab moved back left."""
        if len(self.panes) != 2 or pane.strip.bar.count():return
        other = next(p for p in self.panes if p is not pane)
        current = self.tabs_by_key.get(other.strip.bar.tabData(other.strip.bar.currentIndex()))
        if current:self.focus_tab(current)
        self.merge_panes()

    def add_pane_actions(self, menu, pane):
        menu.addSeparator()
        if len(self.panes) == 2:
            menu.addAction('合回單區', self.merge_panes)
            menu.addSeparator()
        menu.addAction('關閉此分區' if len(self.panes) == 2 else '關閉所有分頁', lambda: self.close_pane_tabs(pane))

    def pane_menu(self, pane, point):
        menu = RoundMenu(self)
        menu.addAction('新分頁', lambda: self.new_tab(pane=pane))
        self.add_pane_actions(menu, pane)
        menu.open_at(point)

    def merge_panes(self):
        if len(self.panes) != 2:return
        left, right = self.panes
        focused = self.tabs_by_key.get(self.active_key)
        files = [t for t in self.tabs_by_key.values() if not isinstance(t.widget, WorkspaceHome)]
        for tab in list(self.tabs_by_key.values()):
            if isinstance(tab.widget, WorkspaceHome) and tab is not focused and (files or len(self.tabs_by_key) > 1):
                self.close_tab(self.index_of(tab.key), decision='discard', make_home=False, pane=self.pane_for(tab))
        right_order = [self.tabs_by_key[right.strip.bar.tabData(i)] for i in range(right.strip.bar.count())]
        bar = left.strip.bar
        bar.blockSignals(True)
        try:
            for tab in right_order:
                self._detach_view(tab)
                tab.pane = left.key
                self.tabs_by_key[tab.key] = tab
                left.pages.addWidget(tab.widget)
                index = bar.addTab(tab.title)
                bar.setTabData(index, tab.key)
                self.update_tab(tab)
            for tab in self.saved_pages.values():
                if tab.pane == right.key:
                    tab.pane = left.key
                    left.pages.addWidget(tab.widget)
        finally:
            bar.blockSignals(False)
        self.panes.remove(right)
        self.active_pane, self.tab_strip, self.pages = left, left.strip, left.pages
        right.container.hide()
        right.container.setParent(None)
        right.container.deleteLater()
        right.strip.hide()
        right.strip.deleteLater()
        self.arrange_navigation()
        if focused and focused.key in self.tabs_by_key:self.focus_tab(focused)
        elif bar.count():self.activate(bar.currentIndex())
        else:self.new_tab()
        self.sync_sidebar()
        self.layout_changed()

    def close_pane_tabs(self, pane):
        if pane not in self.panes:return
        tabs = [self.tabs_by_key[pane.strip.bar.tabData(i)] for i in range(pane.strip.bar.count())]
        checked, failed = {}, []
        for tab in tabs:
            owner = id(tab.document or getattr(tab.widget, 'buffer', tab.widget))
            if owner not in checked:checked[owner] = self.prepare_close(tab)
            if checked[owner]:self.close_tab(self.index_of(tab.key), decision='discard', make_home=False, pane=pane)
            else:failed.append(tab)
        if not pane.strip.bar.count():
            if len(self.panes) == 2:self.collapse_empty_pane(pane)
            else:self.new_tab(pane=pane)
        if failed:self.focus_tab(failed[0])
        self.layout_changed()
        return not failed

    def tab_menu(self, point, pane=None):
        from .workspace_layout import MissingDocument
        pane = pane or self.active_pane
        at = pane.strip.bar.tabAt(point)
        if at < 0:return self.pane_menu(pane, pane.strip.bar.mapToGlobal(point))
        tab = self.tabs_by_key[pane.strip.bar.tabData(at)]
        menu = RoundMenu(self)
        if isinstance(tab.widget, MissingDocument):
            menu.addAction('重試開啟', lambda: self.layout_store.retry(tab))
            menu.addSeparator()
        elif tab.path:
            menu.addAction('在新分頁開啟', lambda: self.duplicate_view(tab))
            menu.addAction('在另一側開啟', lambda: self.duplicate_view(tab, True))
            menu.addAction('移至右側' if pane is self.panes[0] else '移至左側', lambda: self.move_view(tab))
            menu.addSeparator()
            menu.addAction('保存文件 · Ctrl+S', lambda: self.save_tab(tab))
            editor = tab.host.canvas if tab.document else tab.widget
            menu.addAction('復原 · Ctrl+Z', editor.undo)
            menu.addAction('重做 · Ctrl+Y', editor.redo)
            if tab.document and tab.document.error:
                menu.addAction('檢視回復版本', lambda: self.show_recovery(tab.document))
            for location in getattr(tab.document or tab.widget, 'recovery_paths', []):
                path = Path(location)
                if path.is_file() and path.suffix.casefold() in ('.md', '.pcs-category', '.pcs-workflow'):
                    menu.addAction('開啟保留的文件：'+path.name, lambda checked=False, p=path: self.open_path(p, True, new_tab=True, pane=pane))
            context = self.commands.context('tab', tab=tab)
            self.commands.files.add_actions(menu, context)
            menu.addAction('在側欄中定位', lambda: self.reveal_file(tab.path)).setEnabled(context.root is not None)
            menu.addAction('顯示所在資料夾', lambda: self.show_location(tab.path.parent))
            if context.root is None:
                menu.addAction('將所在資料夾加入側欄', lambda: self.add_root(tab.path.parent))
            menu.addSeparator()
        menu.addAction('關閉分頁', lambda: self.close_tab(self.index_of(tab.key), pane=self.pane_for(tab)))
        self.add_pane_actions(menu, pane)
        menu.open_at(pane.strip.bar.mapToGlobal(point))
