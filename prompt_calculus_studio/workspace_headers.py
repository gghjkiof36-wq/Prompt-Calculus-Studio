"""One docking rule for every workspace sidebar heading."""
from PySide6.QtCore import QMargins


class SidebarHeaders:
    def __init__(self, workspace):
        self.workspace = workspace
        self.entries = []

    def register(self, sidebar, header):
        if any(entry[1] is header for entry in self.entries):
            return
        layout = sidebar.layout()
        self.entries.append((sidebar, header, layout.indexOf(header),
                             QMargins(header.layout().contentsMargins())))
        header.setObjectName('WorkspaceSidebarHeader')

    def sync(self):
        w = self.workspace
        target = w.sidebar_target()
        docked = w.navigation_position == 'top' and target is not None and target.isVisible()
        active = next((header for side, header, *_ in self.entries if side is target), None) if docked else None
        for sidebar, header, index, margins in self.entries:
            joined = header is active
            parent = w.tab_row if joined else sidebar
            if header.parentWidget() is not parent:
                sidebar.layout().removeWidget(header)
                header.setParent(parent)
                header.setProperty('topDocked', joined)
                header.layout().setContentsMargins(12, 0, 8, 0) if joined else header.layout().setContentsMargins(margins)
                if not joined:
                    sidebar.layout().insertWidget(index, header)
                header.style().unpolish(header)
                header.style().polish(header)
                header.show()
            if joined:
                header.setGeometry(0, 0, sidebar.width(), w.tab_row.height())
                header.raise_()
        w._sidebar_header_docked = active is not None
        w._tree_header_docked = active is w.tree_heading
        w.tab_strip.bar.join_sidebar = active is not None
        w.tab_strip.bar.update()
