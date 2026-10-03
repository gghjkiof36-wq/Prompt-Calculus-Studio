"""Keep button focus intact while presenting its keyboard-only focus ring."""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QPushButton, QToolButton, QWidget


def _scope(widget):
    # Popups and child overlays share the input modality of their owner. Use
    # QWidget's method explicitly: some PCS widgets expose a `window` owner.
    while widget.parentWidget() is not None:
        widget = widget.parentWidget()
    return widget


class ButtonFocusStyler(QObject):
    def __init__(self, app):
        super().__init__(app)
        app.installEventFilter(self)

    @staticmethod
    def present(widget, keyboard):
        if not isinstance(widget, (QPushButton, QToolButton)):
            return
        keyboard = bool(keyboard)
        if widget.property('pcsKeyboardFocus') == keyboard:
            return
        widget.setProperty('pcsKeyboardFocus', keyboard)
        # Dynamic properties require a style refresh. This changes neither
        # focus policy nor default/checked/pressed state.
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def set_modality(self, widget, keyboard):
        owner = _scope(widget)
        owner.setProperty('pcsKeyboardInput', bool(keyboard))
        focused = QApplication.focusWidget()
        if focused is not None and _scope(focused) is owner:
            self.present(focused, keyboard)

    def eventFilter(self, watched, event):
        if isinstance(watched, QWidget):
            kind = event.type()
            if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.TouchBegin):
                self.set_modality(watched, False)
            elif kind in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
                if event.key() not in (Qt.Key.Key_Shift, Qt.Key.Key_Control,
                                      Qt.Key.Key_Alt, Qt.Key.Key_Meta):
                    self.set_modality(watched, True)
            elif kind == QEvent.Type.FocusIn:
                reason = event.reason()
                # Qt also uses TabFocusReason when hiding an overlay moves
                # focus to the next eligible control. Only real keyboard
                # input, not that automatic restoration, enables the ring.
                if reason == Qt.FocusReason.MouseFocusReason:
                    self.set_modality(watched, False)
                self.present(watched, _scope(watched).property('pcsKeyboardInput'))
            elif kind == QEvent.Type.FocusOut:
                self.present(watched, False)
        return super().eventFilter(watched, event)


def install_button_focus_styling():
    app = QApplication.instance()
    if app is not None and not hasattr(app, '_pcs_button_focus_styler'):
        app._pcs_button_focus_styler = ButtonFocusStyler(app)
