"""Canvas dialog depth and a bounded, dismissible function sheet."""
from PySide6.QtCore import Qt, QEvent, QPoint, QRect, QSize
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget, QDialog, QFrame, QLayout, QApplication


def window_owner(widget):
    while widget is not None and widget.parentWidget() is not None:
        widget=widget.parentWidget()
    return widget


def reduced_motion(widget):
    owner=window_owner(widget)
    return bool(getattr(owner,'state',{}).get('settings',{}).get('reduce_motion',False))


def presenting_parent(parent):
    """A nested sheet covers its visible parent, even for legacy Window callers."""
    active=QApplication.activeModalWidget()
    if active is not None and active.isVisible() and window_owner(active) is window_owner(parent):
        return active
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
