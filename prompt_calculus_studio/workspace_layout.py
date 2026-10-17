"""Atomic view metadata only; document bytes and editing history stay elsewhere."""
import json
import math
from pathlib import Path
import uuid

from PySide6.QtCore import QObject, QTimer, QSaveFile, QIODevice, QEvent, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QWidget, QVBoxLayout
from .widgets import label, button
from .workspace_files import MarkdownPage


class MissingDocument(QWidget):
    def __init__(self, path, reason, retry, view_state=None):
        super().__init__()
        self.error = reason
        self.view_state = view_state if isinstance(view_state, dict) else {}
        self.setObjectName('WorkspaceSurface')
        body = QVBoxLayout(self)
        body.setContentsMargins(36, 32, 36, 32)
        body.addStretch()
        body.addWidget(label('暫時無法開啟這份文件', 'Heading'))
        location = label(str(path), 'Subtle')
        location.setWordWrap(True)
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(location)
        self.reason = label(reason, 'Subtle')
        self.reason.setWordWrap(True)
        body.addWidget(self.reason)
        self.retry_button = button('重試開啟', retry)
        body.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignLeft)
        body.addStretch(2)


def finite_values(values, count):
    return (isinstance(values, list) and len(values) == count
            and all(type(v) in (int, float) and math.isfinite(v) for v in values))


def view_state(tab):
    page = tab.widget
    if isinstance(page, MissingDocument):return page.view_state
    if isinstance(page, MarkdownPage):
        cursor = page.textCursor()
        return dict(cursor=[cursor.position(), cursor.anchor()],
                    scroll=[page.horizontalScrollBar().value(), page.verticalScrollBar().value()])
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
        self.restoring = False
        self.stopped = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.save)

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
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self.w.show_notice('分頁布局尚未儲存：'+str(error)+'\n文件內容仍由各文件保存。')
            return False

    def restore(self):
        if not self.path.exists():return False
        try:
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
        try:
            w = self.w
            w.split_ratio = max(.05, min(.95, ratio))
            if len(panes) == 2:w.ensure_split()
            for pane, saved in zip(w.panes, panes):
                while pane.strip.bar.count():w.close_tab(0, decision='discard', make_home=False, pane=pane)
                mapping = {}
                for record in saved['views']:
                    state = record.get('state') if isinstance(record.get('state'), dict) else {}
                    tab = w.open_path(Path(record['path']), True, new_tab=True, pane=pane, quiet=True) if record.get('path') else w.new_tab(pane=pane)
                    if tab is None:tab = self.missing(record, pane, getattr(w, 'last_open_error', '文件目前無法讀取。'))
                    else:RestoreView(tab, state)
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
            return True
        finally:
            self.restoring = False

    def missing(self, record, pane, error):
        from .workspace_shell import Tab
        path = Path(record['path'])
        page = MissingDocument(path, error, lambda: self.retry(tab), record.get('state'))
        tab = Tab('missing:'+uuid.uuid4().hex, str(record.get('title') or path.stem), page, True, path=path)
        return self.w.add_tab(tab, pane=pane)

    def retry(self, tab):
        if tab.key not in self.w.tabs_by_key:return
        w, pane = self.w, self.w.pane_for(tab)
        index = w.index_of(tab.key)
        opened = w.open_path(tab.path, True, new_tab=True, pane=pane, quiet=True)
        if opened is None:
            tab.widget.error = w.last_open_error
            tab.widget.reason.setText(w.last_open_error)
            return
        state = tab.widget.view_state
        w.close_tab(index, decision='discard', make_home=False, pane=pane)
        pane.strip.bar.moveTab(w.index_of(opened.key), index)
        w.focus_tab(opened)
        RestoreView(opened, state)
        w.refresh_tree()
        self.schedule()
