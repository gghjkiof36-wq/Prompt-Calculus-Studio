"""Canvas dialog depth and a bounded, dismissible function sheet."""
from PySide6.QtCore import Qt, QEvent, QPoint, QRect, QSize, QObject, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget, QDialog, QFrame, QLayout, QApplication
from .elapsed_animation import ElapsedAnimation


def window_owner(widget):
    while widget is not None and widget.parentWidget() is not None:
        widget=widget.parentWidget()
    return widget


def reduced_motion(widget):
    from .theme import window_appearance
    return bool(window_appearance(widget).get('reduce_motion', False))


def presenting_parent(parent):
    """A nested sheet covers its visible parent, even for legacy Window callers."""
    active=QApplication.activeModalWidget() or QApplication.activeWindow()
    if isinstance(active,QDialog) and active.isVisible() and window_owner(active) is window_owner(parent):
        return active
    # Embedded editors occupy only the content column. Modal sheets belong to
    # the workspace window, so changing either sidebar cannot shift the center.
    current=parent
    while current is not None:
        workspace=getattr(current,'workspace_owner',None)
        if workspace is not None:return workspace
        current=current.parentWidget()
    return parent


class DialogShade(QWidget):
    def __init__(self,host):
        super().__init__(host)
        self.setObjectName('CanvasDialogShade')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        host.installEventFilter(self)
        self.setGeometry(host.rect());self.show();self.raise_()

    def eventFilter(self,host,event):
        if event.type()==QEvent.Type.Resize:self.setGeometry(host.rect())
        return False

    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor(0,0,0,120))


class CenteredPromptForm(QObject):
    """Resize a centered child sheet inside a stable transparent native window."""
    def __init__(self,dialog,scroll,minimum_height=440):
        super().__init__(dialog)
        self.dialog=dialog;self.scroll=scroll;self.minimum_height=minimum_height
        self.surface=dialog.findChild(QFrame,'DialogSurface')
        self.preferred_width=dialog.width();self._fitting=False;self._prepared=False;self._disclosure=None;self._prompt_limits=None
        self.scroll_policy=scroll.verticalScrollBarPolicy()
        dialog.layout().removeWidget(self.surface)
        dialog.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        dialog.layout().setEnabled(False)
        self.surface.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.motion=ElapsedAnimation(self);self.motion.setDuration(180)
        self.motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.motion.valueChanged.connect(self.place)
        self.motion.finished.connect(self.finished)
        dialog.installEventFilter(self)
        if dialog.parentWidget():dialog.parentWidget().installEventFilter(self)

    def bounds(self):
        parent=self.dialog.parentWidget()
        screen=(parent.screen() if parent else self.dialog.screen()).availableGeometry().adjusted(20,20,-20,-20)
        if parent is None:return screen
        bounds=QRect(parent.mapToGlobal(QPoint(12,12)),parent.size()-QSize(24,24))
        return bounds.intersected(screen)

    def frame(self):
        bounds=self.bounds()
        rect=QRect(QPoint(),QSize(min(self.preferred_width,bounds.width()),bounds.height()))
        rect.moveCenter(bounds.center());self.dialog.setGeometry(rect)

    def target(self):
        content=self.scroll.widget()
        self.surface.resize(max(1,self.dialog.width()-12),max(1,self.surface.height()))
        for layout in reversed(content.findChildren(QLayout)):layout.invalidate()
        for widget in reversed(content.findChildren(QWidget)):widget.updateGeometry()
        content.layout().activate();content.updateGeometry();self.surface.layout().activate()
        chrome=self.surface.height()-self.scroll.viewport().height()
        height=max(self.minimum_height-12,content.sizeHint().height()+chrome)
        rect=QRect(0,0,max(1,self.dialog.width()-12),min(height,max(1,self.dialog.height()-12)))
        rect.moveCenter(self.dialog.rect().center())
        return rect

    def place(self,rect):
        if self._disclosure:
            panel,start,end,opened,first,last=self._disclosure
            progress=max(0.,min(1.,(rect.height()-first)/(last-first))) if last!=first else 1.
            panel.setFixedHeight(round(start+(end-start)*progress))
        self.surface.setGeometry(rect);self.surface.layout().activate();self.dialog.update()

    def finished(self):
        if self._disclosure:
            panel,_,end,opened,_,_=self._disclosure;self._disclosure=None
            panel.setVisible(opened);panel.setMinimumHeight(0);panel.setMaximumHeight(16777215)
        if self._prompt_limits:
            minimum,maximum=self._prompt_limits;self._prompt_limits=None
            self.dialog.prompt.setMinimumHeight(minimum);self.dialog.prompt.setMaximumHeight(maximum)
        self.scroll.setVerticalScrollBarPolicy(self.scroll_policy)
        self.surface.layout().activate();self.dialog.update()

    def fit(self,*,animate=False,before_show=False,reframe=False,disclosure=None):
        if self._fitting or (not before_show and not self.dialog.isVisible()):return
        self._fitting=True
        try:
            self.motion.stop()
            if self._disclosure and disclosure is None:self.finished()
            self._disclosure=None
            if not self._prepared or reframe:
                self.frame();self._prepared=True
                self.place(QRect(6,6,max(1,self.dialog.width()-12),max(1,self.dialog.height()-12)))
                animate=False
            start=QRect(self.surface.geometry())
            end=self.target()
            if animate and start!=end and not reduced_motion(self.dialog):
                # Only the rounded child moves. Resizing a translucent HWND
                # while it is visible can expose the native backing surface.
                self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                if disclosure:self._disclosure=(*disclosure,start.height(),end.height())
                self.place(start)
                self.motion.setDuration(max(70,min(180,abs(end.height()-start.height())*2)))
                self.motion.setStartValue(start);self.motion.setEndValue(end);self.motion.start()
            else:
                if disclosure:self._disclosure=(*disclosure,start.height(),end.height())
                self.place(end)
                # Child layouts can publish a changed minimum after their
                # first geometry. Settle synchronously before the first paint.
                self.place(self.target());self.finished()
        finally:self._fitting=False

    def toggle(self,panel,opened):
        # Revealing the whole editor before growing the sheet briefly overflows
        # the scroll viewport. Measure without painting, then reveal the clipped
        # panel in step with the sheet, including on its first style polish.
        self.dialog.setUpdatesEnabled(False)
        try:
            if self._prompt_limits is None:
                prompt=self.dialog.prompt
                self._prompt_limits=(prompt.minimumHeight(),prompt.maximumHeight())
                prompt.setFixedHeight(prompt.height())
            start=0 if panel.isHidden() else panel.height()
            self.motion.stop();self._disclosure=None
            panel.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            panel.setMinimumHeight(0);panel.setMaximumHeight(16777215)
            panel.ensurePolished()
            for child in panel.findChildren(QWidget):child.ensurePolished()
            panel.layout().invalidate();panel.layout().activate()
            end=panel.sizeHint().height() if opened else 0
            panel.setFixedHeight(end);panel.show()
            self.fit(animate=True,disclosure=(panel,start,end,opened))
        finally:self.dialog.setUpdatesEnabled(True)

    def eventFilter(self,watched,event):
        from shiboken6 import isValid
        if not isValid(self.dialog) or not isValid(self.surface) or not isValid(self.scroll):return False
        if watched is self.dialog and event.type()==QEvent.Type.Hide:
            self.motion.stop();self.finished();self._prepared=False
        elif watched is self.dialog and event.type()==QEvent.Type.Resize and self._prepared and not self._fitting:
            self.fit()
        elif watched is self.dialog.parentWidget() and self.dialog.isVisible() and event.type() in (QEvent.Type.Resize,QEvent.Type.Move):
            self.fit(reframe=True)
        return False


# Imported here rather than by widgets.py: StudioDialog loads only the small
# parent/shade helpers above at runtime, after its own class is defined.
from .widgets import StudioDialog


class CanvasSheetDialog(StudioDialog):
    """Own the backdrop and clicks in one modal window, like CanvasPalette."""
    def __init__(self,parent):
        super().__init__(parent)
        self._owns_backdrop=True
        self.surface=self.findChild(QFrame,'DialogSurface')
        self.layout().removeWidget(self.surface)
        self.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.layout().setEnabled(False)
        self.sheet_size=None;self.closing_position=None
        parent.installEventFilter(self)

    def showEvent(self,event):
        QDialog.showEvent(self,event)
        if self.sheet_size is None:self.sheet_size=self.size()-QSize(12,12)
        self.fit_window()

    def fit_window(self):
        host=self.parentWidget()
        bounds=QRect(host.mapToGlobal(QPoint()),host.size()).intersected(host.screen().availableGeometry())
        self.setGeometry(bounds);self.place_sheet()

    def place_sheet(self):
        if self.sheet_size is None:return
        area=self.rect().adjusted(20,20,-20,-20)
        size=self.sheet_size.boundedTo(area.size())
        self.surface.resize(size)
        self.surface.move(area.center()-QPoint(size.width()//2,size.height()//2))

    def resizeEvent(self,event):
        QDialog.resizeEvent(self,event)
        if hasattr(self,'sheet_size'):self.place_sheet()

    def eventFilter(self,watched,event):
        if watched is self.parentWidget() and self.isVisible() and event.type() in (QEvent.Type.Resize,QEvent.Type.Move):
            self.fit_window()
        return super().eventFilter(watched,event)

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.fillRect(self.rect(),QColor(0,0,0,120))

    def mousePressEvent(self,event):
        if not self.surface.geometry().contains(event.position().toPoint()):
            self.closing_position=event.globalPosition().toPoint();self.reject()
        event.accept()

    def mouseReleaseEvent(self,event):event.accept()
    def mouseDoubleClickEvent(self,event):event.accept()

    def hideEvent(self,event):
        if self.closing_position is not None and hasattr(self,'canvas'):
            from .palette_cards import ClosingClickGuard
            ClosingClickGuard(self.canvas.view.viewport(),self.closing_position)
            self.closing_position=None
        super().hideEvent(event)
