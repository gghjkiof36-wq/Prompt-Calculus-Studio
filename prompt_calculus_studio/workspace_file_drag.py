"""Sidebar file moves and page opening share one captured drag identity."""
from pathlib import Path
import uuid
from PySide6.QtCore import QMimeData, QTimer, Qt, QPersistentModelIndex, QObject, QEvent
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QAbstractItemView, QWidget
from shiboken6 import isValid
from .workspace_commands import PATH_ROLE, KIND_ROLE

MIME = 'application/x-pcs-sidebar-file-move'


class WorkspaceFileDrag(QObject):
    def __init__(self, tree):
        super().__init__(tree)
        self.tree, self.w = tree, tree.workspace
        self.op = None
        self.token = ''
        self.target = QPersistentModelIndex()
        self.position = None
        self.drop_widgets = []
        self.page_preview = None
        self.expand_timer = QTimer(tree)
        self.expand_timer.setSingleShot(True)
        self.expand_timer.setInterval(650)
        self.expand_timer.timeout.connect(self.expand_target)
        self.scroll_timer = QTimer(tree)
        self.scroll_timer.setInterval(70)
        self.scroll_timer.timeout.connect(self.scroll)
        tree.setDragEnabled(True)
        tree.setAcceptDrops(True)
        tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        tree.setDropIndicatorShown(False)
        tree.setDefaultDropAction(Qt.DropAction.MoveAction)

    def begin(self):
        item = self.tree.currentItem()
        if item is None or item.data(0, KIND_ROLE) == 'root':
            return False
        files = self.w.commands.files
        if files.inline is not None:
            return False
        context = self.w.commands.context('tree', item)
        try:
            self.op = files.begin(context)
        except (OSError, ValueError) as error:
            self.w.show_notice(str(error))
            return False
        self.token = uuid.uuid4().hex
        self.op.cancel = self.finish
        if context.kind in ('markdown', 'workflow', 'category'):
            from .application_events import listen
            from .workspace_tab_drag import DropPreview
            if self.page_preview is None:
                self.page_preview = DropPreview(self.w)
            for pane in self.w.panes:
                for widget in (pane.pages, pane.strip, pane.strip.bar):
                    self.drop_widgets.append((widget, widget.acceptDrops()))
                    widget.setAcceptDrops(True)
            listen(self, QEvent.Type.DragEnter, QEvent.Type.DragMove,
                   QEvent.Type.DragLeave, QEvent.Type.Drop)
        return True

    def mime(self):
        mime = QMimeData()
        mime.setData(MIME, self.token.encode('ascii'))
        return mime

    def start(self):
        if not self.begin():
            return
        drag = QDrag(self.tree)
        drag.setMimeData(self.mime())
        try:
            drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction, Qt.DropAction.MoveAction)
        finally:
            self.finish()
            drag.deleteLater()

    def finish(self):
        from .application_events import unlisten
        unlisten(self)
        for widget, accepted in self.drop_widgets:
            if isValid(widget):
                widget.setAcceptDrops(accepted)
        self.drop_widgets.clear()
        if self.page_preview is not None:
            self.page_preview.clear()
        self.clear_target()
        if self.op is not None:
            op, self.op = self.op, None
            self.token = ''
            self.w.commands.files.end(op)

    def eventFilter(self, watched, event):
        if not isinstance(watched, QWidget) or self.op is None:
            return False
        if watched is self.tree or self.tree.isAncestorOf(watched):
            if self.page_preview is not None:
                self.page_preview.clear()
            return False  # The tree's existing move target remains authoritative.
        if event.type() == QEvent.Type.DragLeave:
            self.page_preview.clear()
            return False
        if not self.owns(event):
            return False
        point = watched.mapToGlobal(event.position().toPoint())
        target = self.w.tab_drag.destination(point, opening_file=True)
        if target is None:
            self.page_preview.clear()
            event.ignore()
            return True
        self.clear_target()
        if event.type() == QEvent.Type.Drop:
            self.page_preview.clear()
            if self.open_page(target) is None:
                event.ignore()
                return True
        else:
            self.page_preview.present(self.w.tab_drag.target_rect)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        return True

    def open_page(self, target):
        """Opening never moves the file or replaces an existing retained page."""
        if self.op is None or self.op.context.kind not in ('markdown', 'workflow', 'category'):
            return None
        try:
            self.w.commands.files.check_source(self.op)
            pane, at = target
            created_split = pane == 'split'
            if created_split:
                pane = self.w.ensure_split(empty=True)
            tab = next((tab for tab in self.w.tabs_by_key.values()
                        if tab.path == self.op.source and self.w.pane_for(tab) is pane), None)
            if tab is not None:
                self.w.retain(tab)
                self.w.focus_tab(tab)
            else:
                tab = self.w.open_path(self.op.source, True, new_tab=True, pane=pane)
            if tab is not None and at is not None:
                bar = pane.strip.bar
                source = next(i for i in range(bar.count()) if bar.tabData(i) == tab.key)
                bar.moveTab(source, min(at, bar.count()-1))
            if tab is None and created_split:
                self.w.collapse_empty_pane(pane)
            return tab
        except (ValueError, OSError) as error:
            self.w.show_notice(str(error))
            return None

    def owns(self, event):
        return (self.op is not None and event.source() is self.tree and
                bytes(event.mimeData().data(MIME)) == self.token.encode('ascii'))

    def destination(self, position):
        item = self.tree.itemAt(position)
        if item is None or item.data(0, KIND_ROLE) not in ('folder', 'root') or self.op is None:
            return None
        path = Path(item.data(0, PATH_ROLE))
        source = self.op.source
        if (path == source or path == source.parent or source in path.parents or
                self.w.locations.root_for(path) != self.op.context.root):
            return None
        return path

    def enter(self, event):
        if self.owns(event):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def move(self, event):
        if not self.owns(event):
            self.clear_target()
            event.ignore()
            return
        self.position = event.position().toPoint()
        self.update_target()
        if not self.scroll_timer.isActive():
            self.scroll_timer.start()
        if self.destination(self.position) is None:
            event.ignore()
        else:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()

    def update_target(self):
        valid = self.position is not None and self.destination(self.position) is not None
        target = QPersistentModelIndex(self.tree.indexAt(self.position)) if valid else QPersistentModelIndex()
        if target != self.target:
            self.expand_timer.stop()
            self.target = target
            self.tree.viewport().update()
            if target.isValid() and not self.tree.isExpanded(target):
                self.expand_timer.start()

    def expand_target(self):
        if self.target.isValid():
            self.tree.setExpanded(self.target, True)

    def scroll(self):
        if self.position is None:
            return
        y, height = self.position.y(), self.tree.viewport().height()
        direction = -1 if y < 28 else 1 if y > height-28 else 0
        if direction:
            bar = self.tree.verticalScrollBar()
            bar.setValue(bar.value()+direction*max(1, bar.singleStep()))
            self.update_target()

    def clear_target(self):
        self.expand_timer.stop()
        self.scroll_timer.stop()
        self.target = QPersistentModelIndex()
        self.position = None
        self.tree.viewport().update()

    def drop(self, event):
        destination = self.destination(event.position().toPoint()) if self.owns(event) else None
        self.clear_target()
        if destination is None:
            event.ignore()
            return
        try:
            self.w.commands.files.move(self.op, destination)
        except (OSError, ValueError) as error:
            self.w.show_notice(str(error))
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
