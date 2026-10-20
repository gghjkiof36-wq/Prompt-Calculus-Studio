"""A small, real-step startup view; expensive browsing stays demand-loaded."""
import sys

from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QApplication, QVBoxLayout, QLabel, QProgressBar, QWidget


def foreground(window):
    """Request focus only at launch, without an always-on-top window."""
    window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
    window.show()
    window.raise_()
    window.activateWindow()
    if sys.platform == 'win32' and QApplication.platformName() == 'windows':
        import ctypes
        from ctypes import wintypes
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        user.SetForegroundWindow.restype = wintypes.BOOL
        hwnd = int(window.winId())
        if not user.SetForegroundWindow(hwnd):
            # Windows may deny activation to a launcher started in the
            # background. Still reveal this explicitly launched window once.
            # Remove topmost immediately; never change the user's focus policy.
            user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user.SetWindowPos.restype = wintypes.BOOL
            flags = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
            user.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)
            try:
                user.SetForegroundWindow(hwnd)
            finally:
                user.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)


def prepare_tool_steps(window):
    """Basic surfaces only: no thumbnails, model scan, service query or run."""
    return [
        ('準備共用介面', window.ensure_utility),
        ('準備媒體庫', lambda: window.utility.gallery.load()),
        ('準備探索與設定', lambda: window.utility.settings_page.load()),
        ('準備個人化設定', lambda: window.utility.settings_page.ensure_preferences()),
        ('準備匯出介面', lambda: window.utility.clean_export.load()),
    ]


def settle_layout(root):
    # Finish inherited fonts, scroll-area geometry and responsive form fields
    # before the first visible frame. No event loop or user callbacks are pumped.
    widgets = [root, *root.findChildren(QWidget)]
    for widget in widgets:
        if widget.isVisibleTo(root):
            widget.ensurePolished()
            if widget.layout():
                widget.layout().activate()
    from .dialogs import AppearanceFormPage
    for widget in widgets:
        if isinstance(widget, AppearanceFormPage) and widget.isVisibleTo(root):
            widget.align_fields()
    for widget in widgets:
        if widget.isVisibleTo(root) and widget.layout():
            widget.layout().activate()


class StartupProgress(QWidget):
    """An overlay inside the actual main window, keeping one native identity."""
    def __init__(self, window, initialize, ready, failed):
        super().__init__(window.centralWidget())
        self.initialize, self.ready, self.failed = initialize, ready, failed
        self.workspace = window
        self.completed = 0
        self.cancelled = False
        self.finished_loading = False
        from .theme import visual_tokens
        colors = visual_tokens(window.display_settings())
        self.setObjectName('WorkspaceStartup')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f'QWidget#WorkspaceStartup {{background:{colors["base"]};}}'
            f'QWidget#WorkspaceStartup QLabel {{color:{colors["text"]};background:transparent;}}'
            f'QWidget#WorkspaceStartup QProgressBar {{border:0;border-radius:3px;background:{colors["divider"]};}}'
            f'QWidget#WorkspaceStartup QProgressBar::chunk {{border-radius:3px;background:{colors["accent"]};}}')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.addStretch()
        self.message = QLabel('正在載入工作區…')
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.message)
        layout.addSpacing(24)
        self.progress = QProgressBar()
        self.progress.setAccessibleName('啟動進度')
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setRange(0, 7)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        layout.addStretch()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.advance)
        self.steps = [('載入工作區', window.initialize_documents), ('恢復分頁', self.restore),
                      *prepare_tool_steps(window)]
        self.setGeometry(self.parentWidget().rect())
        window.installEventFilter(self)
        self.parentWidget().installEventFilter(self)

    def start(self):
        self.parentWidget().setEnabled(False)
        self.show()
        self.raise_()
        foreground(self.workspace)
        self.timer.start(0)

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        elif obj is self.workspace and event.type() == QEvent.Type.Close and not self.finished_loading:
            self.cancelled = True
            self.timer.stop()
        return False

    def restore(self):
        self.initial = self.initialize(self.workspace)

    def advance(self):
        if self.cancelled:
            return
        if self.completed == len(self.steps):
            try:
                self.ready(self.workspace, self.initial)
                settle_layout(self.parentWidget())
            except Exception as error:
                self.abort(error)
            else:
                self.finished_loading = True
                self.workspace._startup_loading = False
                for shortcut in self.workspace.findChildren(QShortcut):
                    if shortcut.parent() is self.workspace:
                        shortcut.setEnabled(True)
                self.parentWidget().setEnabled(True)
                self.hide()
                self.parentWidget().removeEventFilter(self)
                self.workspace.removeEventFilter(self)
                foreground(self.workspace)
            return
        title, action = self.steps[self.completed]
        self.message.setText(title)
        # Separate the progress paint from the next synchronous construction.
        QTimer.singleShot(0, self, lambda: self.run_step(action))

    def run_step(self, action):
        if self.cancelled:
            return
        try:
            action()
        except Exception as error:
            self.abort(error)
            return
        if self.cancelled:
            return
        self.completed += 1
        self.progress.setValue(self.completed)
        self.timer.start(0)

    def abort(self, error):
        self.cancelled = True
        self.failed(error)
        self.workspace.close()
        QApplication.instance().exit(1)
