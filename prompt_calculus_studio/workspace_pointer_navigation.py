"""Mouse side buttons use the workspace's existing navigation controls."""
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QApplication, QWidget


class WorkspacePointerNavigation(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                        QEvent.Type.MouseButtonDblClick):
            return False
        button = event.button()
        if button not in (Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton):
            return False
        if not isinstance(watched, QWidget):
            return False
        owner = watched
        while owner is not None:
            if owner is self.workspace or self.workspace.isAncestorOf(owner):
                break
            proxy = owner.graphicsProxyWidget()
            if proxy is not None and proxy.scene() is not None and any(
                    self.workspace.isAncestorOf(view) for view in proxy.scene().views()):
                break
            # RoundMenu is intentionally a top-level popup to avoid inheriting
            # document QSS; its appearance owner also identifies its workspace.
            owner = getattr(owner, '_appearance_owner', None) or owner.parentWidget()
        if owner is None:
            return False
        if QApplication.activeModalWidget() or QApplication.activePopupWidget():
            # QMenu can activate its hovered row for an unhandled side button.
            # Ignore the gesture here, rather than dispatching it into a menu.
            event.accept()
            return True
        # Consume the entire gesture, including on Canvas, so it cannot also
        # select/pan a document or propagate into a second navigation action.
        event.accept()
        if kind == QEvent.Type.MouseButtonRelease:
            control = (self.workspace.back if button == Qt.MouseButton.BackButton
                       else self.workspace.forward)
            control.click()  # Disabled controls at either history boundary do nothing.
        return True
