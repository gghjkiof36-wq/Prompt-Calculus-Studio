"""Read-only image lightbox for an accepted, self-contained attachment."""
import base64

from PySide6.QtCore import Qt, Signal, QBuffer, QByteArray, QIODevice, QEvent, QRect
from PySide6.QtGui import QColor, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from ..ui_icons import icon
from ..widgets import button


def snapshot_image(item, size=None):
    uri = item.get('data_uri', '')
    if not uri.startswith('data:image/png;base64,') or len(uri) > 3_000_000:
        return QPixmap()
    try:
        data = base64.b64decode(uri.partition(',')[2], validate=True)
    except ValueError:
        return QPixmap()
    source = QBuffer()
    source.setData(QByteArray(data))
    source.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(source)
    bounds = reader.size()
    if not bounds.isValid() or bounds.width() * bounds.height() > 4_000_000:
        return QPixmap()
    if size is not None:
        bounds.scale(size, Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(bounds)
    return QPixmap.fromImage(reader.read())


class AttachmentLightbox(QWidget):
    """One window-owned overlay; opening or dismissing never edits the owner."""
    closed = Signal()

    def __init__(self, source, item):
        super().__init__(source.window())
        self._source = source
        self._previous_focus = QApplication.focusWidget()
        self._finished = False
        self.image = snapshot_image(item)
        self.image_rect = QRect()
        self.setObjectName('ChatImageLightbox')
        self.setAccessibleName('圖片預覽')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.close_button = button('', self.dismiss, 'IconButton')
        self.close_button.setParent(self)
        self.close_button.setIcon(icon('x', '#ffffff', 18))
        self.close_button.setFixedSize(32, 32)
        self.close_button.setToolTip('關閉圖片預覽')
        self.close_button.setAccessibleName('關閉圖片預覽')
        self.close_button.setStyleSheet('QPushButton {background:rgba(0,0,0,150);border:0;border-radius:16px;} '
            'QPushButton:hover,QPushButton:focus {background:rgba(255,255,255,45);}')
        source.installEventFilter(self)
        source.destroyed.connect(self.dismiss)
        self.parentWidget().installEventFilter(self)
        self.setGeometry(self.parentWidget().rect())

    def present(self):
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def dismiss(self):
        if self._finished:
            return
        self._finished = True
        self.hide()
        self.closed.emit()
        previous = self._previous_focus
        if previous is not None and isValid(previous) and previous.isVisible():
            previous.setFocus(Qt.FocusReason.PopupFocusReason)
        self.deleteLater()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
            self.dismiss()
        elif watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())
        return False

    def resizeEvent(self, event):
        self.close_button.move(max(8, self.width() - 48), 16)
        self.image_rect = QRect()
        if not self.image.isNull():
            available = self.rect().adjusted(32, 56, -32, -32)
            size = self.image.size().scaled(available.size(), Qt.AspectRatioMode.KeepAspectRatio)
            # Small images remain sharp at their original dimensions.
            size = size.boundedTo(self.image.size())
            self.image_rect = QRect(0, 0, size.width(), size.height())
            self.image_rect.moveCenter(available.center())
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        # Same window-only dimming as DialogShade/CanvasSheetDialog.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))
        if self.image.isNull():
            painter.setPen(QColor('#ffffff'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '圖片無法顯示。')
        else:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self.image_rect, self.image)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.image_rect.contains(event.position().toPoint()):
            self.dismiss()
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(event)

    def focusNextPrevChild(self, next):
        self.close_button.setFocus(Qt.FocusReason.TabFocusReason)
        return True
