"""Hover disclosure for the workspace's existing live sidebar."""
from PySide6.QtCore import QPoint, QRect, Qt, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QWidget
from .sidebar_peek import SidebarPeekController


class WorkspaceSidebarPeek(SidebarPeekController):
    INSET = 4
    PADDING = 8
    OBSERVED = frozenset((QEvent.Type.MouseMove, QEvent.Type.Enter, QEvent.Type.Leave,
        QEvent.Type.MouseButtonPress, QEvent.Type.Show, QEvent.Type.Hide,
        QEvent.Type.FocusIn, QEvent.Type.LayoutRequest, QEvent.Type.FontChange,
        QEvent.Type.StyleChange, QEvent.Type.Resize, QEvent.Type.Close,
        QEvent.Type.WindowDeactivate, QEvent.Type.PaletteChange))

    def __init__(self, window):
        super().__init__(window, window.sidebar_target)
        self.listening = True
        self._layout.setContentsMargins(*([self.PADDING]*4))
        self.edge=QWidget(window)
        self.edge.setObjectName('SidebarHoverEdge')
        self.edge.setMouseTracking(True)
        self.edge_timer=QTimer(self)
        self.edge_timer.setSingleShot(True)
        self.edge_timer.timeout.connect(self.position_edge)
        self.configure()

    def _enable_tracking(self, widget):
        # Observe stable shell widgets only. ChildAdded can expose partially
        # constructed native widgets; wrapping every child there is unsafe.
        for target in (self.window, self.window.rail, self.overlay):
            if target not in self._tracking and not target.hasMouseTracking():
                target.setMouseTracking(True)
                self._tracking.add(target)

    def configure(self):
        self.enabled = self.window.sidebar_auto_hide
        self._edge_blocked = False
        self.open_timer.setInterval(self.window.sidebar_open_delay)
        self.close_timer.setInterval(self.window.sidebar_close_delay)
        if not self.enabled:
            self.close()
        self.position_edge()

    def position_edge(self):
        target=self._current_target()
        visible=self.enabled and target is not None and target.isHidden() and target.parentWidget().isVisible()
        listening=self.enabled and (visible or self.is_open)
        if listening!=self.listening:
            app=QApplication.instance()
            app.installEventFilter(self) if listening else app.removeEventFilter(self)
            self.listening=listening
        self.edge.setVisible(visible)
        if visible:
            bounds=self._anchor(target)
            self.edge.setGeometry(0,bounds.top(),6,bounds.height())
            self.edge.raise_()

    def eventFilter(self, watched, event):
        if self._shutdown:return False
        kind=event.type()
        if kind not in self.OBSERVED:return False
        if hasattr(self,'edge_timer') and watched is self.window and kind in (QEvent.Type.Resize,QEvent.Type.Show):
            self.edge_timer.start(0)
        return super().eventFilter(watched,event)

    def _anchor(self, target):
        window = self.window
        left = window.rail.width() if window.navigation_position == 'side' else 0
        top = window.tab_row.mapTo(window, QPoint()).y()
        return QRect(left, top, max(0, window.width()-left), max(0, window.height()-top))

    def _edge_contains(self, position, target, *, tolerance=False):
        bounds = self._anchor(target)
        return 0 <= position.x() < bounds.left()+(10 if tolerance else 6) and bounds.top() <= position.y() < bounds.bottom()

    def _hover(self, position):
        if self.enabled:
            super()._hover(position)

    def _is_pin_control(self, widget):
        return widget is self.window.collapse

    def _interaction_active(self):
        app = QApplication.instance()
        focus = app.focusWidget()
        editing = isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))
        return bool(self.window.sidebar_resize.dragging or
                    app.mouseButtons() != Qt.MouseButton.NoButton or
                    editing and focus.isVisible() and self._inside_sidebar(focus) or self._popup_owned(app.activePopupWidget()))

    def _borrow(self, target, *, pinned=False):
        margins=target.layout().contentsMargins() if target.layout() else None
        result = super()._borrow(target, pinned=pinned)
        if result:
            self.borrowed_margins=margins
            self.window.sync_tab_inset()
            if margins is not None:
                target.layout().setContentsMargins(0,0,0,0)
            if target is self.window.sidebar:
                self.window.tree_heading.setFixedHeight(self.window.tab_row.height())
            self.position_edge()
        return result

    def _restore(self, pin):
        target=self.target
        if target is not None and getattr(self,'borrowed_margins',None) is not None:
            target.layout().setContentsMargins(self.borrowed_margins)
            if target is self.window.sidebar:
                self.window.tree_heading.setMinimumHeight(0)
                self.window.tree_heading.setMaximumHeight(16777215)
        result = super()._restore(pin)
        if result:
            self.window.sync_tab_inset()
            self.position_edge()
        return result

    def _apply_surface(self):
        from .color_roles import interaction
        from .theme import visual_tokens
        tokens=visual_tokens(self.window.display_settings())
        background=tokens['raised']
        selected=interaction(background,tokens['text'],.10)
        hover=interaction(background,tokens['text'],.05)
        self.window.tree.peek_colors=dict(self.window.tree.row_colors,sidebar=background,
                                          selected=selected,hover=hover)
        self.overlay.setStyleSheet(
            f'QFrame#SidebarPeekSurface {{background:{background};border:1px solid {tokens["divider"]};border-radius:12px;}}'
            'QWidget[pcsSidebarBorrowed="true"], QFrame#NotebookPanel,'
            'QFrame#WorkspaceSidebarHeader, QListWidget, QTreeWidget'
            ' {background:transparent;border:0;border-radius:0;}'
            f'QListWidget::item:selected {{background:{selected};}}'
            f'QListWidget::item:!selected:hover {{background:{hover};}}')

    def _position_overlay(self):
        if not self.is_open:return
        bounds=self._anchor(self.target).adjusted(self.INSET,self.INSET,-self.INSET,-self.INSET)
        width=min(self._origin['width'],max(1,bounds.width()-64))
        self.overlay.setGeometry(bounds.x(),bounds.y(),width,max(1,bounds.height()))
        # All children stay inside the rounded frame. A native integer mask
        # would cut off antialiasing and create jagged corners at fractional DPI.
        self.overlay.clearMask()
        self.target.setFixedWidth(max(1,width-2*self.PADDING))

    def shutdown(self):
        self.edge_timer.stop()
        super().shutdown()
        self.edge.hide()
