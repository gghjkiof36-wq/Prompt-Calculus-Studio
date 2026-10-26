"""User-controlled tool approval preference; ordinary text needs no approval."""
from PySide6.QtCore import Qt, QSize, QRect, QPoint, Signal
from PySide6.QtWidgets import QApplication, QFrame, QGridLayout, QPushButton, QWidgetAction

from ..theme import font_pixels, visual_tokens
from ..ui_icons import icon
from ..widgets import RoundMenu, label, widget_global_position


CHOICES = (
    ('off', '關閉', '停用對話中的工具操作', 'ban'),
    ('ask', '每次詢問', '每項工具操作先由你核准', 'hand'),
    ('auto', '自動審查', '安全的本機讀取自動通過，其餘先詢問', 'shield-check'),
    ('all', '全部允許', '已支援的工具操作不再逐項詢問', 'shield-alert'),
)


class ApprovalMenu(RoundMenu):
    def __init__(self, control):
        super().__init__(control)
        self.control = control
        self.setObjectName('ChatApprovalMenu')
        self.entries = []
        tokens = control.tokens
        width = min(max(330, control.text_pixels * 23), max(260, control.window().width() - 32))
        self._caption('如何核准工具操作？', width)
        for mode, title, description, glyph in CHOICES:
            action = QWidgetAction(self)
            action.setText(title)
            action.setData(mode)
            action.setCheckable(True)
            action.setChecked(mode == control.mode)
            action.setEnabled(control.service.state != 'closed')
            entry = QFrame(self)
            entry.setObjectName('ApprovalChoice')
            entry.setFixedWidth(width)
            entry.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout = QGridLayout(entry)
            layout.setContentsMargins(10, 6, 10, 6)
            layout.setHorizontalSpacing(8)
            layout.setVerticalSpacing(3)
            layout.setColumnStretch(1, 1)
            mark = label('')
            mark.setFixedSize(18, 18)
            color = tokens['permission_full'] if mode == 'all' else tokens['secondary']
            mark.setPixmap(icon(glyph, color, 16).pixmap(16, 16))
            layout.addWidget(mark, 0, 0, Qt.AlignmentFlag.AlignCenter)
            title_widget = label(title)
            title_widget.setStyleSheet(f'font-size:{control.text_pixels}px;')
            if mode == 'all':
                title_widget.setStyleSheet(f'font-size:{control.text_pixels}px;color:{color};')
            description_widget = label(description, 'Subtle', True)
            description_widget.setStyleSheet(f'font-size:{max(12, control.text_pixels - 1)}px;')
            title_widget.setTextFormat(Qt.TextFormat.PlainText)
            description_widget.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(title_widget, 0, 1, Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(description_widget, 1, 1)
            check = label('')
            check.setFixedSize(16, 18)
            if action.isChecked():
                check.setPixmap(icon('check', tokens['text'], 16).pixmap(16, 16))
            layout.addWidget(check, 0, 2, Qt.AlignmentFlag.AlignCenter)
            entry.setStyleSheet(
                'QFrame#ApprovalChoice {border:0;border-radius:7px;background:transparent;}'
                'QFrame#ApprovalChoice[active="true"] {background:' + tokens['popup_active'] + ';}'
                'QFrame#ApprovalChoice QLabel {background:transparent;padding:0;border:0;}')
            entry.ensurePolished()
            title_height = max(18, title_widget.fontMetrics().height())
            description_height = description_widget.fontMetrics().boundingRect(
                QRect(0, 0, width - 78, 1000), Qt.TextFlag.TextWordWrap, description).height()
            title_widget.setFixedHeight(title_height)
            description_widget.setFixedHeight(description_height)
            entry.setFixedHeight(12 + title_height + 3 + description_height)
            action.setDefaultWidget(entry)
            action.triggered.connect(lambda _checked=False, value=mode: self._choose(value))
            self.addAction(action)
            self.entries.append((action, entry))
        self.hovered.connect(self._hover)

    def _caption(self, text, width):
        caption = label(text, 'Subtle', True)
        caption.setFixedWidth(width)
        caption.setContentsMargins(10, 6, 10, 6)
        caption.setStyleSheet(f'font-size:{max(12, self.control.text_pixels - 1)}px;')
        caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        action = QWidgetAction(self)
        action.setEnabled(False)
        action.setDefaultWidget(caption)
        self.addAction(action)

    def _hover(self, active):
        for action, entry in self.entries:
            if entry.property('active') != (active is action):
                entry.setProperty('active', active is action)
                entry.style().unpolish(entry)
                entry.style().polish(entry)
                entry.update()

    def _choose(self, mode):
        self.close()
        self.control.choose(mode)

    def open_for(self, control, view=None, *, align_right=False):
        # Composer menus open upwards at the trigger. Keep the shared popup
        # appearance and desktop-coordinate mapping, including scene widgets.
        top = widget_global_position(control, QPoint(0, 0), view)
        bottom = widget_global_position(control, QPoint(control.width(), control.height()), view)
        self._prepare(top)
        size = self.sizeHint().expandedTo(self.minimumSize()).boundedTo(self.maximumSize())
        position = QPoint(bottom.x() - size.width() if align_right else top.x(),
                          top.y() - size.height() - 8)
        screen = QApplication.screenAt(top) or control.screen()
        if screen is not None:
            area = screen.availableGeometry().adjusted(4, 4, -4, -4)
            if position.y() < area.top():
                position.setY(bottom.y() + 8)
            position.setX(max(area.left(), min(position.x(), area.right() + 1 - size.width())))
            position.setY(max(area.top(), min(position.y(), area.bottom() + 1 - size.height())))
        self.open_at(position)
        active = next((a for a, _ in self.entries if a.isChecked()), None)
        if active is not None:
            self.setActiveAction(active)


class ApprovalButton(QPushButton):
    errorOccurred = Signal(str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.mode = 'ask'
        self.tokens = visual_tokens()
        self.text_pixels = font_pixels(11)
        self.setObjectName('ChatApprovalButton')
        self.setFixedHeight(32)
        self.setMinimumWidth(32)
        self.setMaximumWidth(132)
        self.setIconSize(QSize(16, 16))
        self.clicked.connect(self.open_menu)
        self.service.changed.connect(self.sync)
        self.sync()

    def sync(self):
        self.mode = getattr(self.service, 'approval_mode', 'ask')
        choice = next((entry for entry in CHOICES if entry[0] == self.mode), CHOICES[1])
        _, self.caption, description, glyph = choice
        color = self.tokens['permission_full'] if self.mode == 'all' else self.tokens['secondary']
        self.setIcon(icon(glyph, color, 16))
        self.setToolTip(self.caption + '：' + description)
        self.setAccessibleName('操作審查：' + self.caption)
        self.setEnabled(self.service.state != 'closed')
        self._apply_style(color)
        self._fit_caption()

    def choose(self, mode):
        if not self.service.configure_approval_mode(mode):
            self.errorOccurred.emit(self.service.error or '無法儲存審查方式。')
        self.sync()

    def open_menu(self):
        ApprovalMenu(self).open_for(self)

    def sizeHint(self):
        return QSize(min(132, self.fontMetrics().horizontalAdvance(getattr(self, 'caption', '每次詢問')) + 36), self.height())

    def _fit_caption(self):
        caption = getattr(self, 'caption', '')
        self.setText(caption if self.width() >= self.fontMetrics().horizontalAdvance(caption) + 36 else '')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_caption()

    def apply_appearance(self, tokens, ui_size=12):
        self.tokens = tokens
        self.text_pixels = font_pixels(max(10, ui_size - 1))
        self.setFixedHeight(max(32, self.text_pixels + 14))
        self.sync()

    def _apply_style(self, color):
        tokens = self.tokens
        self.setStyleSheet(
            f'QPushButton#ChatApprovalButton {{background:{tokens["raised"]};border:1px solid {tokens["divider"]};'
            f'border-radius:{self.height() // 2}px;padding:2px 8px;color:{color};font-size:{self.text_pixels}px;}}'
            f'QPushButton#ChatApprovalButton:hover {{background:{tokens["hover"]};}}'
            f'QPushButton#ChatApprovalButton:focus {{border-color:{tokens["accent_outline"]};}}')
