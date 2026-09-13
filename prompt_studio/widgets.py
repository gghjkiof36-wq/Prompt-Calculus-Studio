from pathlib import Path
from PySide6.QtCore import Qt, QSize, QPoint, QRectF, QObject, QVariantAnimation, QEasingCurve, QEvent
from PySide6.QtGui import QPixmap, QDesktopServices, QIcon, QPainterPath, QRegion, QPainter, QPalette, QImageReader
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QFrame,
                              QDialogButtonBox, QFileDialog, QMessageBox, QDialog, QScrollArea,
                              QLineEdit, QPlainTextEdit, QComboBox, QListView, QListWidget, QMenu, QSizePolicy, QWidget, QBoxLayout, QApplication)

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

    def resizeEvent(self,event):
        required=self.title.sizeHint().width()+sum(a.sizeHint().width() for a in self.actions)+28
        self.flow.setDirection(QBoxLayout.Direction.LeftToRight if self.width()>=required else QBoxLayout.Direction.TopToBottom)
        super().resizeEvent(event)


class WindowShell(QFrame):
    def __init__(self,window):
        super().__init__(window)
        self.setObjectName("Shell"); self.setMouseTracking(True)
        QApplication.instance().installEventFilter(self)

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


class RoundMenu(QMenu):
    """Use Qt's non-blocking popup path for custom-shaped Windows menus.

    QMenu.exec() with this native custom menu crashes on the supported Qt build.
    Actions already connect to their handlers; callers never need a return value.
    """
    def __init__(self,parent=None):
        super().__init__(parent)
        # A native opaque menu paints a rectangular palette behind the QSS
        # radius. Alpha-backed painting avoids that rim without a jagged mask.
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # A submenu hides whenever the pointer returns to its parent. Its
        # action must live until the root popup closes, not until that hover ends.
        if not isinstance(parent,QMenu): self.aboutToHide.connect(self.deleteLater)

    def open_at(self,position):
        self.popup(position)

class ComboBox(QComboBox):
    def __init__(self,*args):
        super().__init__(*args)
        view=QListView(); view.setSpacing(2); self.setView(view)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(6)
        QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateCombo,False)
        self._popup=self.view().window(); self._popup.installEventFilter(self)

    def eventFilter(self,watched,event):
        if watched is getattr(self,'_popup',None) and event.type() in (QEvent.Type.Resize,QEvent.Type.Show):
            rounded_mask(watched,10)
        return super().eventFilter(watched,event)

    def showPopup(self):
        # Install the final outline before the first visible paint.
        rounded_mask(self._popup,10); super().showPopup()


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
