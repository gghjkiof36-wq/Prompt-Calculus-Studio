"""Coalesce UI side effects; this module never submits generation work."""
import copy
from enum import Enum
from PySide6.QtCore import QObject,QTimer

class Change(str,Enum):
    PROMPT='prompt'
    LAYOUT='layout'
    MODELS='models'
    SETTINGS='settings'
    GENERATION='generation'

def layout_only(before,after):
    """History compatibility: geometry never changes text or output resolution."""
    def content(value):
        value=copy.deepcopy(value)
        for key in ('text_positions','text_sizes'):value.pop(key,None)
        multi=value.get('multi_output',{})
        for canvas in multi.get('canvases',{}).values():
            for key in ('position','display_size'):canvas.pop(key,None)
        for output in multi.get('outputs',{}).values():output.pop('panel_ratio',None)
        return value
    return content(before)==content(after)

class ChangeCoordinator(QObject):
    def __init__(self,window):
        super().__init__(window); self.window=window; self.pending=set(); self.render=set()
        self.timer=QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.flush)
    def request(self,scope=Change.PROMPT,refresh=True):
        scope=Change(scope); w=self.window
        # Turning networking off cancels in-flight work immediately, before the UI tick.
        if hasattr(w,'civitai_network'):w.civitai_network.refresh()
        if hasattr(w,'settings_page') and not w.state['settings'].get('online',True):w.settings_page.civitai.stop_images()
        self.pending.add(scope); w.save_timer.start(350)
        if refresh:self.render.add(scope)
        if not self.timer.isActive():self.timer.start(0)
    def flush(self):
        self.timer.stop(); scopes=self.pending; render=self.render; self.pending=set(); self.render=set(); w=self.window
        if w.closing:return
        if Change.LAYOUT in render and hasattr(w,'canvas'):w.canvas.refresh_layout()
        if Change.PROMPT in scopes:
            if Change.PROMPT in render and hasattr(w,'canvas'):w.canvas.update_output()
            if hasattr(w,'comfy'):w.comfy.schedule_sync()
        if Change.GENERATION in scopes and hasattr(w,'comfy'):w.comfy.stateChanged.emit()
    def stop(self):self.timer.stop(); self.pending.clear(); self.render.clear()
