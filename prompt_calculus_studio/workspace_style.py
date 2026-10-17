"""Shared sidebar treatment for every entrance in the document workspace."""
from .theme import visual_tokens


def sidebar_style(settings):
    t = visual_tokens(settings)
    return f'''
        QWidget[pcsWorkspaceSidebar="true"], QFrame#DocumentSidebar[pcsWorkspaceSidebar="true"],
        QFrame#ContextSidebar[pcsWorkspaceSidebar="true"], QFrame#MediaSidebar[pcsWorkspaceSidebar="true"],
        QWidget#ContextSidebar[pcsWorkspaceSidebar="true"] {{ background:{t['sidebar']}; border:0;
            border-top-left-radius:10px; border-top-right-radius:10px; border-bottom-left-radius:10px; }}
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
