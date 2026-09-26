"""Explicit drive shortcuts in non-native dialogs, always hosted by the main window."""
from pathlib import Path
from PySide6.QtCore import QDir,QUrl
from PySide6.QtWidgets import QFileDialog,QDialog

def choose_file(window,title,filters):
    dialog=QFileDialog(window,title)
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog,True)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile); dialog.setNameFilter(filters)
    dialog.setSidebarUrls([QUrl.fromLocalFile(QDir.homePath()),*[QUrl.fromLocalFile(d.absoluteFilePath()) for d in QDir.drives()]])
    previous=window.state['settings'].get('import_directory','')
    if previous and Path(previous).is_dir(): dialog.setDirectory(previous)
    if dialog.exec()!=QDialog.DialogCode.Accepted or not dialog.selectedFiles(): return ''
    path=dialog.selectedFiles()[0]; window.state['settings']['import_directory']=str(Path(path).parent)
    return path
