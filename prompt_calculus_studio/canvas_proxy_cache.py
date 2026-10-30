"""Share a bounded part of Qt's pixmap cache between visible Canvas controls."""
import math
import weakref

from PySide6.QtGui import QPixmapCache, QTransform
from PySide6.QtWidgets import QGraphicsItem, QWidget


PROXIES = weakref.WeakSet()
CACHE_SHARE = .70


def register(proxy):
    PROXIES.add(proxy)


def cache_bytes(proxy, views):
    """Conservative full-item cost; Qt may cache less for very large items."""
    if not proxy.isVisible() or proxy.effectiveOpacity() <= 0:
        return None
    scene = proxy.scene()
    if scene is None:
        return None
    if scene not in views:
        candidates = scene.views()
        view = candidates[0] if len(candidates) == 1 else None
        if (view is not None and view.isVisible() and view.viewport().isVisible()
                and not QWidget.window(view).isMinimized()):
            views[scene] = (view.viewportTransform(), view.viewport().rect(),
                            view.viewport().devicePixelRatioF())
        else:
            views[scene] = None
    current = views[scene]
    if current is None:
        return None
    transform, viewport, dpr = current
    transform = proxy.deviceTransform(transform)
    if transform.type().value > QTransform.TransformationType.TxScale.value:
        return None
    bounds = transform.mapRect(proxy.boundingRect())
    if not all(math.isfinite(n) for n in (bounds.x(), bounds.y(), bounds.width(), bounds.height(), dpr)):
        return None
    rect = bounds.toAlignedRect().adjusted(-1, -1, 1, 1)
    if rect.isEmpty() or not rect.intersects(viewport) or dpr <= 0:
        return None
    return math.ceil(rect.width()*dpr)*math.ceil(rect.height()*dpr)*4


def configure():
    """Release ineligible caches before admitting small controls within budget.

    Keep Qt's global limit unchanged and leave 30% for icons/native styles.
    A shared scene with multiple views uses direct painting: each view would
    otherwise retain another device pixmap for every embedded widget.
    """
    proxies = list(PROXIES)
    views, eligible = {}, []
    for proxy in proxies:
        try:
            cost = cache_bytes(proxy, views)
            if cost is not None:
                eligible.append((cost, id(proxy), proxy))
        except RuntimeError:  # The C++ item can die before its Python wrapper.
            PROXIES.discard(proxy)
    budget = int(QPixmapCache.cacheLimit()*1024*CACHE_SHARE)
    selected = set()
    for cost, _, proxy in sorted(eligible):
        if cost <= budget:
            selected.add(proxy)
            budget -= cost
    for enabled in (False, True):
        mode = (QGraphicsItem.CacheMode.DeviceCoordinateCache if enabled
                else QGraphicsItem.CacheMode.NoCache)
        for proxy in proxies:
            if (proxy in selected) != enabled:
                continue
            try:
                if proxy.cacheMode() != mode:
                    proxy.setCacheMode(mode)
            except RuntimeError:
                PROXIES.discard(proxy)
