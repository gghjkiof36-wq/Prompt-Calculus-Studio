"""File-owned conversation; inference results must match the accepted request.

The application owns its engine. Views own neither requests nor conversation
history. Persist the user turn and frozen request before accepting any output.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from time import monotonic
import uuid
import weakref

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

from ..local_documents import chat, io, schema
from ..local_documents import chat_attachments as attachments
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
        self._generation_started = None
        self._generation_elapsed = None
        self._disposed = False
        self._attachment_job = None
        self._checkpoint_job = None
        self._revision_saving = False
        self._edit_lease = None
        self._edit_turn_id = None
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
        return self._request is not None or self._revision_saving

    @property
    def generation_elapsed_seconds(self):
        """Current/last accepted request; elapsed time is never persisted."""
        if self._request is None or self._generation_started is None:
            return self._generation_elapsed
        return max(0., monotonic() - self._generation_started)

    @property
    def composing(self):
        return any(getattr(view, 'composing', False) for view in self.views)

    @property
    def attachments_busy(self):
        return self._attachment_job is not None

    @property
    def edit_locked(self):
        return self._edit_lease is not None and self._edit_lease() is not None

    def acquire_edit(self, view, turn_id):
        if (self._disposed or self.busy or self.attachments_busy or self.edit_locked or view not in self.views
                or not any(t['id'] == turn_id for t in self.payload['turns'])):
            return False
        self._edit_lease, self._edit_turn_id = weakref.ref(view), turn_id
        self.changed.emit()
        return True

    def release_edit(self, view):
        if self._edit_lease is not None and self._edit_lease() is view:
            self._edit_lease = self._edit_turn_id = None
            self.changed.emit()

    def _edit_allowed(self, view, turn_id):
        return not self.edit_locked or (self._edit_lease() is view and self._edit_turn_id == turn_id)

    @property
    def draft_attachments(self):
        return copy.deepcopy(self.payload.get('draft_attachments', []))

    def add_attachments(self, descriptors):
        """Accept a bounded import job; only its current owner may accept data."""
        if self._disposed or self.busy or self.attachments_busy:
            return False
        try:
            if (not isinstance(descriptors, list) or not descriptors
                    or len(descriptors) + len(self.payload.get('draft_attachments', [])) > attachments.MAX_ATTACHMENTS):
                raise ValueError('每則訊息最多加入 8 個附件。')
            from .attachments import AttachmentImport, freeze_descriptors, pool
            token = (self.key, self.owner_epoch, str(uuid.uuid4()))
            snapshot = copy.deepcopy(dict(self.document, payload=self.payload))
            snapshot['required_features'] = list(snapshot['required_features'])
            job = AttachmentImport(token, freeze_descriptors(descriptors), snapshot)
            job.signals.done.connect(self._attachments_done, Qt.ConnectionType.QueuedConnection)
            self._attachment_job = job
            self.notice = ''
            self.changed.emit()
            pool().start(job)
            return True
        except (ValueError, TypeError, OSError) as error:
            self.notice = str(error)
            self.changed.emit()
            return False

    @Slot(object, object, str)
    def _attachments_done(self, token, additions, error):
        job = self._attachment_job
        if (self._disposed or job is None or job.token != token
                or token[:2] != (self.key, self.owner_epoch)):
            return
        self._attachment_job = None
        try:
            if error:
                raise ValueError(error)
            # Typing may continue during import. Recheck the latest candidate's
            # total size without replacing its current text or other settings.
            candidate = copy.deepcopy(dict(self.document, payload=self.payload))
            candidate['payload']['draft_attachments'] = self.payload.get('draft_attachments', []) + additions
            chat.sync_features(candidate)
            schema.encode(candidate)
            self.payload = candidate['payload']
            self.notice = ''
            self._edited()
        except (ValueError, TypeError, OSError) as error:
            self.notice = str(error)
            self.changed.emit()

    def remove_attachment(self, attachment_id):
        if self._disposed or self.busy or self.attachments_busy:
            return False
        values = self.payload.get('draft_attachments', [])
        kept = [item for item in values if item['id'] != attachment_id]
        if len(kept) == len(values):
            return False
        self.payload['draft_attachments'] = kept
        self.notice = ''
        self._edited()
        return True

    def _cancel_attachment_import(self):
        job, self._attachment_job = self._attachment_job, None
        if job is not None:
            job.cancel.set()

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
        # The receipt's older payload is replaced, not projected. Copy the
        # current owner once so a long history is never copied only to discard it.
        candidate = copy.deepcopy(dict(self.document, payload=self.payload))
        chat.sync_features(candidate)
        return schema.validate(candidate)

    def update_dirty(self):
        self.dirty = self.payload != self.baseline
        self.changed.emit()

    def save(self):
        if not self.flush_views():
            return False
        if not self.finish_checkpoint():
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
                self.request_checkpoint()

    def request_checkpoint(self):
        """Coalesced automatic save; explicit Save/Send still use save()."""
        if self._disposed or self._revision_saving or self.error or not self.flush_views():
            return False
        if any(t.document is self and id(t) in self.workspace.autosave.paused for t in self.workspace.all_pages()):
            return False
        if self._checkpoint_job is not None:
            return True
        if not self.dirty:
            return True
        try:
            from .checkpoint import Checkpoint, pool
            # One isolated copy, with full validation/encoding in the worker.
            # Later deltas can safely change the live active attempt.
            candidate = copy.deepcopy(dict(self.document, payload=self.payload))
            chat.sync_features(candidate)
            token = (self.key, self.owner_epoch, str(self.path), str(uuid.uuid4()))
            job = Checkpoint(token, self.path, candidate, self.receipt)
            job.signals.done.connect(self._checkpoint_done, Qt.ConnectionType.QueuedConnection)
            self._checkpoint_job = job
            pool().start(job)
            return True
        except (ValueError, TypeError, OSError) as error:
            self.error = str(error)
            self.changed.emit()
            return False

    @Slot(object)
    def _checkpoint_done(self, token):
        job = self._checkpoint_job
        if job is not None and job.token == token:
            self._accept_checkpoint(job)

    def _accept_checkpoint(self, job):
        if job is not self._checkpoint_job or not job.finished.is_set():
            return False
        if job.token[:3] != (self.key, self.owner_epoch, str(self.path)):
            self._checkpoint_job = None
            self.error = '保存結果不屬於目前對話；未套用到新的文件。'
            self.changed.emit()
            return False
        self._checkpoint_job = None
        result = job.result
        self.last_result = result
        self.recovery_paths = list(result.recovery_paths)
        if result.status == 'saved':
            self.document, self.receipt = result.document, result.receipt
            self.baseline = job.snapshot['payload']
            self.dirty = self.payload != self.baseline
            self.error = ''
            if self.notice == '對話仍在保存，請稍候再試。':
                self.notice = ''
        else:
            self.error = result.error or '對話背景保存未完成；內容仍保留在畫面。'
            self.dirty = True
        self.changed.emit()
        if self.dirty and not self.error and not self._disposed:
            self.checkpoint_timer.start()
        return True

    def finish_checkpoint(self, timeout=5.0):
        """Drain a writer before identity/path changes, without GUI reentry.

        True means no pending write remains, not that its CAS save succeeded.
        Save As can still rescue a failed/conflicted in-memory candidate.
        """
        job = self._checkpoint_job
        if job is None:
            return True
        if not job.finished.wait(timeout):
            self.notice = '對話仍在保存，請稍候再試。'
            self.changed.emit()
            return False
        return self._accept_checkpoint(job)

    def send(self, text):
        if self.edit_locked:
            return False
        return self._submit(text, retry=False)

    def retry(self, turn_id=None):
        if self.edit_locked or not self.payload['turns'] or turn_id is not None and self.payload['turns'][-1]['id'] != turn_id:
            return False
        return self._submit(self.payload['turns'][-1]['text'], retry=True)

    @property
    def edit_revisions(self):
        result = []
        for number, revision in reversed(list(enumerate(self.payload.get('revisions', []), 1))):
            turn = next((t for t in revision['turns'] if t['id'] == revision['turn_id']), None)
            label = ('編輯前：' + turn['text'].replace('\n', ' ')[:60]) if turn else '還原前的對話'
            result.append(dict(id=revision['id'], label=f'{number} · {label}', turn_count=len(revision['turns'])))
        return result

    def edit_revision_payload(self, revision_id):
        revision = next((r for r in self.payload.get('revisions', []) if r['id'] == revision_id), None)
        if revision is None:
            raise ValueError('找不到這份編輯紀錄。')
        # The marker also allows frozen references to stopped/partial answers.
        return dict(version=1, draft='', system_prompt=revision['system_prompt'],
                    turns=copy.deepcopy(revision['turns']), revisions=[])

    def _message_versions(self):
        from ..local_documents.chat_versions import MessageVersionIndex
        index = getattr(self, '_message_version_index', None)
        if index is None or not index.matches(self.payload):
            index = self._message_version_index = MessageVersionIndex(self.payload)
        return index

    def message_version_count(self, turn_id):
        return self._message_versions().count(turn_id) if not self._disposed else 0

    def message_versions(self, turn_id):
        if self._disposed:
            raise ValueError('原對話已關閉。')
        return self._message_versions().versions(turn_id)

    def branch_message_version_document(self, turn_id, version_id):
        """Copy the selected full path without restoring or saving this owner."""
        if self._disposed:
            raise ValueError('原對話已關閉，無法建立新對話。')
        if self.edit_locked:
            raise ValueError('請先完成或取消目前的訊息編輯。')
        if self.composing:
            raise ValueError('請先完成目前文字輸入，再建立新對話。')
        from ..local_documents.identity import duplicate
        payload, revision_id = self._message_versions().branch_payload(turn_id, version_id)
        candidate = dict(self.document, payload=payload,
                         required_features=list(self.document['required_features']))
        chat.sync_features(candidate)
        branched = duplicate(candidate).document
        branched['extensions']['pcs.chat_message_version'] = dict(
            source_document_id=self.key, source_saved_revision=self.document['revision']['id'],
            turn_id=turn_id, version_id=version_id, edit_revision_id=revision_id)
        return schema.validate(branched)

    def _save_revision_candidate(self, candidate):
        """Publish an edit/restore before changing the live path or its history."""
        if any(t.document is self and id(t) in self.workspace.autosave.paused for t in self.workspace.all_pages()):
            raise ValueError('請先完成或取消目前的文件操作。')
        envelope = copy.deepcopy(dict(self.document, payload=candidate))
        chat.sync_features(envelope)
        schema.encode(envelope)  # Reject the whole candidate before replacing anything.
        self._revision_saving = True
        try:
            result = io.save(self.path.parent, self.path.name, envelope, self.receipt)
        finally:
            self._revision_saving = False
        self.last_result = result
        self.recovery_paths = list(result.recovery_paths)
        if result.status != 'saved':
            self.error = result.error or '修改尚未保存；原對話已保留。'
            raise ValueError(self.error)
        self.document, self.receipt = result.document, result.receipt
        self.payload = candidate
        self.baseline = copy.deepcopy(candidate)
        self.dirty, self.error = False, ''

    def edit_and_resend(self, turn_id, text, *, view=None):
        if self.busy or self._disposed or self.attachments_busy or not self.flush_views() or not self._edit_allowed(view, turn_id):
            return False
        try:
            if not self.finish_checkpoint():
                return False
            position = next((i for i, t in enumerate(self.payload['turns']) if t['id'] == turn_id), None)
            if position is None:
                raise ValueError('找不到這則訊息，原對話已保留。')
            candidate = copy.deepcopy(self.payload)
            chat.archive_revision(candidate, reason='edit', turn_id=turn_id)
            edited = dict(candidate['turns'][position], id=str(uuid.uuid4()), text=text, attempts=[])
            for item in edited.get('attachments', []):
                item['id'] = str(uuid.uuid4())
            candidate['turns'] = candidate['turns'][:position] + [edited]
            return self._submit(text, retry=True, edited_payload=candidate, edit_view=view, edited_turn_id=turn_id)
        except (ValueError, KeyError, TypeError) as error:
            self.notice = str(error)
            self.changed.emit()
            return False

    def restore_edit_revision(self, revision_id):
        if self.busy or self._disposed or self.attachments_busy or self.edit_locked or not self.flush_views():
            return False
        try:
            if not self.finish_checkpoint():
                return False
            restored = self.edit_revision_payload(revision_id)
            candidate = copy.deepcopy(self.payload)
            chat.archive_revision(candidate, reason='restore')
            candidate.update(turns=restored['turns'], system_prompt=restored['system_prompt'])
            self._save_revision_candidate(candidate)
            self.notice = ''
            self.editing.emit()
            self.changed.emit()
            return True
        except (ValueError, OSError, KeyError, TypeError) as error:
            self.notice = str(error)
            self.changed.emit()
            return False

    def branch_document(self, turn_id, attempt_id=None):
        """Return an independent saved-file candidate without touching source.

        The workspace owns exclusive naming/create/open. Merely preparing the
        candidate does not save this owner, cancel its request, select a model,
        change presets or start generation. In-flight source text is a snapshot.
        """
        if self._disposed:
            raise ValueError('原對話已關閉，無法建立分支。')
        if self.edit_locked:
            raise ValueError('請先完成或取消目前的訊息編輯。')
        if not self.flush_views():
            raise ValueError('請先完成目前文字輸入，再建立對話分支。')
        from ..local_documents.identity import duplicate
        # Only the selected prefix needs a snapshot. duplicate() validates and
        # owns the resulting envelope, including independent optional metadata.
        snapshot = dict(self.document, payload=self.payload)
        candidate = dict(snapshot, payload=chat.branch_payload(self.payload, turn_id, attempt_id))
        candidate['required_features'] = list(snapshot['required_features'])
        chat.sync_features(candidate)
        branched = duplicate(candidate).document
        branched['extensions']['pcs.chat_branch'] = dict(
            source_document_id=snapshot['document_id'],
            source_saved_revision=snapshot['revision']['id'],
            turn_id=turn_id, attempt_id=attempt_id)
        return schema.validate(branched)

    def _submit(self, text, *, retry, edited_payload=None, edit_view=None, edited_turn_id=None):
        if (self.busy or self._disposed or self.attachments_busy or not self.flush_views()
                or self.edit_locked and (edited_payload is None or not self._edit_allowed(edit_view, edited_turn_id))):
            return False
        try:
            source_payload = self.payload if edited_payload is None else edited_payload
            source_attachments = (source_payload['turns'][-1].get('attachments', []) if retry
                                  else source_payload.get('draft_attachments', []))
            if not isinstance(text, str) or (not text.strip() and not source_attachments) or len(text) > chat.MAX_TEXT:
                raise ValueError('請先輸入訊息。')
            if any(item['kind'] == 'image' for item in source_attachments):
                raise ValueError(attachments.IMAGE_UNSUPPORTED)
            if not self.service.selected_model:
                raise ValueError('請先選擇本機模型。')
            if not self.service.can_submit:
                raise ValueError('請等待目前操作完成，再傳送訊息。')
            model = self.service.selected_model
            settings = copy.deepcopy(self.service.settings_for_model(model))
            model_preset = self.service.model_preset(model)
            default_prompt = model_preset['system_prompt']
            reasoning = copy.deepcopy(model_preset.get('reasoning'))
            if reasoning is not None:
                from .reasoning import normalize_reasoning
                reasoning = normalize_reasoning(reasoning, max_tokens=settings['max_tokens'])
            own_prompt = source_payload['system_prompt']
            effective_prompt = own_prompt if own_prompt.strip() else default_prompt
            context_payload = dict(source_payload, system_prompt=effective_prompt)
            messages = chat.context_messages(context_payload, text, retry=retry)
            refs = chat.context_references(source_payload, retry=retry)
            params = {key: settings[key] for key in PARAMETERS}
            model_name = next((m['name'] for m in getattr(self.service, 'models', []) if m['id'] == model), model)
            profile = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([model, params, effective_prompt, reasoning], sort_keys=True)))
            turn = source_payload['turns'][-1] if retry else dict(id=str(uuid.uuid4()), text=text, attempts=[])
            if not retry and source_attachments:
                turn['attachments'] = copy.deepcopy(source_attachments)
                for item in turn['attachments']:
                    item['id'] = str(uuid.uuid4())
            attempt = dict(id=str(uuid.uuid4()), request=str(uuid.uuid4()), model=model, model_name=model_name,
                           profile_epoch=profile, parameters=params,
                           system_prompt=effective_prompt, context=refs,
                           text='', status='receiving', reason='')
            request_input = dict(messages=messages, model=model, params=settings)
            if reasoning is not None:
                attempt['reasoning'] = copy.deepcopy(reasoning)
                request_input['reasoning'] = copy.deepcopy(reasoning)
            self.serial += 1
            request = ServiceRequest('local_ai', 'chat', request_id=attempt['request'],
                                     scope=dict(document_id=self.key, owner_epoch=self.owner_epoch,
                                                revision=self.serial, user_message_id=turn['id'],
                                                attempt_id=attempt['id']),
                                     input=request_input)
            # Validate a candidate before mutating a live document or clearing
            # its draft, including turn/attempt limits and reference integrity.
            candidate = copy.deepcopy(source_payload)
            if chat.needs_terminal_context(source_payload, retry=retry):
                candidate.setdefault('revisions', [])
            if retry:
                candidate['turns'][-1]['attempts'].append(attempt)
            else:
                turn['attempts'].append(attempt)
                candidate['turns'].append(turn)
                candidate['draft'] = ''
                if 'draft_attachments' in candidate:
                    candidate['draft_attachments'] = []
            chat.validate_payload(candidate)
            if edited_payload is not None:
                preflight = getattr(self.service, 'validate_submission', None)
                if preflight is not None:
                    preflight(request)
                # Keep the entire original live path until CAS publication.
                # Reserving the request prevents reentrant generation/edits.
                self._request = request
                self._attempt = candidate['turns'][-1]['attempts'][-1]
                self._generation_started = self._generation_elapsed = None
                try:
                    self._save_revision_candidate(candidate)
                except (ValueError, OSError, KeyError, TypeError):
                    self._request = self._attempt = None
                    raise
                self._edit_lease = self._edit_turn_id = None
                self.editing.emit()
                if not self._owns(request):
                    return False
                self.notice = ''
                self._generation_started = monotonic()
                if not self.service.submit(request):
                    if self._owns(request):
                        self._generation_started = self._generation_elapsed = None
                        self._finish_local('failed', self.service.error or '引擎未接受訊息，請確認模型狀態後重試。')
                    return False
                self.changed.emit()
                return True
            self.payload = candidate
            self._attempt = self.payload['turns'][-1]['attempts'][-1]
            # Reserve this owner before save/changed signals can reenter Send,
            # Stop or Save As. The service receives nothing until save succeeds.
            self._request = request
            self._generation_started = self._generation_elapsed = None
            self.dirty = True
            self.editing.emit()
            if not self._owns(request):
                return False
            if not self.save():
                if self._owns(request):
                    self._attempt.update(status='failed', reason='尚未保存訊息，因此未送出。')
                    self._request = self._attempt = None
                    if not retry and source_attachments:
                        self.payload['draft_attachments'] = source_attachments
                        if not self.payload['draft']:
                            self.payload['draft'] = text
                    self._edited()
                return False
            if not self._owns(request):
                return False
            self.notice = ''
            self._generation_started = monotonic()
            if not self.service.submit(request):
                if self._owns(request):
                    self._generation_started = self._generation_elapsed = None
                    if not retry and source_attachments:
                        self.payload['draft_attachments'] = source_attachments
                        if not self.payload['draft']:
                            self.payload['draft'] = text
                    self._finish_local('failed', self.service.error or '引擎未接受訊息，請確認模型狀態後重試。')
                return False
            self.changed.emit()
            return True
        except (ValueError, OSError, KeyError, TypeError) as error:
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

    def _finish_local(self, status, reason, *, cancel_request=None):
        if self._attempt is not None:
            self._attempt.update(status=status, reason=reason)
        if self._generation_started is not None:
            self._generation_elapsed = max(0., monotonic() - self._generation_started)
        self._request = self._attempt = None
        self._generation_started = None
        if cancel_request is not None:
            self.service.cancel(cancel_request.request_id)
        self.checkpoint_timer.stop()
        self.refresh_timer.stop()
        self.notice = reason
        self._edited()
        self.save()

    def stop(self, *, reason='生成已停止', status='stopped'):
        request = self._request
        if request is None:
            return False
        # Revoke acceptance before asking a worker to cancel; late queued
        # events can no longer modify this owner or its replacement file.
        self._finish_local(status, reason, cancel_request=request)
        return True

    def retarget(self, result, target):
        if self.edit_locked or not self.finish_checkpoint():
            return False
        self._cancel_attachment_import()
        self.stop()
        self._generation_started = self._generation_elapsed = None
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
        return True

    def restore_recovery(self, value):
        try:
            if self.edit_locked or not self.finish_checkpoint():
                return False
            candidate = schema.validate(value['document'])
            if candidate['kind'] != 'chat' or candidate['document_id'] != self.key:
                raise ValueError('恢復副本不屬於目前對話。')
            if value['sha'] != self.receipt.sha256:
                raise ValueError('原檔已變更，另有恢復副本；請從恢復入口另存。')
            self._cancel_attachment_import()
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
            return True
        if self.edit_locked or not self.finish_checkpoint():
            return False
        self._cancel_attachment_import()
        self.stop()
        self._disposed = True
        self.checkpoint_timer.stop()
        self.refresh_timer.stop()
        self.service.delta.disconnect(self._delta)
        self.service.finished.disconnect(self._finished)
        return True
