"""The same global destinations, arranged beside or above the workspace."""
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QBoxLayout, QFrame, QSizePolicy


DESTINATIONS = (
    ('home', '首頁', 'home'), ('gallery', '媒體庫', 'media'),
    ('explore', '探索', 'compass'), ('export', '匯出', 'export'),
    ('settings', '設定', 'settings'),
)


class WorkspaceNavigation(QFrame):
    def __init__(self, workspace):
        super().__init__()
        self.setObjectName('DocumentRail')
        self.routes = {}
        self.body = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        for key, title, symbol in DESTINATIONS:
            entry = workspace.tool(symbol, title,
                lambda checked=False, route=key: workspace.open_route(route))
            entry.setCheckable(True)
            self.routes[key] = entry
        self.arrange('side')

    def arrange(self, position):
        horizontal = position == 'top'
        self.horizontal = horizontal
        while self.body.count():
            self.body.takeAt(0)
        self.body.setDirection(QBoxLayout.Direction.LeftToRight if horizontal
                               else QBoxLayout.Direction.TopToBottom)
        self.body.setContentsMargins(0, 0, 0, 4) if horizontal else self.body.setContentsMargins(7, 5, 7, 9)
        self.body.setSpacing(6)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        if not horizontal:
            self.setFixedWidth(52)
        for key, title, _ in DESTINATIONS:
            entry = self.routes[key]
            entry.setText(title if horizontal else '')
            entry.setMinimumWidth(0)
            entry.setMaximumWidth(16777215)
            entry.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            entry.setStyleSheet('padding:0 10px;' if horizontal else 'padding:0;')
            if not horizontal:
                entry.setFixedWidth(38)
            if key == 'settings' and not horizontal:
                self.body.addStretch()
            self.body.addWidget(entry)
            entry.show()
        if horizontal:
            self.body.addStretch()

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        return QSize(0 if getattr(self, 'horizontal', False) else 52, size.height())

    def adapt_width(self, available):
        if not self.horizontal:
            return
        preferred = sum(self.routes[key].fontMetrics().horizontalAdvance(title)+50
                        for key, title, _ in DESTINATIONS) + 24
        compact = available < preferred
        for key, title, _ in DESTINATIONS:
            self.routes[key].setText('' if compact else title)

