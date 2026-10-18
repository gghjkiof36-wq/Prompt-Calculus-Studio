"""Lightweight pane slots; editor trees keep one stable style/focus owner."""
from PySide6.QtCore import QEvent, QPoint, QRect
from PySide6.QtWidgets import QStackedWidget, QWidget


class WorkspacePages(QStackedWidget):
    def __init__(self, workspace):
        super().__init__()
        self.w = workspace
        self.anchors = {}
        self.contents = {}
        self.currentChanged.connect(self.sync_page)
        for host in (workspace.workspace_stack, workspace.home_split, workspace.pane_split):
            host.installEventFilter(self)

    def addWidget(self, page):
        if page in self.anchors:
            return self.indexOf(page)
        previous = getattr(page, '_workspace_pages', None)
        if previous is not None:
            previous.removeWidget(page)
        host = self.w.workspace_stack
        if page.parentWidget() is not host:
            page.hide()
            page.setParent(host)
        page._workspace_pages = self
        anchor = QWidget()
        self.anchors[page] = anchor
        self.contents[anchor] = page
        result = super().addWidget(anchor)
        self.sync_page()
        return result

    def removeWidget(self, page):
        anchor = self.anchors.pop(page, None)
        if anchor is None:
            return
        self.contents.pop(anchor)
        page.hide()
        page._workspace_pages = None
        super().removeWidget(anchor)
        anchor.deleteLater()
        self.sync_page()

    def indexOf(self, page):
        anchor = self.anchors.get(page)
        return super().indexOf(anchor) if anchor is not None else -1

    def currentWidget(self):
        return self.contents.get(super().currentWidget())

    def widget(self, index):
        return self.contents.get(super().widget(index))

    def setCurrentWidget(self, page):
        anchor = self.anchors.get(page)
        if anchor is not None:
            if super().currentWidget() is anchor:
                self.sync_page()
            else:
                # currentChanged already synchronizes the editors.
                super().setCurrentWidget(anchor)

    def sync_page(self, *_):
        current = self.currentWidget()
        visible = self.isVisible()
        for page in self.anchors:
            if page is current and visible:
                page.setGeometry(QRect(self.mapTo(self.w.workspace_stack, QPoint()), self.size()))
                page.show()
                prepare = getattr(page, '_prepare_workspace_show', None)
                if prepare is not None:
                    page._prepare_workspace_show = None
                    prepare()
                page.stackUnder(self.w.workspace_stack.edge)
            else:
                page.hide()
        if hasattr(self.w, 'pane_grip'):
            self.w.pane_grip.request_sync()

    def event(self, event):
        result = super().event(event)
        if hasattr(self, 'anchors') and event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide):
            self.sync_page()
        return result

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide):
            self.sync_page()
        return False
