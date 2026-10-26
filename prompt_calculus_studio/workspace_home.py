"""Centered creation actions and the user's real recent documents."""
from pathlib import Path
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QWidget, QScrollArea, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QFrame
from .theme import visual_tokens
from .workspace_style import workspace_colors
from .ui_icons import icon
from .widgets import button, label


class WorkspaceHome(QScrollArea):
    def __init__(self, root, recent, settings, choose_file, open_file, create_file,
                 *, recoveries=(), recover_file=None):
        super().__init__()
        self.root = root
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setObjectName('ScrollViewport')
        canvas=QWidget()
        canvas.setObjectName('WorkspaceHomeCanvas')
        self.setWidget(canvas)
        outer = QVBoxLayout(canvas)
        outer.setContentsMargins(32, 28, 32, 28)
        outer.addStretch(2)
        center = QHBoxLayout()
        center.addStretch()
        self.content = QWidget()
        self.content.setMaximumWidth(620)
        body = QVBoxLayout(self.content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        self.recover_file = recover_file
        self.recovery_heading = label('未儲存的內容', 'WorkspaceSectionTitle')
        body.addWidget(self.recovery_heading)
        self.recovery_listing = QListWidget()
        self.recovery_listing.setObjectName('WorkspaceRecent')
        self.recovery_listing.setFrameShape(QFrame.Shape.NoFrame)
        self.recovery_listing.setIconSize(QSize(18, 18))
        self.recovery_listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recovery_listing.setAccessibleName('未儲存的內容')
        self.recovery_listing.itemClicked.connect(self.open_recovery)
        self.recovery_listing.itemActivated.connect(self.open_recovery)
        body.addWidget(self.recovery_listing)
        self.recent_heading = label('最近開啟', 'WorkspaceSectionTitle')
        body.addWidget(self.recent_heading)
        self.listing = QListWidget()
        self.listing.setObjectName('WorkspaceRecent')
        self.listing.setFrameShape(QFrame.Shape.NoFrame)
        self.listing.setIconSize(QSize(18, 18))
        self.listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.listing.setAccessibleName('最近開啟的文件')
        self.listing.itemClicked.connect(lambda item: open_file(item.data(Qt.ItemDataRole.UserRole)))
        self.listing.itemActivated.connect(lambda item: open_file(item.data(Qt.ItemDataRole.UserRole)))
        body.addWidget(self.listing)
        body.addSpacing(18)
        body.addWidget(label('新增', 'WorkspaceSectionTitle'))
        self.create_buttons = {}
        from .workspace_commands import NAMES
        for kind, symbol in (('category', 'list'), ('workflow', 'workflow'), ('markdown', 'file-text'), ('chat', 'message-square')):
            title = '新增' + NAMES[kind]
            entry = button(title, lambda checked=False, k=kind: create_file(k), 'WorkspaceCreate')
            entry.setIcon(icon(symbol, 'navigation'))
            entry.setIconSize(QSize(19, 19))
            entry.setMinimumHeight(52)
            entry.setAccessibleName(title)
            body.addWidget(entry)
            self.create_buttons[kind] = entry
        footer = QHBoxLayout()
        self.open_button = button('開啟檔案', choose_file, 'Quiet')
        self.open_button.setIcon(icon('search', 'navigation'))
        self.open_button.setToolTip('開啟檔案 (Ctrl+O)')
        footer.addWidget(self.open_button)
        footer.addStretch()
        body.addSpacing(10)
        body.addLayout(footer)
        center.addWidget(self.content, 1)
        center.addStretch()
        outer.addLayout(center)
        outer.addStretch(3)
        self.apply_appearance(settings)
        self.set_recoveries(recoveries)
        self.set_recent(recent)

    def open_recovery(self, item):
        if self.recover_file is not None:
            self.recover_file(item.data(Qt.ItemDataRole.UserRole))

    def set_recoveries(self, recoveries):
        self.recovery_listing.clear()
        seen = set()
        for location in recoveries:
            path = Path(location)
            identity = str(path).casefold()
            if identity in seen:
                continue
            seen.add(identity)
            item = QListWidgetItem(path.stem)
            item.setIcon(icon({'.pcs-category': 'list', '.pcs-workflow': 'workflow', '.pcs-chat': 'message-square'}.get(path.suffix, 'file-text'), 'navigation'))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip('原位置：' + str(path))
            self.recovery_listing.addItem(item)
        count = self.recovery_listing.count()
        self.recovery_listing.setFixedHeight(min(count, 4)*40+4)
        self.recovery_listing.setVisible(bool(count))
        self.recovery_heading.setVisible(bool(count))

    def apply_appearance(self, settings):
        t = visual_tokens(settings)
        surfaces = workspace_colors(settings)
        self.setStyleSheet(f'''
            QWidget#WorkspaceHomeCanvas {{ background:{surfaces['content']}; }}
            QLabel#WorkspaceSectionTitle {{ font-size:14px; font-weight:600; color:{t['text']}; }}
            QPushButton#WorkspaceCreate {{ text-align:left; padding:12px 18px; border:0; border-radius:7px;
                background:{t['surface']}; color:{t['text']}; font-size:14px; }}
            QPushButton#WorkspaceCreate:hover {{ background:{t['raised']}; }}
            QPushButton#WorkspaceCreate:pressed {{ background:{t['selected']}; }}
            QPushButton#WorkspaceCreate:focus {{ border:1px solid {t['accent']}; }}
            QListWidget#WorkspaceRecent {{ background:transparent; border:0; outline:0; }}
            QListWidget#WorkspaceRecent::item {{ height:36px; padding:0 10px; border-radius:5px; }}
            QListWidget#WorkspaceRecent::item:hover {{ background:{t['hover']}; }}
            QListWidget#WorkspaceRecent::item:selected {{ background:{t['selected']}; color:{t['text']}; }}
        ''')

    def set_recent(self, recent):
        self.listing.clear()
        for location in recent[:5]:
            path = Path(location)
            title = path.stem if path.parent in (Path('.'), self.root) else path.stem + ' — ' + str(path.parent)
            item = QListWidgetItem(title)
            item.setIcon(icon({'.pcs-category': 'list', '.pcs-workflow': 'workflow', '.pcs-chat': 'message-square'}.get(path.suffix, 'file-text'), 'navigation'))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path if path.is_absolute() else self.root/path))
            self.listing.addItem(item)
        self.listing.setFixedHeight(len(recent[:5])*40+4)
        self.listing.setVisible(bool(recent))
        self.recent_heading.setVisible(bool(recent))
