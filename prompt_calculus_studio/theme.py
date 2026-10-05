"""Shared visual tokens and native Windows backdrops; content stays opaque."""
import ctypes
import sys
from functools import lru_cache
from .color_roles import ACTION_SEEDS, color, derive_roles, mix as _mix


TOOLTIP_PADDING = (10, 6)


VISUAL_PALETTES = {
    'graphite': {
        'base':'#141719', 'chrome':'#202527', 'surface':'#23282b',
        'raised':'#2c3235', 'field':'#191e21', 'text':'#f1f0ea',
        'success':'#a6d5b4', 'warning':'#f2cd93', 'error':'#ffb0a7',
        'info':'#b3cfeb',
        'accent_neutral':'#e3d5ba', 'accent_blue':'#a6c6db', 'accent_green':'#b8cbb1',
        'chrome_solid':'#202527', 'sidebar':'#1a1f21',
        'mica_tint':'#343936', 'acrylic_tint':'#3c4042',
    },
    'mist': {
        'base':'#151b23', 'chrome':'#1e2833', 'surface':'#242f3b',
        'raised':'#2d3b49', 'field':'#19222d', 'text':'#f1f5f9',
        'success':'#a2d9bb', 'warning':'#f4cf96', 'error':'#ffb7b0',
        'info':'#b9d7ff',
        'accent_neutral':'#d5dce1', 'accent_blue':'#b7d6f3', 'accent_green':'#add4c5',
        'chrome_solid':'#26323d', 'sidebar':'#1b252f',
        'mica_tint':'#3a4b5d', 'acrylic_tint':'#40576d',
    },
    'paper': {
        'base':'#f5f3ee', 'chrome':'#ebe8e0', 'surface':'#ffffff',
        'raised':'#f0ede6', 'field':'#fcfbf8', 'text':'#292b29',
        'success':'#24623d', 'warning':'#795217', 'error':'#a4382c',
        'info':'#285981',
        'accent_neutral':'#5a513f', 'accent_blue':'#315e7f', 'accent_green':'#456648',
        'chrome_solid':'#e2ded5', 'sidebar':'#e8e5de',
        'mica_tint':'#d8d4c9', 'acrylic_tint':'#d4dad7',
    },
}


@lru_cache(maxsize=128)
def _derived_palette(entries):
    return derive_roles(dict(entries))


def visual_tokens(settings=None, *, foundation=None):
    """Return an independent token map; old settings default to graphite.

    The saved accent remains independent of the palette, as in the preview.
    Tokens have no Qt dependency and never modify the supplied settings.
    """
    settings = settings or {}
    name = settings.get('visual_palette','graphite')
    if name not in VISUAL_PALETTES: name = 'graphite'
    result = dict(VISUAL_PALETTES[name])
    # An explicit foundation is an internal extension point, not a new saved
    # setting. Future custom-color UI can validate it before applying it.
    if foundation is not None:
        unknown = set(foundation)-set(result)-set(ACTION_SEEDS)-{'accent'}
        if unknown:raise ValueError('Unknown foundation colors: '+', '.join(sorted(unknown)))
        result.update({key:color(value) for key,value in foundation.items()})
    accent = settings.get('accent','blue' if name == 'mist' else 'neutral')
    result.setdefault('accent',result.get('accent_'+str(accent),result['accent_neutral']))
    result = dict(_derived_palette(tuple(sorted(result.items()))))
    result['palette'] = name
    # Native close follows the Windows destructive-action convention.
    result['caption_close_hover'] = '#c42c1e'
    result['on_caption_close'] = '#ffffff'
    return result


def widget_palette(settings=None):
    """Palette for native controls, popups and painter-based child widgets."""
    from PySide6.QtGui import QColor, QPalette
    t = visual_tokens(settings)
    palette = QPalette()
    for role,key in (
        ('Window','base'), ('WindowText','text'), ('Base','field'),
        ('AlternateBase','surface'), ('Text','text'), ('Button','surface'),
        ('ButtonText','text'), ('Highlight','selected'), ('HighlightedText','text'),
        ('ToolTipBase','raised'), ('ToolTipText','text'), ('PlaceholderText','muted'),
        ('Link','info'), ('LinkVisited','info'), ('Light','control'),
        ('Midlight','line'), ('Mid','line'), ('Dark','line'), ('Shadow','base'),
    ):
        palette.setColor(getattr(QPalette.ColorRole,role),QColor(t[key]))
    for role in ('WindowText','Text','ButtonText','PlaceholderText','HighlightedText'):
        palette.setColor(QPalette.ColorGroup.Disabled,getattr(QPalette.ColorRole,role),QColor(t['disabled_text']))
    for role in ('Base','Button'):
        palette.setColor(QPalette.ColorGroup.Disabled,getattr(QPalette.ColorRole,role),QColor(t['disabled_bg']))
    return palette


def font_pixels(points):
    """Convert the saved point size to Qt logical pixels, before per-monitor DPR.

    CSS point fonts otherwise depend on cached screen DPI after display resume.
    Geometry and fonts now use the same device-independent coordinate space.
    """
    return max(1,int(points*96/72))


def shell_color(settings):
    """Tint the native material without making text surfaces translucent."""
    t = visual_tokens(settings)
    material = settings.get('material', 'solid')
    if material not in ('mica', 'acrylic'):
        return t['chrome_solid']
    value = max(0, min(100, settings.get(material+'_transparency', 61)))
    rgb = ','.join(str(int(t['chrome'][i:i+2], 16)) for i in (1,3,5))
    # A desktop backdrop needs a strong tint even at the top of this range.
    # Using 0..255 alpha exposed bright backgrounds and flattened the chrome.
    return f'rgba({rgb},{round(255-0.55*value)})'


def stylesheet(settings):
    from .ui_icons import stylesheet_icon_paths
    t = visual_tokens(settings)
    accent = t['accent']
    pad = 5 if settings["density"] == "compact" else 8
    family = settings.get("font_family", "Microsoft JhengHei UI").replace("'", "").replace(";", "")
    assets=stylesheet_icon_paths(t)
    tooltip_size=font_pixels(max(10,round(settings['ui_size']*.78)))
    return f"""
    QWidget {{ color:{t['text']}; font-family:'{family}'; font-size:{font_pixels(settings['ui_size'])}px; }}
    QMainWindow {{ background:transparent; }}
    QFrame#Shell {{ background:transparent; border:0; border-radius:0; }}
    QWidget#TitleBar {{ background:transparent; }}
    QWidget#SettingsSurface {{ background:transparent; }}
    QWidget#ContextSidebar, QFrame#ContextSidebar, QFrame#MediaSidebar {{ background:{t['sidebar']}; border:0; border-right:1px solid {t['divider']}; }}
    QWidget#ContextRail {{ background:transparent; border:0; }}
    QPushButton#ContextRailAction {{ background:transparent; border:1px solid transparent; border-radius:9px; padding:0; }}
    QPushButton#ContextRailAction:hover {{ background:{t['hover']}; }}
    QPushButton#ContextRailAction:checked {{ background:{t.get('rail_selected',t['selected'])}; }}
    QWidget#SidebarBackArea {{ background:transparent; border:0; border-bottom:1px solid {t['divider']}; }}
    QLabel#SidebarHeading {{ color:{t['text']}; font-size:{font_pixels(settings['ui_size']+2)}px; font-weight:600; padding:0 10px; }}
    QListWidget#SidebarNavigation {{ background:transparent; border:0; padding:0; }}
    QListWidget#SidebarNavigation::item {{ padding:7px 10px; border:1px solid transparent; border-radius:8px; }}
    QWidget#MediaContent {{ background:{t['base']}; border:0; border-radius:0; }}
    QFrame#ExploreCard {{ background:{t['surface']}; border:1px solid {t['line']}; border-radius:16px; }}
    /* The graphics card paints this surface; embedded frames have no second background. */
    QFrame#CanvasModuleBody, QFrame[pcsCanvasBody="true"],
    QFrame#InsetPanel[pcsCanvasBody="true"], QFrame#Panel[pcsCanvasBody="true"] {{ background:transparent; border:0; }}
    QFrame#CanvasModuleBody QLineEdit, QFrame#CanvasModuleBody QPlainTextEdit,
    QFrame#CanvasModuleBody QComboBox, QFrame#CanvasModuleBody QSpinBox,
    QFrame#CanvasModuleBody QDoubleSpinBox {{ background:{t['canvas_field']}; }}
    QLabel#ExploreTitle {{ font-size:28px; font-weight:600; }}
    QFrame#PopupPanel {{ background:{t['raised']}; border:1px solid {t['line']}; border-radius:16px; }}
    QPushButton#FilterChip {{ background:{t['surface']}; border:1px solid transparent; padding:6px 12px; border-radius:14px; }}
    QPushButton#FilterChip:checked {{ background:{t['selected']}; border-color:{accent}; }}
    QWidget#SettingsReading, QWidget#ContentSurface {{ background:{t['base']}; border:none; border-radius:0; }}
    QFrame#SettingsReading QTabWidget::pane {{ top:0; }}
    QPushButton#ResultPreview {{ background:{t['field']}; border:1px solid {t['line']}; padding:6px; }}
    QWidget#WorkspaceSurface {{ background:{t['base']}; border:none; border-radius:0; }}
    QFrame#Panel {{ background:{t['base']}; border:none; border-radius:16px; }}
    QFrame#SidePanel {{ background:transparent; border:none; }}
    QFrame#InsetPanel, QFrame#SettingsGroup {{ background:{t['surface']}; border:1px solid {t['line']}; border-radius:16px; }}
    QFrame#ExecutionBar {{ background:{t['surface']}; border:1px solid {t['line']}; border-radius:12px; }}
    QFrame#ExecutionBar QPushButton#Run, QFrame#ExecutionBar QPushButton#StopRun {{ border-radius:8px; padding:4px 10px; }}
    QFrame#ExecutionBar QPushButton#StopRun {{ padding:0; }}
    QFrame#ExecutionBar QSpinBox#RunCount {{ border-radius:8px; }}
    QPushButton#RunActivity {{ background:{t['field']}; border:1px solid transparent; border-radius:8px; padding:4px 10px; }}
    QPushButton#RunActivity:hover {{ background:{t['hover']}; border-color:{t['line']}; }}
    QPushButton#RunActivity:pressed {{ background:{t['selected']}; }}
    QFrame#CanvasGuideInset {{ background:{t['base']}; border:none; }}
    QFrame#CanvasGuide {{ background:{t['surface']}; border:1px solid {t['line']}; border-radius:16px; }}
    QFrame#ManagerCell {{ background:transparent; border:0; border-bottom:1px solid {t['divider']}; }}
    QTableWidget#ManagerTable::item {{ border:0; border-bottom:1px solid {t['divider']}; padding:0 6px; }}
    QFrame#DialogSurface, QFrame#FloatingSurface {{ background:{t['raised']}; border:1px solid {t['control']}; border-radius:20px; }}
    QFrame#ErrorToast {{ background:{t['surface']}; border:1px solid {t['error']}; border-radius:12px; }}
    QFrame#ErrorToast QLabel#Heading {{ color:{t['error']}; }}
    QLabel#Subtle {{ color:{t['secondary']}; }}
    QLabel#CanvasNotice {{ color:{t['secondary']}; background:{t['surface']}; border-radius:6px; padding:6px 10px; }}
    QLabel#DestinationWarning {{ color:{t['warning']}; border:1.5px solid {t['warning']}; border-radius:10px; font-weight:700; }}
    QListWidget#PaletteAssets::item {{ background:{t['surface']}; border:1px solid {t['line']}; border-radius:10px; padding:14px; }}
    QListWidget#PaletteAssets::item:hover {{ background:{t['hover']}; border-color:{t['control']}; }}
    QListWidget#PaletteAssets::item:selected, QListWidget#PaletteAssets::item:selected:hover {{ background:{t['selected']}; border-color:{t['control']}; }}
    QLabel#Heading {{ font-size:{font_pixels(settings['ui_size']+2)}px; font-weight:600; }}
    QLabel#DialogTitle {{ font-size:{font_pixels(settings['ui_size']+4)}px; font-weight:600; }}
    QScrollArea#SettingsReadingPage QLabel#DialogTitle {{ font-size:{font_pixels(settings['ui_size']+7)}px; font-weight:600; }}
    QScrollArea#SettingsReadingPage QLabel#Heading {{ font-size:{font_pixels(settings['ui_size'])}px; font-weight:600; }}
    QScrollArea#SettingsReadingPage QLabel#Subtle {{ font-size:{font_pixels(max(10,settings['ui_size']-1))}px; font-weight:400; color:{t['muted']}; }}
    QLabel#Brand {{ font-size:{font_pixels(settings['ui_size']+3)}px; font-weight:600; }}
    QLabel#BrandMark {{ color:{accent}; font-size:31px; }}
    QLabel#Eyebrow {{ color:{t['muted']}; font-size:{font_pixels(max(9,settings['ui_size']-1))}px; }}
    QLabel#Draft {{ color:{t['secondary']}; }}
    QFrame#SoftDivider {{ background:{t['divider']}; border:none; }}
    QPushButton, QToolButton {{ background:{t['surface']}; border:1px solid {t['line']}; border-radius:10px; padding:8px 14px; }}
    QPushButton:hover, QToolButton:hover {{ background:{t['hover']}; border-color:{t['control']}; }}
    QPushButton:pressed, QToolButton:pressed {{ background:{t['selected']}; }}
    QPushButton:disabled, QToolButton:disabled {{ color:{t['disabled_text']}; background:{t['disabled_bg']}; border-color:{t['line']}; }}
    QPushButton#Primary {{ background:{accent}; color:{t['on_accent']}; border:1px solid {accent}; font-weight:600; }}
    QPushButton#Primary:hover {{ background:{t['accent_hover']}; border-color:{t['accent_hover']}; }}
    QPushButton#Primary:pressed {{ background:{t['accent_pressed']}; border-color:{t['accent_pressed']}; }}
    QPushButton#Primary[feedback="true"] {{ background:{t['surface']}; border-color:{t['success']}; color:{t['success']}; }}
    QPushButton#Primary:disabled {{ background:{t['disabled_bg']}; color:{t['disabled_text']}; border-color:{t['line']}; }}
    QPushButton#Run:disabled {{ background:{t['run_disabled_background']}; color:{t['on_run_disabled']}; border-color:{t['run_disabled_background']}; }}
    QPushButton#Run {{ font-weight:600; }}
    QPushButton#StopRun {{ font-size:24px; padding:0; }}
    QPushButton#StopRun[textButton="true"] {{ font-size:{font_pixels(settings['ui_size'])}px; }}
    QPushButton#StopRun:disabled {{ background:{t['stop_disabled_background']}; color:{t['on_stop_disabled']}; border-color:{t['stop_disabled_background']}; }}
    QPushButton#Run:enabled {{ background:{t['run_background']}; border-color:{t['run_background']}; color:{t['on_run']}; }}
    QPushButton#Run:hover:enabled {{ background:{t['run_hover']}; border-color:{t['run_hover']}; }}
    QPushButton#Run:pressed:enabled {{ background:{t['run_pressed']}; border-color:{t['run_pressed']}; }}
    QPushButton#StopRun:enabled {{ background:{t['stop_background']}; border-color:{t['stop_border']}; color:{t['on_stop']}; }}
    QPushButton#StopRun:hover:enabled {{ background:{t['stop_hover']}; border-color:{t['stop_hover']}; }}
    QPushButton#StopRun:pressed:enabled {{ background:{t['stop_pressed']}; border-color:{t['stop_pressed']}; }}
    QLabel#ConflictNotice {{ color:{t['warning']}; border:1px solid {t['warning']}; border-radius:8px; padding:8px; background:{t['surface']}; }}
    QPushButton#Quiet {{ background:transparent; border:1px solid transparent; }}
    QPushButton#Quiet:hover {{ background:{t['hover']}; border-color:transparent; }}
    QPushButton#SettingsReturn {{ background:transparent; color:{t['secondary']}; border:1px solid transparent; padding:5px 8px; font-weight:400; text-align:left; }}
    QPushButton#SettingsReturn:hover {{ background:{t['hover']}; color:{t['text']}; border-color:transparent; }}
    QPushButton#Quiet:pressed {{ background:{t['selected']}; }}
    QPushButton#Quiet:disabled {{ color:{t['control']}; background:transparent; border-color:transparent; }}
    QPushButton#DialogClose {{ background:transparent; border:1px solid transparent; font-size:24px; padding:0; }}
    QPushButton#DialogClose:hover {{ background:{t['hover']}; border-color:{t['control']}; }}
    QPushButton#Danger {{ color:{t['error']}; }}
    QPushButton#DeleteWorkflow, QPushButton#ClearDraft {{ background:transparent; border:1px solid transparent; color:{t['error']}; }}
    QPushButton#DeleteWorkflow:hover, QPushButton#ClearDraft:hover {{ background:{t['hover']}; border-color:{t['error']}; color:{t['error']}; }}
    QPushButton#DeleteWorkflow:pressed, QPushButton#ClearDraft:pressed {{ background:{t['selected']}; }}
    QPushButton#DeleteWorkflow:disabled, QPushButton#ClearDraft:disabled {{ color:{t['disabled_text']}; background:transparent; border-color:transparent; }}
    QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background:{t['field']}; border:1px solid {t['line']}; border-radius:8px; padding:7px 10px; selection-background-color:{t['selected']}; selection-color:{t['text']}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color:{accent}; }}
    QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ background:{t['disabled_bg']}; color:{t['disabled_text']}; border-color:{t['line']}; }}
    QPlainTextEdit#Prompt {{ font-family:'Cascadia Mono','Consolas','Microsoft JhengHei'; font-size:{font_pixels(settings['prompt_size'])}px; background:{t['field']}; border-color:transparent; }}
    QPlainTextEdit#QuickSearch {{ background:{t['field']}; }}
    QComboBox {{ padding-right:34px; combobox-popup:0; }}
    QComboBox::drop-down {{ subcontrol-origin:padding; subcontrol-position:top right; width:30px; border:none; background:transparent; }}
    QComboBox::down-arrow {{ image:url('{assets['down']}'); width:16px; height:16px; }}
    QComboBox:on {{ border-color:{accent}; }}
    QSpinBox, QDoubleSpinBox {{ padding-right:6px; }}
    QSpinBox QLineEdit, QDoubleSpinBox QLineEdit {{ padding:0; border:none; background:transparent; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin:padding; subcontrol-position:top right; width:28px; border:none; background:transparent; }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin:padding; subcontrol-position:bottom right; width:28px; border:none; background:transparent; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image:url('{assets['up']}'); width:13px; height:13px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image:url('{assets['down']}'); width:13px; height:13px; }}
    QListView, QTreeWidget, QTableWidget {{ background:transparent; border:none; outline:0; }}
    QListView::item {{ padding:{pad}px 12px; border:1px solid transparent; border-radius:10px; }}
    QListView::item:selected {{ background:{t['selected']}; border-color:transparent; color:{t['text']}; }}
    QListView::item:hover {{ background:{t['hover']}; }}
    QListView::item:selected:hover {{ background:{t['selected']}; }}
    QTreeWidget::item {{ padding:0; border:none; background:transparent; }}
    QFrame#PcsComboContainer {{ background:transparent; border:0; padding:0; }}
    QAbstractItemView#CompletionPopup, QAbstractItemView#ComboPopup, QComboBox QAbstractItemView {{ background:transparent; border:1px solid transparent; border-radius:12px; padding:6px; color:{t['text']}; selection-background-color:{t['selected']}; selection-color:{t['text']}; outline:0; }}
    QAbstractItemView#CompletionPopup::item, QAbstractItemView#ComboPopup::item,
    QComboBox QAbstractItemView::item, QFrame#StageParameterChoicesPopup QListWidget::item {{ padding:5px 10px; margin:0; min-height:0; border:0; border-radius:7px; }}
    QAbstractItemView#CompletionPopup::item:hover, QAbstractItemView#CompletionPopup::item:selected,
    QAbstractItemView#CompletionPopup::item:selected:hover,
    QAbstractItemView#ComboPopup::item:hover, QAbstractItemView#ComboPopup::item:selected,
    QAbstractItemView#ComboPopup::item:selected:hover,
    QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected,
    QComboBox QAbstractItemView::item:selected:hover,
    QFrame#StageParameterChoicesPopup QListWidget::item:hover,
    QFrame#StageParameterChoicesPopup QListWidget::item:selected,
    QFrame#StageParameterChoicesPopup QListWidget::item:selected:hover {{ background:{t['popup_active']}; color:{t['text']}; border-color:transparent; }}
    QFrame#StageParameterChoicesPopup {{ background:transparent; border:1px solid transparent; border-radius:12px; }}
    QFrame#StageParameterChoicesPopup QListWidget {{ background:transparent; color:{t['text']}; border:0; padding:0; outline:0; }}
    QFrame#StageParameterChoicesPopup QLabel {{ color:{t['secondary']}; border:0; background:transparent; }}
    QDialog {{ background:transparent; }}
    QMenu {{ background:{t['raised']}; border:1px solid {t['divider']}; border-radius:12px; padding:6px; }}
    QMenu[pcsPopupSurface="true"] {{ background:transparent; border-color:transparent; }}
    QMenu::item {{ padding:5px 10px; margin:0; min-height:0; border:0; border-radius:7px; background:transparent; }}
    QMenu::item:selected {{ background:{t['popup_active']}; color:{t['text']}; }}
    QMenu::item:disabled {{ color:{t['disabled_text']}; }}
    QMenu::separator {{ height:1px; background:{t['divider']}; margin:6px 12px; }}
    QMenu::indicator {{ width:0; }}
    QTabWidget::pane {{ border:none; background:transparent; top:8px; }}
    QScrollArea {{ background:transparent; border:none; }}
    QWidget#ScrollContent, QWidget#ScrollViewport {{ background:transparent; }}
    QTabBar::tab {{ background:transparent; border:1px solid transparent; border-radius:10px; padding:9px 20px; margin-right:4px; color:{t['secondary']}; }}
    QTabBar::tab:selected {{ background:{t['selected']}; border-color:transparent; color:{t['text']}; }}
    QTabBar::tab:hover {{ background:{t['hover']}; color:{t['text']}; }}
    QTabBar::tab:selected:hover {{ background:{t['selected']}; }}
    QTabBar::tear {{ width:0; }}
    QHeaderView::section {{ background:{t['raised']}; padding:7px; border:none; }}
    QScrollBar:vertical {{ background:transparent; width:12px; margin:4px 0; }}
    QScrollBar::handle:vertical {{ background:{t['scrollbar']}; border:0; margin:0 3px; min-height:32px; border-radius:3px; }}
    QScrollBar::handle:vertical:hover {{ background:{t['secondary']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:none; }}
    QScrollBar:horizontal {{ background:transparent; height:12px; margin:0 4px; }}
    QScrollBar::handle:horizontal {{ background:{t['scrollbar']}; border:0; margin:3px 0; min-width:32px; border-radius:3px; }}
    QScrollBar::handle:horizontal:hover {{ background:{t['secondary']}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background:none; }}
    QSplitter::handle {{ background:transparent; width:12px; height:12px; }}
    QSplitter::handle:hover {{ background:{t['hover']}; border-radius:5px; }}
    QCheckBox {{ spacing:8px; padding:4px 0; }}
    QSlider::groove:horizontal {{ height:4px; background:{t['line']}; border-radius:2px; }}
    QSlider::sub-page:horizontal {{ background:{accent}; border-radius:2px; }}
    QSlider::handle:horizontal {{ width:16px; margin:-6px 0; border-radius:8px; background:{accent}; }}
    QSlider::handle:horizontal:hover {{ background:{t['accent_hover']}; }}
    QCheckBox::indicator, QListView::indicator {{ width:18px; height:18px; border:1px solid {t['control']}; background:{t['field']}; border-radius:5px; }}
    QCheckBox::indicator:checked, QListView::indicator:checked {{ background:{accent}; border-color:{accent}; image:url('{assets['check']}'); }}
    QCheckBox:disabled {{ color:{t['disabled_text']}; }}
    QToolTip {{ background:{t['raised']}; color:{t['text']}; font-size:{tooltip_size}px; font-weight:400; border:1px solid {t['divider']}; border-radius:8px; padding:{TOOLTIP_PADDING[1]}px {TOOLTIP_PADDING[0]}px; }}
    QFrame#RecentOverlaySurface {{ background:{t['base']}; border:1px solid {t['divider']}; border-radius:18px; }}
    QWidget#CanvasWorkspaceInset {{ background:{t['base']}; }}
    QFrame#CanvasWorkspaceCapsule {{ background:{t['surface']}; border:1px solid {t['line']}; border-radius:14px; }}
    QFrame#RecentDetailDivider {{ background:{t['divider']}; border:0; }}
    QPushButton#Navigation, QPushButton#SideNavigation {{ background:transparent; border:1px solid transparent; color:{t['secondary']}; text-align:left; }}
    QPushButton#Navigation:hover, QPushButton#SideNavigation:hover {{ background:{t['hover']}; color:{t['text']}; }}
    QPushButton#Navigation:checked, QPushButton#SideNavigation:checked {{ background:{t['selected']}; color:{t['text']}; }}
    QPushButton#IconButton, QToolButton#IconButton {{ background:transparent; border:1px solid transparent; padding:8px; }}
    QPushButton#IconButton:hover, QToolButton#IconButton:hover {{ background:{t['hover']}; }}
    QPushButton#IconButton:checked, QToolButton#IconButton:checked {{ background:{t['selected']}; }}
    QPushButton[pcsKeyboardFocus="true"]:focus, QToolButton[pcsKeyboardFocus="true"]:focus {{ border-color:{t['control']}; }}
    QPushButton#Navigation[pcsKeyboardFocus="true"]:focus, QPushButton#SideNavigation[pcsKeyboardFocus="true"]:focus,
    QPushButton#IconButton[pcsKeyboardFocus="true"]:focus, QToolButton#IconButton[pcsKeyboardFocus="true"]:focus {{ border-color:{accent}; }}
    QPushButton#Navigation:disabled, QPushButton#SideNavigation:disabled {{ background:transparent; color:{t['disabled_text']}; }}
    QWidget[tone="muted"] {{ color:{t['muted']}; }}
    QWidget[tone="success"] {{ color:{t['success']}; }}
    QWidget[tone="warning"] {{ color:{t['warning']}; }}
    QWidget[tone="error"] {{ color:{t['error']}; }}
    QWidget[tone="info"] {{ color:{t['info']}; }}
    """


def apply_backdrop(window, material, settings=None):
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.platformName() != "windows":
        return False
    if sys.platform != "win32" or sys.getwindowsversion().build < 22621:
        return False
    try:
        dwm = ctypes.windll.dwmapi
        hwnd = ctypes.c_void_p(int(window.winId()))
        if settings is None: settings=getattr(window,'state',{}).get('settings',{})
        tokens=visual_tokens(settings)
        dark = ctypes.c_int(tokens['scheme'] != 'light')
        dwm.DwmSetWindowAttribute(hwnd,20,ctypes.byref(dark),ctypes.sizeof(dark))
        # Use a real native frame: DWM owns the single outer contour, caption
        # buttons and shadow. No layered main window and no custom clip region.
        corners=ctypes.c_int(2)
        dwm.DwmSetWindowAttribute(hwnd,33,ctypes.byref(corners),ctypes.sizeof(corners))
        color=tokens['line']
        border=ctypes.c_uint(int(color[5:7]+color[3:5]+color[1:3],16))
        dwm.DwmSetWindowAttribute(hwnd,34,ctypes.byref(border),ctypes.sizeof(border))
        kind = ctypes.c_int({"solid":1,"mica":2,"acrylic":3}[material])
        result = dwm.DwmSetWindowAttribute(hwnd,38,ctypes.byref(kind),ctypes.sizeof(kind))
        class Margins(ctypes.Structure):
            _fields_ = [(n,ctypes.c_int) for n in ("left","right","top","bottom")]
        # Qt's expanded client area needs the frame extended even when our
        # header paints an opaque color. Resetting it to zero lets the native
        # caption paint over the navigation while NCCALCSIZE stays expanded.
        handle=window.windowHandle()
        from PySide6.QtCore import Qt
        expanded=bool(handle and handle.flags() & Qt.WindowType.ExpandedClientAreaHint)
        margins = Margins(-1,-1,-1,-1) if material != "solid" or expanded else Margins(0,0,0,0)
        dwm.DwmExtendFrameIntoClientArea(hwnd,ctypes.byref(margins))
        return result == 0
    except (OSError, AttributeError):
        return False


def update_window_shape(window):
    """DWM rounds the whole native surface, and squares it when maximized."""
    # A QRegion disables native rounding. Never add a second outline or mask.
    if not window.mask().isEmpty(): window.clearMask()
