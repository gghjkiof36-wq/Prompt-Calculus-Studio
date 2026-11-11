"""The four v7 add sources share one append/import boundary."""
from pathlib import Path
from PySide6.QtCore import Qt,QSize
from PySide6.QtGui import QIcon,QPixmap
from PySide6.QtWidgets import QFileDialog,QListWidget,QListWidgetItem,QAbstractItemView,QLineEdit,QDialog
from .widgets import StudioDialog,RoundMenu,dialog_buttons
from .image_source import folder_files,natural_key


def pick_files(panel,folder=False,accepted=None):
    window=panel.owner.window
    if folder:
        path=QFileDialog.getExistingDirectory(window,'選擇圖片資料夾')
        if not path:return False
        try:paths=folder_files(path)
        except OSError as exc:window.notice(str(exc));return False
    else:
        paths,_=QFileDialog.getOpenFileNames(window,'選擇圖片','','圖片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        paths=sorted(paths,key=natural_key)
    from .source_import import start
    return start(panel,paths,accepted)


class CatalogPicker(StudioDialog):
    def __init__(self,panel,kind):
        window=panel.owner.window;super().__init__(window);self.window=window;self.kind=kind
        self.setWindowTitle('媒體庫' if kind=='image' else '最近生成');self.resize(680,560)
        self.search=QLineEdit();self.search.setPlaceholderText('搜尋');self.body.addWidget(self.search)
        self.listing=QListWidget();self.listing.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.listing.setIconSize(QSize(72,72));self.body.addWidget(self.listing,1)
        self.body.addWidget(dialog_buttons(self))
        from .source_thumbnails import Thumbnails
        self.thumbs=Thumbnails(self,window);self.thumbs.ready.connect(self.thumbnail)
        self.finished.connect(self.thumbs.cancel);self.search.textChanged.connect(self.refresh);self.refresh()

    def refresh(self):
        self.listing.clear();self.rows={};paths=[]
        for record in self.window.catalog.rows(self.kind,limit=300,search=self.search.text()):
            item=QListWidgetItem(record['name']);item.setData(Qt.ItemDataRole.UserRole,record['path']);self.listing.addItem(item)
            key=(record['path'],str(record.get('mtime','')),str(record.get('size','')))
            self.rows[key]=item;paths.append((key,record['path']))
        self.thumbs.load(paths)

    def thumbnail(self,key,image):
        if key in self.rows:self.rows[key].setIcon(QIcon(QPixmap.fromImage(image)))

    def paths(self):return [item.data(Qt.ItemDataRole.UserRole) for item in self.listing.selectedItems()]


def pick_catalog(panel,kind='recent',accepted=None):
    dialog=CatalogPicker(panel,kind)
    if dialog.exec()!=QDialog.DialogCode.Accepted:return False
    from .source_import import start
    return start(panel,dialog.paths(),accepted)


def add_menu(panel,accepted=None):
    menu=RoundMenu(panel.owner.window)
    menu.addAction('本機檔案…',lambda:pick_files(panel,accepted=accepted))
    menu.addAction('資料夾…',lambda:pick_files(panel,True,accepted))
    menu.addAction('媒體庫…',lambda:pick_catalog(panel,'image',accepted))
    menu.addAction('最近生成…',lambda:pick_catalog(panel,'recent',accepted))
    return menu
