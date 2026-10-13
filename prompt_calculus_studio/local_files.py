"""Platform file pickers hosted by the main window, retaining the last import folder."""
from pathlib import Path
from PySide6.QtWidgets import QFileDialog

def choose_file(window,title,filters):
    previous=window.state['settings'].get('import_directory','')
    directory=previous if previous and Path(previous).is_dir() else ''
    path,_=QFileDialog.getOpenFileName(window,title,directory,filters)
    if not path:return ''
    window.state['settings']['import_directory']=str(Path(path).parent)
    return path
