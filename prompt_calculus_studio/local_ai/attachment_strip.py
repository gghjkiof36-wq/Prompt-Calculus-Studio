"""Compact attachment snapshots; files are accepted only by the document owner."""
import base64

from PySide6.QtCore import Qt, Signal, QBuffer, QByteArray, QIODevice, QSize
from PySide6.QtGui import QImage, QImageReader, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QWidget

from ..ui_icons import icon
from ..widgets import StudioDialog, button


def mime_attachments(mime):
    """Return explicit clipboard/drop input, without resolving any filename."""
    if mime.hasUrls():
        urls = mime.urls()
        if urls and all(url.isLocalFile() for url in urls):
            return [url.toLocalFile() for url in urls]
    if mime.hasImage():
        value = mime.imageData()
        image = value.toImage() if isinstance(value, QPixmap) else QImage(value)
        if image.isNull() or image.width() * image.height() > 16_000_000:
            raise ValueError('圖片無法讀取，或超過 1600 萬像素。')
        # Implicit sharing preserves the clipboard snapshot without compressing
        # a large bitmap on the UI thread. The owner validates/encodes in its worker.
        return [dict(name='貼上的圖片.png', image=QImage(image))]
    return None


def snapshot_image(item, size):
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
    bounds.scale(size, Qt.AspectRatioMode.KeepAspectRatio)
    reader.setScaledSize(bounds)
    return QPixmap.fromImage(reader.read())


class AttachmentStrip(QScrollArea):
    removeRequested = Signal(str)

    def __init__(self, parent=None, *, read_only=False):
        super().__init__(parent)
        self.read_only = read_only
        self._signature = None
        self._remove_buttons = []
        self._items = []
        self.setObjectName('ChatAttachments')
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFixedHeight(70)
        self.container = QWidget()
        self.items_layout = QHBoxLayout(self.container)
        self.items_layout.setContentsMargins(0, 0, 0, 0)
        self.items_layout.setSpacing(8)
        self.items_layout.addStretch()
        self.setWidget(self.container)
        self.hide()

    def sync(self, items, *, busy=False):
        signature = tuple((a.get('id'), a.get('name'), a.get('size'), a.get('kind'),
                           a.get('data_uri'), a.get('text')) for a in items)
        if signature != self._signature:
            self._signature, self._items = signature, list(items)
            while self.items_layout.count() > 1:
                child = self.items_layout.takeAt(0).widget()
                child.deleteLater()
            self._remove_buttons = []
            for item in items:
                frame = QFrame()
                frame.setObjectName('ChatAttachment')
                frame.setFixedSize(208 if not self.read_only else 180, 52)
                layout = QHBoxLayout(frame)
                layout.setContentsMargins(8, 6, 6, 6)
                layout.setSpacing(6)
                picture = QLabel()
                picture.setFixedSize(32, 32)
                image = snapshot_image(item, QSize(32, 32)) if item.get('kind') == 'image' else QPixmap()
                picture.setPixmap(image if not image.isNull() else icon('file-text', size=22).pixmap(22, 22))
                picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(picture)
                name = QPushButton()
                name.setObjectName('Quiet')
                name.setMinimumWidth(0)
                name.setText(name.fontMetrics().elidedText(item.get('name', '附件'), Qt.TextElideMode.ElideMiddle,
                                                         116 if self.read_only else 100))
                name.setToolTip(item.get('name', '附件'))
                name.setAccessibleName('預覽附件：' + item.get('name', '附件'))
                name.clicked.connect(lambda checked=False, value=item: self.preview(value))
                layout.addWidget(name, 1)
                if not self.read_only:
                    remove = button('', lambda checked=False, identity=item['id']: self.removeRequested.emit(identity), 'IconButton')
                    remove.setIcon(icon('x', size=14))
                    remove.setFixedSize(24, 24)
                    remove.setAccessibleName('移除附件：' + item.get('name', '附件'))
                    remove.setToolTip('移除附件')
                    layout.addWidget(remove)
                    self._remove_buttons.append(remove)
                self.items_layout.insertWidget(self.items_layout.count() - 1, frame)
        for remove in self._remove_buttons:
            remove.setEnabled(not busy)
        self.setVisible(bool(items))

    def apply_appearance(self, tokens):
        self.setStyleSheet('QScrollArea#ChatAttachments, QScrollArea#ChatAttachments>QWidget>QWidget {background:transparent;border:0;}'
            f'QFrame#ChatAttachment {{background:{tokens["raised"]};border:1px solid {tokens["divider"]};border-radius:10px;}}')

    def preview(self, item):
        dialog = StudioDialog(self)
        dialog.setWindowTitle(item.get('name', '附件'))
        dialog.resize(680, 480)
        if item.get('kind') == 'image':
            image = snapshot_image(item, QSize(600, 360))
            content = QLabel()
            content.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if image.isNull():
                content.setText('圖片無法顯示。')
            else:
                content.setPixmap(image)
        else:
            content = QPlainTextEdit()
            content.setReadOnly(True)
            content.setPlainText(item.get('text', ''))
        dialog.body.addWidget(content)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.set_outside_dismiss(True)
        dialog.finished.connect(dialog.deleteLater)
        self._preview = dialog
        dialog.show()
