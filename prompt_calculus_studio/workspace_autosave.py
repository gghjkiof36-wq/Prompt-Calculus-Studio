"""Coalesce edits per file without accepting a still-open material dialog."""
from PySide6.QtCore import QObject, QTimer


class WorkspaceAutosave(QObject):
    delay_ms = 1000

    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.timers = {}
        self.saving = set()
        self.paused = set()
        self.external_poll = QTimer(self)
        self.external_poll.setInterval(1000)
        self.external_poll.timeout.connect(self.poll_markdown)
        self.external_poll.start()

    def poll_markdown(self):
        checked = set()
        for tab in self.workspace.all_pages():
            page = tab.widget
            buffer = getattr(page, 'buffer', None)
            if (buffer is None or id(buffer) in checked or id(tab) in self.paused
                    or self.key(tab) in self.saving):
                continue
            checked.add(id(buffer))
            before = page.error
            page.refresh_external()
            if page.error and page.error != before:
                self.stop(tab)
                self.workspace.show_notice('「' + tab.title + '」' + page.error)
            elif before and not page.error:
                self.schedule(tab)

    @staticmethod
    def key(tab):
        return tab.document.key if tab.document else getattr(getattr(tab.widget, 'buffer', None), 'save_key', tab.key)

    def related(self, tab):
        return [t for t in self.workspace.all_pages() if self.key(t) == self.key(tab)]

    def schedule(self, tab):
        if tab not in self.workspace.all_pages() or id(tab) in self.paused or self.key(tab) in self.saving:
            return
        if not tab.dirty:
            self.stop(tab)
            return
        owner = tab.document or tab.widget
        # A failed write retains its editor and recovery copies. Do not keep
        # producing more copies or notifications until an explicit retry.
        if owner.error or self.editing_dialog(tab):
            self.stop(tab)
            return
        timer = self.timers.get(self.key(tab))
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda owner=tab: self.flush(next(iter(self.related(owner)), owner)))
            self.timers[self.key(tab)] = timer
        timer.start(self.delay_ms)

    @staticmethod
    def editing_dialog(tab):
        return bool(getattr(getattr(tab.widget, 'buffer', None), 'composing', False)) or any(getattr(getattr(host, 'prompt_editor', None), 'active_dialog', None) is not None
                   for host in getattr(tab.document, 'hosts', []))

    def flush(self, tab):
        self.stop(tab)
        if (tab in self.workspace.all_pages() and id(tab) not in self.paused and tab.dirty
                and not (tab.document or tab.widget).error and not self.editing_dialog(tab)):
            self.workspace.save_tab(tab)

    def stop(self, tab):
        timer = self.timers.get(self.key(tab))
        if timer is not None:
            timer.stop()

    def forget(self, tab):
        timer = self.timers.pop(self.key(tab), None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
