"""Packaged native UI diagnostics. Only run on an empty diagnostic database."""
import copy,json,traceback,time
from PySide6.QtCore import QTimer,QPointF
from PySide6.QtWidgets import QApplication
from .repair_check import PaintCounter
from .composition_image import document,layer
from . import multi_output as model
from .snapshots import make_snapshot,restore_snapshot


class Check:
    def __init__(self,window):
        self.w=window; self.path=window.store.directory; self.result={}
        self.watchdog=QTimer(window); self.watchdog.setSingleShot(True); self.watchdog.timeout.connect(lambda:self.finish('timeout')); self.watchdog.start(25000)
    def later(self,fn,delay=250):
        def call():
            try: fn()
            except Exception: self.finish(traceback.format_exc())
        QTimer.singleShot(delay,call)
    def start(self):
        w=self.w; w.state['settings'].update(online=False,material='solid',separate_selections=True,reduce_motion=False,connection_style='orthogonal'); w.apply_theme(); w.set_interface_mode('canvas')
        c=w.canvas; self.cid=next(iter(c.containers)); self.oid=c.data()['current_output']
        c.commit(lambda s:s['multi_output']['canvases'][self.cid].update(name='主體構圖',position=[-900,-380],display_size=[710,660]))
        c.add_tag('ceramic vase, morning light',QPointF(-860,-240))
        self.c2=c.add_canvas(QPointF(-60,-380)); self.o2=c.add_output(QPointF(730,-380))
        c.commit(lambda s:(s['multi_output']['canvases'][self.c2].update(name='負面描述'),model.connect(s,self.c2,self.o2,'text')))
        c.add_tag('blurry, distorted',QPointF(-20,-220))
        doc=document(768,512); doc['background']='#ede8e0'; doc['layers']=[layer('rect',0,360,768,152,fill='#b7ab97'),layer('ellipse',280,100,205,290,fill='#416b82')]
        c.commit(lambda s:(s['multi_output']['canvases'][self.cid].update(image=doc),model.connect(s,self.cid,self.oid,'image'),model.connect(s,self.o2,model.GENERATOR,'execution'),model.connect(s,self.o2,model.PREVIEW,'preview')))
        c.move_cards({self.oid:[-870,450],self.o2:[-80,450],model.GENERATOR:[720,460],'__result_preview__':[1280,450]}); c.fit(); self.later(self.normal)
    def normal(self):
        for line in self.w.canvas.lines.values():
            path=line.path(); assert path.elementCount()<=4
            for i in range(1,path.elementCount()):
                a,b=path.elementAt(i-1),path.elementAt(i); assert a.x==b.x or a.y==b.y
        self.result['orthogonal_single_middle_transition']=True
        self.w.grab().save(str(self.path/'workspace.png')); self.result['normal_size']=[self.w.width(),self.w.height()]
        self.w.canvas.open_editor(self.cid); self.later(self.editor,260)
    def editor(self):
        editor=self.w.canvas.editor_page; assert editor is not None
        editor.show_text.setChecked(False); editor.view.fit(); self.w.grab().save(str(self.path/'composition.png'))
        self.w.resize(980,680); self.w.state['settings'].update(ui_size=18,prompt_size=18); self.w.apply_theme(refresh_fonts=True)
        self.later(self.large)
    def large(self):
        editor=self.w.canvas.editor_page; assert editor.width()<=self.w.width() and editor.height()<=self.w.height()
        editor.view.fit()
        assert editor.view.width()>180
        self.w.grab().save(str(self.path/'composition-large-font.png'))
        self.result['large_font_small_window']=[self.w.width(),self.w.height(),editor.view.width()]
        editor.close_editor(); self.later(self.returned,250)
    def returned(self):
        assert self.w.canvas.editor_page is None; self.w.canvas.fit(); self.w.grab().save(str(self.path/'workspace-large-font.png'))
        snapshot=make_snapshot(self.w.state); restored=restore_snapshot(self.w.state,snapshot)
        self.result['snapshot_same_text']=model.compiled_outputs(restored)==model.compiled_outputs(self.w.state); assert self.result['snapshot_same_text']
        self.w.persist(); self.result['saved_version']=self.w.store.load()['version']; assert self.result['saved_version']==4
        self.counter=PaintCounter(self.w.canvas.view.viewport()); self.later(self.begin_idle,600)
    def begin_idle(self):
        self.counter.count=0; self.cpu=time.process_time(); self.later(self.idle,1000)
    def idle(self):
        self.result['idle_paints_1s']=self.counter.count; self.result['idle_cpu_ms']=round((time.process_time()-self.cpu)*1000,2)
        assert self.counter.count<=2
        self.result['preview_bytes']=sum(v.sizeInBytes() for v in self.w.canvas.image_previews.values() if v is not None); assert self.result['preview_bytes']<=64*1024*1024
        self.result['dpr']=self.w.devicePixelRatioF(); self.finish()
    def finish(self,error=None):
        self.watchdog.stop(); self.result['ok']=not error
        if error: self.result['error']=error
        (self.path/'v08-result.json').write_text(json.dumps(self.result,ensure_ascii=False,indent=2),encoding='utf-8'); self.w.close()


def start(window):
    window.v08_check=Check(window); window.v08_check.later(window.v08_check.start,400)
