"""Ephemeral document-aware material drag; distinct from every move channel."""
import uuid

from PySide6.QtCore import QObject, QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from . import composition, multi_output
from .theme import visual_tokens

MIME = 'application/x-pcs-workspace-text-copy'


class PlacementPreview(QWidget):
    def __init__(self, view):
        super().__init__(view.viewport())
        self.view = view
        self.kind = None
        self.box = QRectF()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()

    def present(self, kind, point, size):
        self.kind = kind
        self.setGeometry(self.view.viewport().rect())
        at = self.view.mapFromScene(point)
        if kind == 'plus':
            self.box = QRectF(at.x()-12, at.y()-12, 24, 24)
        else:
            self.box = self.view.mapFromScene(QRectF(point.x(), point.y(), *size)).boundingRect()
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event):
        tokens = visual_tokens(self.view.canvas.window.workspace_owner.display_settings())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(tokens['accent'])
        if self.kind == 'plus':
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens['raised']))
            painter.drawEllipse(self.box)
            painter.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            center = self.box.center()
            painter.drawLine(center-QPointF(6, 0), center+QPointF(6, 0))
            painter.drawLine(center-QPointF(0, 6), center+QPointF(0, 6))
        else:
            painter.setPen(QPen(color, 1.5))
            fill = QColor(color); fill.setAlpha(18); painter.setBrush(fill)
            radius = 10*self.view.transform().m11()
            painter.drawRoundedRect(self.box, radius, radius)


class MaterialCopyDrag(QObject):
    def __init__(self, transfer, page, ident):
        super().__init__(transfer)
        self.transfer, self.w, self.page = transfer, transfer.w, page
        self.source = transfer.capture(page, ident)
        self.token = uuid.uuid4().hex.encode('ascii')
        self.root = composition.prototype(self.source.value)
        self.overlays, self.sizes = {}, {}
        self.temporary = {}
        self.previous_accept = {}
        self.hover = None
        self.success_pane = None
        self.success = False
        self.cancelled = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.switch_hover)
        for pane in self.w.panes:
            self.previous_accept[pane.key] = pane.strip.bar.acceptDrops()
            pane.strip.bar.setAcceptDrops(True)
        QApplication.instance().installEventFilter(self)

    def matches(self, mime):
        return (not self.cancelled and mime.hasFormat(MIME)
                and bytes(mime.data(MIME)) == self.token)

    def clear(self):
        for overlay in self.overlays.values():overlay.hide()

    def landing(self, view, point):
        canvas = view.canvas
        document = canvas.window.document
        if document is None or document.kind != 'workflow':return False, None
        canvas_id = canvas.canvas_at(point)
        # A port, output or another node cannot be mistaken for blank space.
        for item in canvas.view.scene().items(point):
            while item.parentItem() is not None:item = item.parentItem()
            key = getattr(item, 'key', None)
            if key is None:continue
            if key == canvas_id:return True, canvas_id
            if key in canvas.cards and canvas_id and multi_output.owner(canvas.window.state, key) == canvas_id:
                return True, canvas_id
            return False, None
        return True, canvas_id

    def move(self, view, event):
        self.timer.stop(); self.hover = None
        self.clear()
        if not self.matches(event.mimeData()):event.ignore(); return
        point = view.mapToScene(event.position().toPoint())
        valid, canvas_id = self.landing(view, point)
        if not valid:
            # Enter accepts this gesture, not this particular landing point.
            # Rejecting entry over an output prevents Qt from delivering any
            # subsequent moves into blank space within the same viewport.
            document = view.canvas.window.document
            if event.type() == QEvent.Type.DragEnter and document is not None and document.kind == 'workflow':
                event.setDropAction(Qt.DropAction.CopyAction); event.accept()
            else:event.ignore()
            return
        overlay = self.overlays.get(view)
        if overlay is None:
            overlay = self.overlays[view] = PlacementPreview(view)
        shape = (view, canvas_id is not None)
        if shape not in self.sizes:self.sizes[shape] = self.transfer.size(view.canvas, self.root, canvas_id)
        overlay.present('plus' if canvas_id else 'outline', point, self.sizes[shape])
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def drop(self, view, event):
        self.clear()
        self.timer.stop()
        if not self.matches(event.mimeData()):event.ignore(); return
        point = view.mapToScene(event.position().toPoint())
        valid, canvas_id = self.landing(view, point)
        if not valid:event.ignore(); return
        host = view.canvas.window
        try:
            target = self.transfer.target(host.document.path)
            key = self.transfer.add(self.source, target, canvas_id, point, host)
        except (ValueError, OSError, KeyError) as error:
            self.transfer.error(error)
            key = None
        if key:
            self.success = True
            self.success_pane = next((p.key for p in self.w.panes if p.pages.currentWidget()
                                     is next((t.widget for t in self.w.all_pages() if t.host is host), None)), None)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:event.ignore()

    def switch_hover(self):
        if self.hover is None or self.cancelled:return
        pane, key = self.hover
        tab = self.w.tabs_by_key.get(key)
        if pane not in self.w.panes or tab is None or not tab.document or tab.document.kind != 'workflow':return
        bar = pane.strip.bar
        if key == bar.tabData(bar.currentIndex()):return
        self.temporary.setdefault(pane.key, bar.tabData(bar.currentIndex()))
        # Temporary reveal has no navigation-history or layout-write side
        # effect. The captured source remains usable while its page is hidden.
        bar.blockSignals(True)
        bar.setCurrentIndex(self.w.index_of(key))
        bar.blockSignals(False)
        pane.pages.setCurrentWidget(tab.widget)
        self.w.layout_store.timer.stop()

    def finish(self):
        self.timer.stop()
        self.clear()
        for pane in self.w.panes:
            key = self.temporary.get(pane.key)
            if key and (not self.success or pane.key != self.success_pane):
                tab = self.w.tabs_by_key.get(key)
                if tab:
                    pane.strip.bar.blockSignals(True)
                    pane.strip.bar.setCurrentIndex(self.w.index_of(key))
                    pane.strip.bar.blockSignals(False)
                    pane.pages.setCurrentWidget(tab.widget)
            pane.strip.bar.setAcceptDrops(self.previous_accept.get(pane.key, False))
        if self.success and self.w.active_pane.key == self.success_pane:
            self.w.active_key = self.w.active_pane.strip.bar.tabData(self.w.active_pane.strip.bar.currentIndex())
        self.w.layout_changed()
        QApplication.instance().removeEventFilter(self)
        for overlay in self.overlays.values():overlay.deleteLater()
        self.overlays.clear()
        self.deleteLater()

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.cancelled = True
            self.timer.stop()
            self.clear()
            return False  # QDrag still receives Esc and ends its native loop.
        if kind not in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.DragLeave, QEvent.Type.Drop):return False
        pane = next((p for p in self.w.panes if obj is p.strip.bar), None)
        if pane is None:return False
        if kind == QEvent.Type.DragLeave:
            self.timer.stop(); self.hover = None; return False
        if not self.matches(event.mimeData()):return False
        if kind == QEvent.Type.Drop:
            event.ignore(); self.timer.stop(); return True
        self.clear()
        at = obj.tabAt(event.position().toPoint())
        tab = self.w.tabs_by_key.get(obj.tabData(at)) if at >= 0 else None
        if tab and tab.document and tab.document.kind == 'workflow':
            hover = (pane, tab.key)
            if self.hover != hover:
                self.hover = hover
                self.timer.start()
            event.setDropAction(Qt.DropAction.CopyAction); event.accept()
        else:
            self.timer.stop(); self.hover = None; event.ignore()
        return True


def controller(view):
    owner = getattr(view.canvas.window, 'workspace_owner', None)
    return getattr(getattr(owner, 'material_transfer', None), 'drag', None)


def foreign_legacy(view, event):
    """An id-only legacy payload cannot resolve another document's material."""
    host = view.canvas.window
    source = getattr(event.source(), 'owner', None)
    source_host = getattr(source, 'window', None)
    return (getattr(host, 'workspace_owner', None) is not None
            and source_host is not None
            and getattr(source_host, 'document', None) is not getattr(host, 'document', None))
