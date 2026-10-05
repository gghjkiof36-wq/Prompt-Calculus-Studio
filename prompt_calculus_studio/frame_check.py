"""Native frame regression diagnostics; requires disposable data (see run.py)."""
import ctypes
import json
import traceback
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


class FrameCheck:
    def __init__(self, window):
        self.window=window; self.results=[]; self.index=0
        self.steps=[(material,state) for material in ("solid","mica","acrylic")
                    for state in ("normal","maximized","minimized","restored")]
        self.user=ctypes.windll.user32
        self.user.GetWindowLongPtrW.argtypes=[ctypes.c_void_p,ctypes.c_int]
        self.user.GetWindowLongPtrW.restype=ctypes.c_ssize_t
        self.watchdog=QTimer(window); self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(lambda:self.fail("Frame diagnostic timed out")); self.watchdog.start(18000)

    def next(self):
        try:
            if self.index==len(self.steps):
                self.watchdog.stop(); self.write(dict(ok=True,checks=self.results))
                self.window.close(); return
            material,state=self.steps[self.index]
            if state=="normal":
                self.window.state["settings"]["material"]=material; self.window.apply_theme()
                self.window.showNormal()
            elif state=="maximized": self.window.showMaximized()
            elif state=="minimized": self.window.showMinimized()
            else: self.window.showNormal()
            QTimer.singleShot(180,self.check)
        except Exception: self.fail(traceback.format_exc())

    def check(self):
        try:
            w=self.window; hwnd=ctypes.c_void_p(int(w.winId()))
            style=self.user.GetWindowLongPtrW(hwnd,-16)&0xffffffff
            exstyle=self.user.GetWindowLongPtrW(hwnd,-20)&0xffffffff
            assert style&0xc00000==0xc00000, "Native caption is missing"
            assert style&0x40000, "Native resizing frame is missing"
            assert style&0x30000==0x30000, "Native minimize/maximize buttons are missing"
            assert not exstyle&0x80000, "WS_EX_LAYERED disables DWM rounded corners"
            assert w.mask().isEmpty(), "Custom region disables DWM rounded corners"
            state=self.steps[self.index][1]
            if state=="maximized": assert self.user.IsZoomed(hwnd)
            if state=="minimized": assert self.user.IsIconic(hwnd)
            if state in ("normal","restored"): assert not self.user.IsZoomed(hwnd) and not self.user.IsIconic(hwnd)
            self.results.append(dict(material=self.steps[self.index][0],state=state,
                style=hex(style),exstyle=hex(exstyle),dpr=w.devicePixelRatioF(),
                dpi=self.user.GetDpiForWindow(hwnd),font_pixels=w.final.font().pixelSize()))
            self.index+=1; QTimer.singleShot(0,self.next)
        except Exception: self.fail(traceback.format_exc())

    def write(self, data):
        (self.window.store.directory/"frame-result.json").write_text(json.dumps(data,indent=2),encoding="utf-8")

    def fail(self, error):
        self.watchdog.stop(); self.write(dict(ok=False,error=error,checks=self.results)); QApplication.instance().exit(2)


def start(window):
    window.frame_check=FrameCheck(window); QTimer.singleShot(100,window.frame_check.next)
