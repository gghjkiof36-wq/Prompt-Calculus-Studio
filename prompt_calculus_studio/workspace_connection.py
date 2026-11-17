"""Workspace connection preferences; execution still belongs to each document."""
import json
import copy
import logging
import sqlite3
import weakref

from PySide6.QtCore import QObject, QIODevice, QSaveFile, QTimer
from shiboken6 import isValid

from .comfy_client import ComfyClient, RequestFailure
from .core import local_address


class WorkspaceConnection(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.clients = weakref.WeakSet()
        self.library_client = None
        self.registering = None
        self.registered_client = None
        self.channel = None
        self.channel_receiving = False
        self.channel_generation = 0
        self.retiring_channels = []
        self.event_owners = {}
        self.event_observations = {}
        self.event_status = weakref.WeakKeyDictionary()
        self.pending_event_owners = set()
        self.binding_attempts = set()
        self.path = workspace.data/'workspace-connection.json'
        self.url = 'http://127.0.0.1:8188'
        self.enabled = False
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError('connection preferences must be an object')
            self.url = local_address(value['url'])
            self.enabled = value.get('enabled') is True
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            QTimer.singleShot(0, lambda: workspace.show_notice(
                'ComfyUI 連線設定無法讀取，請在「設定 → 連線與工作流」重新設定。'))

    def live_clients(self):
        return [client for client in self.clients if isValid(client) and not client.stopped]

    def activate_document(self):
        tab = self.workspace.tabs_by_key.get(self.workspace.active_key)
        document = tab.document if tab else None
        if document and document.kind == 'workflow' and document.execution is not None:
            client = document.execution.client
            if self.library_client is not client:
                self.library_client = client
                client.register_library = True

    def attach(self, client):
        self.clients.add(client)
        client.url = self.url
        client.window.state['settings'].update(comfy_url=self.url, comfy_enabled=self.enabled)
        client.stateChanged.connect(self.workspace.refresh_connection)
        if self.enabled:
            # Hosts must finish constructing before transport callbacks use UI.
            QTimer.singleShot(0, client.start_saved_connection)

    def stop_channel(self):
        self.channel_generation += 1
        self.channel_receiving = False
        self.event_status.clear()
        for client in self.live_clients():
            if hasattr(client,'events'):
                client.events.close()
                client.events.ready=False
        if self.channel is not None:
            self.channel.stop()
            self.retiring_channels.append(self.channel)
            self.channel = None
        self.retiring_channels = [channel for channel in self.retiring_channels if channel.running]
        self.event_observations.clear()

    def channels_stopped(self):
        if self.channel is not None: self.stop_channel()
        self.retiring_channels = [channel for channel in self.retiring_channels if channel.running]
        return not self.retiring_channels

    def remember_event_owner(self, execution):
        value = execution.event_owner()
        previous = self.event_owners.get(value['token'], {})
        value['servers'] = sorted(set(previous.get('servers', [])) | {value['server']})
        if self.event_owners.get(value['token']) == value: return
        self.event_owners[value['token']] = value
        if self.channel is not None:
            self.channel.command.emit(dict(kind='owner', value=value))
            if value['token'] in self.pending_event_owners:
                self.pending_event_owners.discard(value['token'])
                self.channel.command.emit(dict(kind='redeliver',owner=value['token']))

    def observe_status(self, client, value):
        if not self.enabled or not client.enabled or client.url != self.url: return
        signature=(client.url,client.token,value.get('capabilities'),value.get('event_channel'),
                   value.get('extension_info'),value.get('extension'))
        if self.event_status.get(client)==signature:return
        from .comfy_event_transport import QtChannel
        identity=client.event_identity(value)
        if identity is not None:
            key = self.url, client.token, identity['server_instance'], identity['epoch']
            previous = self.channel
            if previous is None or key != (previous.url, previous.token, previous.identity['server_instance'], previous.identity['epoch']):
                self.stop_channel()
                self.channel = QtChannel(self.workspace.data, self.url, client.token, identity, self)
                generation=self.channel_generation
                self.channel.deliveryReady.connect(lambda page:self.deliver_events(page,generation))
                self.channel.healthChanged.connect(lambda value:self.channel_health(value,generation))
                for target in self.live_clients():
                    if target.document_polling:target.events.bind(identity)
                for owner in self.event_owners.values():
                    self.channel.command.emit(dict(kind='owner', value=owner))
            if client.document_polling and client.events.identity!=identity:
                client.events.bind(identity)
                client.events.probed=self.authorize_snapshot
        elif self.channel is not None:
            self.stop_channel()
        # Channel replacement invalidates every client's observation. Save
        # afterwards so steady polls do not rebuild owners or emit commands.
        self.event_status[client]=copy.deepcopy(signature)
        info = value.get('extension_info') or value.get('extension') or {}
        reported = info.get('root') if isinstance(info, dict) else None
        if not reported: return
        binding_key = self.url, reported
        if binding_key in self.binding_attempts: return
        self.binding_attempts.add(binding_key)
        from .extension_install import workspace_preferences, bind_reported
        try:
            preferences = workspace_preferences(self.workspace.data)
            changed, reason = bind_reported(preferences, self.url, info)
        except (OSError, ValueError) as exc:
            changed, reason = False, 'preferences_unavailable: ' + str(exc)
        diagnostic = dict(server=self.url, result=reason)
        logger = logging.getLogger(__name__)
        (logger.info if changed else logger.warning)('extension automatic binding: %s', diagnostic)
        if self.channel is not None:
            self.channel.command.emit(dict(kind='diagnostic', name='extension_binding', value=diagnostic))
        if changed:
            # Do not construct the lazy page: its startup hook may check for
            # automatic updates. An existing page only refreshes its status.
            host = self.workspace.utility
            page = getattr(host, 'settings_page', None) if host is not None else None
            manager = page.__dict__.get('_manager') if page is not None else None
            if manager is not None:
                if not manager.extension.startup_checked: manager.extension.skip_startup_update = True
                manager.extension.render()

    def channel_health(self, value, generation):
        if generation!=self.channel_generation or self.channel is None:return
        if 'display' in value:
            if self.channel.identity!=value['identity']:return
            for client in self.live_clients():
                items=value['display'].get(getattr(client.window,'runtime_token',None))
                if items:client.events.display(items)
            return
        self.channel.identity=dict(value['identity'])
        health=value.get('health')
        if health is not None:
            self.channel_receiving=bool(health.get('state')=='receiving' and
                health.get('log_consistent',True) and not health.get('needs_reconcile'))
            if health.get('state') in ('detected','unconfirmed'):
                # The worker's 12s deadline also projects onto the existing
                # connection-check state, including utility pages. Do not wait
                # for the slower metadata request or retire any operation.
                for client in self.live_clients():
                    if client.enabled and not client.connection_checking:
                        client.connection_checking=True
                        client.message='正在確認 ComfyUI 連線…'
                        client.stateChanged.emit()
        for client in self.live_clients():
            if not client.document_polling:continue
            if client.events.identity!=value['identity']:client.events.bind(value['identity'])
            client.events.probed=self.authorize_snapshot
            if value.get('heartbeat'):client.events.heartbeat()
            else:client.events.health(value['health'])
        self.authorize_snapshot()

    def authorize_snapshot(self):
        if self.channel is None:return
        pending=[row for client in self.live_clients() for row in (*client.events.reconcile.pending.values(),*client.events.aux.pending.values())]
        if all(row.get('probed') for row in pending):
            self.channel.command.emit(dict(kind='snapshot_authorized'))

    def deliver_events(self, page, generation):
        if generation!=self.channel_generation or self.channel is None:return
        if page['identity']!=self.channel.identity:return
        client=next((c for c in self.live_clients() if getattr(c.window,'runtime_token',None)==page['owner']),None)
        if client is None:
            self.pending_event_owners.add(page['owner'])
            logging.getLogger(__name__).warning('event delivery remains pending for unavailable owner %s',page['owner'])
            return
        if client.events.identity!=page['identity']:client.events.bind(page['identity'])
        def apply(index=0):
            if generation!=self.channel_generation or client.stopped or page['identity']!=self.channel.identity:return
            try:
                if index==len(page['events']):
                    self.channel.command.emit(dict(kind='delivered',page=page,running=client.events.running_prompts()))
                    if page['snapshot'] and page['done']:
                        client.events.snapshot_ready=True
                        client.events.ready=client.events.channel_healthy and not client.events.reconcile.pending
                        self.channel.command.emit(dict(kind='reconciled'))
                    return
                client.events.receive(page['events'][index],lambda:apply(index+1),snapshot=bool(page['snapshot']))
            except (sqlite3.Error,OSError,ValueError) as exc:
                logging.getLogger(__name__).warning('document event acceptance pending: %s',exc)
                QTimer.singleShot(1000,lambda:apply(index))
        apply()

    def observe_poll(self, client, route, data, value):
        if self.channel is None or client.url != self.channel.url: return
        from .comfy_event_transport import poll_evidence
        evidence = poll_evidence(route, data, value)
        if not evidence: return
        owner = getattr(client.window, 'runtime_token', '')
        fresh = []
        for entity, outcome in evidence:
            key = owner, entity
            if self.event_observations.get(key) != outcome:
                self.event_observations[key] = outcome
                fresh.append((entity, outcome))
        if fresh: self.channel.command.emit(dict(kind='poll', owner=owner, evidence=fresh))

    def save(self, url, enabled):
        payload = json.dumps(dict(url=url, enabled=enabled), ensure_ascii=False).encode('utf-8')
        output = QSaveFile(str(self.path))
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        if output.write(payload) != len(payload):
            output.cancelWriting()
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())
        self.url, self.enabled = url, enabled

    def connect_to(self, address):
        address = local_address(address)
        changed_address = address != self.url
        clients = self.live_clients()
        if any(client.url != address and (
                client.run_id or client.generation.batch or client.queue.busy()
                or client.input_flow.has_work() or client.polling or client.replies
                or self.workspace.scheduler.pending(client))
                for client in clients):
            self.workspace.show_notice('目前仍有工作使用原連線；請待工作結束後再切換 ComfyUI 位址。')
            return False
        if changed_address and self.workspace.runtimes.errors:
            self.workspace.show_notice('任務紀錄仍待確認，請先處理原連線的紀錄再切換位址。')
            return False
        try:
            self.save(address, True)
        except OSError as exc:
            self.workspace.show_notice('ComfyUI 連線設定無法儲存：'+str(exc))
            return False
        if changed_address:
            self.registering = None
            self.registered_client = None
        self.stop_channel()
        # Keep a status-only client available when focus moves from Canvas
        # to Markdown or a new tab. It never owns a generation or import.
        self.workspace.ensure_utility()
        clients = self.live_clients()
        for client in clients:
            client.window.state['settings'].update(comfy_url=address, comfy_enabled=True)
            if client.url != address:
                ComfyClient.connect_to(client, address)
            else:
                ComfyClient.recheck_connection(client)
            if hasattr(client.window, 'runtime_token'):
                self.remember_event_owner(client.window)
        return True

    def disconnect(self):
        try:
            self.save(self.url, False)
        except OSError as exc:
            self.workspace.show_notice('ComfyUI 連線設定無法儲存：'+str(exc))
            return False
        self.stop_channel()
        for client in self.live_clients():
            ComfyClient.disconnect(client)
        self.registering = None
        self.registered_client = None
        return True


class WorkspaceComfyClient(ComfyClient):
    """Use real transport without giving service pages a document's work."""
    def __init__(self, window):
        self.workspace = getattr(window, 'workspace_owner', None)
        super().__init__(window)
        if self.workspace is not None:
            self.workspace.comfy_connection.attach(self)
            self.workspace.scheduler.attach(self)
        self.operation_checks = set()
        with self.window.store.db:
            self.window.store.db.execute('''CREATE TABLE IF NOT EXISTS workspace_operations
                (id TEXT PRIMARY KEY, server TEXT, route TEXT, ended INTEGER NOT NULL, status TEXT)''')
        if self.workspace is not None and hasattr(window, 'runtime_token'):
            self.workspace.comfy_connection.remember_event_owner(window)

    def request(self, route, data=None, done=None, failed=None, raw=None, content_type='application/json', **options):
        if self.stopped:
            return
        if self.workspace is None:
            return super().request(route, data, done, failed, raw, content_type, **options)
        scheduler = self.workspace.scheduler
        auxiliary = route in ('workflow/native/open', 'workflow/native/inspect', 'workflow/queue/capture')
        mutation = route in ('workflow/native/start', 'workflow/native/apply', 'workflow/queue/submit',
                             'workflow/queue/variant', '/prompt', '/upload/image', 'desktop/command',
                             'desktop/interrupt')
        if route in ('workflow/native/start','workflow/queue/submit','/prompt') and not self.dispatch_allowed:
            (failed or self.window.notice)(RequestFailure(self.scheduling_message or '無法確認',rejected=True))
            return
        if auxiliary:
            allowed = scheduler.acquire_operation(self)
        else:
            allowed = not mutation or scheduler.owns(self)
        if not allowed:
            error = RequestFailure(scheduler.operation_blocker(self), rejected=True)
            (failed or self.window.notice)(error)
            return
        ident = data.get('id') if isinstance(data, dict) else None
        if auxiliary:
            if not ident:
                (failed or self.window.notice)(RequestFailure('操作缺少任務身分，未送出。', rejected=True))
                return
            if not self.events.aux.owns(ident):
                with self.window.store.db:
                    self.window.store.db.execute('INSERT INTO workspace_operations VALUES (?,?,?,0,?)',
                                                (ident, self.url, route, 'submitting'))
        channel_generation = self.workspace.comfy_connection.channel_generation
        channel = self.workspace.comfy_connection.channel
        event_ident = (data.get('attempt') if route.startswith('workflow/queue/') else ident) if isinstance(data, dict) else None
        if channel is not None and event_ident and self.url == channel.url:
            if route in ('workflow/native/start', 'workflow/queue/submit', 'workflow/native/cancel', 'workflow/queue/cancel'):
                channel.command.emit(dict(kind='watch',
                    entity=('queue_attempts:' if route.startswith('workflow/queue/') else 'native_operations:') + event_ident,
                    phase='cancelling' if route.endswith('/cancel') else 'submitting'))
        def received(value):
            manager = self.workspace.comfy_connection
            try:
                if channel_generation == manager.channel_generation:
                    if route == 'desktop/status': manager.observe_status(self, value)
            except Exception:
                logging.getLogger(__name__).exception('channel capability observation failed')
            if ident and (route in ('workflow/native/status', 'workflow/native/cancel') or auxiliary):
                self.reconcile_operation(ident, value)
            if done:
                done(value)
        def rejected(error):
            if auxiliary and getattr(error, 'rejected', False):
                self.reconcile_operation(ident, dict(state='failed'))
            (failed or self.window.notice)(error)
        return super().request(route, data, received, rejected, raw, content_type, **options)

    def reset_connection(self):
        if hasattr(self, 'operation_checks'):
            self.operation_checks.clear()
        return super().reset_connection()

    def reconcile_operation(self, ident, value):
        if self.events.aux.owns(ident):return
        state = value.get('state')
        ended = state in ('opened', 'inspected', 'captured', 'applied', 'failed', 'settled')
        with self.window.store.db:
            self.window.store.db.execute(
                'UPDATE workspace_operations SET ended=?,status=? WHERE id=? AND (ended=0 OR ?=1)',
                (int(ended), state or 'unknown', ident, int(ended)))
        chain = self.input_flow.chain
        row = chain.store.read(ident)
        if row and row.get('kind') == 'input' and ended:
            chain.store.update(ident, ended=True, status=state)

    def poll(self):
        operations=set()
        if self.connected and not self.stopped and self.document_polling:
            db = self.window.store.db
            operations = {r[0] for r in db.execute(
                'SELECT id FROM workspace_operations WHERE ended=0 AND server=?', (self.url,))}
            operations.update(r[0] for r in db.execute("SELECT id FROM stage_header WHERE kind='input' AND json_extract(body,'$.server')=? AND json_extract(body,'$.sent')=1 AND COALESCE(json_extract(body,'$.ended'),0)=0 AND COALESCE(json_extract(body,'$.aux_version'),0)!=1",(self.url,)))
        connection=self.workspace.comfy_connection if self.workspace else None
        chain=self.input_flow.chain
        slow=bool(connection and connection.channel_receiving and self.connected and self.event_capable and
            (not self.document_polling or self.events.ready) and not operations and not self.run_id and
            not (self.register_library and self.owns_library_registration) and
            (not getattr(chain,'applying',None) or self.events.aux.owns(chain.applying)) and not getattr(chain,'apply_queue',()) and
            not getattr(chain,'apply_cancellations',()))
        # Only the periodic timer is coalesced. Explicit rechecks/commands keep
        # their immediate poll. Pending owner registration and C2 input/auxiliary
        # unmanaged operations keep 1.5s; covered input/aux use durable receipts.
        # Reuse the operation lookup above; do not add another queue scan.
        if not (self.sender() is self.timer and slow and self._status_clock()-self._desktop_status_at<8):
            super().poll()
        if not self.connected or self.stopped or not self.document_polling:return
        for ident in operations - self.operation_checks:
            self.operation_checks.add(ident)
            def done(value, key=ident):
                self.operation_checks.discard(key)
            def failed(error, key=ident):
                self.operation_checks.discard(key)
            self.request('workflow/native/status', dict(id=ident), done, failed)

    @property
    def dispatch_allowed(self):
        if self.event_capable is False:return False
        if self.events.enabled and self.events.identity is not None and not self.events.ready:return False
        if self.workspace is None:
            return True
        if not self.workspace.scheduler.claim(self):
            return False
        connection = self.workspace.comfy_connection
        if connection.registering is not None:
            return False
        if connection.registered_client is not self:
            self.register_document_library()
            return False
        return True

    @property
    def scheduling_message(self):
        if self.stopped:return ''
        if any(item.get('error')=='無法確認' for item in self.events.aux.active.values()):return '無法確認'
        if self.event_capable is False:return '需要更新擴充'
        if self.events.enabled and any(j.get('error')=='無法確認' for j in self.generation.jobs.values()):return '無法確認'
        return self.workspace.scheduler.description(self) if self.workspace else ''

    def start_saved_connection(self):
        if not self.stopped and self.workspace.comfy_connection.enabled:
            ComfyClient.recheck_connection(self)

    @property
    def document_polling(self):
        # DocumentExecution retains a fixed owner across focus and Save As.
        # Utility settings pages only observe transport/capability status.
        return hasattr(self.window, 'document_id')

    @property
    def can_run(self):
        document = getattr(self.window, 'document', None)
        return bool(document is not None and document.kind == 'workflow' and super().can_run)

    def interrupt(self, clear_pending=False):
        if not self.connected and self.document_polling:
            self.input_flow.chain.cancel()
            self.workspace.scheduler.refresh()
            return
        result = super().interrupt(clear_pending)
        if clear_pending and self.connected and self.document_polling:
            # Input-only and frontend operations may have no Stage run, or
            # survive a restart after their callbacks were lost. Cancel by
            # original ID, never through the server's global interrupt.
            chain = self.input_flow.chain
            for ident,item in list(self.events.aux.active.items()):
                if item['action']!='apply' and item['sent']:self.events.aux.cancel(ident)
            for item in chain.store.rows('input'):
                if item.get('sent') and not item.get('ended') and item.get('server') == self.url:
                    chain.request_input_cancel(item)
            operations = list(self.window.store.db.execute(
                'SELECT id FROM workspace_operations WHERE ended=0 AND server=?', (self.url,)))
            for (ident,) in operations:
                self.request('workflow/native/cancel', dict(id=ident),
                             failed=lambda error: self.window.notice('取消尚未確認：'+str(error)))
            self.workspace.scheduler.refresh()
        return result

    @property
    def workflow_import_owner(self):
        return getattr(self.window, 'document', None)

    def receive_workflow(self):
        # A retired execution may finish its jobs, but cannot accept imports.
        if self.owns_library_registration:
            super().receive_workflow()

    @property
    def owns_library_registration(self):
        if self.workspace is None or not self.document_polling:
            return False
        document = self.window.document
        if document is None:
            return False
        tab = self.workspace.tabs_by_key.get(self.workspace.active_key)
        owner = self.workspace.scheduler.owner
        target = (owner == self.window.runtime_token if owner is not None
                  else tab is not None and tab.document is document)
        return (target and document.kind == 'workflow'
                and self.workspace.comfy_connection.registering is None)

    def register_document_library(self):
        # The bridge has one current library. Serialize registrations so a
        # slow response for the previous tab cannot overtake the new target.
        connection = self.workspace.comfy_connection
        if connection.registering is not None or self.stopped:
            return
        operation = object()
        chain = self.input_flow.chain

        def terminate(error=None):
            # Duplicate or late replies may neither pause an old document nor
            # release/replace a newer document's registration.
            if connection.registering is not operation:
                return
            try:
                if error is None:
                    connection.registered_client = self
                else:
                    message = '工作流連線設定未完成：'+str(error)
                    with chain.store.transaction():
                        for run in chain.runs(True):
                            if run['status'] in ('running', 'waiting'):
                                chain.store.patch(run['id'], status='paused', pause_reason='error', message=message)
                    self.window.notice(message)
            finally:
                connection.registering = None
                chain.notify()
                chain.later()
                self.workspace.scheduler.refresh()

        try:
            connection.registering = operation
            connection.registered_client = None
            self.register_library = False
            self.window.persist()
            self.request('config', dict(library=str(self.window.store.directory/'studio.sqlite3')),
                         done=lambda _: terminate(), failed=terminate)
        except Exception as exc:
            terminate(exc)

    @property
    def receives_unscoped_results(self):
        # Runners fetch their own prompt IDs. A server-wide result feed must
        # never be copied into every open Canvas or the utility host.
        return False

    def connect_to(self, address):
        if self.workspace is None:
            return super().connect_to(address)
        return self.workspace.comfy_connection.connect_to(address)

    def recheck_connection(self):
        self.events.results.refresh_requested=True
        if self.workspace is None:
            return super().recheck_connection()
        return self.workspace.comfy_connection.connect_to(self.workspace.comfy_connection.url)

    def disconnect(self):
        if self.workspace is None:
            return super().disconnect()
        return self.workspace.comfy_connection.disconnect()
