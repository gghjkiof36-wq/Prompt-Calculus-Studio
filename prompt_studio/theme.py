"""Neutral theme plus optional native Windows backdrops; no per-card blur."""
import ctypes
import sys
from pathlib import Path


def font_pixels(points):
    """Convert the saved point size to Qt logical pixels, before per-monitor DPR.

    CSS point fonts otherwise depend on cached screen DPI after display resume.
    Geometry and fonts now use the same device-independent coordinate space.
    """
    return max(1,round(points*96/72))


def stylesheet(settings):
    accent = {"neutral":"#e5e5e5", "blue":"#a9caff", "green":"#a9dcc2"}[settings["accent"]]
    pad = 6 if settings["density"] == "compact" else 10
    family = settings.get("font_family", "Microsoft JhengHei UI").replace("'", "").replace(";", "")
    shell = "#1c1c1c" if settings.get("material") == "solid" else "rgba(14,14,14,100)"
    if settings.get("material") == "acrylic":
        transparency=settings.get("acrylic_transparency",61)
        shell=f"rgba(14,14,14,{round(255*(100-transparency)/100)})"
    assets=(Path(__file__).parent/"assets").as_posix()
    return f"""
    QWidget {{ color:#eeeeee; font-family:'{family}'; font-size:{font_pixels(settings['ui_size'])}px; }}
    QMainWindow {{ background:transparent; }}
    QFrame#Shell {{ background:{shell}; border:0; border-radius:0; }}
    QWidget#TitleBar {{ background:transparent; }}
    QFrame#WorkspaceSurface {{ background:#111111; border:none; border-radius:18px; }}
    QFrame#Panel {{ background:#111111; border:none; border-radius:16px; }}
    QFrame#SidePanel {{ background:transparent; border:none; }}
    QFrame#InsetPanel, QFrame#SettingsGroup {{ background:#202020; border:1px solid #303030; border-radius:16px; }}
    QFrame#DialogSurface {{ background:#202020; border:1px solid #484848; border-radius:20px; }}
    QLabel#Subtle {{ color:#aeaeae; }}
    QLabel#Heading {{ font-size:{font_pixels(settings['ui_size']+2)}px; font-weight:600; }}
    QLabel#DialogTitle {{ font-size:{font_pixels(settings['ui_size']+4)}px; font-weight:600; }}
    QLabel#Brand {{ font-size:{font_pixels(settings['ui_size']+3)}px; font-weight:600; }}
    QLabel#BrandMark {{ color:#e0e0e0; font-size:31px; }}
    QLabel#Eyebrow {{ color:#a6a6a6; font-size:{font_pixels(max(9,settings['ui_size']-1))}px; }}
    QLabel#Draft {{ color:#c2c2c2; }}
    QFrame#SoftDivider {{ background:rgba(255,255,255,18); border:none; }}
    QPushButton, QToolButton {{ background:#2b2b2b; border:1px solid #3c3c3c; border-radius:10px; padding:8px 14px; }}
    QPushButton:hover, QToolButton:hover {{ background:#363636; border-color:#606060; }}
    QPushButton:pressed {{ background:#444444; }}
    QPushButton:disabled {{ color:#777777; background:#222222; }}
    QPushButton#Primary {{ background:{accent}; color:#121212; border:1px solid {accent}; font-weight:600; }}
    QPushButton#Primary:hover {{ background:#f4f4f4; border-color:#f4f4f4; }}
    QPushButton#Primary:pressed {{ background:#b6b6b6; border-color:#b6b6b6; }}
    QPushButton#Primary[feedback="true"] {{ background:#c0d3c5; border-color:#c0d3c5; color:#15221a; }}
    QPushButton#Primary:disabled {{ background:#383838; color:#858585; border-color:#444444; }}
    QPushButton#Run {{ background:#098bea; color:white; border:1px solid #239ef3; font-weight:600; }}
    QPushButton#Run:hover {{ background:#249ff4; }}
    QPushButton#Run:disabled {{ background:#28485c; color:#8ca3b3; border-color:#3b5666; }}
    QPushButton#StopRun {{ background:#682d31; color:#f1a6aa; border:1px solid #783b40; font-size:24px; padding:0; }}
    QPushButton#StopRun:hover {{ background:#943e44; color:white; }}
    QPushButton#StopRun:disabled {{ background:#3b292b; color:#7d6567; border-color:#493538; }}
    QLabel#ConflictNotice {{ color:#e2c879; border:1px solid #91772f; border-radius:8px; padding:8px; background:#302b1d; }}
    QPushButton#Quiet {{ background:transparent; border:1px solid transparent; }}
    QPushButton#Quiet:hover {{ background:rgba(255,255,255,18); border-color:rgba(255,255,255,28); }}
    QPushButton#Quiet:pressed {{ background:#404040; }}
    QPushButton#DialogClose {{ background:transparent; border:1px solid transparent; font-size:24px; padding:0; }}
    QPushButton#DialogClose:hover {{ background:#454545; border-color:#606060; }}
    QPushButton#Danger {{ color:#f0a7a7; }}
    QPushButton#ClearDraft {{ background:transparent; border:1px solid transparent; color:#e5a0a0; }}
    QPushButton#ClearDraft:hover {{ background:rgba(201,76,76,35); border-color:rgba(220,100,100,48); color:#ffc0c0; }}
    QPushButton#ClearDraft:pressed {{ background:rgba(201,76,76,65); }}
    QPushButton#ClearDraft:disabled {{ background:transparent; border-color:transparent; color:#707070; }}
    QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{ background:#181818; border:1px solid #393939; border-radius:10px; padding:9px 12px; selection-background-color:#565656; }}
    QLineEdit:focus, QPlainTextEdit:focus {{ border:1px solid #b1b1b1; }}
    QPlainTextEdit#Prompt {{ font-family:'Cascadia Mono','Consolas','Microsoft JhengHei'; font-size:{font_pixels(settings['prompt_size'])}px; background:#181818; border-color:transparent; }}
    QPlainTextEdit#QuickSearch {{ background:#232323; }}
    QComboBox {{ padding-right:34px; }}
    QComboBox::drop-down {{ subcontrol-origin:padding; subcontrol-position:top right; width:30px; border:none; background:transparent; }}
    QComboBox::down-arrow {{ image:url('{assets}/chevron-down.svg'); width:16px; height:16px; }}
    QComboBox:on {{ border-color:#707070; }}
    QSpinBox {{ padding-right:34px; }}
    QSpinBox QLineEdit {{ padding:0; border:none; background:transparent; }}
    QSpinBox::up-button {{ subcontrol-origin:padding; subcontrol-position:top right; width:28px; border:none; background:transparent; }}
    QSpinBox::down-button {{ subcontrol-origin:padding; subcontrol-position:bottom right; width:28px; border:none; background:transparent; }}
    QSpinBox::up-arrow {{ image:url('{assets}/chevron-up.svg'); width:13px; height:13px; }}
    QSpinBox::down-arrow {{ image:url('{assets}/chevron-down.svg'); width:13px; height:13px; }}
    QListView, QTreeWidget, QTableWidget {{ background:transparent; border:none; outline:0; }}
    QListView::item {{ padding:{pad}px 12px; border:1px solid transparent; border-radius:10px; }}
    QListView::item:selected {{ background:#353535; border-color:#616161; color:#ffffff; }}
    QListView::item:hover {{ background:#282828; }}
    QTreeWidget::item {{ padding:0; border:none; background:transparent; }}
    QAbstractItemView#CompletionPopup, QComboBox QAbstractItemView {{ background:#292929; border:1px solid #484848; border-radius:10px; padding:5px; color:#eeeeee; selection-background-color:#454545; outline:0; }}
    QDialog {{ background:transparent; }}
    QMenu {{ background:#292929; border:1px solid #424242; border-radius:12px; padding:7px; }}
    QMenu::item {{ padding:9px 16px; margin:2px; border-radius:7px; background:transparent; }}
    QMenu::item:selected {{ background:#424242; }}
    QMenu::item:disabled {{ color:#7b7b7b; }}
    QMenu::separator {{ height:1px; background:#454545; margin:6px 12px; }}
    QMenu::indicator {{ width:0; }}
    QTabWidget::pane {{ border:none; background:transparent; top:8px; }}
    QScrollArea {{ background:transparent; border:none; }}
    QWidget#ScrollContent, QWidget#ScrollViewport {{ background:transparent; }}
    QTabBar::tab {{ background:transparent; border:1px solid transparent; border-radius:10px; padding:9px 20px; margin-right:4px; color:#b4b4b4; }}
    QTabBar::tab:selected {{ background:rgba(255,255,255,22); border-color:rgba(255,255,255,25); color:#f5f5f5; }}
    QTabBar::tab:hover {{ background:rgba(255,255,255,13); color:#eeeeee; }}
    QTabBar::tear {{ width:0; }}
    QHeaderView::section {{ background:#303030; padding:7px; border:none; }}
    QScrollBar:vertical {{ background:#181818; width:10px; margin:3px 1px; border-radius:4px; }}
    QScrollBar::handle:vertical {{ background:#777777; min-height:32px; border-radius:4px; }}
    QScrollBar::handle:vertical:hover {{ background:#a0a0a0; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:none; }}
    QScrollBar:horizontal {{ background:#181818; height:10px; margin:1px 3px; border-radius:4px; }}
    QScrollBar::handle:horizontal {{ background:#777777; min-width:32px; border-radius:4px; }}
    QScrollBar::handle:horizontal:hover {{ background:#a0a0a0; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background:none; }}
    QSplitter::handle {{ background:transparent; width:12px; height:12px; }}
    QSplitter::handle:hover {{ background:rgba(255,255,255,12); border-radius:5px; }}
    QCheckBox {{ spacing:8px; padding:4px 0; }}
    QSlider::groove:horizontal {{ height:4px; background:#464646; border-radius:2px; }}
    QSlider::sub-page:horizontal {{ background:{accent}; border-radius:2px; }}
    QSlider::handle:horizontal {{ width:16px; margin:-6px 0; border-radius:8px; background:{accent}; }}
    QSlider::handle:horizontal:hover {{ background:#ffffff; }}
    QCheckBox::indicator, QListView::indicator {{ width:18px; height:18px; border:1px solid #686868; background:#181818; border-radius:5px; }}
    QCheckBox::indicator:checked, QListView::indicator:checked {{ background:{accent}; border-color:{accent}; image:url('{assets}/check.svg'); }}
    QToolTip {{ background:#303030; color:#ffffff; border:1px solid #666666; padding:5px; }}
    """


def apply_backdrop(window, material):
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.platformName() != "windows":
        return False
    if sys.platform != "win32" or sys.getwindowsversion().build < 22621:
        return False
    try:
        dwm = ctypes.windll.dwmapi
        hwnd = ctypes.c_void_p(int(window.winId()))
        dark = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd,20,ctypes.byref(dark),ctypes.sizeof(dark))
        # Use a real native frame: DWM owns the single outer contour, caption
        # buttons and shadow. No layered main window and no custom clip region.
        corners=ctypes.c_int(2)
        dwm.DwmSetWindowAttribute(hwnd,33,ctypes.byref(corners),ctypes.sizeof(corners))
        border=ctypes.c_uint(0x00353535)
        dwm.DwmSetWindowAttribute(hwnd,34,ctypes.byref(border),ctypes.sizeof(border))
        kind = ctypes.c_int({"solid":1,"mica":2,"acrylic":3}[material])
        result = dwm.DwmSetWindowAttribute(hwnd,38,ctypes.byref(kind),ctypes.sizeof(kind))
        class Margins(ctypes.Structure):
            _fields_ = [(n,ctypes.c_int) for n in ("left","right","top","bottom")]
        margins = Margins(-1,-1,-1,-1) if material != "solid" else Margins(0,0,0,0)
        dwm.DwmExtendFrameIntoClientArea(hwnd,ctypes.byref(margins))
        return result == 0
    except (OSError, AttributeError):
        return False


def update_window_shape(window):
    """DWM rounds the whole native surface, and squares it when maximized."""
    # A QRegion disables native rounding. Never add a second outline or mask.
    if not window.mask().isEmpty(): window.clearMask()
