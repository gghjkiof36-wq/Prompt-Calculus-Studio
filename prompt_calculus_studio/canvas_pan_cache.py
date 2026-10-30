"""A bounded, temporary raster backing for a Canvas camera drag.

The view owns interaction and still moves its real scrollbars. Call begin/end
at camera-drag boundaries and paint with an identity viewport painter before
normal scene painting. A False result requires the normal paint path. Pass an
immutable appearance key when colors depend on state outside Qt's palette.

During a drag only, translation rounds to the nearest physical pixel (at most
half a pixel per axis). No image scaling/interpolation or camera snapping is
used. end() requests a normal repaint, restoring the exact subpixel position.
"""
import math

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QGraphicsProxyWidget, QWidget


class PanRasterCache(QObject):
    def __init__(self, view, *, max_bytes=32*1024*1024, overscan=128):
        super().__init__(view)
        self.view = view
        self.max_bytes = max(0, int(max_bytes))
        self.overscan = max(0, int(overscan))
        self.active = False
        self.image = None
        self.captures = self.hits = 0
        self.last_offset = (0, 0)
        self._scene = None
        self._widgets = []
        self._key = self._origin = None
        self._pad = self._revision = 0
        self._capturing = False
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)

    @property
    def byte_count(self):
        return self.image.sizeInBytes() if self.image is not None else 0

    def begin(self):
        if self.active:
            return
        self.active = True
        self._sync_scene()

    def end(self):
        was_active = self.active
        self.active = False
        self.invalidate()
        self._disconnect_scene()
        self._unwatch_widgets()
        if was_active:
            self.view.viewport().update()

    def invalidate(self, *_):
        had_image = self.image is not None
        self.image = None
        self._key = self._origin = None
        self._revision += 1
        if self.active and had_image:
            self.view.viewport().update()

    def _disconnect_scene(self):
        if self._scene is not None:
            try:
                self._scene.changed.disconnect(self.invalidate)
            except (RuntimeError, TypeError):
                pass
        self._scene = None

    def _sync_scene(self):
        scene = self.view.scene()
        if scene is not self._scene:
            self._disconnect_scene()
            self.invalidate()
            self._scene = scene
            if scene is not None:
                scene.changed.connect(self.invalidate)

    def _unwatch_widgets(self):
        for widget in self._widgets:
            try:
                widget.removeEventFilter(self)
            except RuntimeError:
                pass
        self._widgets = []

    def _watch_widgets(self):
        # Only rescan on a capture, never on a buffer hit. Widget-only updates
        # must invalidate too, including synchronous repaint before changed.
        self._unwatch_widgets()
        if self._scene is None:
            return
        for item in self._scene.items():
            if isinstance(item, QGraphicsProxyWidget) and item.widget() is not None:
                root = item.widget()
                self._widgets.extend([root, *root.findChildren(QWidget)])
        for widget in self._widgets:
            widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if not self.active:
            return False
        kind = event.type()
        if watched in (self.view, self.view.viewport()) and kind in (QEvent.Type.Hide, QEvent.Type.Close):
            self.end()
        elif kind in (QEvent.Type.Resize, QEvent.Type.StyleChange, QEvent.Type.FontChange,
                      QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange,
                      QEvent.Type.EnabledChange, QEvent.Type.DynamicPropertyChange,
                      QEvent.Type.LayoutRequest, QEvent.Type.ChildAdded):
            self.invalidate()
        elif kind in (QEvent.Type.UpdateRequest, QEvent.Type.UpdateLater,
                      QEvent.Type.Hide, QEvent.Type.Show) and watched in self._widgets:
            # Viewport update requests also come from camera movement; only
            # embedded widget requests imply changed scene content.
            self.invalidate()
        elif kind == QEvent.Type.Paint and watched in self._widgets and not self._capturing:
            self.invalidate()
        return False

    def _padding(self, width, height, dpr):
        if width*height*4 > self.max_bytes:
            return None
        low, high = 0, round(self.overscan*dpr)
        while low < high:
            middle = (low+high+1)//2
            if (width+2*middle)*(height+2*middle)*4 <= self.max_bytes:
                low = middle
            else:
                high = middle-1
        return low

    def paint(self, painter, appearance_key=None):
        """Paint the entire viewport backing, or return False for direct paint."""
        if (not self.active or self._capturing or not painter.worldTransform().isIdentity()
                or painter.opacity() != 1
                or painter.compositionMode() != QPainter.CompositionMode.CompositionMode_SourceOver):
            return False
        self._sync_scene()
        view = self.view
        if self._scene is None:
            return False
        matrix = view.viewportTransform()
        if not matrix.isAffine() or matrix.m12() or matrix.m21() or matrix.m11() <= 0 or matrix.m22() <= 0:
            self.invalidate()
            return False
        rect = view.viewport().rect()
        dpr = painter.device().devicePixelRatioF()
        if rect.isEmpty() or not math.isfinite(dpr) or dpr <= 0:
            return False
        key = (rect.width(), rect.height(), dpr, matrix.m11(), matrix.m22(), appearance_key)
        if self.image is not None and key == self._key:
            dx = round((matrix.dx()-self._origin[0])*dpr)
            dy = round((matrix.dy()-self._origin[1])*dpr)
            if abs(dx) <= self._pad and abs(dy) <= self._pad:
                self.hits += 1
            else:
                self.invalidate()
        else:
            self.invalidate()
        if self.image is None:
            width, height = math.ceil(rect.width()*dpr), math.ceil(rect.height()*dpr)
            pad = self._padding(width, height, dpr)
            if pad is None:
                return False
            image = QImage(width+2*pad, height+2*pad, QImage.Format.Format_RGB32)
            if image.isNull():
                return False
            image.setDevicePixelRatio(dpr)
            base = QColor(view.backgroundBrush().color())
            if not base.isValid():
                base = view.palette().base().color()
            base.setAlpha(255)
            image.fill(base)
            self._watch_widgets()
            revision = self._revision
            logical_pad = math.ceil(pad/dpr)
            source = QRect(-logical_pad, -logical_pad,
                           rect.width()+2*logical_pad, rect.height()+2*logical_pad)
            # Integer physical padding preserves the viewport's raster phase,
            # even when DPR does not map the logical overscan to whole pixels.
            target = QRectF(pad/dpr-logical_pad, pad/dpr-logical_pad,
                            source.width(), source.height())
            capture = QPainter(image)
            capture.setRenderHints(view.renderHints())
            self._capturing = True
            try:
                view.render(capture, target, source, Qt.AspectRatioMode.IgnoreAspectRatio)
            finally:
                capture.end()
                self._capturing = False
            if revision != self._revision:
                return False
            self.image, self._key, self._pad = image, key, pad
            self._origin = (matrix.dx(), matrix.dy())
            self.captures += 1
            dx = dy = 0
        self.last_offset = (dx, dy)
        painter.save()
        try:
            painter.setClipRect(QRectF(rect), Qt.ClipOperation.IntersectClip)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawImage(QPointF((dx-self._pad)/dpr, (dy-self._pad)/dpr), self.image)
        finally:
            painter.restore()
        return True
