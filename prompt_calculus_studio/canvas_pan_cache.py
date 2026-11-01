"""A bounded, temporary raster backing for a Canvas camera gesture.

The view owns interaction and still moves its real scrollbars. Call begin/end
at camera-drag boundaries and paint with an identity viewport painter before
normal scene painting. A False result requires the normal paint path. Pass an
immutable appearance key when colors depend on state outside Qt's palette.

During a drag, translation rounds to the nearest physical pixel (at most half
a pixel per axis), without interpolation or camera snapping. Wheel gestures
can briefly resample a fully covering backing within a bounded scale range.
After 120 ms without wheel input, normal painting restores exact text/edges.
"""
import math
import weakref
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QGraphicsProxyWidget, QWidget
from shiboken6 import isValid


FRAME_BYTES = 32*1024*1024
ZOOM_FRAMES = 3
_zoom_owner = None


@dataclass(frozen=True)
class _Backing:
    image: QImage
    key: tuple
    origin: tuple
    pad: int


class PanRasterCache(QObject):
    def __init__(self, view, *, max_bytes=32*1024*1024, overscan=128):
        super().__init__(view)
        self.view = view
        self.max_bytes = min(FRAME_BYTES, max(0, int(max_bytes)))
        self.max_zoom_bytes = ZOOM_FRAMES*self.max_bytes
        self.overscan = max(0, int(overscan))
        self.active = False
        self.allow_zoom = False
        self._wheel_interactive = None
        self._pan_interactive = None
        self.zoom_timer = QTimer(self)
        self.zoom_timer.setSingleShot(True)
        self.zoom_timer.timeout.connect(self.finish_zoom)
        self.image = None
        self.captures = self.hits = 0
        self.archive_hits = 0
        self._zoom_backings = []
        self._zoom_scope = None
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
        if self._zoom_backings:
            # The active image aliases one of these entries, never a fourth.
            return sum(backing.image.sizeInBytes() for backing in self._zoom_backings)
        return self.image.sizeInBytes() if self.image is not None else 0

    def begin(self):
        if getattr(self.view, 'pan', None) is not None and self._pan_interactive is None:
            # Camera scrolling makes Qt replay its last scene mouse move.
            # Suspend hover for the owned pan, just as for wheel zoom, while
            # retaining all scene/widget invalidations for real content edits.
            if self.allow_zoom:
                self.end()  # Restore wheel policy before transferring ownership.
            self._pan_interactive = self.view.isInteractive()
            self.view.setInteractive(False)
        if self.active:
            return
        self.active = True
        self._sync_scene()

    def begin_zoom(self):
        """Reuse camera pixels only while wheel input continues, never at rest."""
        scene = self.view.scene()
        if getattr(self.view, 'pan', None) is not None or (scene is not None and scene.mouseGrabberItem() is not None):
            return
        global _zoom_owner
        previous = _zoom_owner() if _zoom_owner is not None else None
        if previous is not None and previous is not self and previous.allow_zoom:
            try:
                previous.finish_zoom()
            except RuntimeError:  # A deleted C++ view can outlive its wrapper.
                previous._zoom_backings.clear()
                previous.image = None
        _zoom_owner = weakref.ref(self)
        if self._wheel_interactive is None:
            self._wheel_interactive = self.view.isInteractive()
            # Qt replays the last mouse move after camera transforms. Hover
            # changes on embedded controls otherwise discard each wheel frame.
            # This flag only gates scene input; focus and live updates remain.
            self.view.setInteractive(False)
        self.allow_zoom = True
        self.begin()
        self.zoom_timer.start(120)

    def finish_zoom(self):
        self._release_zoom_owner()
        self._restore_interaction()
        self.allow_zoom = False
        if getattr(self.view, 'pan', None) is not None:
            self.invalidate()
        else:
            self.end()

    def _release_zoom_owner(self):
        global _zoom_owner
        if _zoom_owner is not None and _zoom_owner() is self:
            _zoom_owner = None

    def _restore_interaction(self):
        if self._wheel_interactive is not None:
            interactive, self._wheel_interactive = self._wheel_interactive, None
            self.view.setInteractive(interactive)

    def end(self):
        was_active = self.active
        self._release_zoom_owner()
        self._restore_interaction()
        if self._pan_interactive is not None:
            interactive, self._pan_interactive = self._pan_interactive, None
            self.view.setInteractive(interactive)
        self.zoom_timer.stop()
        self.allow_zoom = False
        self.active = False
        self.invalidate()
        self._disconnect_scene()
        self._unwatch_widgets()
        if was_active:
            self.view.viewport().update()

    def invalidate(self, *_):
        # Real scene/widget changes invalidate every scale, including a frame
        # currently being captured. Camera-only misses keep the other scales.
        self._zoom_backings.clear()
        self._zoom_scope = None
        self._clear_current()

    def _clear_current(self):
        had_image = self.image is not None
        self.image = None
        self._key = self._origin = None
        self._revision += 1
        if self.active and had_image:
            self.view.viewport().update()

    def _zoom_target(self, backing, matrix, key, rect, dpr):
        if key[:3] != backing.key[:3] or key[5:] != backing.key[5:]:
            return None
        ratio_x, ratio_y = matrix.m11()/backing.key[3], matrix.m22()/backing.key[4]
        x = (matrix.dx()-ratio_x*backing.origin[0])*dpr-backing.pad*ratio_x
        y = (matrix.dy()-ratio_y*backing.origin[1])*dpr-backing.pad*ratio_y
        candidate = QRectF(x,y,backing.image.width()*ratio_x,backing.image.height()*ratio_y)
        if (.75 <= ratio_x <= 1.5 and abs(ratio_x-ratio_y)<1e-9
                and candidate.contains(QRectF(0,0,math.ceil(rect.width()*dpr),math.ceil(rect.height()*dpr)))):
            return candidate
        return None

    def _select_zoom_backing(self, matrix, key, rect, dpr):
        scope = key[:3], key[5:]
        if self._zoom_scope != scope:
            self._zoom_backings.clear()
            self._zoom_scope = scope
        for index in range(len(self._zoom_backings)):
            target = self._zoom_target(self._zoom_backings[index], matrix, key, rect, dpr)
            if target is not None:
                backing = self._zoom_backings.pop(index)
                self._zoom_backings.insert(0, backing)
                if self.image is not backing.image:
                    self.archive_hits += 1
                self.image, self._key = backing.image, backing.key
                self._origin, self._pad = backing.origin, backing.pad
                self.hits += 1
                return target
        self._clear_current()
        # Evict BEFORE allocating, bounding both retained and in-flight frames.
        while self._zoom_backings and (len(self._zoom_backings) >= ZOOM_FRAMES
                or self.byte_count+self.max_bytes > self.max_zoom_bytes):
            self._zoom_backings.pop()
        return None

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

    def _watch_widgets(self, refresh_cache):
        # Only rescan on a capture, never on a buffer hit. Widget-only updates
        # must invalidate too, including synchronous repaint before changed.
        self._unwatch_widgets()
        if self._scene is None:
            return
        for item in self._scene.items():
            if isinstance(item, QGraphicsProxyWidget) and item.widget() is not None:
                if refresh_cache and item.cacheMode() == item.CacheMode.DeviceCoordinateCache:
                    # Qt keeps separate viewport and QImage pixmaps for this
                    # proxy, but shares their dirty region. A live viewport
                    # paint can consume an edit before the next camera capture.
                    # Reapplying the same mode clears the old target pixmaps.
                    item.setCacheMode(item.CacheMode.DeviceCoordinateCache)
                root = item.widget()
                self._widgets.extend([root, *root.findChildren(QWidget)])
        for widget in self._widgets:
            widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if not self.active:
            return False
        if not isValid(self.view):
            # Child destruction events can outlast the QGraphicsView wrapper.
            self._release_zoom_owner()
            self._zoom_backings.clear()
            self.image = None
            return False
        kind = event.type()
        if self.allow_zoom and (kind in (
                QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride,
                QEvent.Type.InputMethod, QEvent.Type.ContextMenu,
                QEvent.Type.DragEnter, QEvent.Type.TouchBegin)
                or (kind == QEvent.Type.MouseMove and event.buttons() != Qt.MouseButton.NoButton)):
            # Restore before Qt dispatches this actual action to scene items.
            # Returning False keeps clicks, editing, undo and drags intact.
            self.finish_zoom()
            return False
        if kind == QEvent.Type.LayoutRequest and watched in (self.view,self.view.viewport()):
            # Camera changes reposition viewport overlays. Those controls are
            # not in view.render's scene backing; their layout is independent.
            # Scene/proxy layouts remain watched, and viewport size is keyed.
            return False
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
        # Wheel zoom also reveals content on all four edges. The existing
        # byte budget remains authoritative, including on high-DPI screens.
        low, high = 0, round((max(self.overscan,512) if self.allow_zoom else self.overscan)*dpr)
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
        previous_key = self._key
        target = None
        if self.allow_zoom:
            target = self._select_zoom_backing(matrix, key, rect, dpr)
        if target is not None:
            painter.save()
            try:
                painter.setClipRect(QRectF(rect), Qt.ClipOperation.IntersectClip)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawImage(QRectF(target.x()/dpr,target.y()/dpr,target.width()/dpr,target.height()/dpr), self.image)
            finally:
                painter.restore()
            return True
        if self.image is not None and key == self._key:
            dx = round((matrix.dx()-self._origin[0])*dpr)
            dy = round((matrix.dy()-self._origin[1])*dpr)
            if abs(dx) <= self._pad and abs(dy) <= self._pad:
                self.hits += 1
            else:
                self.invalidate()
        elif not self.allow_zoom:
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
            # Ordinary camera movement can reuse current widget content when
            # recapturing past an edge. Refresh it for a new/invalidated frame.
            refresh_cache = (previous_key is None or previous_key[:3] != key[:3]
                             or previous_key[5] != appearance_key)
            self._watch_widgets(refresh_cache)
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
            if self.allow_zoom:
                self._zoom_backings.insert(0, _Backing(image, key, self._origin, pad))
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
