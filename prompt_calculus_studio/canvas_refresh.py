"""Per-view invalidation for the v7 embedded Canvas.

Transactions still present synchronously before their final Undo projection.
The queued notification tail only presents state that view has not seen. No
notification receiver writes, saves, or invokes a renderer synchronously.
"""
import copy
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit, QPlainTextEdit


@dataclass(frozen=True)
class Difference:
    mode: str = 'full'
    roots: frozenset = frozenset()


def _active_content(value):
    """The active scene duplicates top-level fields; retain all inactive data."""
    result=dict(value)
    scenes=value.get('workspace_scenes',{})
    if 'items' in scenes:
        result['workspace_scenes']=dict(scenes,items={k:v for k,v in scenes['items'].items()
                                                    if k!=value.get('workspace')})
    return result


def _geometry_free(value):
    result={k:v for k,v in value.items() if k not in ('text_positions','text_sizes')}
    if 'multi_output' in value:
        data=value['multi_output']
        result['multi_output']=dict(data,canvases={key:{k:v for k,v in canvas.items()
            if k not in ('position','display_size')} for key,canvas in data['canvases'].items()})
    return result


def _shape(node):
    editable={'name','prompt','weight','enabled','excludes','deduplicate','combination',
              'sequence','variants','round_drafts','selection_cursor'}
    return ({k:v for k,v in node.items() if k not in editable|{'children','overlays'}},
            tuple(_shape(child) for child in node.get('children',[])),
            tuple(_shape(child) for child in node.get('overlays',[])))


def _text_changed(before,after):
    # Navigation, enable toggles and folding are separate survey rows. A
    # confirmed text edit can also carry the dialog's weight/exclusion options.
    for key in ('name','prompt','combination','round_drafts'):
        if before.get(key)!=after.get(key):return True
    for field in ('sequence','variants'):
        if (before.get(field) or {}).get('groups')!=(after.get(field) or {}).get('groups'):return True
    return any(_text_changed(a,b) for field in ('children','overlays')
               for a,b in zip(before.get(field,[]),after.get(field,[])))


def difference(before, after):
    """Translate top-level notifications conservatively, never 'uses = one card'."""
    if before is None or before.get('workspace')!=after.get('workspace'):
        return Difference()
    if before==after:return Difference('none')
    old,new=_active_content(before),_active_content(after)
    if _geometry_free(old)==_geometry_free(new):
        keys={key for field in ('text_positions','text_sizes')
              for key in old.get(field,{}).keys()|new.get(field,{}).keys()
              if old.get(field,{}).get(key)!=new.get(field,{}).get(key)}
        if len(keys)==1 and keys<=old.get('uses',{}).keys():
            return Difference('geometry')
        return Difference()
    a,b=old.get('uses',{}),new.get('uses',{})
    if list(a)!=list(b):return Difference()
    roots=frozenset(key for key in a if a[key]!=b[key])
    if len(roots)!=1:return Difference()
    key=next(iter(roots))
    if _shape(a[key])!=_shape(b[key]):return Difference()
    if not _text_changed(a[key],b[key]):return Difference()
    # Root-local content is the only supported content invalidation. Connection,
    # membership, output, source, library, and unknown changes all stay full.
    old=dict(old,uses={});new=dict(new,uses={})
    if old!=new:return Difference()
    return Difference('roots',roots)


class CanvasRefreshScheduler(QObject):
    def __init__(self,canvas):
        super().__init__(canvas)
        self.canvas=canvas
        self.timer=QTimer(self);self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.flush)
        self.pending=False;self.full=False;self.adopted=False
        self.settings=None;self.owner=None
        self.presented()
        canvas.window.documentChanged.connect(self.notified)

    def enabled(self):
        return self.canvas.window.state.get('multi_output',{}).get('version',0)>=7

    def notified(self,fields,reason,kind):
        if not self.enabled() or kind=='derived':return
        from .document_operations import MULTI_HISTORY_FIELDS
        unknown=fields!='ALL' and bool(set(fields)-set(MULTI_HISTORY_FIELDS)-{'settings','content_history'})
        self.request(full=fields=='ALL' or unknown,adopted=fields=='ALL' or unknown)
        # Siblings keep their existing full path. Each owns its timer and its
        # presented state; an origin's presentation cannot acknowledge a sibling.
        document=self.canvas.window.document
        for host in document.hosts:
            scheduler=getattr(host.canvas,'refresh_scheduler',None)
            if scheduler is not None and scheduler is not self:
                scheduler.request(full=True,adopted=fields=='ALL')

    def request(self,*,full=False,adopted=False):
        if not self.enabled() or self.canvas.window.closing:return
        self.pending=True;self.full|=full;self.adopted|=adopted
        if not self.timer.isActive():self.timer.start(0)

    def consume(self):
        full=self.full or self.adopted
        self.timer.stop();self.pending=False;self.full=False;self.adopted=False
        return full

    def presented(self):
        self.owner=(self.canvas.window.store,self.canvas.window.state.get('workspace'))
        self.settings=copy.deepcopy(self.canvas.window.state.get('settings',{}))

    def busy(self):
        canvas=self.canvas
        focus=QApplication.focusWidget()
        return (canvas.view.scene().mouseGrabberItem() is not None
                or canvas.connection_gesture.anchor is not None
                or isinstance(focus,(QLineEdit,QTextEdit,QPlainTextEdit)))

    def plan(self,now,*,full=False):
        canvas=self.canvas
        if (full or self.busy() or self.settings!=canvas.window.state.get('settings',{})
                or self.owner!=(canvas.window.store,canvas.window.state.get('workspace'))
                or getattr(canvas,'_scene_refresh_pending',True)):
            return Difference()
        return difference(getattr(canvas,'_visible_scene_state',None),now)

    def flush(self):
        if not self.pending:return
        canvas=self.canvas
        if canvas.window.closing:self.consume();return
        now=canvas.history_state()
        # N01's late content notification and save-synchronize never save or
        # present twice. ALL remains an unconditional full invalidation.
        if (not self.adopted and now==getattr(canvas,'_visible_scene_state',None)
                and self.settings==canvas.window.state.get('settings',{})
                and self.owner==(canvas.window.store,canvas.window.state.get('workspace'))):
            self.consume();return
        plan=self.plan(now,full=self.full or self.adopted)
        self.consume()
        canvas.refresh(_difference=plan)
