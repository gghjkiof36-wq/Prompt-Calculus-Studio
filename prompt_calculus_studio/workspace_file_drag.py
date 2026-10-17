"""Single-item sidebar moves. This MIME never represents Canvas materials."""
from pathlib import Path
import uuid
from PySide6.QtCore import QMimeData, QTimer, Qt, QPersistentModelIndex
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QAbstractItemView
from .workspace_commands import PATH_ROLE, KIND_ROLE

MIME = 'application/x-pcs-sidebar-file-move'


class WorkspaceFileDrag:
    def __init__(self, tree):
        self.tree, self.w = tree, tree.workspace
        self.op = None
        self.token = ''
        self.target = QPersistentModelIndex()
        self.position = None
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
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.finish()
            drag.deleteLater()

    def finish(self):
        self.clear_target()
        if self.op is not None:
            op, self.op = self.op, None
            self.token = ''
            self.w.commands.files.end(op)

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
