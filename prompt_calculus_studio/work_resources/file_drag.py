"""Move selected resource files through the existing workspace file owner."""
from pathlib import Path
import uuid

from PySide6.QtCore import QMimeData, QTimer, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QAbstractItemView

from ..workspace_locations import CommandContext

MIME = 'application/x-pcs-resource-file-move'


class ResourceFileDrag:
    def __init__(self, tree, page):
        self.tree, self.page = tree, page
        self.ops = []
        self.token = ''
        self.ticket = None
        self.position = None
        self.target_row = -1
        self.scroll_timer = QTimer(tree)
        self.scroll_timer.setInterval(70)
        self.scroll_timer.timeout.connect(self.scroll)
        tree.setDragEnabled(True)
        tree.setAcceptDrops(True)
        tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        tree.setDropIndicatorShown(False)

    def context(self, row):
        if not row or row['kind'] not in ('document', 'folder'):
            return None
        path = Path(row.get('absolute_path') or row['location'])
        root = self.page.workspace.locations.root_for(path)
        if root is None or root == path:
            return None
        return CommandContext('resources', path, row['kind'], root, path.parent)

    def begin(self):
        page = self.page
        if self.ops or page.jobs.active or page.workspace.commands.files.inline is not None:
            return False
        rows = [item.data(0, Qt.ItemDataRole.UserRole) for item in self.tree.selectedItems()]
        contexts = [self.context(row) for row in rows]
        if not contexts or any(context is None for context in contexts):
            return False
        # A selected parent already carries its children; never move them twice.
        contexts = [c for c in contexts if not any(p.path in c.path.parents for p in contexts)]
        files = page.workspace.commands.files
        try:
            for context in contexts:
                op = files.begin(context)
                self.ops.append(op)
                op.cancel = self.finish
        except (OSError, ValueError) as error:
            self.finish()
            page.say(str(error))
            return False
        self.token = uuid.uuid4().hex
        self.ticket = page.ticket
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
            drag.exec(Qt.DropAction.MoveAction, Qt.DropAction.MoveAction)
        finally:
            self.finish()
            drag.deleteLater()

    def finish(self):
        self.clear_target()
        files = self.page.workspace.commands.files
        ops, self.ops = self.ops, []
        self.token = ''
        for op in ops:
            files.end(op, refresh=False)
        if self.page.search_pending:
            self.page.search_timer.start(0)

    def owns(self, event):
        return (bool(self.ops) and event.source() is self.tree and
                bytes(event.mimeData().data(MIME)) == self.token.encode('ascii') and
                self.ticket == self.page.ticket)

    def destination(self, position):
        item = self.tree.itemAt(position)
        row = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not row or row['kind'] != 'folder' or not self.ops:
            return None
        path = Path(row['location'])
        root = self.page.workspace.locations.root_for(path)
        if root is None or any(root != op.context.root or path in (op.source, op.source.parent)
                               or op.source in path.parents for op in self.ops):
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
        row = self.tree.indexAt(self.position).row() if self.destination(self.position) else -1
        if row != self.target_row:
            self.target_row = row
            self.tree.viewport().update()

    def clear_target(self):
        self.scroll_timer.stop()
        self.position = None
        if self.target_row != -1:
            self.target_row = -1
            self.tree.viewport().update()

    def scroll(self):
        if self.position is None:
            return
        y, height = self.position.y(), self.tree.viewport().height()
        direction = -1 if y < 28 else 1 if y > height - 28 else 0
        if direction:
            bar = self.tree.verticalScrollBar()
            bar.setValue(bar.value() + direction * max(1, bar.singleStep()))
            self.update_target()

    def drop(self, event):
        target = self.destination(event.position().toPoint()) if self.owns(event) else None
        self.clear_target()
        event.ignore()
        if target is None:
            return
        files = self.page.workspace.commands.files
        completed, errors = [], []
        contexts = [op.context for op in self.ops]
        try:
            # Validate the whole captured selection before making any changes.
            for op in self.ops:
                files.check_source(op)
            self.finish()
            # Each move may repair links in the next selected Markdown file.
            # Recapture that updated file for its own transaction, synchronously,
            # without pumping events or rebuilding the list between operations.
            for context in contexts:
                op = None
                try:
                    op = files.begin(context)
                    completed.append(files.move(op, target))
                except (OSError, ValueError) as error:
                    errors.append(context.path.name + '：' + str(error))
                    break
                finally:
                    if op is not None:
                        files.end(op, refresh=False)
        except (OSError, ValueError) as error:
            errors.append(str(error))
        finally:
            self.finish()
        if completed:
            files.refresh(completed[-1])
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            self.page.rebuild(self.page.store.register(contexts[0].root))
        if errors:
            message = f'已移動 {len(completed)} 項，尚有 {len(contexts)-len(completed)} 項未移動。\n' if completed else ''
            self.page.workspace.show_notice(message + '\n'.join(errors))
