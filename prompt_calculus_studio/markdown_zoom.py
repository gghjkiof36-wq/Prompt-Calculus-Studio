"""Handle document zoom, including WebEngine children, without scaling chrome."""
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QWidget
from .application_events import listen


class DocumentZoom(QObject):
    def __init__(self, surface):
        super().__init__(surface);self.surface = surface;self.wheel_remainder = 0
        listen(self,QEvent.Type.Wheel,QEvent.Type.ShortcutOverride,QEvent.Type.KeyPress)

    def eventFilter(self, watched, event):
        s = self.surface
        if not isinstance(watched,QWidget) or not s.isVisible():return False
        if not any(watched is view or view.isAncestorOf(watched) for view in (s.editor,s.reader)):return False
        mods = event.modifiers()
        if not mods & Qt.KeyboardModifier.ControlModifier or mods & Qt.KeyboardModifier.AltModifier:return False
        if event.type() == QEvent.Type.Wheel:
            self.wheel_remainder += event.angleDelta().y()
            steps = int(self.wheel_remainder/120)
            if steps:self.wheel_remainder -= steps*120;s.set_zoom(s.zoom_percent+steps*10)
            event.accept();return True
        key = event.key()
        if key not in (Qt.Key.Key_Plus,Qt.Key.Key_Equal,Qt.Key.Key_Minus,Qt.Key.Key_0):return False
        if event.type() == QEvent.Type.KeyPress:
            s.set_zoom(100 if key == Qt.Key.Key_0 else s.zoom_percent+(-10 if key == Qt.Key.Key_Minus else 10))
        event.accept();return True
