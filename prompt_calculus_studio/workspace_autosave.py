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

    def schedule(self, tab):
        if tab not in self.workspace.all_pages() or id(tab) in self.paused or tab.key in self.saving:
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
        timer = self.timers.get(tab.key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self.flush(tab))
            self.timers[tab.key] = timer
        timer.start(self.delay_ms)

    @staticmethod
    def editing_dialog(tab):
        editor = getattr(getattr(tab.document, 'host', None), 'prompt_editor', None)
        return getattr(editor, 'active_dialog', None) is not None

    def flush(self, tab):
        self.stop(tab)
        if (tab in self.workspace.all_pages() and id(tab) not in self.paused and tab.dirty
                and not self.editing_dialog(tab)):
            self.workspace.save_tab(tab)

    def stop(self, tab):
        timer = self.timers.get(tab.key)
        if timer is not None:
            timer.stop()

    def forget(self, tab):
        timer = self.timers.pop(tab.key, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
