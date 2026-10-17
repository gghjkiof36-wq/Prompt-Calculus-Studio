"""Shared sidebar treatment for every entrance in the document workspace."""
from .theme import visual_tokens


def workspace_colors(settings):
    t = visual_tokens(settings)
    # Keep the material frame brighter than the inset file/navigation surface.
    # Other palettes retain their established light/dark relationships.
    return dict(sidebar=t['base'] if t['palette']=='graphite' else t['sidebar'],
                content=t.get('canvas_background', t['base']))


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
        QWidget[pcsWorkspaceSidebar="true"] QLabel#SidebarHeading {{ min-height:32px; font-size:13px; font-weight:600; color:{t['secondary']}; }}
        QWidget[pcsWorkspaceSidebar="true"] QListWidget,
        QTreeWidget#DocumentTree {{ background:transparent; border:0; outline:0; }}
        QWidget[pcsWorkspaceSidebar="true"] QListWidget::item,
        QWidget[pcsWorkspaceSidebar="true"] QListWidget#SidebarNavigation::item,
        QTreeWidget#DocumentTree::item {{ height:28px; min-height:28px; padding:2px 8px; border:0; border-radius:5px; }}
        QWidget[pcsWorkspaceSidebar="true"] QListWidget::item:selected,
        QTreeWidget#DocumentTree::item:selected {{ background:{t['raised']}; color:{t['text']}; }}
        QWidget[pcsWorkspaceSidebar="true"] QListWidget::item:hover,
        QTreeWidget#DocumentTree::item:hover {{ background:{t['hover']}; }}
        QTreeWidget#DocumentTree::item:selected,
        QTreeWidget#DocumentTree::branch:selected {{ background:{t['raised']}; border-radius:0; }}
        QWidget#SidebarBackArea {{ border:0; background:transparent; }}
    '''
