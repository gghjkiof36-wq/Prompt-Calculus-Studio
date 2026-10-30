"""Small physical-pixel tiles for standard rounded module surfaces."""
import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QTransform

from .canvas_raster import LAYERS, RasterCache


TILES = RasterCache(max_bytes=4 * 1024 * 1024, max_entry_bytes=512 * 1024,
                    max_entries=128)


def paint_rounded_surface(painter, outline, colors, header_bottom, draw):
    """Preserve corners/header pixels; extend only uniform body rows/columns.

    Nonstandard outlines and blending retain the original drawing path. The
    compressed shape loses whole physical pixels, so its opposite edges keep
    their original subpixel phase without allocating a full-card bitmap.
    """
    transform = painter.deviceTransform()
    sx, sy = transform.m11(), transform.m22()
    pen = painter.pen()
    if (outline.elementCount() != 17 or not transform.isAffine()
            or transform.m12() or transform.m21() or sx <= 0 or sy <= 0
            or painter.opacity() != 1 or painter.hasClipping()
            or painter.compositionMode() != QPainter.CompositionMode.CompositionMode_SourceOver
            or pen.style() != Qt.PenStyle.SolidLine
            or pen.brush().style() != Qt.BrushStyle.SolidPattern
            or pen.widthF() > 4):
        return False
    rect = outline.boundingRect()
    rx = outline.elementAt(3).x - rect.left()
    ry = outline.elementAt(0).y - rect.top()
    expected = QPainterPath(); expected.addRoundedRect(rect, rx, ry)
    relative_header = header_bottom - rect.top()
    if expected != outline or not 0 <= relative_header <= rect.height():
        return False

    bounds = transform.mapRect(rect.adjusted(-8, -8, 8, 14))
    left, top = math.floor(bounds.left()), math.floor(bounds.top())
    width, height = math.ceil(bounds.right()) - left, math.ceil(bounds.bottom()) - top
    # A normal full-layer cache needs only one blit. Reserve nine-slice work
    # for surfaces that would exceed its per-entry limit and redraw vectors.
    if width * height * 4 <= LAYERS.max_entry_bytes:
        return False

    # The entire header and its transition remain above the stretched body
    # row. Generous straight-edge margins also retain shadow and pen extents.
    removed_x = max(0, math.floor((rect.width() - 2 * rx - 32) * sx))
    retained_height = max(2 * ry + 32, relative_header + ry + 32)
    removed_y = max(0, math.floor((rect.height() - retained_height) * sy))
    tile_width, tile_height = width - removed_x, height - removed_y
    if (tile_width < 3 or tile_height < 3
            or tile_width * tile_height * 4 > TILES.max_entry_bytes):
        return False
    dx = transform.dx() + rect.x() * sx - left
    dy = transform.dy() + rect.y() * sy - top
    local_width = rect.width() - removed_x / sx
    local_height = rect.height() - removed_y / sy
    middle_y = (math.floor(dy + (max(relative_header, ry) + 16) * sy)
                if removed_y else tile_height // 2)
    if not 0 < middle_y < tile_height - 1:
        return False
    device = painter.device()
    dpr = device.devicePixelRatioF()
    dpi = device.logicalDpiX(), device.logicalDpiY()
    stroke = (pen.color().rgba(), pen.widthF(), pen.isCosmetic(),
              pen.capStyle().value, pen.joinStyle().value, pen.miterLimit())
    key = (tile_width, tile_height, rx, ry, relative_header, middle_y,
           tuple(sorted(colors.items())), stroke, painter.renderHints().value, dpi, dpr,
           *(round(value, 9) for value in (sx, sy, dx, dy, local_width, local_height)))
    image = TILES.get(key)
    if image is None:
        image = QImage(tile_width, tile_height, QImage.Format.Format_ARGB32_Premultiplied)
        if image.isNull():
            return False
        image.setDevicePixelRatio(dpr)
        image.setDotsPerMeterX(round(dpi[0] / .0254))
        image.setDotsPerMeterY(round(dpi[1] / .0254))
        image.fill(0)
        local = QPainter(image)
        try:
            local.setRenderHints(painter.renderHints())
            local.setPen(pen); local.setBrush(painter.brush())
            local.setWorldTransform(QTransform(sx/dpr, 0, 0, sy/dpr, dx/dpr, dy/dpr))
            shape = QPainterPath()
            shape.addRoundedRect(QRectF(0, 0, local_width, local_height), rx, ry)
            draw(local, shape, colors, relative_header)
        finally:
            local.end()
        TILES.put(key, image)

    xs = (0, tile_width // 2, tile_width // 2 + 1, tile_width)
    ys = (0, middle_y, middle_y + 1, tile_height)
    xd = (left, left + xs[1], left + xs[2] + removed_x, left + width)
    yd = (top, top + ys[1], top + ys[2] + removed_y, top + height)
    body = QColor(colors.get('canvas_body', colors['surface']))
    body.setAlpha(colors.get('canvas_alpha', 255))
    opaque_center = (removed_y and body.alpha() == 255
                     and image.pixelColor(xs[1], ys[1]) == body)
    painter.save()
    try:
        painter.resetTransform()
        painter.setWorldTransform(painter.deviceTransform().inverted()[0])
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for y in range(3):
            for x in range(3):
                target = QRectF(xd[x], yd[y], xd[x+1]-xd[x], yd[y+1]-yd[y])
                source = QRectF(xs[x], ys[y], xs[x+1]-xs[x], ys[y+1]-ys[y])
                if x == 1 and y == 1 and opaque_center:
                    painter.fillRect(target, body)
                else:
                    painter.drawImage(target, image, source)
    finally:
        painter.restore()
    return True
