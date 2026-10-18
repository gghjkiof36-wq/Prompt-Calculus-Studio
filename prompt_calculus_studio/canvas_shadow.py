"""Small, device-aligned shadow tiles; glyphs and widget paints stay native."""
import math

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter, QPainterPath, QTransform

from .canvas_raster import RasterCache


TILES = RasterCache(max_bytes=4 * 1024 * 1024, max_entry_bytes=1024 * 1024,
                    max_entries=128)


def paint_rounded_shadow(painter, outline, nested, strength, covered, draw):
    """Return False for shapes/transforms requiring the original vector path.

    A rounded rectangle's straight shadow edges have constant cross-sections.
    Remove only whole device pixels from their middle, render the remaining
    small rectangle at the original scale/subpixel phase, and extend those
    constant rows/columns. Corners are copied 1:1, never scaled from another
    zoom level. No full-card bitmap is allocated during continuous zoom.
    """
    transform = painter.deviceTransform()
    sx, sy = transform.m11(), transform.m22()
    if (outline.elementCount() != 17 or not transform.isAffine()
            or transform.m12() or transform.m21() or sx <= 0 or sy <= 0
            or painter.opacity() != 1
            or painter.compositionMode() != QPainter.CompositionMode.CompositionMode_SourceOver):
        return False
    rect = outline.boundingRect()
    rx = outline.elementAt(3).x - rect.left()
    ry = outline.elementAt(0).y - rect.top()
    expected = QPainterPath()
    expected.addRoundedRect(rect, rx, ry)
    if expected != outline:
        return False

    # Keep both corners, their full pen extents and an undisturbed center row.
    removed_x = max(0, math.floor((rect.width() - 2 * rx - 32) * sx))
    removed_y = max(0, math.floor((rect.height() - 2 * ry - 32) * sy))
    bounds = transform.mapRect(rect.translated(0, 3 if nested else 6).adjusted(-7, -7, 7, 7))
    left, top = math.floor(bounds.left()), math.floor(bounds.top())
    width, height = math.ceil(bounds.right()) - left, math.ceil(bounds.bottom()) - top
    tile_width, tile_height = width - removed_x, height - removed_y
    if (tile_width < 3 or tile_height < 3
            or tile_width * tile_height * 4 > TILES.max_entry_bytes):
        return False
    dx, dy = transform.dx() + rect.x() * sx - left, transform.dy() + rect.y() * sy - top
    local_width, local_height = rect.width() - removed_x / sx, rect.height() - removed_y / sy
    key = (tile_width, tile_height, rx, ry, nested, strength, painter.renderHints().value,
           *(round(value, 9) for value in (sx, sy, dx, dy, local_width, local_height)))
    image = TILES.get(key)
    if image is None:
        image = QImage(tile_width, tile_height, QImage.Format.Format_ARGB32_Premultiplied)
        if image.isNull():
            return False
        image.fill(0)
        local = QPainter(image)
        try:
            local.setRenderHints(painter.renderHints())
            local.setTransform(QTransform(sx, 0, 0, sy, dx, dy))
            shape = QPainterPath()
            shape.addRoundedRect(QRectF(0, 0, local_width, local_height), rx, ry)
            draw(local, shape, nested, strength, False)
        finally:
            local.end()
        TILES.put(key, image)

    xs = (0, tile_width // 2, tile_width // 2 + 1, tile_width)
    ys = (0, tile_height // 2, tile_height // 2 + 1, tile_height)
    xd = (left, left + xs[1], left + xs[2] + removed_x, left + width)
    yd = (top, top + ys[1], top + ys[2] + removed_y, top + height)
    center = QRectF(xd[1], yd[1], xd[2] - xd[1], yd[2] - yd[1])
    skip_center = covered and outline.contains(transform.inverted()[0].mapRect(center))
    painter.save()
    try:
        painter.resetTransform()
        painter.setWorldTransform(painter.deviceTransform().inverted()[0])
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        for y in range(3):
            for x in range(3):
                if skip_center and x == 1 and y == 1:
                    continue
                target = QRectF(xd[x], yd[y], xd[x + 1] - xd[x], yd[y + 1] - yd[y])
                source = QRectF(xs[x], ys[y], xs[x + 1] - xs[x], ys[y + 1] - ys[y])
                painter.drawImage(target, image, source)
    finally:
        painter.restore()
    return True
