"""Explicit attachment imports on a bounded, non-inference worker pool."""
import base64
import copy
import hashlib
from pathlib import Path
from threading import Event
import uuid

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..local_documents import chat_attachments as contract, schema

_pool = None


def pool():
    global _pool
    if _pool is None:
        _pool = QThreadPool()
        _pool.setMaxThreadCount(1)
    return _pool


def _check(cancel):
    if cancel.is_set():
        raise ValueError('附件讀入已取消。')


def _image(raw, cancel):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
    from PySide6.QtGui import QImage, QImageReader
    _check(cancel)
    buffer = QBuffer()
    buffer.setData(QByteArray(raw))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    width, height = size.width(), size.height()
    if (bytes(reader.format()).lower() not in (b'png', b'jpeg', b'webp', b'bmp')
            or reader.imageCount() != 1 or min(width, height) <= 0 or width * height > 16_000_000):
        raise ValueError('圖片格式不支援、無法讀取，或超過 1,600 萬像素。')
    # The chat keeps a bounded image snapshot; the selected original is untouched.
    if width * height > 4_000_000:
        ratio = (4_000_000 / (width * height)) ** .5
        reader.setScaledSize(QSize(max(1, int(width * ratio)), max(1, int(height * ratio))))
    image = reader.read()
    if image.isNull():
        raise ValueError('無法讀取這張圖片。')
    return _canonical_image(image, cancel)


def _canonical_image(image, cancel):
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImage
    if image.isNull() or image.width() * image.height() > 16_000_000:
        raise ValueError('圖片無法讀取，或超過 1,600 萬像素。')
    _check(cancel)
    if image.width() * image.height() > 4_000_000:
        ratio = (4_000_000 / (image.width() * image.height())) ** .5
        image = image.scaled(max(1, int(image.width() * ratio)), max(1, int(image.height() * ratio)),
                             Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    # Fresh pixel storage excludes arbitrary image text/profile metadata.
    clean = QImage(image.constBits(), image.width(), image.height(), image.bytesPerLine(), image.format()).copy()
    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    if not clean.save(output, 'PNG'):
        raise ValueError('無法建立對話圖片快照。')
    _check(cancel)
    data = bytes(output.data())
    if len(data) > contract.MAX_IMAGE_BYTES:
        raise ValueError('對話圖片快照超過 2 MiB，請選擇較小的圖片。')
    uri = 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')
    contract.image_bytes(uri)
    return data, uri


def freeze_descriptors(descriptors):
    """A clipboard QImage is implicitly shared; PNG encoding stays in worker."""
    from PySide6.QtGui import QImage
    result = []
    for value in descriptors:
        if isinstance(value, dict) and 'image' in value:
            if not isinstance(value['image'], QImage):
                raise ValueError('剪貼簿圖片格式無效。')
            result.append(dict(name=value.get('name', '貼上的圖片.png'), image=QImage(value['image'])))
        else:
            result.append(copy.deepcopy(value))
    return result


def _read(path, cancel):
    path = Path(path).expanduser()
    if not path.is_absolute() or str(path).startswith('\\\\'):
        raise ValueError('請選擇本機檔案。')
    if not path.is_file() or path.stat().st_size > contract.MAX_FILE_BYTES:
        raise ValueError('附件不存在或超過單檔 8 MiB。')
    _check(cancel)
    with path.open('rb') as stream:
        data = stream.read(contract.MAX_FILE_BYTES + 1)
    _check(cancel)
    if len(data) > contract.MAX_FILE_BYTES:
        raise ValueError('附件超過單檔 8 MiB。')
    return path, data


def prepare(descriptor, cancel):
    _check(cancel)
    source = ''
    if isinstance(descriptor, (str, Path)):
        path = Path(descriptor)
        if path.suffix.lower() in schema.SUFFIXES.values():
            from ..workspace_chat_attachments import prepare_pcs_attachment
            descriptor = prepare_pcs_attachment(path, path.parent)
        else:
            path, raw = _read(path, cancel)
            if raw.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8', b'BM')) or raw[:4] == b'RIFF':
                descriptor = dict(name=path.name, data=raw, mime='image/*')
            else:
                try:
                    text = raw.decode('utf-8-sig')
                except UnicodeError as error:
                    raise ValueError('目前只支援 UTF-8 文字檔與 PNG、JPEG、WebP、BMP 圖片。') from error
                if any(ord(c) < 32 and c not in '\t\r\n' for c in text):
                    raise ValueError('這個檔案不是可讀取的文字附件。')
                descriptor = dict(name=path.name, kind='text', text=text)
    elif isinstance(descriptor, dict) and descriptor.get('source_type') == 'pcs':
        from ..workspace_chat_attachments import prepare_pcs_attachment
        descriptor = prepare_pcs_attachment(descriptor['path'], descriptor['workspace_root'])
    if not isinstance(descriptor, dict):
        raise ValueError('附件來源格式無效。')
    name = descriptor.get('name', '')
    source = descriptor.get('source', '')
    item = dict(id=str(uuid.uuid4()), name=name, source=source)
    if descriptor.get('kind') == 'text':
        text = descriptor.get('text')
        if not isinstance(text, str) or len(text) > contract.MAX_TEXT_CHARS:
            raise ValueError('文字附件最多 200,000 字元。')
        raw = text.encode('utf-8')
        item.update(kind='text', mime='text/plain', text=text)
    elif 'image' in descriptor:
        raw, uri = _canonical_image(descriptor['image'], cancel)
        item.update(kind='image', mime='image/png', data_uri=uri)
    else:
        raw = descriptor.get('data')
        if not isinstance(raw, bytes) or len(raw) > contract.MAX_FILE_BYTES:
            raise ValueError('圖片資料無效或超過單檔 8 MiB。')
        raw, uri = _image(raw, cancel)
        item.update(kind='image', mime='image/png', data_uri=uri)
    item.update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    contract.validate_list([item])
    return item


class _Signals(QObject):
    done = Signal(object, object, str)


class AttachmentImport(QRunnable):
    def __init__(self, token, descriptors, snapshot):
        super().__init__()
        self.token, self.descriptors, self.snapshot = token, descriptors, snapshot
        self.cancel = Event()
        self.signals = _Signals()

    def run(self):
        try:
            additions = [prepare(value, self.cancel) for value in self.descriptors]
            values = self.snapshot['payload'].get('draft_attachments', []) + additions
            contract.validate_list(values)
            self.snapshot['payload']['draft_attachments'] = values
            contract.sync_feature(self.snapshot)
            schema.encode(self.snapshot)  # Reject size/structure before owner acceptance.
            _check(self.cancel)
            self.signals.done.emit(self.token, additions, '')
        except Exception as error:
            detail = str(error) if isinstance(error, (ValueError, OSError)) else '附件無法讀取。'
            self.signals.done.emit(self.token, [], detail)
