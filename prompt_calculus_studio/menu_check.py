"""Opt-in native UI regression check; only runs with a fresh diagnostic DB."""
import json
import traceback
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QTimer, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton
from .media import import_image, scan_models
from .widgets import RoundMenu


class MenuCheck:
    def __init__(self,window):
        self.window=window; self.results=[]; self.index=0
        self.app=QApplication.instance(); self.errors=[]
        window.error=self.errors.append
        window.state["settings"]["online"]=False; window.first_models=False
        directory=window.store.directory
        model_root=directory/"fixture-models"; (model_root/"loras").mkdir(parents=True)
        (model_root/"loras"/"fixture.safetensors").write_bytes(b"not a model: diagnostic fixture")
        window.catalog.merge_models(scan_models(model_root),model_root)
        window.models.root.setText(str(model_root)); window.models.refresh(); window.models.list.setCurrentRow(0)
        window.models.trigger.setPlainText("fixture trigger"); window.models.save()
        album="diagnostic-album"; window.catalog.put("album",dict(id=album,name="Diagnostic"))
        image=QImage(32,32,QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.gray)
        path=directory/"fixture.png"; image.save(str(path))
        record=import_image(path,directory,album); window.catalog.put("image",record,album)
        window.gallery.album=album; window.gallery.refresh_albums()
        item=window.state["items"][0]; window.current_module=item["module"]
        window.state["selections"]={item["module"]:[item["id"]]}
        window.refresh_modules(); window.refresh_library(); window.refresh_builder()
        self.steps=[]
        for repeat in range(2):
            for entry in ("workspace","data","module","item","builder","model","gallery"):
                self.steps.append((entry,"dismiss"))
        self.steps.extend([("module","copy"),("item","copy"),("builder","copy"),("model","copy"),
                           ("workspace","settings"),("workspace","new"),("workspace","delete-cancel"),("workspace","delete-confirm"),
                           ("gallery","attach")])
        self.watchdog=QTimer(window); self.watchdog.setSingleShot(True); self.watchdog.timeout.connect(lambda:self.fail("Native menu check timed out")); self.watchdog.start(20000)

    def click(self,widget,pos):
        local=QPointF(pos); global_pos=QPointF(widget.mapToGlobal(pos))
        for typ,buttons in [(QEvent.Type.MouseButtonPress,Qt.MouseButton.LeftButton),(QEvent.Type.MouseButtonRelease,Qt.MouseButton.NoButton)]:
            event=QMouseEvent(typ,local,global_pos,Qt.MouseButton.LeftButton,buttons,Qt.KeyboardModifier.NoModifier)
            QCoreApplication.sendEvent(widget,event)

    def open_entry(self,name):
        w=self.window
        w.tabs.setCurrentIndex({"model":1,"gallery":2}.get(name,0))
        if name in ("workspace","data"):
            text="⋯" if name=="workspace" else "資料與備份"
            control=next(b for b in w.findChildren(QPushButton) if b.text()==text)
            self.click(control,control.rect().center())
        else:
            if name=="module": view=w.module_list; item=view.currentItem()
            elif name=="item": view=w.library; item=view.item(0)
            elif name=="builder": view=w.selected; item=view.topLevelItem(0).child(0)
            elif name=="model": view=w.models.list; item=view.item(0)
            else: view=w.gallery.images; item=view.item(0)
            view.setCurrentItem(item)
            view.customContextMenuRequested.emit(view.visualItemRect(item).center())

    def next(self):
        if self.index==len(self.steps): self.finish(); return
        try:
            self.entry,self.action=self.steps[self.index]
            self.open_entry(self.entry)
            QTimer.singleShot(90,self.inspect)
        except Exception: self.fail(traceback.format_exc())

    def inspect(self):
        try:
            menus=[m for m in self.window.findChildren(RoundMenu) if m.isVisible()]
            if len(menus)!=1: raise AssertionError(f"Expected one visible menu, got {len(menus)}")
            menu=menus[0]
            if self.action=="dismiss": menu.close()
            else:
                labels={"copy":("複製本模組已選提示詞" if self.entry=="module" else "複製此提示詞" if self.entry=="item" else "複製觸發詞" if self.entry=="model" else "複製"),
                        "settings":"工作區設定","new":"新增工作區…","delete-cancel":"刪除目前工作區…", "delete-confirm":"刪除目前工作區…","attach":"附上目前工作區資料"}
                action=next(a for a in menu.actions() if a.text()==labels[self.action])
                self.dialog_visits=0
                if self.action in ("settings","new","delete-cancel","delete-confirm"):
                    self.dialog_timer=QTimer(self.window); self.dialog_timer.setInterval(60); self.dialog_timer.timeout.connect(self.handle_dialog); self.dialog_timer.start()
                menu.setActiveAction(action)
                self.click(menu,menu.actionGeometry(action).center())
                if self.action=="copy":
                    expected="fixture trigger" if self.entry=="model" else "1girl, red dress"
                    if self.app.clipboard().text()!=expected: raise AssertionError("Menu copy action did not run")
                if self.action in ("settings","new","delete-cancel","delete-confirm"):
                    self.dialog_timer.stop(); self.dialog_timer.deleteLater()
                    if self.dialog_visits<1: raise AssertionError("Menu did not open its dialog")
                if self.action in ("new","delete-cancel") and len(self.window.state["workspaces"])!=2:
                    raise AssertionError("Workspace creation/cancellation did not preserve the expected state")
                if self.action=="delete-confirm" and len(self.window.state["workspaces"])!=1:
                    raise AssertionError("Workspace delete action did not complete")
                if self.action=="attach" and not self.window.gallery.record.get("manual"):
                    raise AssertionError("Gallery action did not save a manual snapshot")
            QTimer.singleShot(60,self.complete_step)
        except Exception: self.fail(traceback.format_exc())

    def handle_dialog(self):
        try:
            dialog=self.app.activeModalWidget()
            if not isinstance(dialog,QDialog): return
            self.dialog_visits+=1
            if self.action=="new" and dialog.windowTitle()=="新增工作區":
                dialog.findChild(QLineEdit).setText("Menu diagnostic workspace"); dialog.accept()
            elif self.action=="delete-confirm": dialog.accept()
            else: dialog.reject()
        except Exception: self.fail(traceback.format_exc())

    def complete_step(self):
        try:
            if self.errors: raise AssertionError(str(self.errors))
            if self.window.findChildren(RoundMenu): raise AssertionError("Closed menu was not released")
            self.results.append(f"{self.entry}:{self.action}"); self.index+=1
            QTimer.singleShot(0,self.next)
        except Exception: self.fail(traceback.format_exc())

    def finish(self):
        self.watchdog.stop()
        self.write(dict(ok=True,checks=self.results,platform=self.app.platformName()))
        self.window.close()

    def write(self,result):
        (self.window.store.directory/"menu-result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")

    def fail(self,error):
        self.watchdog.stop(); self.write(dict(ok=False,error=error,checks=self.results)); self.app.exit(2)


def start(window):
    window.menu_check=MenuCheck(window)
    QTimer.singleShot(100,window.menu_check.next)
