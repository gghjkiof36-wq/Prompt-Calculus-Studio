"""Application-owned Qt projection; inference never runs on the GUI/Jobs pool."""
from __future__ import annotations

import copy
import hashlib
import json
from http.client import HTTPException
from queue import Empty, Queue
from threading import Event, Lock
import time

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from ..service_adapters import ServiceRequest
from .engine import Cancellation, Cancelled, InferenceRuntime, RuntimeFactory
from .approval import normalize_mode
from .library import ModelLibrary
from .registry import MAX_MODEL_PROMPT, MODEL_PARAMETERS, ModelRegistry
from .runtime import LlamaRuntime
from .settings import LOAD_KEYS, normalize_settings, resolve_runtime, validate_messages


BUSY = frozenset(('loading', 'generating', 'stopping', 'unloading'))


def for_workspace(workspace):
    """Share the registry and engine between Explore and every conversation.

    Constructing the service reads local configuration only. A submitted chat
    prepares its chosen model; browsing or selecting a model never loads it.
    """
    service = getattr(workspace, 'local_ai', None)
    if service is None:
        service = LocalAIService(workspace.data, workspace)
        workspace.local_ai = service
    return service


class _InferenceThread(QThread):
    status = Signal(object)
    text = Signal(object, str)
    result = Signal(object, str, str)

    def __init__(self, parent=None, *, runtime_factory: RuntimeFactory = LlamaRuntime):
        super().__init__(parent)
        self._runtime_factory = runtime_factory
        self.commands = Queue()
        self.closing = Event()
        self.exiting = Event()
        self._lock = Lock()
        self._current_cancel = None
        self._idle_minutes = 10
        self._epoch = 0

    def set_idle_minutes(self, value):
        with self._lock:
            self._idle_minutes = value

    def enqueue(self, kind, epoch, *args):
        cancel = Cancellation()
        with self._lock:
            if self.closing.is_set() or self.exiting.is_set():
                return None
            self._current_cancel = cancel
            self.commands.put((kind, epoch, args, cancel))
        return cancel

    def request_shutdown(self):
        self.closing.set()
        with self._lock:
            cancel = self._current_cancel
        if cancel is not None:
            cancel.cancel()

    def _publish(self, runtime, state, error=''):
        self.status.emit({'epoch': self._epoch, 'state': state, 'error': error,
                          'model': runtime.model['id'] if runtime and runtime.model else '',
                          'settings': dict(runtime.settings) if runtime and runtime.settings else None})

    def run(self):
        runtime: InferenceRuntime | None = None
        last_use = time.monotonic()
        failure = ''
        try:
            runtime = self._runtime_factory()
            while not self.closing.is_set():
                try:
                    kind, self._epoch, args, cancel = self.commands.get(timeout=.25)
                except Empty:
                    if runtime.model is not None and not runtime.alive:
                        runtime.stop()
                        self._publish(runtime, 'error', '本地引擎意外停止，請重新傳送訊息。')
                    with self._lock:
                        idle = self._idle_minutes
                    if runtime.alive and idle and time.monotonic() - last_use >= idle * 60:
                        self._publish(runtime, 'unloading')
                        runtime.stop()
                        self._publish(runtime, 'unloaded')
                    continue
                if self.closing.is_set():
                    cancel.cancel()
                if kind == 'load':
                    self._publish(runtime, 'loading')
                    try:
                        runtime.start(args[0], args[1], cancel)
                        self._publish(runtime, 'ready')
                    except Cancelled:
                        runtime.stop()
                        self._publish(runtime, 'unloaded')
                    except Exception as exc:
                        runtime.stop()
                        self._publish(runtime, 'error', _display_error(exc))
                elif kind == 'unload':
                    self._publish(runtime, 'unloading')
                    runtime.stop()
                    self._publish(runtime, 'unloaded')
                elif kind == 'chat':
                    request, model, messages, params = args
                    pieces = []
                    preparing = False

                    def delta(text):
                        cancel.check()
                        pieces.append(text)
                        self.text.emit(request, text)

                    try:
                        cancel.check()
                        # One command owns both preparation and generation, so
                        # another document cannot take the slot between them.
                        if not _runtime_matches(runtime, model['id'], params):
                            preparing = True
                            self._publish(runtime, 'loading')
                            runtime.start(model, params, cancel)
                            cancel.check()
                            preparing = False
                        self._publish(runtime, 'generating')
                        cancel.check()
                        outcome, detail = runtime.generate(messages, params, cancel, delta)
                        cancel.check()
                    except Exception as exc:
                        if cancel.event.is_set() or isinstance(exc, Cancelled):
                            outcome, detail = 'cancelled', ''.join(pieces)
                        else:
                            outcome, detail = 'error', _display_error(exc)
                    # Even an error may leave server-side inference running.
                    # An idle slot or confirmed child exit is the release point.
                    if preparing:
                        runtime.stop()
                        state = 'unloaded' if outcome == 'cancelled' else 'error'
                    else:
                        idle = runtime.ensure_idle()
                        state = 'ready' if idle else 'unloaded'
                    self.result.emit(request, outcome, detail)
                    self._publish(runtime, state, detail if outcome == 'error' else '')
                last_use = time.monotonic()
                with self._lock:
                    if self._current_cancel is cancel:
                        self._current_cancel = None
        except Exception as exc:
            # Stop accepting work before the error reaches the GUI; cleanup can
            # take longer than the queued status delivery when the OS is busy.
            with self._lock:
                self.exiting.set()
            failure = _display_error(exc)
            self._publish(runtime, 'error', failure)
        finally:
            with self._lock:
                self.exiting.set()
            # A rare OS termination failure must keep the supervisor alive,
            # rather than falsely allowing the shell to destroy its owner.
            while runtime is not None:
                try:
                    runtime.stop()
                except Exception as exc:
                    self._publish(runtime, 'error', _display_error(exc))
                if not runtime.alive:
                    break
                time.sleep(.5)
            # A queued request during shutdown is still owed a terminal event.
            while True:
                try:
                    kind, _epoch, args, _cancel = self.commands.get_nowait()
                except Empty:
                    break
                if kind == 'chat':
                    cancelled = self.closing.is_set() or _cancel.event.is_set()
                    self.result.emit(args[0], 'cancelled' if cancelled else 'error',
                                     '' if cancelled else failure or '本地引擎未能準備完成，請重試。')


def _runtime_matches(runtime, model_id, settings):
    return bool(runtime.alive and runtime.model and runtime.model['id'] == model_id
                and runtime.settings is not None
                and all(runtime.settings[key] == settings[key] for key in LOAD_KEYS))


def _display_error(exc):
    if isinstance(exc, (RuntimeError, ValueError)):
        return str(exc)[:1000]
    if isinstance(exc, (OSError, HTTPException)):
        return '無法與本地引擎通訊，或引擎已停止。請確認引擎與模型後重新傳送。'
    return '本地推論未完成，請重新傳送訊息。'


class LocalAIService(QObject):
    """One instance per app, independent of chat pages and document owners.

    Public methods run on the owning GUI thread. submit() reserves one slot and
    prepares its frozen model/settings before generating on the private thread.
    Selecting a model or configuring settings never starts a thread/process.
    finished(request, status, detail): detail is the entire accepted text for
    completed/limited/cancelled, and a user-facing message for error. The caller
    must still check its document scope and retain already emitted deltas.
    """
    changed = Signal()
    delta = Signal(object, str)
    finished = Signal(object, str, str)

    def __init__(self, data_dir, parent=None, *, runtime_factory: RuntimeFactory = LlamaRuntime):
        super().__init__(parent)
        self._runtime_factory = runtime_factory
        self._registry = ModelRegistry(data_dir)
        self._worker = None
        self._epoch = 0
        self._active = None
        self._cancel = None
        self._loaded_model = ''
        self._loaded_settings = None
        self._closing = False
        self.error = self._registry.error
        self.state = 'error' if self.error else self._available_state()
        self._model_library = ModelLibrary(
            self._registry, self,
            can_apply=lambda: not self._closing and self._active is None and self.state not in BUSY)
        self._model_library.changed.connect(self.changed.emit)
        self.changed.connect(self._model_library.accept_pending)

    @property
    def model_folder(self):
        return self._model_library.folder

    @property
    def default_model_folder(self):
        return self._model_library.default_folder

    @property
    def model_folder_busy(self):
        return self._model_library.busy

    @property
    def model_folder_state(self):
        return self._model_library.state

    @property
    def model_folder_message(self):
        return self._model_library.message

    def set_model_folder(self, path):
        return self._model_library.set_folder(path)

    def reset_model_folder(self):
        return self._model_library.reset_folder()

    def refresh_model_folder(self):
        return self._model_library.refresh()

    def cancel_model_folder_scan(self):
        return self._model_library.cancel()

    @property
    def settings(self):
        return dict(self._registry.settings)

    @property
    def approval_mode(self):
        return self._registry.approval_mode

    def configure_approval_mode(self, value):
        """User preference only; never a generation parameter or model output.

        Text generation has no action executor and is unaffected by all modes.
        Mode changes therefore need not stop an in-flight plain-text response.
        """
        if self._closing:
            return self._fail('本地 AI 正在關閉。')
        try:
            mode = normalize_mode(value)
            if mode == self.approval_mode:
                return True
            self._registry.update(approval_mode=mode)
        except (OSError, ValueError, TypeError) as exc:
            return self._fail(f'審查方式未保存：{exc}')
        self.error = ''
        self.changed.emit()
        return True

    def settings_for_model(self, model_id):
        if not any(model['id'] == model_id for model in self._registry.models):
            raise ValueError('這個模型已不在清單中，請重新選擇模型。')
        preset = self._registry.model_presets.get(model_id)
        return normalize_settings(preset['parameters'] if preset else {}, base=self.settings)

    @property
    def generation_settings(self):
        return self.settings_for_model(self.selected_model) if self.selected_model else self.settings

    def model_preset(self, model_id):
        settings = self.settings_for_model(model_id)
        preset = self._registry.model_presets.get(model_id)
        overrides = copy.deepcopy(preset['parameters']) if preset else {}
        prompt = preset['system_prompt'] if preset else ''
        # The token includes inherited/common values, so a staged form cannot
        # save over a newer preset or a common configuration changed elsewhere.
        revision = hashlib.sha256(json.dumps([model_id, settings, overrides, prompt],
                                             sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        return dict(model_id=model_id, parameters={key: settings[key] for key in MODEL_PARAMETERS},
                    overrides=overrides, system_prompt=prompt, revision=revision)

    def configure_model(self, model_id, values, *, system_prompt=None, expected_revision=None):
        if not self._can_edit():
            return False
        try:
            current = self.model_preset(model_id)
            if expected_revision != current['revision']:
                raise ValueError('模型設定已變更，請重新讀取後再儲存。')
            if not isinstance(values, dict) or set(values) - set(MODEL_PARAMETERS):
                raise ValueError('模型設定包含不支援的項目。')
            settings = normalize_settings(values, base=self.settings_for_model(model_id))
            prompt = current['system_prompt'] if system_prompt is None else system_prompt
            if not isinstance(prompt, str) or len(prompt) > MAX_MODEL_PROMPT:
                raise ValueError('模型預設指示格式無效或過長。')
            presets = copy.deepcopy(self._registry.model_presets)
            # Freeze all six values together: subsequent default edits cannot
            # combine a sparse context/max-token pair into an invalid profile.
            presets[model_id] = dict(parameters={key: settings[key] for key in MODEL_PARAMETERS},
                                     system_prompt=prompt)
            if presets != self._registry.model_presets:
                self._registry.update(model_presets=presets)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return self._fail(f'模型設定未保存：{exc}')
        self.error = ''
        self.changed.emit()
        return True

    def reset_model_preset(self, model_id, *, expected_revision=None):
        if not self._can_edit():
            return False
        try:
            if expected_revision != self.model_preset(model_id)['revision']:
                raise ValueError('模型設定已變更，請重新讀取後再重置。')
            presets = copy.deepcopy(self._registry.model_presets)
            if model_id in presets:
                del presets[model_id]
                self._registry.update(model_presets=presets)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return self._fail(f'模型設定未重置：{exc}')
        self.error = ''
        self.changed.emit()
        return True

    @property
    def models(self):
        return copy.deepcopy(self._registry.models)

    @property
    def selected_model(self):
        return self._registry.selected_model

    @property
    def loaded_model(self):
        return self._loaded_model

    @property
    def loaded_settings(self):
        return dict(self._loaded_settings) if self._loaded_settings is not None else None

    @property
    def needs_reload(self):
        return bool(self._loaded_model and (
            self.selected_model != self._loaded_model or self._loaded_settings is None or
            any(self.generation_settings[key] != self._loaded_settings[key] for key in LOAD_KEYS)))

    @property
    def can_submit(self):
        """Whether Send may reserve the slot; validation failures remain explicit."""
        return bool(self.selected_model and not self._closing and self._active is None
                    and self.state not in BUSY
                    and (self._worker is None or not self._worker.exiting.is_set()))

    def _available_state(self):
        try:
            resolve_runtime(self._registry.settings['runtime_path'])
        except (OSError, ValueError):
            return 'unavailable'
        return 'unloaded'

    def _fail(self, message):
        self.error = str(message)
        self.changed.emit()
        return False

    def _can_edit(self):
        if self._closing:
            return self._fail('本地 AI 正在關閉。')
        if self._worker is not None and self._worker.exiting.is_set():
            self.state = 'stopping'
            return self._fail('本地引擎仍在清理程序，請等待停止完成。')
        if self.state in BUSY:
            return self._fail('請先停止目前操作，再變更模型或設定。')
        return True

    def _ensure_worker(self):
        if self._worker is None:
            worker = _InferenceThread(self, runtime_factory=self._runtime_factory)
            worker.set_idle_minutes(self.settings['idle_minutes'])
            worker.status.connect(self._on_status, Qt.ConnectionType.QueuedConnection)
            worker.text.connect(self._on_delta, Qt.ConnectionType.QueuedConnection)
            worker.result.connect(self._on_result, Qt.ConnectionType.QueuedConnection)
            worker.finished.connect(self._on_thread_finished, Qt.ConnectionType.QueuedConnection)
            self._worker = worker
            worker.start()
        return self._worker

    def configure(self, values):
        if not self._can_edit():
            return False
        try:
            settings = normalize_settings(values, base=self.settings)
            self._registry.update(settings=settings)
        except (OSError, ValueError, TypeError) as exc:
            return self._fail(f'設定未保存：{exc}')
        if self._worker is not None:
            self._worker.set_idle_minutes(settings['idle_minutes'])
        # Keep the current model until the next submitted request needs these
        # settings. Merely editing configuration does not consume model resources.
        if not self._loaded_model:
            self.state = self._available_state()
        self.error = ''
        self.changed.emit()
        return True

    def register_model(self, path):
        if not self._can_edit():
            return None
        try:
            record = self._registry.register(path)
        except (OSError, ValueError, TypeError) as exc:
            self._fail(f'模型未加入：{exc}')
            return None
        self.error = ''
        self.changed.emit()
        return record

    def select_model(self, model_id):
        if not self._can_edit():
            return False
        if not any(m['id'] == model_id for m in self.models):
            return self._fail('模型已不在清單中，請重新加入。')
        try:
            self._registry.update(selected_model=model_id)
        except (OSError, ValueError) as exc:
            return self._fail(f'模型選擇未保存：{exc}')
        self.error = ''
        self.changed.emit()
        return True

    def load(self):
        if not self._can_edit():
            return False
        model = next((m for m in self.models if m['id'] == self.selected_model), None)
        if model is None:
            return self._fail('請先加入並選擇一個 GGUF 模型。')
        settings = self.settings_for_model(model['id'])
        try:
            resolve_runtime(settings['runtime_path'])
        except (OSError, ValueError) as exc:
            self.state = 'unavailable'
            return self._fail(exc)
        self._epoch += 1
        self.state, self.error = 'loading', ''
        self._cancel = self._ensure_worker().enqueue('load', self._epoch, model, settings)
        if self._cancel is None:
            self.state = 'error'
            return self._fail('本地引擎正在清理程序，無法開始載入。請等待停止完成後重試。')
        self.changed.emit()
        return True

    def unload(self):
        if self._closing:
            return False
        if self.state == 'loading':
            if self._cancel is not None:
                self._cancel.cancel()
            self.state = 'stopping'
            self.changed.emit()
            return True
        if self.state in BUSY:
            return self._fail('請先停止回覆，待停止完成後再卸載。')
        if self._worker is None:
            self.state, self.error = self._available_state(), ''
            self.changed.emit()
            return True
        self._epoch += 1
        self.state, self.error = 'unloading', ''
        self._cancel = self._worker.enqueue('unload', self._epoch)
        if self._cancel is None:
            self.state = 'error'
            return self._fail('本地引擎仍在清理程序，請等待停止完成。')
        self.changed.emit()
        return True

    def submit(self, request):
        if not self.can_submit:
            return self._fail('請先選擇模型，並等待目前操作完成。')
        try:
            if not isinstance(request, ServiceRequest) or request.service != 'local_ai' or request.operation != 'chat':
                raise ValueError('本地對話請求格式無效。')
            payload = request.input
            if not isinstance(payload, dict) or set(payload) != {'messages', 'model', 'params'}:
                raise ValueError('本地對話請求缺少訊息、模型或參數。')
            model = next((m for m in self.models if m['id'] == payload['model']), None)
            if model is None or payload['model'] != self.selected_model:
                raise ValueError('選用的模型已變更，請重新傳送。')
            if not isinstance(payload['params'], dict) or set(payload['params']) != set(self.settings):
                raise ValueError('請求缺少完整的參數快照。')
            params = normalize_settings(payload['params'])
            messages = validate_messages(payload['messages'])
            resolve_runtime(params['runtime_path'])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return self._fail(exc)
        self._epoch += 1
        self._active = request
        prepared = (self._loaded_model == model['id'] and self._loaded_settings is not None
                    and all(params[key] == self._loaded_settings[key] for key in LOAD_KEYS))
        self.state, self.error = ('generating' if prepared else 'loading'), ''
        self._cancel = self._ensure_worker().enqueue('chat', self._epoch, request, model, messages, params)
        if self._cancel is None:
            self._active = None
            self.state = 'error'
            return self._fail('本地引擎正在關閉，無法接受新請求。')
        self.changed.emit()
        return True

    def cancel(self, request_id):
        if self._active is None or self._active.request_id != request_id:
            return False
        if self._cancel is not None:
            self._cancel.cancel()
        self.state = 'stopping'
        self.changed.emit()
        return True

    def shutdown(self):
        """Nonblocking. Wait for inference and discovery workers to exit.

        The shell must retain this service and retry close while False. Never
        terminate a QThread that may still own a subprocess or pending receipt.
        """
        self._closing = True
        library_closed = self._model_library.shutdown()
        if self._worker is None:
            self.state = 'closed' if library_closed else 'stopping'
            return library_closed
        self._worker.request_shutdown()
        if self._worker.isRunning():
            self.state = 'stopping'
            return False
        # Queued Qt terminal events can still be pending after run() returned.
        # The owner gets a final cancellation with its already received text.
        if self._active is not None:
            return False
        self.state = 'closed' if library_closed else 'stopping'
        return library_closed

    def resume_after_close_abort(self):
        """Restore manual controls after another workspace owner vetoes close.

        This cannot reverse a live shutdown. Wait for the worker and every
        accepted request's terminal receipt first. No model/thread is started.
        """
        if not self._closing:
            return True
        if self._active is not None or (self._worker is not None and self._worker.isRunning()):
            return False
        if not self._model_library.resume_after_close_abort():
            return False
        worker, self._worker = self._worker, None
        # A finished QThread's signals may still be queued. Invalidate status
        # events and detach the old worker before allowing another explicit load.
        self._epoch += 1
        self._cancel = None
        self._loaded_model, self._loaded_settings = '', None
        self._closing = False
        self.error = self._registry.error
        self.state = 'error' if self.error else self._available_state()
        if worker is not None:
            worker.deleteLater()
        self.changed.emit()
        return True

    @Slot(object)
    def _on_status(self, event):
        if event['epoch'] != self._epoch:
            return
        self._loaded_model = event['model']
        self._loaded_settings = event['settings']
        cancelling = self._cancel is not None and self._cancel.event.is_set()
        keep_stopping = cancelling and event['state'] in ('loading', 'generating')
        self.state = 'stopping' if self._closing or keep_stopping else event['state']
        self.error = event['error']
        self.changed.emit()

    @Slot(object, str)
    def _on_delta(self, request, text):
        if self._active == request:
            self.delta.emit(request, text)

    @Slot(object, str, str)
    def _on_result(self, request, status, detail):
        if self._active != request:
            return
        self._active = None
        self._cancel = None
        if status == 'error':
            self.error = detail
        self.finished.emit(request, status, detail)
        self.changed.emit()

    @Slot()
    def _on_thread_finished(self):
        worker = self.sender()
        if worker is not self._worker:
            return
        if self._active is not None:
            request = self._active
            self._active = None
            self.finished.emit(request, 'error', '推論工作意外停止，已保留收到的內容。')
        self._loaded_model, self._loaded_settings = '', None
        self.state = ('stopping' if self._model_library.busy else 'closed') if self._closing else 'error'
        if not self._closing and not self.error:
            self.error = '本地推論工作已停止，請重新傳送訊息。'
        self._worker = None
        self._cancel = None
        worker.deleteLater()
        self.changed.emit()
