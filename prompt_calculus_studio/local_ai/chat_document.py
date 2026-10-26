"""File-owned conversation; inference results must match the accepted request.

The application owns its engine. Views own neither requests nor conversation
history. Persist the user turn and frozen request before accepting any output.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import uuid
import weakref

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..local_documents import chat, io, schema
from ..service_adapters import ServiceRequest


PARAMETERS = ('context_size', 'max_tokens', 'temperature', 'top_p', 'seed', 'gpu_layers')


class ChatDocument(QObject):
    changed = Signal()
    editing = Signal()
    kind = 'chat'

    def __init__(self, path, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.path = Path(path).absolute()
        loaded = io.load(self.path.parent, self.path.name)
        if loaded.document['kind'] != self.kind:
            raise ValueError('這不是對話文件。')
        self.document, self.receipt = loaded.document, loaded.receipt
        self.key = self.document['document_id']
        self.payload = copy.deepcopy(self.document['payload'])
        self.baseline = copy.deepcopy(self.payload)
        self.data = self.path.parent
        self.views = weakref.WeakSet()
        self.host, self.hosts = None, []
        self.execution, self.runtimes = None, None
        self.retired_executions = []
        self.last_result = None
        self.error = self.notice = ''
        self.recovery_paths = []
        self.draft_dirty = False
        self.dirty = chat.interrupt(self.payload)
        self.owner_epoch = str(uuid.uuid4())
        self.serial = 0
        self._request = None
        self._attempt = None
        self._disposed = False
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(60)
        self.refresh_timer.timeout.connect(self.changed.emit)
        self.checkpoint_timer = QTimer(self)
        self.checkpoint_timer.setSingleShot(True)
        self.checkpoint_timer.setInterval(1500)
        self.checkpoint_timer.timeout.connect(self._checkpoint)
        from .service import for_workspace
        self.service = for_workspace(workspace)
        self.service.delta.connect(self._delta)
        self.service.finished.connect(self._finished)

    @property
    def title(self):
        return self.path.stem

    @property
    def options(self):
        return dict(system_prompt=self.payload['system_prompt'])

    @property
    def busy(self):
        return self._request is not None

    @property
    def composing(self):
        return any(getattr(view, 'composing', False) for view in self.views)

    def flush_views(self):
        return not self.composing and not any(getattr(view, 'has_uncommitted_draft', False) for view in self.views)

    def _edited(self):
        self.dirty = True
        self.editing.emit()
        self.changed.emit()

    def set_draft(self, text):
        if not isinstance(text, str) or len(text) > chat.MAX_TEXT:
            self.notice = '草稿過長，請減少內容。'
            self.changed.emit()
            return False
        if text != self.payload['draft']:
            self.payload['draft'] = text
            self.notice = ''
            self._edited()
        return True

    def set_options(self, options):
        if (not isinstance(options, dict) or set(options) != {'system_prompt'}
                or not isinstance(options['system_prompt'], str)
                or len(options['system_prompt']) > chat.MAX_TEXT):
            self.notice = '對話指示格式無效或過長。'
            self.changed.emit()
            return False
        if options['system_prompt'] != self.payload['system_prompt']:
            self.payload['system_prompt'] = options['system_prompt']
            self.notice = ''
            self._edited()
        return True

    def project(self, remap=False):
        candidate = copy.deepcopy(self.document)
        candidate['payload'] = copy.deepcopy(self.payload)
        return schema.validate(candidate)

    def update_dirty(self):
        self.dirty = self.payload != self.baseline
        self.changed.emit()

    def save(self):
        if not self.flush_views():
            return False
        if any(t.document is self and id(t) in self.workspace.autosave.paused for t in self.workspace.all_pages()):
            return False
        if not self.dirty:
            return not self.error
        try:
            result = io.save(self.path.parent, self.path.name, self.project(), self.receipt)
            self.last_result = result
            self.recovery_paths = list(result.recovery_paths)
            if result.status != 'saved':
                raise ValueError(result.error or '對話尚未保存。')
            self.document, self.receipt = result.document, result.receipt
            self.baseline = copy.deepcopy(self.payload)
            self.dirty, self.error = False, ''
            self.changed.emit()
            return True
        except (ValueError, OSError, KeyError) as error:
            self.error = str(error)
            self.dirty = True
            self.changed.emit()
            return False

    def _checkpoint(self):
        # Do not repeatedly write conflict copies. Explicit Save/Save As can
        # retry; the in-memory owner continues retaining every received token.
        if self.dirty and not self.error:
            if self.composing:
                self.checkpoint_timer.start()
            else:
                self.save()

    def send(self, text):
        return self._submit(text, retry=False)

    def retry(self):
        if not self.payload['turns']:
            return False
        return self._submit(self.payload['turns'][-1]['text'], retry=True)

    def _submit(self, text, *, retry):
        if self.busy or self._disposed or not self.flush_views():
            return False
        try:
            if not isinstance(text, str) or not text.strip() or len(text) > chat.MAX_TEXT:
                raise ValueError('請先輸入訊息。')
            if self.service.state != 'ready' or not self.service.selected_model:
                raise ValueError('請先選擇並載入本機模型。')
            messages = chat.context_messages(self.payload, text, retry=retry)
            refs = chat.context_references(self.payload, retry=retry)
            settings = copy.deepcopy(self.service.settings)
            params = {key: settings[key] for key in PARAMETERS}
            model = self.service.selected_model
            model_name = next((m['name'] for m in getattr(self.service, 'models', []) if m['id'] == model), model)
            profile = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([model, params], sort_keys=True)))
            turn = self.payload['turns'][-1] if retry else dict(id=str(uuid.uuid4()), text=text, attempts=[])
            attempt = dict(id=str(uuid.uuid4()), request=str(uuid.uuid4()), model=model, model_name=model_name,
                           profile_epoch=profile, parameters=params,
                           system_prompt=self.payload['system_prompt'], context=refs,
                           text='', status='receiving', reason='')
            self.serial += 1
            request = ServiceRequest('local_ai', 'chat', request_id=attempt['request'],
                                     scope=dict(document_id=self.key, owner_epoch=self.owner_epoch,
                                                revision=self.serial, user_message_id=turn['id'],
                                                attempt_id=attempt['id']),
                                     input=dict(messages=messages, model=model, params=settings))
            # Validate a candidate before mutating a live document or clearing
            # its draft, including turn/attempt limits and reference integrity.
            candidate = copy.deepcopy(self.payload)
            if retry:
                candidate['turns'][-1]['attempts'].append(attempt)
            else:
                turn['attempts'].append(attempt)
                candidate['turns'].append(turn)
                candidate['draft'] = ''
            chat.validate_payload(candidate)
            self.payload = candidate
            self._attempt = self.payload['turns'][-1]['attempts'][-1]
            self.dirty = True
            self.editing.emit()
            if not self.save():
                self._attempt.update(status='failed', reason='尚未保存訊息，因此未送出。')
                self._attempt = None
                self._edited()
                return False
            self._request = request
            self.notice = ''
            if not self.service.submit(request):
                self._finish_local('failed', self.service.error or '引擎未接受訊息，請確認模型狀態後重試。')
                return False
            self.changed.emit()
            return True
        except (ValueError, KeyError, TypeError) as error:
            self.notice = str(error)
            self.changed.emit()
            return False

    def _owns(self, request):
        return (not self._disposed and self._request is not None and request == self._request
                and request.scope.get('document_id') == self.key
                and request.scope.get('owner_epoch') == self.owner_epoch
                and self._attempt is not None and self._attempt['status'] == 'receiving')

    @Slot(object, str)
    def _delta(self, request, text):
        if not self._owns(request):
            return
        if len(self._attempt['text']) + len(text) > chat.MAX_TEXT:
            self.stop(reason='回覆已達文件長度上限。', status='limited')
            return
        self._attempt['text'] += text
        self.dirty = True
        if not self.refresh_timer.isActive():
            self.refresh_timer.start()
        if not self.checkpoint_timer.isActive() and not self.error:
            self.checkpoint_timer.start()

    @Slot(object, str, str)
    def _finished(self, request, status, detail):
        if not self._owns(request):
            return
        if status in ('completed', 'cancelled', 'limited'):
            if not detail.startswith(self._attempt['text']) or len(detail) > chat.MAX_TEXT:
                self._finish_local('failed', '回覆串流不一致；已保留收到的內容。')
                return
            self._attempt['text'] = detail
            target = {'completed': 'complete', 'cancelled': 'stopped', 'limited': 'limited'}[status]
            self._finish_local(target, '已達最大輸出長度。' if status == 'limited' else '')
        else:
            self._finish_local('failed', detail or '模型未完成回覆。')

    def _finish_local(self, status, reason):
        if self._attempt is not None:
            self._attempt.update(status=status, reason=reason)
        self._request = self._attempt = None
        self.checkpoint_timer.stop()
        self.refresh_timer.stop()
        self.notice = reason
        self._edited()
        self.save()

    def stop(self, *, reason='已停止，保留目前回覆。', status='stopped'):
        request = self._request
        if request is None:
            return False
        # Revoke acceptance before asking a worker to cancel; late queued
        # events can no longer modify this owner or its replacement file.
        self._finish_local(status, reason)
        self.service.cancel(request.request_id)
        return True

    def retarget(self, result, target):
        self.stop()
        self.owner_epoch = str(uuid.uuid4())
        self.path = Path(target).absolute()
        self.data = self.path.parent
        self.document, self.receipt = result.document, result.receipt
        self.key = self.document['document_id']
        self.payload = copy.deepcopy(self.document['payload'])
        self.baseline = copy.deepcopy(self.payload)
        self.dirty = self.draft_dirty = False
        self.error = self.notice = ''
        self.last_result = result
        self.recovery_paths = list(result.recovery_paths)
        self.changed.emit()

    def restore_recovery(self, value):
        try:
            candidate = schema.validate(value['document'])
            if candidate['kind'] != 'chat' or candidate['document_id'] != self.key:
                raise ValueError('恢復副本不屬於目前對話。')
            if value['sha'] != self.receipt.sha256:
                raise ValueError('原檔已變更，另有恢復副本；請從恢復入口另存。')
            self.payload = copy.deepcopy(candidate['payload'])
            chat.interrupt(self.payload)
            self.update_dirty()
            return True
        except (ValueError, KeyError, TypeError) as error:
            self.notice = str(error)
            self.workspace.show_notice(self.notice)
            return False

    def export_markdown(self, directory):
        from ..workspace_file_io import available_targets, create_markdown, name_collision, TargetNameCollision
        if not self.flush_views():
            raise ValueError('請先完成目前文字輸入，再匯出對話。')
        root = self.workspace.locations.directory(directory)
        raw = chat.markdown(self.payload, self.title).encode('utf-8')
        for target in available_targets(root / (self.title + '.md')):
            try:
                create_markdown(target, raw)
                return target
            except TargetNameCollision:
                continue
            except (OSError, ValueError) as error:
                if not name_collision(error):
                    raise

    def dispose(self):
        if self._disposed:
            return
        self.stop()
        self._disposed = True
        self.checkpoint_timer.stop()
        self.refresh_timer.stop()
        self.service.delta.disconnect(self._delta)
        self.service.finished.disconnect(self._finished)
