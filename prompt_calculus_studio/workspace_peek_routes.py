"""Resolve hover destinations without navigating their document/tool pages."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QListWidgetItem
from .ui_icons import icon


class PeekRoutes:
    def __init__(self, controller):
        self.peek = controller
        self.window = controller.window
        self.previews = {}
        self.bound = set()

    def target(self, route):
        w = self.window
        if route == 'home':
            return w.sidebar
        host = w.ensure_utility()
        if route == 'gallery':
            gallery = host.gallery.load()
            if route not in self.bound:
                gallery.albums.itemClicked.connect(self.open_album)
                gallery.albums.itemActivated.connect(self.open_album)
                self.bound.add(route)
            if w.active_route != route:
                gallery.populate_albums()
            target = gallery.folder_panel
        elif route == 'export':
            target = host.clean_export.sidebar
            if route not in self.bound:
                for button in target.findChildren(QPushButton):
                    button.clicked.connect(self.open_export)
                self.bound.add(route)
        elif route == w.active_route:
            target = w.sidebar_target()
        else:
            target = self.navigation(route)
        host.prepare_workspace_sidebar(target)
        return target

    def navigation(self, route):
        if route not in self.previews:
            from .settings_page import NavigationSidebar, navigation_entries
            # Settings and Explore share a live page. Preview just its route
            # list, so hovering never changes that page or starts CivitAI work.
            owner = QWidget(self.window)
            owner.hide()
            body = QVBoxLayout(owner)
            back = QPushButton()
            target = NavigationSidebar(back)
            body.addWidget(target)
            section = 'explore' if route == 'explore' else 'appearance'
            area, _, entries = navigation_entries(section, workspace=True)
            target.back_area.hide()
            target.layout().itemAt(1).spacerItem().changeSize(0, 0)
            target.heading.setText(area)
            target.navigation.setAccessibleName(area+'導覽')
            for key, text, symbol in entries:
                item = QListWidgetItem(icon(symbol, 'navigation'), text)
                item.setData(Qt.ItemDataRole.UserRole+1, key)
                target.navigation.addItem(item)
            target.navigation.setCurrentRow(0)
            def navigate(item):
                self.window.open_route(route, item.data(Qt.ItemDataRole.UserRole+1))
            target.navigation.itemClicked.connect(navigate)
            target.navigation.itemActivated.connect(navigate)
            self.previews[route] = target
        return self.previews[route]

    def open_album(self, item):
        host = self.window.utility
        if self.peek.target is not host.gallery.folder_panel or self.window.active_route == 'gallery':
            return
        gallery = host.gallery
        # Clicking an already selected item does not emit currentItemChanged.
        # Otherwise choose_album has already refreshed it through that signal.
        if gallery.album != item.data(Qt.ItemDataRole.UserRole):
            gallery.choose_album(item)
        self.peek.close()
        host.show_page(gallery, refresh_gallery=False)

    def open_export(self):
        if self.window.active_route != 'export' and self.peek.target is self.window.utility.clean_export.sidebar:
            self.window.open_route('export')
