"""Incremental disclosure state; persist once after interaction settles."""
import json
from pathlib import Path
from PySide6.QtCore import QTimer, QSaveFile, QIODevice
from .workspace_commands import PATH_ROLE
from .workspace_locations import path_key


class WorkspaceTreeState:
    def __init__(self, workspace):
        self.w, self.tree = workspace, workspace.tree
        saved = workspace.read_navigation_json('workspace-tree.json')
        saved = saved if isinstance(saved, dict) else {}
        self.loaded = saved.get('version') == 1
        expanded = saved.get('expanded')
        self.expanded = {path_key(p) for p in expanded[:20000] if isinstance(p, str) and Path(p).is_absolute()} if isinstance(expanded, list) else set()
        self.selected = saved.get('selected') if isinstance(saved.get('selected'), str) else None
        self.scroll = saved.get('scroll', 0) if type(saved.get('scroll')) is int else 0
        self.restoring = False
        self.revealed = set()
        self.branches = set()
        self.closed_branches = set()
        self.fold_action = None
        self.dirty = False
        self.timer = QTimer(workspace)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.save)
        self.tree.itemExpanded.connect(lambda item: self.disclosure(item, True))
        self.tree.itemCollapsed.connect(lambda item: self.disclosure(item, False))
        self.tree.currentItemChanged.connect(self.selection)
        self.tree.verticalScrollBar().valueChanged.connect(self.scrolled)

    def changed(self):
        if not self.restoring:
            self.dirty = True
            self.timer.start()

    def disclosure(self, item, expanded):
        value = item.data(0, PATH_ROLE)
        if value:
            key = path_key(value)
            self.expanded.add(key) if expanded else self.expanded.discard(key)
            if key in self.branches:
                self.closed_branches.discard(key) if expanded else self.closed_branches.add(key)
            self.sync_button()
            self.changed()

    def rebuild_branches(self):
        self.branches = {key for key, item in self.w.path_items.items() if item.childCount()}
        self.closed_branches = self.branches-self.expanded
        self.sync_button()

    def sync_button(self):
        expand = bool(self.closed_branches)
        self.w.tree_fold.setEnabled(bool(self.branches))
        if expand != self.fold_action:
            from .ui_icons import icon
            self.fold_action = expand
            title = '全部展開' if expand else '全部收合'
            self.w.tree_fold.setIcon(icon('unfold-all' if expand else 'fold-all', 'navigation'))
            self.w.tree_fold.setToolTip(title)
            self.w.tree_fold.setAccessibleName(title)

    def toggle_all(self):
        self.tree.expandAll() if self.closed_branches else self.tree.collapseAll()

    def selection(self, item, previous=None):
        if not self.restoring:
            self.selected = item.data(0, PATH_ROLE) if item else None
            self.changed()

    def scrolled(self, value):
        if not self.restoring:
            self.scroll = value
            self.changed()

    def restore_scroll(self, value):
        self.restoring = True
        try:
            self.tree.verticalScrollBar().setValue(value)
            self.scroll = self.tree.verticalScrollBar().value()
        finally:
            self.restoring = False

    def relocate(self, source, target):
        from .workspace_file_io import relocate
        self.expanded = {path_key(relocate(Path(p), source, target)) for p in self.expanded}
        if self.selected:
            self.selected = str(relocate(Path(self.selected), source, target))
        self.changed()

    def save(self):
        self.timer.stop()
        if not self.dirty:
            return
        value = dict(version=1, expanded=sorted(self.expanded), selected=self.selected, scroll=self.scroll)
        output = QSaveFile(str(self.w.data/'workspace-tree.json'))
        output.setDirectWriteFallback(False)
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
        if (not output.open(QIODevice.OpenModeFlag.WriteOnly) or output.write(raw) != len(raw)
                or not output.commit()):
            self.w.show_notice('資料夾展收狀態尚未保存：' + output.errorString())
            return
        self.dirty = False
