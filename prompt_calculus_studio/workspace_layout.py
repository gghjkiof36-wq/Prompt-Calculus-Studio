"""Atomic view metadata only; document bytes and editing history stay elsewhere."""
import json
import hashlib
import math
from pathlib import Path
import uuid

from PySide6.QtCore import QObject, QTimer, QSaveFile, QIODevice, QEvent, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QScrollArea, QSizePolicy
from .widgets import label, button
from .workspace_files import MarkdownPage


class MissingDocument(QWidget):
    def __init__(self, path, reason, retry, view_state=None, *, recover=None, directories=()):
        super().__init__()
        self.error = reason
        self.view_state = view_state if isinstance(view_state, dict) else {}
        self.recovered_path = None
        self.recover = recover
        self.busy = False
        self.setObjectName('WorkspaceSurface')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        body = QVBoxLayout(content)
        body.setContentsMargins(36, 32, 36, 32)
        body.addStretch()
        form = QWidget()
        form.setMaximumWidth(640)
        fields = QVBoxLayout(form)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(12)
        body.addWidget(form)
        fields.addWidget(label('暫時無法開啟這份文件', 'Heading', True))
        location = label(str(path), 'Subtle')
        location.setWordWrap(True)
        location.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        fields.addWidget(location)
        self.reason = label(reason, 'Subtle')
        self.reason.setWordWrap(True)
        self.reason.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        fields.addWidget(self.reason)
        if recover:
            from .workspace_file_dialogs import NameEdit
            fields.addSpacing(12)
            fields.addWidget(label('檔案名稱', 'Subtle'))
            self.name = NameEdit(path.stem + ' 副本')
            self.name.setAccessibleName('新檔名稱')
            fields.addWidget(self.name)
            fields.addWidget(label('儲存位置', 'Subtle'))
            self.directory = QComboBox()
            self.directory.setEditable(True)
            self.directory.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            self.directory.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.directory.setMinimumContentsLength(12)
            self.directory.setLineEdit(NameEdit())
            self.directory.setAccessibleName('新檔儲存位置')
            self.directory.lineEdit().setPlaceholderText('選擇或貼上資料夾位置')
            self.directory.addItems([str(folder) for folder in directories])
            fields.addWidget(self.directory)
            self.message = label('', 'Subtle', True)
            self.message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.message.hide()
            fields.addWidget(self.message)
            actions = QHBoxLayout()
            self.recover_button = button('另存新檔', self.submit_recovery, 'Primary')
            actions.addWidget(self.recover_button)
            self.another_button = button('另存另一份', self.choose_another)
            self.another_button.hide()
            actions.addWidget(self.another_button)
            self.name.returnPressed.connect(self.submit_recovery)
            self.directory.lineEdit().returnPressed.connect(self.submit_recovery)
        else:
            actions = QHBoxLayout()
        self.retry_button = button('重試', retry)
        actions.addWidget(self.retry_button)
        actions.addStretch()
        fields.addLayout(actions)
        body.addStretch(2)

    def submit_recovery(self):
        if self.busy or self.name.composing or self.directory.lineEdit().composing:return
        self.busy = True
        self.recover_button.setEnabled(False)
        self.another_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        try:
            self.recover(self.name.text(), self.directory.currentText())
        finally:
            self.busy = False
            self.recover_button.setEnabled(True)
            self.another_button.setEnabled(True)
            self.retry_button.setEnabled(True)

    def recovery_error(self, message, *, allow_new=False):
        self.message.setText(message)
        self.message.show()
        if self.recovered_path:
            self.recover_button.setText('開啟文件')
            self.name.setEnabled(False)
            self.directory.setEnabled(False)
        self.another_button.setVisible(allow_new)

    def choose_another(self):
        self.recovered_path = None
        self.name.setEnabled(True)
        self.directory.setEnabled(True)
        self.recover_button.setText('另存新檔')
        self.another_button.hide()
        self.message.setText('請另存為新文件，先前建立的檔案會保留。')
        self.name.setFocus()
        self.name.selectAll()


def finite_values(values, count):
    return (isinstance(values, list) and len(values) == count
            and all(type(v) in (int, float) and math.isfinite(v) for v in values))


def view_state(tab):
    page = tab.widget
    if isinstance(page, MissingDocument):return page.view_state
    if isinstance(page, MarkdownPage):
        cursor = page.textCursor()
        return dict(cursor=[cursor.position(), cursor.anchor()],
                    scroll=[page.horizontalScrollBar().value(), page.verticalScrollBar().value()],
                    reading_scroll=page.md_surface.reader.verticalScrollBar().value())
    if tab.host:
        tab.host.remember_canvas_view()
        result = dict(camera=getattr(tab.host, 'local_canvas_view', None))
        editor = getattr(tab.host, 'prompt_editor', None)
        if editor is None:return result
        current = editor.listing.currentItem()
        selected = current.data(Qt.ItemDataRole.UserRole) if current else None
        if isinstance(selected, str):result['selected'] = selected
        result['scroll'] = [editor.listing.verticalScrollBar().value(), editor.scroll.verticalScrollBar().value()]
        return result
    return {}


class RestoreView(QObject):
    """Apply scroll/camera after the real viewport has its restored size."""
    def __init__(self, tab, state):
        super().__init__(tab.widget)
        self.tab, self.state, self.pending = tab, state, True
        tab.widget.installEventFilter(self)
        self.apply()
        if tab.widget.isVisible():QTimer.singleShot(0, self.finish)

    def eventFilter(self, obj, event):
        if self.pending and event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, self.finish)
        return False

    def finish(self):
        if not self.pending:return
        self.apply()
        self.pending = False
        self.tab.widget.removeEventFilter(self)

    def apply(self):
        tab, state = self.tab, self.state
        if isinstance(tab.widget, MarkdownPage):
            page = tab.widget
            workspace = page.file_title.workspace
            mode = getattr(workspace, 'markdown_mode', state.get('markdown_mode', 'write'))
            page.md_surface.apply_mode(mode, focus=False)
            saved = state.get('cursor')
            if finite_values(saved, 2):
                maximum = max(0, page.document().characterCount()-1)
                cursor = page.textCursor()
                cursor.setPosition(max(0, min(maximum, int(saved[1]))))
                cursor.setPosition(max(0, min(maximum, int(saved[0]))), QTextCursor.MoveMode.KeepAnchor)
                page.setTextCursor(cursor)
            scroll = state.get('scroll')
            if finite_values(scroll, 2):
                page.horizontalScrollBar().setValue(int(scroll[0]))
                page.verticalScrollBar().setValue(int(scroll[1]))
            reading_scroll = state.get('reading_scroll')
            if type(reading_scroll) is int and reading_scroll >= 0:
                page.md_surface.reader.verticalScrollBar().setValue(reading_scroll)
        elif tab.host:
            camera = state.get('camera')
            if finite_values(camera, 3) and .01 <= camera[0] <= 100:
                tab.host.local_canvas_view = list(camera)
                tab.host.canvas._hidden_view = None
                tab.host.canvas.restore_view()
            editor = getattr(tab.host, 'prompt_editor', None)
            if editor is None:return
            if isinstance(state.get('selected'), str):
                for i in range(editor.listing.count()):
                    item = editor.listing.item(i)
                    if item.data(Qt.ItemDataRole.UserRole) == state['selected']:
                        editor.listing.setCurrentItem(item)
                        break
            scroll = state.get('scroll')
            if finite_values(scroll, 2):
                editor.listing.verticalScrollBar().setValue(int(scroll[0]))
                editor.scroll.verticalScrollBar().setValue(int(scroll[1]))


class WorkspaceLayout(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.w = workspace
        self.path = workspace.data/'workspace-layout.json'
        from .draft_cache import DraftCache
        self.recovery = DraftCache(workspace.data)
        self.restoring = False
        self.stopped = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.save)

    def eventFilter(self, obj, event):
        if obj is self.w and event.type() == QEvent.Type.Show:
            self.w.removeEventFilter(self)
            QTimer.singleShot(0, self.finish_restore_geometry)
        return False

    def finish_restore_geometry(self):
        if self.stopped:return
        if len(self.w.panes) == 2:
            total = self.w.pane_split.width() - self.w.pane_split.handleWidth()
            left = round(total*self.w.split_ratio)
            self.w.pane_split.setSizes([left, total-left])
        # The saved camera belongs to the final pane size, not the hidden
        # splitter's temporary size hints during construction.
        for restore in getattr(self, '_geometry_views', []):
            restore.apply()
        self._geometry_views = []

    def schedule(self):
        if not self.restoring and not self.stopped:self.timer.start()

    def snapshot(self):
        panes = []
        for pane in self.w.panes:
            bar = pane.strip.bar
            views = []
            for i in range(bar.count()):
                tab = self.w.tabs_by_key[bar.tabData(i)]
                views.append(dict(id=tab.key, title=tab.title, path=str(tab.path) if tab.path else None,
                                  state=view_state(tab)))
            panes.append(dict(views=views, current=bar.tabData(bar.currentIndex())))
        return dict(version=1, ratio=self.w.split_ratio, panes=panes,
                    active=self.w.panes.index(self.w.active_pane))

    def save(self):
        self.timer.stop()
        if self.stopped or self.restoring:return True
        try:
            raw = json.dumps(self.snapshot(), ensure_ascii=False, allow_nan=False).encode('utf-8')
            output = QSaveFile(str(self.path))
            output.setDirectWriteFallback(False)
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):raise OSError(output.errorString())
            if output.write(raw) != len(raw):
                output.cancelWriting()
                raise OSError('無法完整寫入分頁布局。')
            if not output.commit():raise OSError(output.errorString())
            self.recovery.clear('layout')
            return True
        except (OSError, ValueError, RuntimeError) as error:
            try:self.recovery.write('layout', self.snapshot())
            except (OSError, ValueError) as cache_error:self.w.show_notice(str(cache_error))
            return False

    def restore(self, value=None):
        if value is None:value = self.recovery.read('layout')
        if value is None and not self.path.exists():return False
        try:
            if value is None:
                raw = self.path.read_bytes()
                if len(raw) > 2*1024*1024:raise ValueError('布局記錄過大')
                value = json.loads(raw)
            if not isinstance(value, dict) or value.get('version') != 1:raise ValueError('布局版本不支援')
            panes = value.get('panes')
            if not isinstance(panes, list) or not 1 <= len(panes) <= 2:raise ValueError('分區記錄不完整')
            identities = set()
            for pane in panes:
                if not isinstance(pane, dict) or not isinstance(pane.get('views'), list) or len(pane['views']) > 500:
                    raise ValueError('分頁記錄不完整')
                for view in pane['views']:
                    if not isinstance(view, dict) or not isinstance(view.get('id'), str):raise ValueError('分頁身分不完整')
                    if not view['id'] or view['id'] in identities:raise ValueError('分頁身分重複')
                    identities.add(view['id'])
                    if view.get('path') is not None and (not isinstance(view['path'], str) or not Path(view['path']).is_absolute()):
                        raise ValueError('文件位置不完整')
            ratio = value.get('ratio', .5)
            if type(ratio) not in (int, float) or not math.isfinite(ratio):ratio = .5
        except (OSError, ValueError, TypeError) as error:
            self.w.show_notice('無法還原分頁布局，已開啟新分頁：'+str(error))
            return False
        self.restoring = True
        self._geometry_views = []
        try:
            w = self.w
            w.split_ratio = max(.05, min(.95, ratio))
            if len(panes)==1 and len(w.panes)==2:
                extra=w.panes[-1]
                while extra.strip.bar.count():
                    if not w.close_tab(0,decision='discard',make_home=False,pane=extra):return False
                if extra in w.panes:w.collapse_empty_pane(extra)
            if len(panes) == 2:w.ensure_split()
            for pane, saved in zip(w.panes, panes):
                while pane.strip.bar.count():w.close_tab(0, decision='discard', make_home=False, pane=pane)
                mapping = {}
                for record in saved['views']:
                    state = record.get('state') if isinstance(record.get('state'), dict) else {}
                    tab = w.open_path(Path(record['path']), True, new_tab=True, pane=pane, quiet=True) if record.get('path') else w.new_tab(pane=pane)
                    if tab is None:tab = self.missing(record, pane, getattr(w, 'last_open_error', '文件目前無法讀取。'))
                    else:self._geometry_views.append(RestoreView(tab, state))
                    mapping[record['id']] = tab
                if not pane.strip.bar.count():w.new_tab(pane=pane)
                current = mapping.get(saved.get('current'))
                if current:w.focus_tab(current)
            active = value.get('active')
            active = active if type(active) is int and 0 <= active < len(w.panes) else 0
            pane = w.panes[active]
            w.focus_tab(w.tabs_by_key[pane.strip.bar.tabData(pane.strip.bar.currentIndex())])
            w.history = [('document', w.active_key, w.tabs_by_key[w.active_key].slot)]
            w.history_at = 0
            w.record_visit(w.history[0])
            if w.isVisible():QTimer.singleShot(0, self.finish_restore_geometry)
            else:w.installEventFilter(self)
            return True
        finally:
            self.restoring = False

    def missing(self, record, pane, error):
        from .workspace_shell import Tab
        path = Path(record['path'])
        recover, directories = None, []
        if self.w.close_recovery.has_record(path):
            recover = lambda name, directory: self.recover(tab, name, directory)
            for folder in (path.parent, self.w.locations.current, *self.w.locations.roots):
                try:folder = self.w.locations.directory(folder)
                except (ValueError, OSError, TypeError):continue
                if folder not in directories:directories.append(folder)
        if not path.exists():error = '原檔可能已被移動或刪除。'
        page = MissingDocument(path, error, lambda: self.retry(tab), record.get('state'),
                               recover=recover, directories=directories)
        tab = Tab('missing:'+uuid.uuid4().hex, str(record.get('title') or path.stem), page, True, path=path)
        return self.w.add_tab(tab, pane=pane)

    def open_recovery(self, path, *, pane=None):
        from .workspace_recovery import key
        path = Path(path)
        existing = next((view for view in self.w.all_pages() if view.path and key(view.path) == key(path)), None)
        if existing:
            if existing.key not in self.w.tabs_by_key:
                return self.w.add_tab(existing, pane=pane or self.w.active_pane)
            self.w.focus_tab(existing)
            return existing
        if not self.w.close_recovery.has_record(path):
            self.refresh_recovery_homes()
            return None
        opened = self.w.open_path(path, True, new_tab=True, pane=pane, quiet=True)
        if opened is not None:return opened
        return self.missing(dict(path=str(path), title=path.stem), pane or self.w.active_pane,
                            self.w.last_open_error)

    def refresh_recovery_homes(self):
        from .workspace_home import WorkspaceHome
        from .workspace_recovery_inventory import missing_records
        records = missing_records(self.w.close_recovery)
        for view in self.w.all_pages():
            if isinstance(view.widget, WorkspaceHome):view.widget.set_recoveries(records)

    def retry(self, tab):
        if tab.key not in self.w.tabs_by_key:return
        w, pane = self.w, self.w.pane_for(tab)
        opened = w.open_path(tab.path, True, new_tab=True, pane=pane, quiet=True)
        if opened is None:
            tab.widget.error = w.last_open_error
            tab.widget.reason.setText(w.last_open_error)
            return
        self.replace_missing(tab, opened)

    def recover(self, tab, name, directory):
        if tab.key not in self.w.tabs_by_key:return
        from .workspace_recovery_export import RecoveryPublishedError
        from .workspace_recovery import key
        page, w = tab.widget, self.w
        related = [view for view in w.all_pages() if isinstance(view.widget, MissingDocument)
                   and key(view.path) == key(tab.path)]
        # Updating a visible view records a new visit and can prune forward
        # history. Resolve hidden views before any such navigation occurs.
        related.sort(key=lambda view: view.key not in w.saved_pages)
        try:
            if page.recovered_path is None:
                page.recovered_path = w.close_recovery.recover_as(tab.path, name, directory)
        except RecoveryPublishedError as error:
            for view in related:
                view.widget.recovered_path = error.published_path
                view.widget.recovery_error(str(error))
            return
        except (ValueError, OSError) as error:
            page.recovery_error(str(error))
            return
        target = page.recovered_path
        for view in related:view.widget.recovered_path = target
        try:expected = w.close_recovery.verify_recovered(tab.path, target)
        except (ValueError, OSError) as error:
            for view in related:view.widget.recovery_error(str(error), allow_new=True)
            return
        failed, activated = False, None
        for view in related:
            hidden = view.key in w.saved_pages
            opened = w.open_path(target, True, new_tab=True, pane=w.pane_for(view), quiet=True, background=hidden)
            if opened is None:
                view.widget.recovery_error('已另存至 ' + str(target) + '\n暫時無法開啟：' + w.last_open_error)
                failed = True
                continue
            try:
                w.close_recovery.verify_recovered(tab.path, target)
                actual = (opened.document.receipt.sha256 if opened.document
                          else hashlib.sha256(opened.widget.encoded()).hexdigest())
                if actual != expected:raise ValueError('開啟的內容與恢復稿不符，恢復稿仍保留。')
            except (ValueError, OSError) as error:
                w._detach_view(opened)
                w.dispose_page(opened)
                view.widget.recovery_error(str(error), allow_new=True)
                failed = True
                continue
            self.replace_missing(view, opened)
            if view is tab:activated = opened
        if activated:w.focus_tab(activated)
        elif tab.key in w.tabs_by_key:w.focus_tab(tab)
        if not failed:
            w.close_recovery.clear(tab.path)
            self.refresh_recovery_homes()

    def replace_missing(self, tab, opened):
        w, pane = self.w, self.w.pane_for(tab)
        state = tab.widget.view_state
        if tab.key in w.saved_pages:
            # A history view stays hidden and keeps its navigation slot. Its
            # content is now the same saved file as the visible recovered view.
            w._detach_view(opened)
            opened.slot = tab.slot
            w.saved_pages.pop(tab.key)
            w.saved_pages[opened.key] = opened
            w.history = [(entry[0], opened.key, entry[2]) if entry[0] == 'document' and entry[1] == tab.key
                         else entry for entry in w.history]
            w.dispose_page(tab)
        else:
            index = w.index_of(tab.key)
            w.close_tab(index, decision='discard', make_home=False, pane=pane)
            pane.strip.bar.moveTab(w.index_of(opened.key), index)
            w.focus_tab(opened)
        RestoreView(opened, state)
        w.refresh_tree()
        self.schedule()
