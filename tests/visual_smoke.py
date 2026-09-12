"""Render the actual Qt widgets and measure a small, bounded fixture workload."""
import ctypes
import json
import os
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/"vendor"))
if "--native" not in sys.argv: os.environ["QT_QPA_PLATFORM"]="offscreen"
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase,QImage,QPainter,QColor,QFont
from PySide6.QtCore import Qt,QTimer
from prompt_studio.window import Window
from prompt_studio.dialogs import SettingsDialog, ModuleDialog
from prompt_studio.widgets import RoundMenu, InputDialog
from PySide6.QtTest import QTest
from prompt_studio.media import scan_models,import_image
from prompt_studio.core import uid

OUT=ROOT/"qa"/"screenshots"; OUT.mkdir(parents=True,exist_ok=True)
DATA=ROOT/"qa"/("visual-"+uid())
started=time.perf_counter(); app=QApplication([])
if app.platformName()=="offscreen":
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msjh.ttc")
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msjhbd.ttc")
window=Window(DATA); window.state["settings"]["online"]=False; window.first_models=False
window.state["selections"]={window.state["items"][i]["module"]:[window.state["items"][i]["id"]] for i in (0,2,4,6)}
window.refresh_library(); window.refresh_builder(); window.show(); app.processEvents()
startup=time.perf_counter()-started
window.grab().save(str(OUT/"prompt.png"))
if app.platformName()=="windows":
    QTest.qWait(150); window.screen().grabWindow(int(window.winId())).save(str(OUT/"prompt-desktop.png"))
root=Path(r"D:\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable\ComfyUI\models")
if root.is_dir():
    started=time.perf_counter(); records=scan_models(root); scan_time=time.perf_counter()-started
    window.catalog.merge_models(records,root); window.models.root.setText(str(root)); window.models.refresh(); window.tabs.setCurrentIndex(1)
    window.models.list.setCurrentRow(0); app.processEvents(); window.grab().save(str(OUT/"models.png"))
else: records=[]; scan_time=0

album=uid(); window.catalog.put("album",dict(id=album,name="縮圖與參數測試"))
for n in range(6):
    image=QImage(1024,768,QImage.Format.Format_RGB32); image.fill(QColor(38+10*n,44+8*n,47+8*n))
    painter=QPainter(image); painter.setPen(QColor("#dedede")); painter.setFont(QFont("Microsoft JhengHei",28)); painter.drawText(image.rect(),Qt.AlignmentFlag.AlignCenter,f"縮圖測試 {n+1}\n這是測試圖，並非生成範例"); painter.end()
    image.setText("prompt",json.dumps({"3":{"class_type":"KSampler","inputs":{"steps":20,"cfg":5.5,"seed":12}}}))
    path=DATA/f"fixture-{n+1}.png"; image.save(str(path))
    record=import_image(path,DATA,album); window.catalog.put("image",record,album)
window.gallery.album=album; window.gallery.refresh_albums(); window.tabs.setCurrentIndex(2); window.gallery.images.setCurrentRow(0)
app.processEvents(); window.grab().save(str(OUT/"gallery.png"))
dialog=SettingsDialog(window); dialog.show(); app.processEvents(); dialog.grab().save(str(OUT/"settings.png")); dialog.close()
dialog=ModuleDialog(window); dialog.show(); app.processEvents(); dialog.grab().save(str(OUT/"module-dialog.png")); dialog.close()
menu=RoundMenu(window); menu.addAction("複製此提示詞"); menu.addAction("加入／取消選擇"); menu.addAction("編輯提示詞與預覽圖"); menu.addSeparator(); menu.addAction("刪除項目…")
menu.popup(window.mapToGlobal(window.rect().center())); app.processEvents(); menu.grab().save(str(OUT/"context-menu.png")); menu.close()
window.workspace.showPopup(); app.processEvents(); window.workspace.view().window().grab().save(str(OUT/"workspace-popup.png")); window.workspace.hidePopup()
window.tabs.setCurrentIndex(0); window.resize(1100,740); app.processEvents(); window.grab().save(str(OUT/"compact.png"))
window.state["settings"]["ui_size"]=18; window.state["settings"]["prompt_size"]=18; window.apply_theme(); app.processEvents(); window.grab().save(str(OUT/"large-font.png"))
window.state["settings"]["ui_size"]=11; window.state["settings"]["prompt_size"]=12; window.apply_theme(); window.resize(1440,900)

class Memory(ctypes.Structure):
    _fields_=[("cb",ctypes.c_ulong),("PageFaultCount",ctypes.c_ulong)]+[(name,ctypes.c_size_t) for name in ("PeakWorkingSetSize","WorkingSetSize","QuotaPeakPagedPoolUsage","QuotaPagedPoolUsage","QuotaPeakNonPagedPoolUsage","QuotaNonPagedPoolUsage","PagefileUsage","PeakPagefileUsage","PrivateUsage")]
memory=Memory(); memory.cb=ctypes.sizeof(memory)
ctypes.windll.kernel32.GetCurrentProcess.restype=ctypes.c_void_p
ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(ctypes.windll.kernel32.GetCurrentProcess()),ctypes.byref(memory),memory.cb)
cpu=time.process_time(); clock=time.perf_counter()
def finish():
    elapsed=time.perf_counter()-clock
    metrics=dict(startup_seconds=round(startup,3),model_count=len(records),scan_seconds=round(scan_time,3),
        rss_mb=round(memory.WorkingSetSize/1024**2,1),private_mb=round(memory.PrivateUsage/1024**2,1),
        idle_cpu_seconds=round(time.process_time()-cpu,4),idle_wall_seconds=round(elapsed,3),
        platform=app.platformName(),native_material=window.native_material)
    (OUT/"metrics.json").write_text(json.dumps(metrics,indent=2),encoding="utf-8"); print(json.dumps(metrics),flush=True)
    window.close()
QTimer.singleShot(3500,finish)
app.exec()
