"""The established compact connection control, shared by both window shells."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QHBoxLayout
from .widgets import label
from .theme import visual_tokens


class ConnectionPill(QPushButton):
    def __init__(self, request):
        super().__init__()
        self.setObjectName('ConnectionPill')
        self.clicked.connect(request)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        status = QHBoxLayout(self)
        status.setContentsMargins(11, 0, 11, 0)
        status.setSpacing(8)
        self.dot = label('')
        self.dot.setFixedSize(10, 10)
        self.brand = label('ComfyUI · 未連線', 'Subtle')
        for child in (self.dot, self.brand):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            status.addWidget(child)

    def refresh(self, settings, client=None):
        connected = bool(getattr(client, 'connected', False))
        checking = bool(getattr(client, 'connection_checking', False))
        self.short = '連線中…' if checking else '已連線' if connected else '未連線'
        self.full = 'ComfyUI · ' + self.short
        self.brand.setText(self.full)
        self.setAccessibleName(self.full)
        self.setToolTip(self.full + '\n' + ('正在確認連線，請稍候。' if checking else '使用已儲存的位址重新檢查並連線。'))
        self.setEnabled(not checking)
        colors = visual_tokens(settings)
        self.dot.setStyleSheet('background:' + colors['warning' if checking else 'connected_indicator' if connected else 'control'] + '; border-radius:5px;')
        self.brand.ensurePolished()
        height = max(32, self.brand.fontMetrics().height() + 12)
        self.setFixedHeight(height)
        self.setStyleSheet(f"""
            QPushButton#ConnectionPill {{ background:{colors['surface']}; border:1px solid {colors['line']}; border-radius:{height//2}px; padding:0; }}
            QPushButton#ConnectionPill:hover {{ background:{colors['hover']}; }}
            QPushButton#ConnectionPill:pressed {{ background:{colors['selected']}; }}
            QPushButton#ConnectionPill[pcsKeyboardFocus="true"]:focus {{ border-color:{colors['accent']}; }}
            QPushButton#ConnectionPill:disabled {{ background:{colors['surface']}; }}
        """)
        self.setFixedWidth(22 + self.dot.width() + 8 + self.brand.fontMetrics().horizontalAdvance(self.full))
