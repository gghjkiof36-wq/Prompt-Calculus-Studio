"""Lazy document/Canvas menus; source identity is captured once per popup."""
from collections import Counter
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog
from .widgets import RoundMenu


def add_entry(page, menu, ident):
    owner = getattr(page.window, 'workspace_owner', None)
    if owner is None or getattr(page.window, 'document', None) is None:return
    from . import materials, prompt_materials
    if prompt_materials.kind(materials.item(page.window.state, ident)) != 'text':return
    submenu = menu.addMenu('加入Canvas…')
    submenu.addAction('選擇 Canvas 文件').setEnabled(False)
    controller = MaterialMenu(page, ident, submenu)
    submenu._material_menu = controller
    return submenu


def open_menu(page, ident, position):
    if ident is None:return
    menu = RoundMenu(page)
    controller = MaterialMenu(page, ident, menu)
    menu._material_menu = controller
    menu.open_at(position)
    return menu


class MaterialMenu:
    def __init__(self, page, ident, menu):
        self.page, self.menu = page, menu
        self.transfer = page.window.workspace_owner.material_transfer
        self.source = None
        try:
            self.source = self.transfer.capture(page, ident)
        except (ValueError, KeyError) as error:
            menu.addAction(str(error)).setEnabled(False)
            return
        menu.aboutToShow.connect(self.populate)

    def populate(self):
        menu = self.menu
        menu.clear()
        paths = self.transfer.candidates()
        counts = Counter(p.stem.casefold() for p in paths)
        for path in paths:
            title = path.stem
            if counts[title.casefold()] > 1:title += ' · '+str(path.parent)
            child = menu.addMenu(menu.fontMetrics().elidedText(title, Qt.TextElideMode.ElideMiddle, 420))
            child.addAction('讀取畫布…').setEnabled(False)
            child.menuAction().setToolTip(str(path))
            child.menuAction().setData(str(path))
            child.aboutToShow.connect(lambda m=child, p=path:self.populate_target(m, p))
        if not paths:menu.addAction('沒有已接入的 Canvas 文件').setEnabled(False)
        menu.addSeparator()
        menu.addAction('選擇其他Canvas…', self.choose_other)

    def populate_target(self, menu, path):
        menu.clear()
        try:
            target = self.transfer.target(path)
            choices = self.transfer.canvases(target)
            menu.addAction('Canvas空白處', lambda:self.transfer.add(self.source, target))
            if choices:menu.addSeparator()
            counts = Counter(name for _, name in choices)
            for index, (key, name) in enumerate(choices):
                title = name if counts[name] == 1 else name+' · '+str(index+1)
                action = menu.addAction(title, lambda checked=False, key=key:self.transfer.add(self.source, target, key))
                action.setData(key)
        except (ValueError, OSError, KeyError) as error:
            menu.addAction('無法開啟這份 Canvas').setEnabled(False)
            menu.setToolTip(str(error))

    def choose_other(self):
        w = self.transfer.w
        path, _ = QFileDialog.getOpenFileName(w, '選擇 Canvas 文件', str(w.locations.current or ''),
                                              'Canvas 文件 (*.pcs-workflow)')
        if not path:return
        menu = RoundMenu(self.page)
        self.populate_target(menu, Path(path))
        # This choice still asks for the explicit Canvas/blank destination.
        menu._material_menu = self
        menu.open_at(self.page.mapToGlobal(self.page.rect().center()))
