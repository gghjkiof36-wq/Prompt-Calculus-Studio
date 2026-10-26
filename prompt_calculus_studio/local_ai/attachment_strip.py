"""Compact attachment snapshots; files are accepted only by the document owner."""
from PySide6.QtCore import Qt, Signal, QSize, QRectF
from PySide6.QtGui import QImage, QPixmap, QPainter, QPainterPath, QColor, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QWidget

from ..ui_icons import icon
from ..widgets import StudioDialog, button
from .attachment_preview import AttachmentLightbox, snapshot_image


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


class ImageThumbnail(QPushButton):
    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.setObjectName('ChatImageThumbnail')
        self.setFixedSize(64, 64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(item.get('name', '圖片'))
        self.setAccessibleName('預覽圖片：' + item.get('name', '圖片'))
        self.image = snapshot_image(item, QSize(128, 128))
        self.tokens = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        shape = QPainterPath()
        shape.addRoundedRect(area, 10, 10)
        painter.setClipPath(shape)
        tokens = self.tokens
        painter.fillRect(self.rect(), QColor(tokens['raised']) if tokens else self.palette().button())
        if self.image.isNull():
            painter.drawPixmap(21, 21, icon('image', size=22).pixmap(22, 22))
        else:
            # Fill the compact square. The lightbox shows the complete image.
            image = self.image.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                      Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((self.width() - image.width()) // 2, (self.height() - image.height()) // 2, image)
        painter.setClipping(False)
        color = tokens['accent' if self.hasFocus() else 'divider'] if tokens else self.palette().mid().color()
        painter.setPen(QPen(QColor(color), 2 if self.hasFocus() else 1))
        painter.drawPath(shape)


class AttachmentStrip(QScrollArea):
    removeRequested = Signal(str)

    def __init__(self, parent=None, *, read_only=False):
        super().__init__(parent)
        self.read_only = read_only
        self._signature = None
        self._remove_buttons = []
        self._items = []
        self._preview = None
        self._tokens = None
        self.setObjectName('ChatAttachments')
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFixedHeight(80)
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
                child = self.items_layout.takeAt(1 if self.read_only else 0).widget()
                child.deleteLater()
            self._remove_buttons = []
            for item in items:
                if item.get('kind') == 'image':
                    frame = QFrame()
                    frame.setFixedSize(64, 64)
                    picture = ImageThumbnail(item, frame)
                    picture.tokens = self._tokens
                    picture.clicked.connect(lambda checked=False, value=item: self.preview(value))
                    if not self.read_only:
                        remove = self._remove_button(item)
                        remove.setParent(frame)
                        remove.move(40, 0)
                        remove.setObjectName('ChatImageRemove')
                        remove.raise_()
                    self._append_frame(frame)
                    continue
                frame = QFrame()
                frame.setObjectName('ChatAttachment')
                frame.setFixedSize(208 if not self.read_only else 180, 52)
                layout = QHBoxLayout(frame)
                layout.setContentsMargins(8, 6, 6, 6)
                layout.setSpacing(6)
                picture = QLabel()
                picture.setFixedSize(32, 32)
                picture.setPixmap(icon('file-text', size=22).pixmap(22, 22))
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
                    remove = self._remove_button(item)
                    layout.addWidget(remove)
                self._append_frame(frame)
        for remove in self._remove_buttons:
            remove.setEnabled(not busy)
        self.setVisible(bool(items))

    def _append_frame(self, frame):
        # Sent attachments share the right edge of the user's message bubble.
        index = self.items_layout.count() if self.read_only else self.items_layout.count() - 1
        self.items_layout.insertWidget(index, frame)

    def _remove_button(self, item):
        remove = button('', lambda checked=False, identity=item['id']: self.removeRequested.emit(identity), 'IconButton')
        remove.setIcon(icon('x', size=14))
        remove.setFixedSize(24, 24)
        remove.setAccessibleName('移除附件：' + item.get('name', '附件'))
        remove.setToolTip('移除附件')
        self._remove_buttons.append(remove)
        return remove

    def apply_appearance(self, tokens):
        self._tokens = tokens
        self.setStyleSheet('QScrollArea#ChatAttachments, QScrollArea#ChatAttachments>QWidget>QWidget {background:transparent;border:0;}'
            f'QFrame#ChatAttachment {{background:{tokens["raised"]};border:1px solid {tokens["divider"]};border-radius:10px;}}'
            f'QPushButton#ChatImageRemove {{background:{tokens["raised"]};border:1px solid {tokens["divider"]};border-radius:12px;padding:0;}}')
        for thumbnail in self.findChildren(ImageThumbnail):
            thumbnail.tokens = tokens
            thumbnail.update()

    def preview(self, item):
        if self._preview is not None:
            if isinstance(self._preview, AttachmentLightbox):
                self._preview.dismiss()
            else:
                self._preview.close()
        if item.get('kind') == 'image':
            lightbox = AttachmentLightbox(self, item)
            self._preview = lightbox
            lightbox.closed.connect(lambda: self._preview_closed(lightbox))
            lightbox.present()
            return
        dialog = StudioDialog(self)
        dialog.setWindowTitle(item.get('name', '附件'))
        dialog.resize(680, 480)
        content = QPlainTextEdit()
        content.setReadOnly(True)
        content.setPlainText(item.get('text', ''))
        dialog.body.addWidget(content)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.set_outside_dismiss(True)
        dialog.finished.connect(dialog.deleteLater)
        dialog.finished.connect(lambda: self._preview_closed(dialog))
        self._preview = dialog
        dialog.show()

    def _preview_closed(self, preview):
        if self._preview is preview:
            self._preview = None
