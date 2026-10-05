"""Refresh cached display metrics after a display/resume notification.

This is event driven, not a periodic monitor. It never changes Windows display
settings, saved font preferences, window geometry, prompt text or selections.
"""
import sys
from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QApplication


class DisplayRecovery(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window=window; self.active=True; self.screen=None; self.handle=None
        self.timer=QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.refresh)
        self.settle=QTimer(self); self.settle.setSingleShot(True); self.settle.timeout.connect(self.refresh)
        self.reasons=set(); self.refresh_count=0
        QApplication.instance().applicationStateChanged.connect(self.application_state)
        self.bind_window()

    def bind_window(self):
        handle=self.window.windowHandle()
        if handle and handle is not self.handle:
            self.handle=handle; handle.screenChanged.connect(self.screen_changed)
        if handle: self.bind_screen(handle.screen())

    def bind_screen(self, screen):
        if screen is self.screen: return
        if self.screen:
            try:
                self.screen.logicalDotsPerInchChanged.disconnect(self.screen_metrics)
                self.screen.geometryChanged.disconnect(self.screen_metrics)
            except (RuntimeError,TypeError): pass
        self.screen=screen
        if screen:
            screen.logicalDotsPerInchChanged.connect(self.screen_metrics)
            screen.geometryChanged.connect(self.screen_metrics)

    def screen_changed(self, screen):
        self.bind_screen(screen); self.schedule("screen")

    def screen_metrics(self, *_): self.schedule("metrics")

    def application_state(self, state):
        if state==Qt.ApplicationState.ApplicationActive: self.schedule("activation")

    def schedule(self, reason):
        if not self.active: return
        self.reasons.add(reason); self.timer.start(350)
        if reason in ("resume","display","dpi"): self.settle.start(1500)

    def native_event(self, event_type, message):
        if sys.platform!="win32" or bytes(event_type)!=b"windows_generic_MSG": return
        from ctypes import wintypes
        msg=wintypes.MSG.from_address(int(message))
        self.native_notification(msg.message,msg.wParam)

    def native_notification(self, message, parameter):
        if message==0x0218 and parameter in (0x6,0x7,0x12): self.schedule("resume")
        elif message==0x02E0: self.schedule("dpi")
        elif message in (0x007E,0x031E): self.schedule("display")

    def refresh(self):
        if not self.active: return
        self.bind_window()
        w=self.window; sizes=w.split.sizes(); builder_sizes=w.builder_split.sizes()
        w.apply_theme(preserve_layout=True,refresh_fonts=True)
        w.split.setSizes(sizes); w.builder_split.setSizes(builder_sizes)
        for view in (w.library,w.selected,w.module_list,w.models.list,w.gallery.images):
            view.doItemsLayout(); view.viewport().update()
        w.update(); self.reasons.clear(); self.refresh_count+=1

    def stop(self):
        self.active=False; self.timer.stop(); self.settle.stop()
