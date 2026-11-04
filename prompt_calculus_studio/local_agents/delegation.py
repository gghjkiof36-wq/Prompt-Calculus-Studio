"""Local task admission and receipts, using the ordinary chat owner and AI slot."""
import copy
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..local_ai.service import for_workspace
from ..local_documents import chat, io
from ..local_documents.paths import root_path
from ..workspace_file_io import available_targets, named_path
from .store import AgentStore
from .delegation_protocol import DelegationError, fingerprint
from .delegation_store import TaskStore, TERMINAL, attempt_in, now, result_of


def conversation_path(root, agent_name, task_id):
    label = ''.join(c if ord(c) >= 32 and c not in '<>:"/\\|?*' else '_' for c in agent_name)
    label = label.encode('utf-16-le')[:64].decode('utf-16-le', errors='ignore').strip(' .') or 'Agent'
    return named_path(root, f'本地委派 - {label} - {task_id[:8]}.pcs-chat')


class DelegationService(QObject):
    changed = Signal()

    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.store = TaskStore(workspace.data)
        self.enabled = self.store.settings()
        self.active = {}
        self.closing = False
        self.error = ''
        self._service = None
        from .delegation_server import DelegationServer
        self.receiver = DelegationServer(self)
        if self.enabled:
            try:
                self.receiver.start()
            except (ValueError, OSError) as exc:
                self.error = str(exc)

    @property
    def ready(self):
        return (not self.closing and getattr(self.workspace, 'workspace_ready', True)
                and not getattr(self.workspace, '_startup_loading', False)
                and not getattr(self.workspace, '_ai_close_pending', False)
                and not getattr(getattr(self.workspace, 'layout_store', None), 'stopped', False))

    @property
    def service(self):
        if self._service is None:
            self._service = for_workspace(self.workspace)
            self._service.review.connect(self.review)
            self._service.finished.connect(self.finished)
        return self._service

    def set_enabled(self, project_id, enabled):
        AgentStore(self.workspace.data).get('projects', project_id)
        if type(enabled) is not bool:
            raise ValueError('本機委派開關無效。')
        before = set(self.enabled)
        current = before | {project_id} if enabled else before - {project_id}
        if current == before:
            return
        self.store.save_settings(current)
        self.enabled = current
        if not enabled:
            for context in list(self.active.values()):
                if context['record']['project_id'] == project_id:
                    self._stop(context)
        # Rotate both token and connection identity; old discovery is not authority.
        self.receiver.stop()
        try:
            if current:
                self.receiver.start()
        except (ValueError, OSError):
            self.store.save_settings(before)
            self.enabled = before
            if before:
                self.receiver.start()
            raise
        self.error = ''
        self.changed.emit()

    def allowed(self, project_id):
        if project_id not in self.enabled:
            raise DelegationError('project_disabled', '此專案未允許本機委派。')
        try:
            return AgentStore(self.workspace.data).get('projects', project_id)
        except ValueError:
            raise DelegationError('unknown_project', '找不到這個專案。') from None

    def catalog(self):
        registry = AgentStore(self.workspace.data)
        projects = [p for p in registry.value['projects'] if p['id'] in self.enabled]
        ids = {p['id'] for p in projects}
        service = getattr(self.workspace, 'local_ai', None)
        return dict(availability='busy' if service is not None and not service.can_accept else 'ready',
                    projects=[dict(id=p['id'], name=p['name'], delegation_enabled=True) for p in projects],
                    agents=[dict(id=a['id'], name=a['name'], project_id=a['project'], model=a['model'])
                            for a in registry.value['agents'] if a['project'] in ids])

    def dispatch(self, request):
        if not self.ready:
            raise DelegationError('workspace_unavailable', 'PCS 工作區正在開啟或關閉。')
        command = request['command']
        if command == 'catalog':
            return self.catalog()
        if command == 'submit':
            return self.submit(request['project_id'], request['agent_id'], request['task'], request['idempotency_key'])
        record = self.get(request['task_id'])
        if command == 'stop':
            context = self.active.get(record['task_id'])
            if context and not context.get('settled'):
                self._stop(context)
            return self.status(record['task_id'])
        if command == 'result':
            if record['state'] not in TERMINAL:
                raise DelegationError('not_finished', '任務仍在處理中，請先查詢進度。')
            return dict(self.summary(record), result=copy.deepcopy(record.get('result')))
        return self.status(record['task_id'])

    def submit(self, project_id, agent_id, task, key):
        self.allowed(project_id)
        task_id = fingerprint(key)
        signature = fingerprint(dict(project_id=project_id, agent_id=agent_id, task=task))
        record = self.store.read(task_id)
        if record is not None:
            if record['input_hash'] != signature:
                raise DelegationError('idempotency_conflict', '這個重送識別碼已用於另一項工作。')
            return self.status(task_id)
        registry = AgentStore(self.workspace.data)
        try:
            agent = registry.get('agents', agent_id)
        except ValueError:
            raise DelegationError('unknown_agent', '找不到這個 Agent。') from None
        if agent['project'] != project_id:
            raise DelegationError('wrong_project', '這個 Agent 不屬於指定專案。')
        if not self.service.can_accept:
            raise DelegationError('busy', '本地模型正在處理其他工作，請完成後再交辦。')
        # Validate the registered role before creating a document. Actual request
        # and model settings are still frozen by ChatDocument at submission.
        registry.request(agent_id, task, self.service.approval_mode)
        root = root_path(self.workspace.root)
        target = conversation_path(root, agent['name'], task_id)
        document = chat.empty()
        document['payload']['agent_id'] = agent_id
        chat.sync_features(document)
        record = dict(version=1, task_id=task_id, input_hash=signature, project_id=project_id,
                      agent_id=agent_id, state='preparing', created_at=now(), document_id=document['document_id'],
                      path=str(target), turn_id='', attempt_id='', request_id='', scope={}, result=None)
        # Durable admission precedes file publication and any inference. A crash
        # after this point is interrupted, never an invitation to repeat work.
        self.store.write(record)
        context = dict(record=record, owner=None, attempt=None, ended=False, ticket=None, settled=False)
        self.active[task_id] = context
        try:
            for candidate in available_targets(target, limit=100):
                record['path'] = str(candidate)
                self.store.write(record)
                created = io.create(root, candidate.name, document)
                if created.name_collision and not created.published:
                    continue
                target = candidate
                break
            if created.status != 'saved':
                raise DelegationError('save_failed', created.error or '尚未保存委派對話。')
            tab = self.workspace.open_path(target, retained=True, background=True)
            owner = tab.document if tab is not None else None
            if owner is None or owner.key != document['document_id']:
                raise DelegationError('owner_unavailable', '委派對話未能開啟。')
            context['owner'] = owner

            def accepted(request):
                if not self.ready or project_id not in self.enabled:
                    raise DelegationError('project_disabled', '此專案已停止本機委派。')
                record.update(scope=dict(request.scope), request_id=request.request_id,
                              turn_id=request.scope['user_message_id'], attempt_id=request.scope['attempt_id'])
                context['attempt'] = attempt_in(owner.payload, record['turn_id'], record['attempt_id'])
                record['accepted_configuration'] = copy.deepcopy(dict(request.input))
                # Full input (not model output) records actual runtime settings.
                # The public result is projected separately and never returns it.
                self.store.write(record)

            if not owner.send(task, before_submit=accepted):
                if context['attempt'] is None and owner.payload['turns']:
                    turn = owner.payload['turns'][0]
                    if turn['attempts']:
                        context['attempt'] = turn['attempts'][0]
                        record.update(turn_id=turn['id'], attempt_id=context['attempt']['id'],
                                      request_id=context['attempt']['request'])
                context['ended'] = True
                self.freeze(context, fallback='failed', error=owner.error or owner.notice)
        except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
            context['ended'] = True
            self.freeze(context, fallback='save_failed', error=str(exc))
        return self.status(task_id)

    def review(self, request, ticket):
        context = self.context_for(request)
        if context is not None and not ticket['done'].is_set():
            # Observation only. The original document owns the approval dialog.
            context['ticket'] = ticket

    def context_for(self, request):
        return next((c for c in self.active.values() if not c.get('settled')
                     and c['record']['request_id'] == request.request_id
                     and c['record']['scope'] == dict(request.scope)), None)

    def finished(self, request, status, detail):
        context = self.context_for(request)
        if context is None:
            return
        context['ended'] = True
        context['ticket'] = None
        # All document receivers first accept/reject the worker result and save.
        QTimer.singleShot(0, self, lambda: self.freeze(context))

    def _stop(self, context):
        if context.get('settled'):
            return
        context['record']['state'] = 'stopping'
        owner, request_id = context['owner'], context['record']['request_id']
        if owner is not None and owner._request is not None and owner._request.request_id == request_id:
            owner.stop()
        elif request_id and self.service._active is not None and self.service._active.request_id == request_id:
            self.service.cancel(request_id)
        if self.service._active is None or self.service._active.request_id != request_id:
            context['ended'] = True
            self.freeze(context, fallback='stopped')

    def freeze(self, context, *, fallback='interrupted', error=''):
        if context.get('settled') or not context['ended']:
            return
        record = context['record']
        owner = context['owner']
        candidate = context['attempt']
        path = Path(record['path'])
        if owner is not None and owner.key == record['document_id']:
            candidate = attempt_in(owner.payload, record['turn_id'], record['attempt_id']) or candidate
            if owner.path.resolve().is_relative_to(Path(self.workspace.root).resolve()):
                path = owner.path
        candidate = copy.deepcopy(candidate)
        saved, receipt = False, None
        try:
            if not path.resolve().is_relative_to(Path(self.workspace.root).resolve()):
                raise ValueError('委派對話已離開工作區內容根。')
            loaded = io.load(self.workspace.root, path.relative_to(self.workspace.root).as_posix())
            stored = attempt_in(loaded.document['payload'], record['turn_id'], record['attempt_id'])
            if (loaded.document['document_id'] == record['document_id'] and stored is not None
                    and stored.get('request') == record['request_id']):
                if candidate is None:
                    candidate = copy.deepcopy(stored)
                saved = stored == candidate and stored['status'] in TERMINAL
                if saved:
                    receipt = dict(document_id=record['document_id'], turn_id=record['turn_id'],
                                   attempt_id=record['attempt_id'], request_id=record['request_id'],
                                   document_sha256=loaded.receipt.sha256, attempt_sha256=fingerprint(stored))
        except (ValueError, OSError, KeyError) as exc:
            error = error or str(exc)
        state = candidate.get('status', fallback) if candidate else fallback
        if state == 'receiving':
            state = 'interrupted'
        elif candidate and not saved:
            state = 'save_failed'
        record.update(state=state, finished_at=now(), path=str(path), error=error,
                      result=result_of(candidate, saved=saved, state=state, receipt=receipt))
        context['settled'] = True
        context['ticket'] = None
        self.persist(context)

    def persist(self, context):
        record = context['record']
        try:
            self.store.write(record)
            self.active.pop(record['task_id'], None)
        except (ValueError, OSError) as exc:
            # Keep the bounded, filtered candidate in memory. A later explicit
            # query can retry this receipt only; it never repeats inference.
            record['state'] = 'save_failed'
            record['error'] = '委派回執尚未保存：' + str(exc)
            record['result']['state'] = 'save_failed'
            self.error = record['error']
            self.workspace.show_notice(self.error)

    def get(self, task_id):
        context = self.active.get(task_id)
        record = context['record'] if context else self.store.read(task_id)
        if record is None:
            raise DelegationError('unknown_task', '找不到這個委派任務。')
        self.allowed(record['project_id'])
        if context and context.get('settled'):
            self.persist(context)
        elif context and context['ended']:
            self.freeze(context)
        elif context is None and record['state'] not in TERMINAL:
            # Reopen recovery is a readback, never a new model submission.
            context = dict(record=record, owner=None, attempt=None, ended=True, ticket=None, settled=False)
            self.active[task_id] = context
            self.freeze(context)
        return record

    def summary(self, record):
        return {key: copy.deepcopy(record.get(key)) for key in
                ('task_id', 'project_id', 'agent_id', 'state', 'path', 'document_id', 'turn_id', 'attempt_id', 'request_id', 'error')}

    def status(self, task_id):
        record = self.get(task_id)
        response = self.summary(record)
        context = self.active.get(task_id)
        if context and not context.get('settled') and not context['ended']:
            ticket = context['ticket']
            owner = context['owner']
            if record['state'] == 'stopping' or owner is not None and not owner.busy:
                response['state'] = 'stopping'
            elif ticket is not None and not ticket['done'].is_set():
                response['state'] = 'waiting_approval'
            else:
                response['state'] = 'preparing' if self.service.state in ('loading', 'preparing') else 'working'
        return response

    def shutdown(self):
        self.closing = True
        self.receiver.stop()


def delegation_for(workspace):
    if getattr(workspace, 'agent_delegation', None) is None:
        workspace.agent_delegation = DelegationService(workspace)
    return workspace.agent_delegation
