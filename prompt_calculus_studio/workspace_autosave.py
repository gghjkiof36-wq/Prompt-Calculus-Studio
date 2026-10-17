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
        return any(getattr(getattr(host, 'prompt_editor', None), 'active_dialog', None) is not None
                   for host in getattr(tab.document, 'hosts', []))

    def flush(self, tab):
        self.stop(tab)
        if (tab in self.workspace.all_pages() and id(tab) not in self.paused and tab.dirty
                and not self.editing_dialog(tab)):
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
