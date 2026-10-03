from pathlib import Path
from PySide6.QtCore import Qt, QSize, QPoint, QPointF, QRectF, QObject, QVariantAnimation, QEasingCurve, QEvent
from PySide6.QtGui import QPixmap, QDesktopServices, QIcon, QPainterPath, QRegion, QPainter, QPalette, QImageReader, QFont, QFontMetrics
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QFrame,
                              QDialogButtonBox, QFileDialog, QMessageBox, QDialog, QScrollArea,
                              QLineEdit, QPlainTextEdit, QComboBox, QFontComboBox, QListView, QListWidget, QMenu, QSizePolicy, QWidget, QBoxLayout, QApplication,QStyledItemDelegate,QStyle,QStyleOptionViewItem,QStyleOptionComboBox,QStylePainter)
from .popup_surface import POPUP_WIDTH, PopupSurface, popup_row_height, popup_width

def label(text, role=None, wrap=False):
    result = QLabel(text)
    if role:
        result.setObjectName(role)
    result.setWordWrap(wrap)
    result.setTextFormat(Qt.TextFormat.PlainText)
    return result

class ElidedLabel(QLabel):
    """Single-line status that keeps its beginning and full text tooltip."""
    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        text=self.fontMetrics().elidedText(self.text(),Qt.TextElideMode.ElideRight,self.contentsRect().width())
        painter.drawText(self.contentsRect(),self.alignment(),text)


def button(text, callback, role=None):
    result = QPushButton(text)
    if role:
        result.setObjectName(role)
    result.clicked.connect(callback)
    return result

def row(*widgets):
    result = QHBoxLayout()
    result.setSpacing(8)
    for widget in widgets:
        if widget is None:
            result.addStretch()
        else:
            result.addWidget(widget)
    return result

def panel(role="Panel"):
    frame = QFrame()
    frame.setCursor(Qt.CursorShape.ArrowCursor)
    frame.setObjectName(role)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20,20,20,20)
    layout.setSpacing(12)
    return frame, layout

def scrolling(widget):
    scroll = QScrollArea()
    if not widget.objectName(): widget.setObjectName("ScrollContent")
    scroll.viewport().setObjectName("ScrollViewport")
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setWidgetResizable(True)
    scroll.setWidget(widget)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    return scroll


class ActionHeader(QWidget):
    """Keep actions beside the title when they fit, above the editor otherwise."""
    def __init__(self, title, *actions):
        super().__init__()
        self.title=title; self.actions=actions
        self.flow=QBoxLayout(QBoxLayout.Direction.LeftToRight,self)
        self.flow.setContentsMargins(0,0,0,0); self.flow.setSpacing(8)
        self.flow.addWidget(title)
        self.flow.addLayout(row(None,*actions))

    def reflow(self):
        visible=[a for a in self.actions if not a.isHidden()]
        required=self.title.sizeHint().width()+sum(a.sizeHint().width() for a in visible)+8*(len(visible)+1)
        direction=QBoxLayout.Direction.LeftToRight if self.width()>=required else QBoxLayout.Direction.TopToBottom
        if self.flow.direction()!=direction:self.flow.setDirection(direction)

    def resizeEvent(self,event):
        self.reflow()
        super().resizeEvent(event)

    def event(self,event):
        result=super().event(event)
        if hasattr(self,'flow') and event.type() in (QEvent.Type.LayoutRequest,QEvent.Type.FontChange,QEvent.Type.StyleChange):self.reflow()
        return result


class WindowShell(QFrame):
    def __init__(self,window):
        super().__init__(window)
        self.setObjectName("Shell"); self.setMouseTracking(True)
        QApplication.instance().installEventFilter(self)

    def paintEvent(self,event):
        from PySide6.QtGui import QColor
        from .theme import shell_color
        settings=dict(self.window().state['settings'])
        settings.update(getattr(self.window(),'appearance_preview',{}))
        if not getattr(self.window(),'native_material',False):settings['material']='solid'
        value=shell_color(settings)
        if value.startswith('rgba('):
            color=QColor(*[int(v) for v in value[5:-1].split(',')])
        else:color=QColor(value)
        painter=QPainter(self); painter.fillRect(self.rect(),color); painter.end()
        super().paintEvent(event)

    def eventFilter(self,watched,event):
        # Only reset the shell's inherited resize cursor. Editors keep their
        # own I-beam, and splitter handles keep their own resize cursor.
        if event.type() in (QEvent.Type.Enter,QEvent.Type.MouseMove) and isinstance(watched,QWidget) and watched is not self:
            if QWidget.window(watched) is QWidget.window(self) and self.testAttribute(Qt.WidgetAttribute.WA_SetCursor): self.unsetCursor()
        return super().eventFilter(watched,event)

    def leaveEvent(self,event):
        self.unsetCursor(); super().leaveEvent(event)

    def edges(self,pos):
        edges=Qt.Edge(0)
        if pos.x()<7: edges|=Qt.Edge.LeftEdge
        if pos.x()>self.width()-7: edges|=Qt.Edge.RightEdge
        if pos.y()<7: edges|=Qt.Edge.TopEdge
        if pos.y()>self.height()-7: edges|=Qt.Edge.BottomEdge
        return edges

    def mouseMoveEvent(self,event):
        edges=self.edges(event.position())
        if edges & (Qt.Edge.LeftEdge|Qt.Edge.RightEdge) and edges & (Qt.Edge.TopEdge|Qt.Edge.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if edges in (Qt.Edge.LeftEdge|Qt.Edge.TopEdge,Qt.Edge.RightEdge|Qt.Edge.BottomEdge) else Qt.CursorShape.SizeBDiagCursor)
        elif edges & (Qt.Edge.LeftEdge|Qt.Edge.RightEdge): self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edges: self.setCursor(Qt.CursorShape.SizeVerCursor)
        else: self.unsetCursor()
        super().mouseMoveEvent(event)

    def mousePressEvent(self,event):
        edges=self.edges(event.position())
        if edges and event.button()==Qt.MouseButton.LeftButton and not self.window().isMaximized():
            self.window().windowHandle().startSystemResize(edges)
        super().mousePressEvent(event)

def dialog_buttons(dialog, save=None):
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    box.button(QDialogButtonBox.StandardButton.Save).setText("儲存")
    box.button(QDialogButtonBox.StandardButton.Save).setObjectName("Primary")
    box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
    for control in box.buttons(): control.setMinimumWidth(76)
    box.accepted.connect(save or dialog.accept)
    box.rejected.connect(dialog.reject)
    return box

class StudioDialog(QDialog):
    """A lightweight in-app sheet with the same typography and rounded surface."""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        outer=QVBoxLayout(self); outer.setContentsMargins(6,6,6,6)
        surface=QFrame(); surface.setObjectName("DialogSurface"); outer.addWidget(surface)
        content=QVBoxLayout(surface); content.setContentsMargins(26,22,26,24); content.setSpacing(20)
        self.heading=label("","DialogTitle")
        close=button("×",self.reject,"DialogClose"); close.setFixedSize(40,40); close.setToolTip("關閉"); close.setAccessibleName('關閉視窗')
        content.addLayout(row(self.heading,None,close))
        self.body=QVBoxLayout(); self.body.setSpacing(16); content.addLayout(self.body,1)
        self.windowTitleChanged.connect(self.heading.setText)
        self.reveal=QVariantAnimation(self); self.reveal.setDuration(140)
        self.reveal.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.reveal.valueChanged.connect(self.setWindowOpacity)

    def exec(self):
        result=super().exec()
        # Callers can read accepted fields immediately; release closed sheets
        # when control returns to the application's event loop.
        self.deleteLater()
        return result

    def showEvent(self,event):
        super().showEvent(event)
        screen=self.screen().availableGeometry()
        self.resize(min(self.width(),screen.width()-40),min(self.height(),screen.height()-40))
        center=self.parentWidget().mapToGlobal(self.parentWidget().rect().center()) if self.parentWidget() else screen.center()
        self.move(max(screen.left()+12,min(center.x()-self.width()//2,screen.right()-self.width()-12)),
                  max(screen.top()+12,min(center.y()-self.height()//2,screen.bottom()-self.height()-12)))
        self.reveal.setStartValue(0.88); self.reveal.setEndValue(1.0); self.reveal.start()


class DismissibleSheet(StudioDialog):
    """Read-only browsing sheet: the Qt popup grab dismisses outside clicks."""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self._finished=False

    def done(self,result):
        if self._finished: return
        self._finished=True
        super().done(result)

    def hideEvent(self,event):
        super().hideEvent(event)
        # Qt closes a popup on an outside click by hiding it, not by calling
        # QDialog.reject. Finish exactly once so borrowed content is restored.
        if not self._finished: self.done(QDialog.DialogCode.Rejected)


class CheckList(QListWidget):
    """Every click toggles the whole row, including a rapid double click."""
    def toggle_at(self,event):
        item=self.itemAt(event.position().toPoint())
        if item and event.button()==Qt.MouseButton.LeftButton:
            self.setCurrentItem(item)
            item.setCheckState(Qt.CheckState.Unchecked if item.checkState()==Qt.CheckState.Checked else Qt.CheckState.Checked)
            event.accept(); return True
        return False

    def mousePressEvent(self,event):
        if not self.toggle_at(event): super().mousePressEvent(event)

    def mouseDoubleClickEvent(self,event): self.mousePressEvent(event)
    def mouseReleaseEvent(self,event): event.accept()


class InputDialog:
    @staticmethod
    def _show(parent,title,description,editor):
        dialog=StudioDialog(parent); dialog.setWindowTitle(title); dialog.resize(480,250)
        dialog.body.addWidget(label(description,"Subtle",True)); dialog.body.addWidget(editor)
        if isinstance(editor,QPlainTextEdit): dialog.resize(560,390)
        buttons=dialog_buttons(dialog)
        action=buttons.button(QDialogButtonBox.StandardButton.Save)
        action.setText("建立" if title.startswith("新增") else "確定"); action.setDefault(True)
        dialog.body.addWidget(buttons); editor.setFocus()
        return dialog.exec()==QDialog.DialogCode.Accepted

    @staticmethod
    def getText(parent,title,description,text=""):
        editor=QLineEdit(text); editor.selectAll()
        ok=InputDialog._show(parent,title,description,editor)
        return editor.text(),ok

    @staticmethod
    def getItem(parent,title,description,items,current=0,editable=False):
        editor=ComboBox(); editor.addItems(items); editor.setCurrentIndex(current); editor.setEditable(editable)
        ok=InputDialog._show(parent,title,description,editor)
        return editor.currentText(),ok

    @staticmethod
    def getMultiLineText(parent,title,description,text=""):
        editor=QPlainTextEdit(text)
        ok=InputDialog._show(parent,title,description,editor)
        return editor.toPlainText(),ok


def ask(parent, title, text):
    dialog=StudioDialog(parent); dialog.setWindowTitle(title); dialog.resize(530,270)
    message=label(text,None,True); message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    dialog.body.addWidget(message)
    confirm=button("確定",dialog.accept,"Primary"); cancel=button("取消",dialog.reject)
    confirm.setMinimumWidth(76); cancel.setMinimumWidth(76)
    dialog.body.addLayout(row(None,cancel,confirm)); cancel.setDefault(True); cancel.setFocus()
    return dialog.exec()==QDialog.DialogCode.Accepted


def information(parent,title,text):
    dialog=StudioDialog(parent); dialog.setWindowTitle(title); dialog.resize(520,250)
    dialog.body.addWidget(label(text,None,True)); dialog.body.addLayout(row(None,button("知道了",dialog.accept,"Primary")))
    dialog.exec()


def rounded_mask(widget,radius=12):
    path=QPainterPath(); path.addRoundedRect(QRectF(widget.rect()),radius,radius)
    widget.setMask(QRegion(path.toFillPolygon().toPolygon()))


def sync_popup_appearance(popup,owner,host=None):
    """Carry the owning window's theme into a separate native popup."""
    host=host or QWidget.window(owner)
    popup.setPalette(QWidget.palette(owner))
    popup.setFont(QWidget.font(owner))
    source=QWidget.styleSheet(host)
    if popup.styleSheet()!=source:popup.setStyleSheet(source)


class _PopupAppearance(QObject):
    def __init__(self,popup,owner,host=None):
        super().__init__(popup)
        self.owner=owner;self.host=host
        popup.installEventFilter(self)

    def eventFilter(self,popup,event):
        if event.type()==QEvent.Type.Show:
            sync_popup_appearance(popup,self.owner,self.host)
            model=popup.model()
            text_width=max((popup.fontMetrics().horizontalAdvance(str(model.index(i,0).data() or ''))
                            for i in range(model.rowCount())),default=0)
            area=popup.screen().availableGeometry().adjusted(8,8,-8,-8)
            width=popup_width(self.owner.width(),text_width,area.width())
            popup.setFixedWidth(width)
            popup.move(max(area.left(),min(popup.x(),area.right()+1-width)),
                       max(area.top(),min(popup.y(),area.bottom()+1-popup.height())))
        return False


def style_completion(completer,owner,host=None):
    popup=completer.popup();popup.setObjectName('CompletionPopup')
    popup._pcs_surface=PopupSurface(popup,owner)
    popup.setItemDelegate(ComboItemDelegate(popup))
    popup.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    popup.setTextElideMode(Qt.TextElideMode.ElideRight)
    popup._pcs_appearance=_PopupAppearance(popup,owner,host)
    sync_popup_appearance(popup,owner,host)


def widget_global_position(widget,position,view=None):
    """Map an embedded widget through its scene/view, including zoom and pan."""
    host=widget
    while host is not None and host.graphicsProxyWidget() is None:host=host.parentWidget()
    proxy=host.graphicsProxyWidget() if host is not None else None
    if proxy is None or proxy.scene() is None:return widget.mapToGlobal(position)
    view=view or next(iter(proxy.scene().views()),None)
    if view is None:return widget.mapToGlobal(position)
    scene=proxy.mapToScene(QPointF(widget.mapTo(host,position)))
    return view.viewport().mapToGlobal(view.mapFromScene(scene))


class RoundMenu(QMenu):
    """Use Qt's non-blocking popup path for custom-shaped Windows menus.

    QMenu.exec() with this native custom menu crashes on the supported Qt build.
    Actions already connect to their handlers; callers never need a return value.
    """
    def __init__(self,parent=None):
        # Set the bypass flag before parenting: proxy embedding can happen
        # during QMenu construction, before its first show event.
        super().__init__()
        # A native opaque menu paints a rectangular palette behind the QSS
        # radius. Alpha-backed painting avoids that rim without a jagged mask.
        # Popup children of scene widgets otherwise become child proxies. Our
        # anchors are desktop coordinates and menus must keep their own scale.
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.BypassGraphicsProxyWidget)
        if parent is not None:self.setParent(parent,self.windowFlags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setProperty('pcsPopupSurface',True)
        self._appearance_owner=parent
        self._synced_popup_style='';self._local_popup_style=''
        self._surface=PopupSurface(self,parent,paint_in_filter=False)
        self.aboutToShow.connect(self._prepare_show)
        # A submenu hides whenever the pointer returns to its parent. Its
        # action must live until the root popup closes, not until that hover ends.
        if not isinstance(parent,QMenu): self.aboutToHide.connect(self.deleteLater)

    def addMenu(self,*args):
        # QMenu's title/icon overloads instantiate plain QMenu internally.
        # Build owned RoundMenu children so submenu hover uses the same surface
        # and hiding a child does not delete its action before the root closes.
        if len(args)==1 and isinstance(args[0],str):
            submenu=RoundMenu(self);submenu.setTitle(args[0])
        elif len(args)==2 and isinstance(args[0],QIcon) and isinstance(args[1],str):
            submenu=RoundMenu(self);submenu.setIcon(args[0]);submenu.setTitle(args[1])
        else:
            return super().addMenu(*args)
        super().addMenu(submenu)
        return submenu

    def _prepare_show(self):
        # Qt opens hover submenus inside C++ and bypasses the Python popup()
        # method. aboutToShow runs before Qt measures that popup's geometry.
        parent=self.parentWidget()
        position=parent.mapToGlobal(parent.rect().center()) if isinstance(parent,QMenu) else self.pos()
        self._prepare(position)

    def _prepare(self,position):
        if self._appearance_owner is not None:
            if self.styleSheet()!=self._synced_popup_style:
                self._local_popup_style=self.styleSheet()
            sync_popup_appearance(self,self._appearance_owner)
            if self._local_popup_style:self.setStyleSheet(self.styleSheet()+'\n'+self._local_popup_style)
            self._synced_popup_style=self.styleSheet()
        screen=QApplication.screenAt(position) or self.screen()
        if screen is not None:
            available=max(1,screen.availableGeometry().width()-16)
            self.setMinimumWidth(min(POPUP_WIDTH,available))
            self.setMaximumWidth(available)
        self.ensurePolished()

    def popup(self,position,atAction=None):
        self._prepare(position)
        super().popup(position,atAction)

    def paintEvent(self,event):
        self._surface.paint()
        super().paintEvent(event)

    def open_at(self,position):
        self.popup(position)

    def open_for(self,control,view=None):
        """Anchor button menus to the visible trigger, including scene widgets."""
        top=widget_global_position(control,QPoint(0,0),view)
        bottom=widget_global_position(control,QPoint(control.width(),control.height()),view)
        self._prepare(top);size=self.sizeHint().expandedTo(self.minimumSize()).boundedTo(self.maximumSize())
        # The gap is in desktop pixels, independent of canvas zoom.
        position=QPoint(top.x(),bottom.y()+4)
        screen=QApplication.screenAt(top) or control.screen()
        if screen is not None:
            area=screen.availableGeometry().adjusted(4,4,-4,-4)
            if position.y()+size.height()>area.bottom()+1:
                position.setY(top.y()-size.height()-4)
            if position.x()+size.width()>area.right()+1:
                position.setX(bottom.x()-size.width())
            position.setX(max(area.left(),min(position.x(),area.right()+1-size.width())))
            position.setY(max(area.top(),min(position.y(),area.bottom()+1-size.height())))
        self.open_at(position)

class ComboItemDelegate(QStyledItemDelegate):
    def initStyleOption(self,option,index):
        super().initStyleOption(option,index)
        option.state &= ~QStyle.StateFlag.State_HasFocus

    def sizeHint(self,option,index):
        measured=QStyleOptionViewItem(option)
        self.initStyleOption(measured,index)
        size=super().sizeHint(measured,index)
        icon_height=measured.decorationSize.height() if not measured.icon.isNull() else 0
        size.setHeight(popup_row_height(measured.fontMetrics,icon_height))
        return size


class _FontComboItemDelegate(ComboItemDelegate):
    """Preview the family at the UI size, not Qt's native point-size fallback."""
    def initStyleOption(self,option,index):
        super().initStyleOption(option,index)
        font=QFont(option.widget.font() if option.widget is not None else option.font)
        font.setFamily(str(index.data(Qt.ItemDataRole.DisplayRole) or font.family()))
        option.font=font;option.fontMetrics=QFontMetrics(font)


class _ComboPopupBehavior:
    def __init__(self,*args):
        super().__init__(*args)
        self._auto_label_tooltip=''
        self.currentTextChanged.connect(self._sync_label_tooltip)
        font_picker=isinstance(self,QFontComboBox)
        view=self.view() if font_picker else QListView()
        if not font_picker:self.setView(view)
        view.setObjectName('ComboPopup');view.setSpacing(0)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setTextElideMode(Qt.TextElideMode.ElideRight)
        view.setItemDelegate(_FontComboItemDelegate(view) if font_picker else ComboItemDelegate(view))
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(6)
        QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo,False)
        self._popup=self.view().window(); self._popup.setObjectName('PcsComboContainer')
        self._surface=PopupSurface(self._popup,self)
        self._popup.installEventFilter(self)

    def _label_option(self):
        option=QStyleOptionComboBox();self.initStyleOption(option)
        field=self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,self)
        width=max(0,field.width()-2)
        if not option.currentIcon.isNull():width=max(0,width-option.iconSize.width()-4)
        full=option.currentText
        option.currentText=option.fontMetrics.elidedText(full,Qt.TextElideMode.ElideRight,width)
        return option,full

    def _sync_label_tooltip(self,*_):
        if not hasattr(self,'_auto_label_tooltip'):return
        current=self.toolTip()
        # Help supplied by the caller remains authoritative. Only replace a
        # tooltip which this control generated for its own shortened label.
        if current and current!=self._auto_label_tooltip:return
        option,full=self._label_option()
        text=full if not self.isEditable() and option.currentText!=full else ''
        self._auto_label_tooltip=text
        if current!=text:QWidget.setToolTip(self,text)

    def paintEvent(self,event):
        self._sync_label_tooltip()
        if self.isEditable() or self.currentIndex()<0:
            return super().paintEvent(event)
        option,_=self._label_option()
        painter=QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox,option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel,option)

    def event(self,event):
        if event.type()==QEvent.Type.ToolTip:self._sync_label_tooltip()
        result=super().event(event)
        if event.type() in (QEvent.Type.Resize,QEvent.Type.FontChange,QEvent.Type.StyleChange,QEvent.Type.Show):
            self._sync_label_tooltip()
        return result

    def eventFilter(self,watched,event):
        if watched is getattr(self,'_popup',None) and event.type() in (QEvent.Type.Resize,QEvent.Type.Show):
            # The alpha-painted surface owns the edge. A QRegion mask would
            # cut its anti-alias coverage again at fractional display scale.
            watched.clearMask()
        return super().eventFilter(watched,event)

    def showPopup(self):
        # Inherited item padding can change after the hidden view cached its
        # rows. Synchronize them before Qt measures and positions the popup.
        view=self.view();sync_popup_appearance(self._popup,self)
        self._popup.ensurePolished(); view.ensurePolished()
        view.style().polish(view)
        # Share a compact short-menu width and allow bounded extra room for
        # long names and large type. A vertical scrollbar handles long menus.
        bounds=self.screen().availableGeometry()
        text_width=max((view.fontMetrics().horizontalAdvance(self.itemText(i)) for i in range(self.count())),default=0)
        width=popup_width(self.width(),text_width,bounds.width()-16)
        view.setFixedWidth(width);self._popup.setFixedWidth(width);view.doItemsLayout()
        # Arrow keys change the popup's tentative index without committing the
        # combo. Escape must not leave that tentative choice armed for Enter
        # on the next opening, including custom model roots and columns.
        current=self.model().index(self.currentIndex(),self.modelColumn(),self.rootModelIndex())
        view.setCurrentIndex(current)
        if current.isValid():view.scrollTo(current)
        # Install the final outline before the first visible paint.
        self._popup.clearMask();super().showPopup()


class ComboBox(_ComboPopupBehavior,QComboBox):
    pass


class FontComboBox(_ComboPopupBehavior,QFontComboBox):
    pass


class SplitterFold(QObject):
    """Animate just a splitter's geometry; no snapshots or idle repaint loop."""
    def __init__(self, splitter, widget):
        super().__init__(splitter)
        self.splitter=splitter; self.widget=widget; self.expanded=True
        self.extent=0; self.minimum=widget.minimumSize(); self.policy=widget.sizePolicy()
        self.animation=QVariantAnimation(self); self.animation.setDuration(190)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.animation.valueChanged.connect(self.step); self.animation.finished.connect(self.finish)

    def set_expanded(self, expanded, animated=True):
        moving=self.animation.state()==QVariantAnimation.State.Running
        self.animation.stop()
        index=self.splitter.indexOf(self.widget)
        sizes=self.splitter.sizes()
        if self.expanded and not expanded and sizes[index] and not moving: self.extent=sizes[index]
        self.expanded=expanded
        if not animated:
            self.widget.setVisible(expanded)
            self.widget.setMinimumSize(self.minimum); self.widget.setSizePolicy(self.policy)
            return
        self.widget.setMinimumSize(0,0)
        self.widget.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored)
        self.widget.show()
        # Save the actual sizes before showing a collapsed pane redistributes them.
        self.start_sizes=sizes; self.index=index
        self.neighbor=index+1 if index+1<len(sizes) else index-1
        total=sizes[index]+sizes[self.neighbor]
        target=min(self.extent or int(total*0.4),int(total*0.65)) if expanded else 0
        self.animation.setStartValue(float(sizes[index])); self.animation.setEndValue(float(target))
        self.animation.start()

    def step(self, value):
        sizes=list(self.start_sizes); delta=int(value)-sizes[self.index]
        sizes[self.index]+=delta; sizes[self.neighbor]-=delta
        self.splitter.setSizes(sizes)

    def finish(self):
        self.widget.setVisible(self.expanded)
        self.widget.setMinimumSize(self.minimum); self.widget.setSizePolicy(self.policy)

    def toggle(self): self.set_expanded(not self.expanded)

def image_path(parent):
    return QFileDialog.getOpenFileName(parent,"選擇預覽圖片","","圖片 (*.png *.jpg *.jpeg *.webp *.bmp)")[0]

def preview_path(store, relative):
    if not relative or not isinstance(relative,str):
        return None
    path = (store.directory/relative).resolve()
    return path if path.is_relative_to(store.directory.resolve()) and path.is_file() else None

def set_preview(widget, store, relative, size=240):
    path = preview_path(store,relative)
    pixmap = QPixmap(str(path)) if path else QPixmap()
    if pixmap.isNull():
        widget.setPixmap(QPixmap())
        widget.setText("尚未設定預覽圖片\n可拖入圖片，或按下方按鈕選擇" if widget.acceptDrops() else "尚未設定預覽圖片")
    else:
        widget.setPixmap(pixmap.scaled(size,size,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))

def thumb_icon(store, relative, size):
    path=preview_path(store,relative)
    if not path: return QIcon()
    return QIcon(QPixmap(str(path)).scaled(size,size,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))

def record_pixmap(store,record,size):
    from .media_paths import preview_file
    path=preview_file(store.directory,record)
    if path is None: return QPixmap()
    reader=QImageReader(str(path)); reader.setAutoTransform(True); reader.setAllocationLimit(128)
    target=reader.size()
    if target.isValid() and max(target.width(),target.height())>size*2:
        target.scale(size*2,size*2,Qt.AspectRatioMode.KeepAspectRatio); reader.setScaledSize(target)
    image=reader.read()
    if image.isNull(): return QPixmap()
    result=QPixmap.fromImage(image).scaled(size*2,size*2,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
    result.setDevicePixelRatio(2); return result

def record_icon(store,record,size): return QIcon(record_pixmap(store,record,size))

def set_record_preview(widget,store,record,size=240):
    pixmap=record_pixmap(store,record,size); widget.setPixmap(pixmap)
    if pixmap.isNull(): widget.setText('圖片無法讀取，可重新連結原圖。' if record else '選擇圖片')

def open_file(parent, path):
    if not Path(path).is_file():
        information(parent,"找不到原檔","原檔可能已搬動，請重新連結檔案。")
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

def reveal_file(parent,path):
    folder=Path(path).resolve().parent
    if not folder.is_dir(): information(parent,'找不到資料夾','原圖所在的資料夾已不存在。'); return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

def open_url(parent, url):
    parsed = QUrl(url.strip())
    if parsed.scheme() not in ("http", "https") or not parsed.host():
        information(parent,"網址無效","請填寫完整的 https:// 或 http:// 網址。")
        return
    QDesktopServices.openUrl(parsed)
