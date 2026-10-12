"""A native, temporary editor at the card's existing weight number."""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLineEdit, QGraphicsProxyWidget


def submitted_weight(text):
    """Return tenths; incomplete/invalid text is handled only on submission."""
    text = text.strip()
    if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', text):
        return 10
    try:
        value = max(Decimal('-100'), min(Decimal('100'), Decimal(text)))
        return int((value * 10).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return 10


class WeightEdit(QLineEdit):
    def __init__(self, card):
        super().__init__(f"{card.value['weight'] / 10:.1f}")
        self.card = card
        self.finished = False
        self.composing = False
        self.owner = (card.canvas.window.store, card.canvas.window.state['workspace'])
        self.setAccessibleName('權重')
        self.setFrame(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(card.canvas.font())
        from .canvas_items import canvas_colors
        colors = canvas_colors(card.canvas)
        self.setStyleSheet(f"QLineEdit {{background:{colors['surface']}; color:{colors['text']}; border:0; border-radius:6px; padding:0; selection-background-color:{colors['selected']};}}")

    def keyPressEvent(self, event):
        if self.composing and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            super().keyPressEvent(event); return
        if event.key() == Qt.Key.Key_Escape:
            self.finish(False); event.accept(); return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish(True); event.accept(); return
        super().keyPressEvent(event)

    def inputMethodEvent(self,event):
        self.composing=bool(event.preeditString())
        super().inputMethodEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.finish(True)

    def finish(self, save):
        if self.finished:
            return
        self.finished = True
        card = self.card; canvas = card.canvas; key = card.key
        value = submitted_weight(self.text())
        prior = card.value['weight']
        self.hide()
        proxy = card.weight_editor
        proxy.hide(); card.weight_editor = None; proxy.deleteLater()
        card.update()
        if save and value != prior:
            owner = self.owner
            def apply():
                if owner == (canvas.window.store, canvas.window.state['workspace']) and canvas.cards.get(key) is card:
                    canvas.change_weight(key, value=value)
            QTimer.singleShot(0, apply)


def edit_weight(card):
    if getattr(card, 'weight_editor', None) is not None:
        card.weight_editor.widget().setFocus(); return
    proxy = QGraphicsProxyWidget(card)
    card.weight_editor = proxy
    edit = WeightEdit(card)
    proxy.setWidget(edit)
    proxy.setGeometry(card.weight_rect)
    proxy.setZValue(10)
    edit.setFocus(Qt.FocusReason.MouseFocusReason)
    edit.selectAll()
