"""Static opt-in callback labels, including callbacks delivered by ZeroTimerEvent.

No stack inspection, arbitrary method profiler, instance text or document data.
Install before constructing owners so Qt connects the wrapped bound methods.
"""
from functools import wraps
from importlib import import_module

from .performance_recorder import object_class


CALLBACKS = (
    ('stage_runner', 'StageRunner', 'pump', 'stage pump'),
    ('stage_batches', None, 'expand', 'batch expansion'),
    ('comfy_client', 'ComfyClient', 'poll', 'ComfyUI poll'),
    ('comfy_client', 'ComfyClient', 'sync', 'ComfyUI sync'),
    ('input_bridge', 'InputBridge', 'poll', 'input bridge'),
    ('workspace_scheduler', 'WorkspaceScheduler', 'refresh', 'workspace scheduler'),
    ('canvas_refresh', 'CanvasRefreshScheduler', 'flush', 'canvas refresh'),
    ('multi_canvas', 'MultiCanvas', 'history_projection', 'history projection'),
    ('run_controls', 'RunControls', 'refresh', 'run controls'),
    ('changes', 'ChangeCoordinator', 'flush', 'change coordinator'),
    ('document_host', 'DesktopDocument', 'flush_views', 'document views'),
    ('document_host', 'DesktopDocument', 'save', 'document save'),
    ('window', 'Window', 'persist', 'document persist'),
    ('workspace_autosave', 'WorkspaceAutosave', 'flush', 'autosave'),
    ('workspace_autosave', 'WorkspaceAutosave', 'poll_markdown', 'external file poll'),
    ('workspace_ambient', 'WorkspaceAmbient', 'advance', 'ambient animation'),
    ('workspace_performance', 'WorkspacePerformance', 'tick', 'monitor heartbeat'),
)
FEATURES = frozenset(row[3] for row in CALLBACKS)


def wrap_callback(recorder, feature, callback):
    if feature not in FEATURES:
        raise ValueError('Unregistered performance feature')

    @wraps(callback)
    def measured(*args, **kwargs):
        class_name = object_class(args[0]) if args else 'callback'
        started = recorder.begin('Callback', class_name)
        try:
            return callback(*args, **kwargs)
        finally:
            recorder.end(started, feature, class_name)
    return measured


def install(recorder):
    for module, cls, name, feature in CALLBACKS:
        owner = import_module('.'+module, __package__)
        if cls:
            owner = getattr(owner, cls)
        original = getattr(owner, name)
        setattr(owner, name, wrap_callback(recorder, feature, original))
        recorder.undo.append((owner, name, original))
