"""Shared sidebar treatment for every entrance in the document workspace."""
from .theme import visual_tokens, font_pixels
from .color_roles import interaction


def workspace_colors(settings):
    t = visual_tokens(settings)
    # Keep the material frame brighter than the inset file/navigation surface.
    # Other palettes retain their established light/dark relationships.
    sidebar = t['base'] if t['palette']=='graphite' else t['sidebar']
    return dict(sidebar=sidebar, content=t.get('canvas_background', t['base']),
                selected=interaction(sidebar, t['text'], .08),
                hover=interaction(sidebar, t['text'], .04))


def sidebar_style(settings):
    t = visual_tokens(settings)
    surfaces = workspace_colors(settings)
    return f'''
        QStackedWidget#WorkspaceInset {{ background:transparent; border:0; }}
        QWidget#WorkspaceSurface, QWidget#SettingsReading, QWidget#ContentSurface,
        QWidget#MediaContent, QWidget#DocumentPane, QStackedWidget#DocumentPages {{ background:{surfaces['content']}; }}
        QWidget[pcsWorkspaceSidebar="true"], QFrame#DocumentSidebar[pcsWorkspaceSidebar="true"],
        QFrame#ContextSidebar[pcsWorkspaceSidebar="true"], QFrame#MediaSidebar[pcsWorkspaceSidebar="true"],
        QWidget#ContextSidebar[pcsWorkspaceSidebar="true"] {{ background:{surfaces['sidebar']}; border:0;
            border-radius:0; border-right:1px solid {t['divider']}; }}
        QWidget[pcsWorkspaceSidebar="true"] QLabel#Heading,
        QWidget[pcsWorkspaceSidebar="true"] QLabel#SidebarHeading {{ min-height:36px; padding:0 8px;
            font-size:{font_pixels(settings['ui_size']+1)}px; font-weight:600; color:{t['text']}; }}
        QWidget[pcsWorkspaceSidebar="true"] QListWidget,
        QTreeWidget#DocumentTree {{ background:transparent; border:0; outline:0; }}
        QWidget[pcsWorkspaceSidebar="true"] QListWidget::item,
        QWidget[pcsWorkspaceSidebar="true"] QListWidget#SidebarNavigation::item,
        QTreeWidget#DocumentTree::item {{ height:28px; min-height:28px; padding:2px 8px; border:0; border-radius:6px; }}
        QWidget[pcsWorkspaceSidebar="true"] QListWidget::item:selected {{ background:{surfaces['selected']}; color:{t['text']}; }}
        QWidget[pcsWorkspaceSidebar="true"] QListWidget::item:!selected:hover {{ background:{surfaces['hover']}; }}
        QTreeWidget#DocumentTree::item, QTreeWidget#DocumentTree::item:hover,
        QTreeWidget#DocumentTree::item:selected, QTreeWidget#DocumentTree::branch,
        QTreeWidget#DocumentTree::branch:hover, QTreeWidget#DocumentTree::branch:selected {{ background:transparent; border:0; }}
        QWidget#SidebarBackArea {{ border:0; background:transparent; }}
    '''
