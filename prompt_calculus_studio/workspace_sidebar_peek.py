"""Hover disclosure for the workspace's existing live sidebar."""
from PySide6.QtCore import QPoint, QRect, Qt, QEvent, QTimer, QEasingCurve
from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QWidget, QGraphicsOpacityEffect
from PySide6.QtGui import QCursor
from .sidebar_peek import SidebarPeekController
from .elapsed_animation import ElapsedAnimation
from .dialog_presentation import reduced_motion


class WorkspaceSidebarPeek(SidebarPeekController):
    INSET = 4
    PADDING = 8
    OBSERVED = frozenset((QEvent.Type.MouseMove, QEvent.Type.Enter, QEvent.Type.Leave,
        QEvent.Type.MouseButtonPress, QEvent.Type.Show, QEvent.Type.Hide,
        QEvent.Type.FocusIn, QEvent.Type.LayoutRequest, QEvent.Type.FontChange,
        QEvent.Type.StyleChange, QEvent.Type.Resize, QEvent.Type.Close,
        QEvent.Type.WindowDeactivate, QEvent.Type.PaletteChange))

    def __init__(self, window):
        self.hover_route = None
        self.hover_target = None
        super().__init__(window, window.sidebar_target)
        from .workspace_peek_routes import PeekRoutes
        self.route_targets = PeekRoutes(self)
        self.listening = True
        self.progress = 1.
        self.hiding = False
        self.effect = QGraphicsOpacityEffect(self.overlay)
        self.effect.setEnabled(False)
        self.overlay.setGraphicsEffect(self.effect)
        self.motion = ElapsedAnimation(self)
        self.motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.motion.valueChanged.connect(self.motion_frame)
        self.motion.finished.connect(self.finish_motion)
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
        for target in (self.window, self.window.rail, self.overlay, *self.window.routes.values()):
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
        visible=self.enabled and self._can_peek(target) and not self.is_open
        listening=self.enabled and (visible or self.is_open)
        if listening!=self.listening:
            app=QApplication.instance()
            app.installEventFilter(self) if listening else app.removeEventFilter(self)
            self.listening=listening
        self.edge.setVisible(visible)
        if visible:
            bounds=self._anchor(target)
            # Cover only the document gutter, leaving rail buttons clickable.
            self.edge.setGeometry(bounds.left(),bounds.top(),24,bounds.height())
            self.edge.raise_()

    def _current_target(self):
        return self.hover_target if self.hover_target is not None else super()._current_target()

    def _can_borrow(self, target):
        # An inactive destination's real sidebar can be borrowed too. Its
        # hidden page retains its layout, selection and scroll position.
        return bool(target is not None and target.parentWidget() and self.window.isVisible())

    def _can_peek(self, target):
        w = self.window
        expanded = w.width() >= 980 if w.sidebar_preference is None else w.sidebar_preference
        return not expanded and self._can_borrow(target)

    def route_at(self, position):
        for route, entry in self.window.routes.items():
            if entry.isVisible() and entry.rect().contains(entry.mapFrom(self.window, position)):
                return route
        return None

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
        if self.route_at(position) is not None:return True
        bounds = self._anchor(target)
        return 0 <= position.x() < bounds.left()+(32 if tolerance else 24) and bounds.top() <= position.y() < bounds.bottom()

    def _hover(self, position):
        if self.enabled and not self._changing:
            route = self.route_at(self.window.mapFromGlobal(position))
            if (route is not None and route != (self.hover_route or self.window.active_route)
                    and self._can_peek(self._current_target()) and not self._interaction_active()):
                self.close()
                self._changing = True
                try:
                    self.hover_target = self.route_targets.target(route)
                    self.hover_route = route
                finally:
                    self._changing = False
                self._edge_blocked = False
            if self.hiding:
                local = self.window.mapFromGlobal(position)
                if self.overlay.geometry().contains(local) or self._edge_contains(local,self.target,tolerance=True):
                    self.animate(False)
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
        self.motion.stop()
        self.hiding = False
        self.progress = 1. if reduced_motion(self.window) else 0.
        self.effect.setEnabled(self.progress != 1.)
        self.effect.setOpacity(self.progress)
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
            if self.progress != 1.:
                self.animate(False)
        return result

    def animate(self, hiding):
        self.motion.stop()
        self.hiding = hiding
        if reduced_motion(self.window):
            self.finish_motion()
            return
        self.effect.setEnabled(True)
        self.motion.setDuration(110 if hiding else 150)
        self.motion.setStartValue(self.progress)
        self.motion.setEndValue(0. if hiding else 1.)
        self.motion.start()

    def motion_frame(self, value):
        self.progress = float(value)
        self.effect.setOpacity(self.progress)
        self._position_overlay()

    def finish_motion(self):
        self.motion.stop()
        if self.hiding:
            self.close()
        else:
            self.progress = 1.
            self.effect.setEnabled(False)
            self._position_overlay()

    def _close_if_outside(self):
        if not self.is_open or self.is_pinned_overlay:
            return
        point = self.window.mapFromGlobal(QCursor.pos())
        if (self.overlay.geometry().contains(point) or self._edge_contains(point,self.target,tolerance=True)
                or self._interaction_active()):
            return
        self.animate(True)

    def _restore(self, pin):
        self.hover_target = None
        self.hover_route = None
        self.motion.stop()
        self.hiding = False
        self.progress = 1.
        self.effect.setEnabled(False)
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
        self.overlay.setGeometry(bounds.x()-round((1.-self.progress)*24),bounds.y(),width,max(1,bounds.height()))
        # All children stay inside the rounded frame. A native integer mask
        # would cut off antialiasing and create jagged corners at fractional DPI.
        self.overlay.clearMask()
        self.target.setFixedWidth(max(1,width-2*self.PADDING))

    def shutdown(self):
        self.motion.stop()
        self.edge_timer.stop()
        super().shutdown()
        self.edge.hide()
