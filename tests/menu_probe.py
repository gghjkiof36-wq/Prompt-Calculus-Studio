"""Run each menu path in a separate process so native crashes are observable."""
import faulthandler
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/"vendor"))
os.environ.setdefault("QT_QPA_PLATFORM","windows")
faulthandler.enable()
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMenu
from prompt_studio.widgets import RoundMenu
from prompt_studio.window import Window

app=QApplication([])
window=Window(ROOT/"qa"/f"menu-probe-{os.getpid()}")
window.state["settings"]["online"]=False; window.show()
mode=sys.argv[1] if len(sys.argv)>1 else "workspace"
if mode=="direct": RoundMenu.exec=QMenu.exec
def close_menu():
    for widget in app.topLevelWidgets():
        if isinstance(widget,QMenu) and widget.isVisible(): widget.close()
def run():
    print("opening "+mode,flush=True)
    QTimer.singleShot(500,close_menu)
    if mode in ("workspace","direct"): window.workspace_menu()
    else:
        menu=(QMenu if mode=="plain" else RoundMenu)(window); menu.addAction("Test")
        if mode=="popup":
            menu.popup(window.mapToGlobal(window.rect().center()))
            QTimer.singleShot(650,window.close); return
        menu.exec(window.mapToGlobal(window.rect().center()))
    print("returned "+mode,flush=True); window.close()
QTimer.singleShot(100,run)
QTimer.singleShot(5000,lambda:os._exit(91))
app.exec()
print("completed "+mode,flush=True)
