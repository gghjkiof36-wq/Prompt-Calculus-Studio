"""Transient workspace messages that never reserve content space or focus."""
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import QLabel


class WorkspaceNotice(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('CanvasNotice')
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.expire = QTimer(self)
        self.expire.setSingleShot(True)
        self.expire.timeout.connect(self.hide)
        parent.installEventFilter(self)
        self.hide()

    def present(self, text, duration=6000):
        self.setText(text)
        self.reposition()
        self.show()
        self.raise_()
        self.expire.start(duration)

    def reposition(self):
        parent = self.parentWidget()
        self.setFixedWidth(min(560, max(1, parent.width()-32)))
        self.setFixedHeight(self.heightForWidth(self.width()))
        self.move((parent.width()-self.width())//2, 16)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.reposition()
        return super().eventFilter(watched, event)
