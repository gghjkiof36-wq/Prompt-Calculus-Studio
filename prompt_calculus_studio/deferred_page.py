"""Construct a document host's secondary tool only when it is requested."""
from PySide6.QtWidgets import QWidget, QVBoxLayout


class DeferredToolPage(QWidget):
    def __init__(self, factory, parent=None, *, parented=False):
        super().__init__(parent)
        self._factory = factory
        self._parented = parented
        self._page = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    @property
    def is_loaded(self):
        return self._page is not None

    def load(self):
        if self._page is None:
            self._page = self._factory(self) if self._parented else self._factory()
            self.layout().addWidget(self._page)
            self.layout().activate()
        return self._page

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self.load(), name)

    def __setattr__(self, name, value):
        # Page state (including assignments before the first visit) belongs to
        # the real page. QWidget's own API and our private fields stay here.
        if name.startswith('_') or hasattr(type(self), name):
            super().__setattr__(name, value)
        else:
            setattr(self.load(), name, value)

    def showEvent(self, event):
        self.load()
        super().showEvent(event)


def page_ready(page):
    return not isinstance(page, DeferredToolPage) or page.is_loaded
