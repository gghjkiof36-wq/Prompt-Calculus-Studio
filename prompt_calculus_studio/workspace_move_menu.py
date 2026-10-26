"""Lazy folder submenus, with one captured file-operation lifetime."""
from PySide6.QtCore import QTimer, Qt
from .local_documents import paths
from .workspace_locations import INTERNAL_NAMES
from .ui_icons import icon


class MoveMenu:
    def __init__(self, files, menu, context):
        self.files, self.w, self.menu, self.context = files, files.w, menu, context
        self.op = None
        self.committing = False
        self.closed = False
        self.root_menu = menu
        from .widgets import RoundMenu
        while isinstance(self.root_menu.parentWidget(), RoundMenu):
            self.root_menu = self.root_menu.parentWidget()
        # Keep the captured source paused while browsing sibling submenus too.
        # A hidden child is not the end of the root popup's interaction.
        menu.aboutToShow.connect(self.start)
        self.root_menu.aboutToHide.connect(self.defer_finish)
        self.root_menu.destroyed.connect(self.finish)
        self.populate(menu, context.root, top=True)

    def start(self):
        if self.op is not None or self.closed:
            return
        try:
            self.op = self.files.begin(self.context)
            self.op.cancel = self.finish
        except (OSError, ValueError) as error:
            self.w.show_notice(str(error))
            self.menu.setEnabled(False)

    def defer_finish(self):
        QTimer.singleShot(0, self.finish)

    def finish(self, *_):
        if self.committing:
            return
        self.closed = True
        if self.op is not None:
            op, self.op = self.op, None
            self.files.end(op)

    def folders(self, directory):
        if directory is None:
            return []
        directory = self.w.locations.directory(directory)
        result = []
        for child in directory.iterdir():
            if child.name.casefold() in INTERNAL_NAMES or paths.is_link(child) or not child.is_dir():
                continue
            try:
                self.w.locations.directory(child)
            except (OSError, ValueError):
                continue
            result.append(child)
        return sorted(result, key=lambda p: p.name.casefold())

    def valid(self, directory):
        source = self.context.path
        return (directory is not None and self.w.locations.root_for(directory) == self.context.root
                and directory != source and source not in directory.parents)

    def destination_action(self, menu, title, directory):
        same = directory == self.context.path.parent
        action = menu.addAction(icon('folder', 'navigation'), title + ('（目前位置）' if same else ''),
                                lambda: self.choose(directory))
        action.setData(str(directory))
        action.setToolTip(str(directory))
        action.setEnabled(self.valid(directory) and not same)
        return action

    def populate(self, menu, directory, top=False):
        menu.clear()
        menu.setToolTipsVisible(True)
        if directory is None:
            menu.addAction('請先將所在資料夾加入側欄').setEnabled(False)
            return
        title = ('最外層' if directory == self.w.locations.initial else
                 menu.fontMetrics().elidedText(directory.name, Qt.TextElideMode.ElideRight, 220)) if top else '移至此資料夾'
        self.destination_action(menu, title, directory)
        menu.addSeparator()
        try:
            children = self.folders(directory)
            for child in children:
                title = menu.fontMetrics().elidedText(child.name, Qt.TextElideMode.ElideRight, 260)
                if self.valid(child) and self.folders(child):
                    submenu = menu.addMenu(icon('folder', 'navigation'), title)
                    submenu.menuAction().setData(str(child))
                    submenu.menuAction().setToolTip(str(child))
                    submenu.aboutToShow.connect(lambda m=submenu, p=child: self.populate(m, p))
                else:
                    self.destination_action(menu, title, child)
        except (OSError, ValueError) as error:
            menu.addAction('無法讀取這個資料夾').setEnabled(False)
            menu.setToolTip(str(error))
        menu.addSeparator()
        action = menu.addAction('新增資料夾…', lambda: self.choose(directory, new_folder=True))
        action.setEnabled(self.valid(directory))

    def choose(self, directory, new_folder=False):
        if self.closed:
            return
        self.start()
        if self.op is None:
            return
        if new_folder:
            from dataclasses import replace
            from .workspace_creation import start_creation
            self.committing = True
            def finished():
                self.committing = False
                self.finish()
            start_creation(self.w.commands, 'folder', replace(self.context, destination=directory),
                           after_create=lambda target: self.files.move(self.op, target), finished=finished)
            return
        self.committing = True
        try:
            self.files.move(self.op, directory)
        except (OSError, ValueError) as error:
            self.w.show_notice(str(error))
        finally:
            self.committing = False
            self.finish()
