"""Display-only typed queue stamp cache; retain the original synchronous reader.

The background candidate did not meet the strict warm gate. Evidence and the
discarded candidate are in qa/03k-prealpha3-p6-2-20261117/p6-2a/REPORT.md.
"""
from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from shiboken6 import isValid

from .preview_loader import scope
from .preview_requests import stamp, use_worker


def read_icon(path):
    if not path:
        return QIcon()
    reader = QImageReader(str(path))
    reader.setScaledSize(QSize(52, 52))
    return QIcon(QPixmap.fromImage(reader.read()))


class QueueIconCache:
    def __init__(self, parent, window, owner):
        self.parent, self.window, self.owner = parent, window, owner
        self.context = None
        self.cache = {}

    def owner_context(self):
        return scope(self.window, self.window.store), self.owner(), self.parent.devicePixelRatioF()

    def current(self):
        return self.context == self.owner_context()

    def load(self, rows):
        """Rows contain an entry id, target item, resolved path and source receipt.

        Like the original signature shortcut, unchanged entries need no reads.
        On a status/entry refresh, compare each expensive source's file stamp.
        Cheap-image stamp overhead failed the first-open gate, so those retain
        the original reader path. No document, journal or receipt is changed.
        """
        if self.window.is_closed():
            self.cache.clear()
            return
        context = self.owner_context()
        if self.context != context:
            self.cache.clear()
            self.context = context
        retained = {}
        for ident, target, path, source in rows:
            if not isValid(target):
                continue
            if path and use_worker(path, source=source):
                key = str(path), stamp(path), (source or {}).get('sha256', '')
                old = self.cache.get(ident)
                if old is not None and old[0] == key:
                    if old[1] is not target:
                        target.setIcon(old[2])
                    retained[ident] = key, target, old[2]
                    continue
                icon = read_icon(path)
                retained[ident] = key, target, icon
            else:
                icon = read_icon(path)
            target.setIcon(icon)
        self.cache = retained
