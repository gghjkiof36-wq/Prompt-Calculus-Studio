"""Compatibility facade for the shared, size-keyed, byte-bounded preview pool."""
from PySide6.QtCore import QObject, Signal
from .preview_loader import PreviewAdapter, close_window


class Thumbnails(QObject):
    ready = Signal(object, object)

    def __init__(self, parent, window):
        super().__init__(parent)
        self.adapter = PreviewAdapter(parent, window, window.store, 'picker')
        self.adapter.ready.connect(lambda key, image, status: self.ready.emit(key, image))

    def cancel(self, *_):
        self.adapter.cancel()

    def load(self, paths):
        self.adapter.load([(key, path, '', 144, 108, 'fit') for key, path in paths])
