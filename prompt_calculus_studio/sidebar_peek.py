"""Temporarily reveal a collapsed sidebar above the current content.

The original widget is borrowed, so its selection and actions remain live. No
copy of the page or its model is created, and the content layout is not resized.
"""
import weakref

from PySide6.QtCore import QObject, QEvent, QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QPalette, QPainterPath, QRegion
from PySide6.QtWidgets import QApplication, QBoxLayout, QFrame, QGridLayout, QSplitter, QVBoxLayout, QWidget
from shiboken6 import isValid

from .theme import visual_tokens


class SidebarPeekController(QObject):
    def __init__(self,window,target_provider,*,open_delay=450,close_delay=650):
        super().__init__(window)
        self.window=window; self.target_provider=target_provider
        self._borrowed=None; self._origin=None; self._changing=False
        self._shutdown=False
        self._edge_blocked=False
        self._pinned_overlay=False
        self._pending=None; self._peek_popups=weakref.WeakSet(); self._tracking=weakref.WeakSet()
        self.overlay=QFrame(window); self.overlay.setObjectName('SidebarPeekSurface')
        self.overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        self.overlay.setAutoFillBackground(False)
        self._layout=QVBoxLayout(self.overlay); self._layout.setContentsMargins(0,0,1,0); self._layout.setSpacing(0)
        self.overlay.hide()
        self.open_timer=QTimer(self); self.open_timer.setSingleShot(True); self.open_timer.setInterval(open_delay); self.open_timer.timeout.connect(self._open_pending)
        self.close_timer=QTimer(self); self.close_timer.setSingleShot(True); self.close_timer.setInterval(close_delay); self.close_timer.timeout.connect(self._close_if_outside)
        app=QApplication.instance(); app.installEventFilter(self); app.focusChanged.connect(self._focus_changed)
        self._enable_tracking(window)

    @property
    def is_open(self):return self._borrowed is not None

    @property
    def target(self):return self._borrowed

    @property
    def is_pinned_overlay(self):return self.is_open and self._pinned_overlay

    def _enable_tracking(self,widget):
        if self._shutdown or not isValid(widget):return
        for child in (widget,*widget.findChildren(QWidget)):
            if child not in self._tracking and not child.hasMouseTracking():
                child.setMouseTracking(True); self._tracking.add(child)

    def _current_target(self):
        try:target=self.target_provider()
        except (AttributeError,RuntimeError):return None
        return target if isinstance(target,QWidget) and isValid(target) else None

    def _in_window(self,widget):
        return isValid(self.window) and isinstance(widget,QWidget) and isValid(widget) and (widget is self.window or self.window.isAncestorOf(widget))

    def _owning_layout(self,layout,target):
        """Widgets in nested box/grid layouts keep the outer QWidget parent."""
        if layout is None:return None
        index=layout.indexOf(target)
        if index>=0 and isinstance(layout,(QBoxLayout,QGridLayout)):return layout,index
        for i in range(layout.count()):
            child=layout.itemAt(i).layout()
            found=self._owning_layout(child,target) if child is not None else None
            if found:return found
        return None

    def _inside_sidebar(self,widget):
        target=self._borrowed
        if not target or not widget or not isValid(widget):return False
        if self._is_resize_control(widget):return True
        # QWidget.isAncestorOf stops at a popup window boundary. Its explicit
        # parent chain still identifies menus opened by the borrowed sidebar.
        while widget is not None and isValid(widget):
            if widget is target:return True
            widget=widget.parentWidget()
        return False

    def _is_resize_control(self,widget):
        handle=getattr(getattr(self.window,'sidebar_resize',None),'handle',None)
        return widget is handle and handle is not None

    def _is_pin_control(self,widget):
        control=getattr(getattr(self.window,'studio_navigation',None),'sidebar',None)
        return bool(control and isValid(control) and (widget is control or isinstance(widget,QWidget) and control.isAncestorOf(widget)))

    def _popup_owned(self,popup):
        return bool(popup and (self._inside_sidebar(popup) or popup in self._peek_popups))

    def _interaction_active(self):
        app=QApplication.instance(); focus=app.focusWidget(); popup=app.activePopupWidget()
        resizing=bool(getattr(getattr(self.window,'sidebar_resize',None),'dragging',False))
        return bool(resizing or (focus and focus.isVisible() and self._inside_sidebar(focus)) or (popup and self._popup_owned(popup)))

    def _anchor(self,target):
        parent=self._origin['parent'] if target is self._borrowed and self._origin else target.parentWidget()
        if not parent or not isValid(parent):return QRect()
        top=max(0,parent.mapTo(self.window,QPoint()).y())
        rail=getattr(self.window,'context_rail',None)
        left=rail.mapTo(self.window,QPoint()).x()+rail.width() if rail and rail.isVisible() else 0
        return QRect(left,top,max(0,self.window.width()-left),max(0,self.window.height()-top))

    def _edge_contains(self,position,target,*,tolerance=False):
        bounds=self._anchor(target)
        rail=getattr(self.window,'context_rail',None)
        if rail and rail.contains_peek_trigger(position):return True
        return 0<=position.x()<(10 if tolerance else 6) and bounds.top()<=position.y()<bounds.bottom()+1

    def _hover(self,global_position):
        if self._changing:return
        position=self.window.mapFromGlobal(global_position)
        target=self._current_target()
        if target is None or not self._edge_contains(position,target,tolerance=True):self._edge_blocked=False
        if self.is_open:
            if self.is_pinned_overlay:return
            if self.overlay.geometry().contains(position) or self._edge_contains(position,self._borrowed,tolerance=True):self.close_timer.stop()
            elif not self._interaction_active() and not self.close_timer.isActive():self.close_timer.start()
            return
        if (self._edge_blocked or QApplication.mouseButtons()!=Qt.MouseButton.NoButton or not target or not target.isHidden()
                or not target.parentWidget() or not target.parentWidget().isVisible()
                or not self._edge_contains(position,target,tolerance=self._pending is target)):
            self.open_timer.stop(); self._pending=None; return
        if self._pending is not target:
            self._pending=target; self.open_timer.start()
        elif not self.open_timer.isActive():self.open_timer.start()

    def _open_pending(self):
        target=self._pending; self._pending=None
        if (not target or not isValid(target) or target is not self._current_target()
                or not target.isHidden() or QApplication.mouseButtons()!=Qt.MouseButton.NoButton
                or not self._edge_contains(self.window.mapFromGlobal(QCursor.pos()),target,tolerance=True)):
            return
        self._borrow(target)

    def _borrow(self,target,*,pinned=False):
        if self._shutdown or not target or not target.parentWidget() or not target.parentWidget().isVisible():return False
        parent=target.parentWidget(); found=self._owning_layout(parent.layout(),target) if parent else None
        layout,index=found if found else (None,-1)
        if isinstance(parent,QSplitter):
            origin=dict(parent=parent,kind='splitter',index=parent.indexOf(target),sizes=parent.sizes())
        elif isinstance(layout,QBoxLayout):
            origin=dict(parent=parent,kind='box',layout=layout,index=index,stretch=layout.stretch(index),alignment=layout.itemAt(index).alignment())
        elif isinstance(layout,QGridLayout):
            origin=dict(parent=parent,kind='grid',layout=layout,position=layout.getItemPosition(index),alignment=layout.itemAt(index).alignment())
        else:return False
        origin.update(minimum=target.minimumSize(),maximum=target.maximumSize(),policy=target.sizePolicy(),hidden=target.isHidden())
        width=target.minimumWidth() if target.minimumWidth()==target.maximumWidth() else target.width() if target.width()>100 else target.sizeHint().width()
        resize=getattr(self.window,'sidebar_resize',None)
        origin['width']=resize.limit(resize.preferred_width(),overlay=True) if resize else max(220,min(480,width,self.window.width()-64))
        self._changing=True
        try:
            self._origin=origin; self._borrowed=target; self._pinned_overlay=pinned
            target.setProperty('pcsSidebarBorrowed',True)
            if layout:layout.removeWidget(target)
            target.setParent(self.overlay); target.setMinimumSize(0,0); target.setMaximumSize(16777215,16777215)
            self._layout.addWidget(target); self._apply_surface(); self._position_overlay()
            target.show(); self.overlay.show(); self.overlay.raise_()
        finally:self._changing=False
        if resize:resize.schedule()
        return True

    def _apply_surface(self):
        settings=getattr(self.window,'state',{}).get('settings',{})
        tokens=visual_tokens(settings)
        palette=self.overlay.palette(); palette.setColor(QPalette.ColorRole.Window,QColor(tokens['sidebar'])); self.overlay.setPalette(palette)
        self.overlay.setStyleSheet(f"QFrame#SidebarPeekSurface {{ background:{tokens['sidebar']}; border:0; border-right:1px solid {tokens['divider']}; }} QWidget[pcsSidebarBorrowed=\"true\"] {{ background:{tokens['sidebar']}; border:0; }}")

    def _position_overlay(self):
        if not self.is_open:return
        bounds=self._anchor(self._borrowed)
        width=min(self._origin['width'],max(1,bounds.width()-64));height=max(1,bounds.height())
        self.overlay.setGeometry(bounds.x(),bounds.y(),width,height)
        # Continue the workspace's left outer contour; the divider alongside
        # content is a straight internal edge, never a separate floating card.
        radius=min(18,width/2,height/2)
        path=QPainterPath();path.moveTo(radius,0);path.lineTo(width,0);path.lineTo(width,height)
        path.lineTo(radius,height);path.quadTo(0,height,0,height-radius)
        path.lineTo(0,radius);path.quadTo(0,0,radius,0)
        self.overlay.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def set_width(self,width):
        if self.is_open and self._origin['width']!=width:
            self._origin['width']=width;self._position_overlay()

    def _close_if_outside(self):
        if not self.is_open or self.is_pinned_overlay:return
        position=self.window.mapFromGlobal(QCursor.pos())
        if self.overlay.geometry().contains(position) or self._edge_contains(position,self._borrowed,tolerance=True) or self._interaction_active():return
        self.close()

    def _focus_changed(self,previous,current):
        if not self.is_open or self.is_pinned_overlay:return
        if self._interaction_active():self.close_timer.stop()
        elif not self.overlay.geometry().contains(self.window.mapFromGlobal(QCursor.pos())):self.close_timer.start()

    def close(self):
        """Return the borrowed widget to its original collapsed layout slot."""
        return self._restore(False)

    def pin(self):
        """Return and show the widget; its page owner then records the preference."""
        return self._restore(True)

    def show_pinned(self):
        """Keep the live sidebar above narrow content until explicitly closed."""
        target=self._current_target()
        if self._shutdown or not target:return False
        self.open_timer.stop(); self.close_timer.stop(); self._pending=None
        if self.is_open and self._borrowed is target:
            self._pinned_overlay=True; return True
        if self.is_open:self.close()
        return self._borrow(target,pinned=True)

    def shutdown(self):
        """Release application-wide observers even when a closed window survives."""
        if self._shutdown:return
        self.close(); self._shutdown=True
        app=QApplication.instance()
        if app:
            app.removeEventFilter(self)
            try:app.focusChanged.disconnect(self._focus_changed)
            except (RuntimeError,TypeError):pass
        for widget in self._tracking:
            if isValid(widget):widget.setMouseTracking(False)
        self._tracking.clear()

    def _restore(self,pin):
        self.open_timer.stop(); self.close_timer.stop(); self._pending=None
        self._edge_blocked=True
        target=self._borrowed; origin=self._origin
        if not target:return False
        if not isValid(target):
            self._borrowed=None; self._origin=None; self._pinned_overlay=False; self.overlay.hide(); return False
        self._changing=True
        try:
            self._borrowed=None; self._origin=None; self._pinned_overlay=False; self._peek_popups.clear()
            self._layout.removeWidget(target); target.hide()
            target.setProperty('pcsSidebarBorrowed',False)
            if isValid(origin['parent']):
                target.setParent(origin['parent']); target.setMinimumSize(origin['minimum']); target.setMaximumSize(origin['maximum']); target.setSizePolicy(origin['policy'])
                if origin['kind']=='splitter':
                    parent=origin['parent']; parent.insertWidget(origin['index'],target)
                    sizes=origin['sizes'][:]
                    if pin:sizes[origin['index']]=max(sizes[origin['index']],origin['width'])
                    target.setVisible(pin or not origin['hidden']); parent.setSizes(sizes)
                elif origin['kind']=='box':
                    origin['layout'].insertWidget(origin['index'],target,origin['stretch'],origin['alignment']); target.setVisible(pin or not origin['hidden'])
                else:
                    origin['layout'].addWidget(target,*origin['position'],origin['alignment']); target.setVisible(pin or not origin['hidden'])
            self.overlay.hide()
        finally:self._changing=False
        resize=getattr(self.window,'sidebar_resize',None)
        if resize:resize.schedule()
        return True

    def eventFilter(self,watched,event):
        if self._changing or self._shutdown:return False
        kind=event.type()
        if (self.is_open and kind in (QEvent.Type.Show,QEvent.Type.Hide,QEvent.Type.FocusIn,QEvent.Type.LayoutRequest,QEvent.Type.MouseMove,QEvent.Type.Enter)
                and self._in_window(watched) and self._current_target() is not self._borrowed):
            self.close()
        if kind==QEvent.Type.ChildAdded and self._in_window(watched):
            child=event.child()
            if isinstance(child,QWidget):QTimer.singleShot(0,lambda ref=weakref.ref(child):self._track_new_child(ref))
        elif kind in (QEvent.Type.MouseMove,QEvent.Type.Enter) and self._in_window(watched):
            self._hover(event.globalPosition().toPoint() if hasattr(event,'globalPosition') else QCursor.pos())
        elif kind==QEvent.Type.Leave and self._in_window(watched):
            self._hover(QCursor.pos())
        elif kind==QEvent.Type.MouseButtonPress and self._in_window(watched):
            self.open_timer.stop(); self._pending=None
            if self.is_open and not self.is_pinned_overlay and not self._is_pin_control(watched) and not self._is_resize_control(watched) and not self.overlay.geometry().contains(self.window.mapFromGlobal(event.globalPosition().toPoint())) and not self._popup_owned(QApplication.activePopupWidget()):self.close()
        elif kind==QEvent.Type.Show and self.is_open and isinstance(watched,QWidget) and watched.isWindow() and watched.windowType()==Qt.WindowType.Popup:
            if self._inside_sidebar(watched) or self._inside_sidebar(QApplication.focusWidget()) or self.overlay.geometry().contains(self.window.mapFromGlobal(QCursor.pos())):
                self._peek_popups.add(watched); self.close_timer.stop()
        elif kind==QEvent.Type.Hide and watched in self._peek_popups:
            self._peek_popups.discard(watched); self._focus_changed(None,None)
        elif self.is_open and watched is self._borrowed and kind in (QEvent.Type.LayoutRequest,QEvent.Type.FontChange,QEvent.Type.StyleChange):
            self._position_overlay()
        elif watched is self.window:
            if kind in (QEvent.Type.Close,QEvent.Type.Hide) or kind==QEvent.Type.WindowDeactivate and not self.is_pinned_overlay:self.close()
            elif kind==QEvent.Type.Resize and self.is_open:self._position_overlay()
            elif kind==QEvent.Type.PaletteChange and self.is_open:self._apply_surface()
        return False

    def _track_new_child(self,reference):
        if self._shutdown:return
        child=reference()
        if child is not None and isValid(child) and self._in_window(child):self._enable_tracking(child)
