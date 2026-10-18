"""Bounded parallel image decoding; QPixmap creation stays on the GUI thread."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QImageReader


def decode_image(path, size):
    if path is None:
        return QImage()
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    reader.setAllocationLimit(128)
    target = reader.size()
    if target.isValid() and max(target.width(), target.height()) > size*2:
        target.scale(size*2, size*2, Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(target)
    image = reader.read()
    if not image.isNull() and max(image.width(), image.height()) > size*2:
        image = image.scaled(size*2, size*2, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    image.setDevicePixelRatio(2)
    return image


def decode_batch(paths, size):
    if len(paths) < 4:
        return [decode_image(path, size) for path in paths]
    from concurrent.futures import ThreadPoolExecutor
    # All requested previews finish in this operation. No work is moved to
    # scrolling/another visit, no worker touches widgets or the SQLite owner.
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='pcs-preview') as pool:
        return list(pool.map(lambda path: decode_image(path, size), paths))
