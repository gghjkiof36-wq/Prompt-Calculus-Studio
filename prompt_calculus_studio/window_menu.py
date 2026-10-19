"""A themed window menu; Windows still performs the system commands."""
from PySide6.QtCore import QPoint, Qt
from shiboken6 import isValid


def open_window_menu(window, position=None):
    controller = getattr(window, '_expanded_window_chrome', None)
    if controller is None or controller.native_api is None:
        return False
    if not window.isVisible() or not window.isEnabled():
        return False
    existing = getattr(controller, 'system_menu', None)
    if existing is not None and isValid(existing) and existing.isVisible():
        existing.close()
        return True
    from .widgets import RoundMenu
    menu = RoundMenu(window)
    menu.setObjectName('WindowSystemMenu')
    controller.system_menu = menu
    flags = window.windowFlags()
    maximized = window.isMaximized()
    minimized = window.isMinimized()
    fullscreen = window.isFullScreen()
    normal = not (maximized or minimized or fullscreen)
    resizable = window.minimumSize() != window.maximumSize()
    rows = (
        ('還原(&R)', 0xF120, maximized or minimized or fullscreen),
        ('移動(&M)', 0xF010, normal),
        ('調整大小(&S)', 0xF000, normal and resizable),
        ('最小化(&N)', 0xF020, not minimized and bool(flags & Qt.WindowType.WindowMinimizeButtonHint)),
        ('最大化(&X)', 0xF030, not maximized and not fullscreen and resizable
         and bool(flags & Qt.WindowType.WindowMaximizeButtonHint)),
    )
    for title, command, enabled in rows:
        action = menu.addAction(title)
        action.setData(command)
        action.setEnabled(enabled)
    menu.addSeparator()
    close = menu.addAction('關閉(&C)\tAlt+F4')
    close.setData(0xF060)
    close.setEnabled(bool(flags & Qt.WindowType.WindowCloseButtonHint))
    # Post, rather than Send: release Qt's popup grab before Windows starts its
    # own keyboard move/resize loop. Closing still follows the app's closeEvent.
    menu.triggered.connect(lambda action: controller.native_api.post_system_command(
        controller.hwnd, action.data()))
    keyboard = position is None
    if keyboard:
        top = getattr(window, 'studio_navigation', None)
        height = top.height() if top is not None else 40
        position = window.mapToGlobal(QPoint(8, height))
    menu.open_at(position)
    if keyboard:
        menu.setActiveAction(next((a for a in menu.actions() if a.isEnabled() and not a.isSeparator()), None))
    return True
