"""Owner-bound v7 image imports. Workers copy files; only the UI commits."""
import copy
from PySide6.QtCore import QObject, QTimer
from .image_bindings import import_source


def active(window, key=None):
    owner=getattr(window,'document',None) or window
    tasks=getattr(owner,'_source_imports',{})
    return bool(tasks) if key is None else key in tasks


def blocks_execution(window):
    owner=getattr(window,'document',None) or window
    pending=getattr(owner,'_source_imports',{})
    if not pending:return False
    from .stage_model import input_nodes
    data=window.state['multi_output']
    upstream=input_nodes(window.state,[*data.get('clip_inputs',{}),*data.get('image_inputs',{})])
    return bool(set(pending)&set(upstream))


class SourceImport(QObject):
    def __init__(self, panel, paths, accepted=None):
        window=panel.owner.window
        super().__init__(window)
        self.window=window;self.key=panel.key;self.panel=panel;self.accepted=accepted
        self.directory=window.store.directory;self.store=window.store
        self.workspace=window.state['workspace'];self.context=window.canvas_edit_context
        self.owner=getattr(window,'document',None) or window
        self.original=copy.deepcopy(window.state['canvas_functions']['images'][self.key])
        self.paths=list(paths);self.completed=0;self.cancelled=False;self.job=None
        self.timer=QTimer(self);self.timer.setInterval(50);self.timer.timeout.connect(self.progress)

    def valid(self):
        w=self.window
        from .flow_data import incoming
        return (not w.is_closed() and self.store is w.store and self.workspace==w.state['workspace']
                and self.context is w.canvas_edit_context
                and not incoming(w.state,self.key,'image') and not self.original.get('stage_reference')
                and w.state.get('canvas_functions',{}).get('images',{}).get(self.key)==self.original)

    def start(self):
        if not self.paths or not self.valid() or active(self.window,self.key):return False
        if self.window.comfy.input_flow.source_active(self.key):
            self.window.notice('這份圖片清單仍有未完成工作；先完成或停止這批供應，再更換清單。');return False
        if not self.window.jobs.start('匯入圖片',self.work,self.finish,self.failed,announce=False):return False
        self.job=self.window.jobs.active
        if not hasattr(self.owner,'_source_imports'):self.owner._source_imports={}
        self.owner._source_imports[self.key]=self
        self.timer.start();self.progress()
        self.window.comfy.stateChanged.emit()
        return True

    def work(self, cancel):
        images=[];errors=[]
        for path in self.paths:
            if cancel.is_set():raise InterruptedError('已取消匯入。')
            try:images.append(import_source(path,self.directory))
            except (ValueError,OSError) as exc:errors.append(f'{path}: {exc}')
            self.completed+=1
        return images,errors

    def progress(self):
        self.check_current()
        from shiboken6 import isValid
        if isValid(self.panel):self.panel.import_progress(self)

    def check_current(self):
        if not self.cancelled and not self.valid():self.cancel(source_changed=True)
        return not self.cancelled

    def cancel(self,*,source_changed=False):
        if self.cancelled:return
        already_requested=self.job is not None and self.job.cancel.is_set()
        self.cancelled=True
        if self.job:self.job.cancel.set()
        if source_changed and not already_requested and not self.window.is_closed():
            self.window.notice('已取消匯入圖片：圖片來源已變更。')

    def release(self):
        self.timer.stop()
        self.owner._source_imports.pop(self.key,None)
        from shiboken6 import isValid
        if isValid(self.panel):self.panel.import_progress(None)
        if not self.window.is_closed():self.window.comfy.stateChanged.emit()
        self.deleteLater()

    def failed(self, message):
        self.check_current()
        cancelled=self.cancelled or (self.job is not None and self.job.cancel.is_set())
        self.release()
        if not cancelled and not self.window.is_closed():self.window.notice(message)

    def finish(self, result):
        valid=self.check_current()
        images,errors=result
        self.release()
        if not valid:return
        if self.window.comfy.input_flow.source_active(self.key):
            self.window.notice('執行期間圖片清單已固定；可檢視及切圖，不能改變這批清單。');return
        if images:
            if self.accepted:self.accepted(images)
            else:
                from .source_list import append_items
                self.window.canvas.commit(lambda state:append_items(state,self.key,images,self.directory),label='加入圖片')
        if errors:self.window.notice('未能匯入：\n'+'\n'.join(errors))


def start(panel, paths, accepted=None):
    task=SourceImport(panel,paths,accepted)
    if task.start():return True
    task.deleteLater();return False
