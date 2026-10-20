"""Workspace connection preferences; execution still belongs to each document."""
import json
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
        return True

    def disconnect(self):
        try:
            self.save(self.url, False)
        except OSError as exc:
            self.workspace.show_notice('ComfyUI 連線設定無法儲存：'+str(exc))
            return False
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

    def request(self, route, data=None, done=None, failed=None, raw=None, content_type='application/json'):
        if self.stopped:
            return
        if self.workspace is None:
            return super().request(route, data, done, failed, raw, content_type)
        scheduler = self.workspace.scheduler
        auxiliary = route in ('workflow/native/open', 'workflow/native/inspect', 'workflow/queue/capture')
        mutation = route in ('workflow/native/start', 'workflow/native/apply', 'workflow/queue/submit',
                             'workflow/queue/variant', '/prompt', '/upload/image', 'desktop/command',
                             'desktop/interrupt')
        if auxiliary:
            allowed = scheduler.acquire_operation(self)
        else:
            allowed = not mutation or scheduler.owns(self)
        if not allowed:
            error = RequestFailure('另一份 PCS 工作流正在執行或等待確認，這次操作未送出。', rejected=True)
            (failed or self.window.notice)(error)
            return
        ident = data.get('id') if isinstance(data, dict) else None
        if auxiliary:
            if not ident:
                (failed or self.window.notice)(RequestFailure('操作缺少任務身分，未送出。', rejected=True))
                return
            with self.window.store.db:
                self.window.store.db.execute('INSERT INTO workspace_operations VALUES (?,?,?,0,?)',
                                            (ident, self.url, route, 'submitting'))
        def received(value):
            if ident and (route in ('workflow/native/status', 'workflow/native/cancel') or auxiliary):
                self.reconcile_operation(ident, value)
            if done:
                done(value)
        def rejected(error):
            if auxiliary and getattr(error, 'rejected', False):
                self.reconcile_operation(ident, dict(state='failed'))
            (failed or self.window.notice)(error)
        return super().request(route, data, received, rejected, raw, content_type)

    def reset_connection(self):
        if hasattr(self, 'operation_checks'):
            self.operation_checks.clear()
        return super().reset_connection()

    def reconcile_operation(self, ident, value):
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
        super().poll()
        if not self.connected or self.stopped or not self.document_polling:
            return
        db = self.window.store.db
        operations = {r[0] for r in db.execute(
            'SELECT id FROM workspace_operations WHERE ended=0 AND server=?', (self.url,))}
        operations.update(row['id'] for row in self.input_flow.chain.store.rows('input')
                          if row.get('server') == self.url and row.get('sent') and not row.get('ended'))
        for ident in operations - self.operation_checks:
            self.operation_checks.add(ident)
            def done(value, key=ident):
                self.operation_checks.discard(key)
            def failed(error, key=ident):
                self.operation_checks.discard(key)
            self.request('workflow/native/status', dict(id=ident), done, failed)

    @property
    def dispatch_allowed(self):
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
        operation = object()
        connection.registering = operation
        self.register_library = False
        self.window.persist()

        def finished(_=None):
            if connection.registering is operation:
                connection.registering = None
                connection.registered_client = self
                self.input_flow.chain.later()

        def failed(message):
            if connection.registering is operation:
                connection.registering = None
            for run in self.input_flow.chain.runs(True):
                if run['status'] == 'running':
                    self.input_flow.chain.pause(run['id'], '工作流連線設定未完成：'+str(message), reason='error')
            self.window.notice(message)

        self.request('config', dict(library=str(self.window.store.directory/'studio.sqlite3')),
                     done=finished, failed=failed)

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
        if self.workspace is None:
            return super().recheck_connection()
        return self.workspace.comfy_connection.connect_to(self.workspace.comfy_connection.url)

    def disconnect(self):
        if self.workspace is None:
            return super().disconnect()
        return self.workspace.comfy_connection.disconnect()
