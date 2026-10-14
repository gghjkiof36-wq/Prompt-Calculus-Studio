"""Coalesce UI side effects; this module never submits generation work."""
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
        def without(mapping,fields):return {k:v for k,v in mapping.items() if k not in fields}
        # These projections are compared immediately, never saved or mutated.
        # Copy only the dictionaries with ignored fields, not the entire library.
        result=without(value,('text_positions','text_sizes'))
        if 'multi_output' in value:
            multi=dict(value['multi_output']);result['multi_output']=multi
            for kind in ('canvases','outputs','clip_inputs','image_inputs','schedulers','stages'):
                fields=('collapsed',)
                if kind=='canvases':fields+=('position','display_size','edit_view')
                elif kind=='outputs':fields+=('panel_ratio','preview_collapsed','source_collapsed')
                if kind in multi:multi[kind]={k:without(v,fields) for k,v in multi[kind].items()}
            if 'workflow_order' in multi:multi['workflow_order']=without(multi['workflow_order'],('collapsed',))
        if 'canvas_functions' in value:
            functions=without(value['canvas_functions'],('preview_display',));result['canvas_functions']=functions
            if 'images' in functions:functions['images']={k:without(v,('collapsed','management_collapsed')) for k,v in functions['images'].items()}
            if 'previews' in functions:functions['previews']={k:without(v,('collapsed',)) for k,v in functions['previews'].items()}
        return result
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
