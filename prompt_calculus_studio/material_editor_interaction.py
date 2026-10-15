"""Pointer actions for material rows and immediate preview opening."""
from PySide6.QtCore import Qt, QEvent, QObject, QTimer, QSize, QPoint
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout, QToolButton
from . import materials
from .ui_icons import icon
from .views import widget_colors


class PreviewClickBridge(QObject):
    """Keep a double click on its original row across preview-column reflow."""
    def __init__(self, page):
        super().__init__(page)
        self.page = page; self.ident = None; self.release_pending = False
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.cancel)

    def arm(self, ident, position):
        self.cancel(); self.ident = ident; self.position = QPoint(position)
        QApplication.instance().installEventFilter(self)
        self.timer.start(QApplication.doubleClickInterval())

    def cancel(self):
        self.timer.stop(); self.ident = None; self.release_pending = False
        QApplication.instance().removeEventFilter(self)

    def eventFilter(self, watched, event):
        kind = event.type()
        if self.release_pending and kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self.cancel(); return True
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            ident = self.ident
            match = (ident and event.button() == Qt.MouseButton.LeftButton and isinstance(watched, QWidget)
                     and (watched is self.page or self.page.isAncestorOf(watched))
                     and (event.globalPosition().toPoint()-self.position).manhattanLength()
                         <= QApplication.styleHints().mouseDoubleClickDistance())
            if match:
                self.ident = None
                self.page.edit_material(ident)
                # The second release must not activate a control in the sheet
                # which has just appeared under the pointer.
                self.release_pending = True
                return True
            self.cancel()
        elif kind in (QEvent.Type.Wheel, QEvent.Type.KeyPress) or (watched is self.page and kind == QEvent.Type.Hide):
            self.cancel()
        return False


class MaterialRowActions(QWidget):
    """Two real buttons, shared by the hovered row; no per-item widgets."""
    def __init__(self, listing):
        super().__init__(listing.viewport())
        self.listing = listing; self.ident = None; self.pressed_id = None; self.appearance = None
        self.setObjectName('MaterialRowActions'); self.setFixedSize(68, 32)
        layout = QHBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(4)
        self.more = QToolButton(self); self.pin = QToolButton(self)
        for control in (self.more, self.pin):
            control.setFixedSize(32, 32); control.setIconSize(QSize(18, 18))
            control.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            control.setCursor(Qt.CursorShape.PointingHandCursor)
            control.pressed.connect(self._pressed)
            control.installEventFilter(self); layout.addWidget(control)
        self.more.setToolTip('更多操作'); self.more.setAccessibleName('更多素材操作')
        self.more.clicked.connect(lambda: self._invoke(False))
        self.pin.clicked.connect(lambda: self._invoke(True))
        self.installEventFilter(self); self.setMouseTracking(True)
        listing.setMouseTracking(True); listing.viewport().installEventFilter(self)
        listing.verticalScrollBar().valueChanged.connect(self.clear)
        listing.model().modelAboutToBeReset.connect(self.clear)
        listing.dragStarted.connect(self.clear)
        self.hide()

    def clear(self, *_):
        self.ident = None; self.hide(); self.listing.viewport().update()

    def _pressed(self):
        self.pressed_id = self.ident
        self.listing.stop_scroll()
        self.listing.owner.preview_click.cancel()

    def _invoke(self, pin):
        ident = self.pressed_id; self.pressed_id = None
        position = self.more.mapToGlobal(self.more.rect().bottomLeft())
        self.clear()
        if ident is None: return
        if pin: self.listing.owner.pin_material(ident)
        else: self.listing.owner.material_menu_at(ident, position)
        self.sync_cursor()

    def sync_cursor(self):
        listing = self.listing; viewport = listing.viewport()
        point = viewport.mapFromGlobal(QCursor.pos())
        if (not listing.isVisible() or listing._drag_active or listing.owner.active_dialog
                or QApplication.activePopupWidget() or QApplication.mouseButtons() or not viewport.rect().contains(point)):
            self.clear(); return
        index = listing.indexAt(point)
        rect = listing.visualRect(index).adjusted(1, 6, -1, -6) if index.isValid() else None
        if rect is None or not rect.contains(point) or listing.stack_depth(index) > .05:
            self.clear(); return
        self.ident = index.data(Qt.ItemDataRole.UserRole)
        value = materials.item(listing.owner.window.state, self.ident)
        colors = widget_colors(listing)
        appearance = (colors['selected'], colors['secondary'], colors['accent'], bool(value.get('pinned')))
        if self.appearance != appearance:
            self.appearance = appearance
            self.setStyleSheet('QWidget#MaterialRowActions { background: transparent; } '
                'QToolButton { background: transparent; border: 0; border-radius: 6px; padding: 0; } '
                f'QToolButton:hover {{ background: {colors["selected"]}; }}')
            self.more.setIcon(icon('more-horizontal', colors['secondary']))
            pinned = bool(value.get('pinned'))
            self.pin.setIcon(icon('pin-filled' if pinned else 'pin', colors['secondary']))
            self.pin.setToolTip('已釘選' if pinned else '釘選')
            self.pin.setAccessibleName('取消釘選' if pinned else '釘選')
        self.move(rect.right()-self.width()-10, rect.center().y()-self.height()//2)
        self.show(); self.raise_(); viewport.update()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Type.MouseMove, QEvent.Type.Enter):
            if not (self.more.isDown() or self.pin.isDown()): self.sync_cursor()
        elif kind == QEvent.Type.Leave:
            QTimer.singleShot(0, self.sync_cursor)
        elif watched is self.listing.viewport() and kind in (QEvent.Type.Resize, QEvent.Type.Hide):
            self.clear()
        return False
