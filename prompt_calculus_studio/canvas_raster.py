"""Bounded, device-resolution reuse of unchanged Canvas paint layers.

This does not cache widgets, scene state or hit areas. Full viewport updates
remain enabled; only the expensive rasterization inside a paint is reused.
"""
from collections import OrderedDict
import math

from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage, QPainter, QTransform


class RasterCache:
    def __init__(self, max_bytes=48 * 1024 * 1024, max_entry_bytes=2 * 1024 * 1024,
                 max_entries=256):
        self.max_bytes = max_bytes
        self.max_entry_bytes = min(max_entry_bytes, max_bytes)
        self.max_entries = max_entries
        self.entries = OrderedDict()
        self.byte_count = 0

    def get(self, key):
        image = self.entries.get(key)
        if image is not None:
            self.entries.move_to_end(key)
        return image

    def put(self, key, image):
        size = image.sizeInBytes()
        if size > self.max_entry_bytes:
            return
        previous = self.entries.pop(key, None)
        if previous is not None:
            self.byte_count -= previous.sizeInBytes()
        while self.entries and (self.byte_count + size > self.max_bytes
                                or len(self.entries) >= self.max_entries):
            self.byte_count -= self.entries.popitem(last=False)[1].sizeInBytes()
        self.entries[key] = image
        self.byte_count += size

    def clear(self):
        self.entries.clear()
        self.byte_count = 0


# A process-wide byte budget also bounds multiple canvases and old zoom levels.
# Keys contain only visual values, never references to windows, items or models.
LAYERS = RasterCache()
# Large container shadows must not evict the text cards during a fractional
# pan. Oversized shadows use the original vectors, with no image allocation.
SHADOWS = RasterCache(max_bytes=8 * 1024 * 1024, max_entry_bytes=512 * 1024)


def paint_cached(painter, key, bounds, draw, cache=LAYERS):
    transform = painter.deviceTransform()
    sx, sy = transform.m11(), transform.m22()
    if (not transform.isAffine() or transform.m12() or transform.m21()
            or sx <= 0 or sy <= 0
            or painter.compositionMode() != QPainter.CompositionMode.CompositionMode_SourceOver):
        draw(painter)
        return
    rect = transform.mapRect(bounds)
    left, top = math.floor(rect.left()), math.floor(rect.top())
    width, height = math.ceil(rect.right()) - left, math.ceil(rect.bottom()) - top
    # Very large/zoomed surfaces stay vector drawn instead of allocating huge
    # temporary images or evicting every small card on each frame.
    if width <= 0 or height <= 0 or width * height * 4 > cache.max_entry_bytes:
        draw(painter)
        return
    dx, dy = transform.dx() - left, transform.dy() - top
    device = painter.device()
    dpi = device.logicalDpiX(), device.logicalDpiY()
    token = (key, width, height, round(sx, 9), round(sy, 9),
             round(dx, 6), round(dy, 6), dpi, painter.renderHints().value,
             painter.font().key())
    image = cache.get(token)
    if image is None:
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        if image.isNull():
            draw(painter)
            return
        image.fill(0)
        image.setDotsPerMeterX(round(dpi[0] / .0254))
        image.setDotsPerMeterY(round(dpi[1] / .0254))
        local = QPainter(image)
        try:
            local.setRenderHints(painter.renderHints())
            local.setFont(painter.font())
            local.setPen(painter.pen())
            local.setBrush(painter.brush())
            local.setWorldTransform(QTransform(sx, 0, 0, sy, dx, dy))
            draw(local)
        finally:
            local.end()
        cache.put(token, image)
    # Composite at physical pixel coordinates: no resampling of text on pan,
    # and a new raster for each zoom/DPR/subpixel phase, not a stretched bitmap.
    painter.save()
    painter.resetTransform()
    painter.setWorldTransform(painter.deviceTransform().inverted()[0])
    painter.drawImage(QPointF(left, top), image)
    painter.restore()
