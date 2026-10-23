"""Same-user local IPC; PCS owns preview, acceptance, writer and receipts."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot, QUrl, QRunnable, QThreadPool, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QLocalServer
from shiboken6 import isValid

from .connector_store import TransferStore, atomic_json, public_record, operation_key
from .connector_destinations import DestinationCatalog, DestinationError
from .conversation_archive import Cancelled, load_archive
from .conversation_import import Destination, ImportResult, import_selected
from .local_documents.directory_pin import pinned_parent
from .local_documents import paths
from .retained_files import read_local


def discovery_directory():
    override = os.environ.get('PCS_CONNECTOR_DISCOVERY')
    if override:
        return Path(override).absolute()
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local' / 'share')))
    return base / 'PromptCalculusStudio' / 'Connector' / 'instances'


class DirectSaveJob(QRunnable):
    def __init__(self, operation, function, completed):
        super().__init__()
        self.operation = operation
        self.function = function
        self.completed = completed
        self.cancel = threading.Event()
        self.submitted = False

    def run(self):
        error = ''
        try:
            self.function(self.cancel)
        except Exception as failure:
            error = str(failure)
        finally:
            self.completed.emit(self.operation, error)


class ConnectorService(QObject):
    changed = Signal()
    direct_done = Signal(str, str)

    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.instance_id = hashlib.sha256(str(workspace.data.resolve()).casefold().encode()).hexdigest()[:24]
        self.pipe_name = 'pcs-connector-' + self.instance_id
        self.token = secrets.token_hex(32)
        self.directory = discovery_directory()
        self.store = None
        self.dialogs = {}
        self.writing = set()
        self.direct_jobs = {}
        self.direct_pool = QThreadPool(self)
        self.direct_pool.setMaxThreadCount(1)
        self.direct_done.connect(self.direct_finished, Qt.ConnectionType.QueuedConnection)
        self.closing = False
        self.error = ''
        self.settings_path = workspace.data / 'chrome-connector.json'
        self.settings = dict(enabled=False, destination=str(workspace.root))
        self.destinations = DestinationCatalog(lambda: list(workspace.locations.roots),
                                               lambda: self.settings['destination'], self.check_isolation)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.setMaxPendingConnections(8)
        self.server.newConnection.connect(self.accept_connections)
        self.clients = {}
        try:
            if self.settings_path.exists():
                saved = json.loads(read_local(self.settings_path, 64 * 1024))
                if (not isinstance(saved, dict) or type(saved.get('enabled')) is not bool
                        or not isinstance(saved.get('destination'), str)):
                    raise ValueError('Chrome 連接設定無效。')
                self.settings.update(enabled=saved['enabled'], destination=saved['destination'])
            if self.settings['enabled']:
                self.start()
        except (ValueError, OSError) as error:
            self.error = str(error)

    @property
    def ready(self):
        return (self.server.isListening() and not self.closing and self.workspace.workspace_ready
                and not self.workspace._startup_loading and not self.workspace.layout_store.stopped)

    def ensure_store(self):
        self.check_isolation()
        if self.store is None:
            folder = self.directory.parent / 'transfers' / self.instance_id
            self.store = TransferStore(folder)
            self.store.recover()
        return self.store

    def check_isolation(self, destination=None):
        folder = (self.directory.parent / 'transfers' / self.instance_id).resolve()
        roots = list(self.workspace.locations.roots)
        roots.append(Path(destination or self.settings['destination']))
        for root in roots:
            root = Path(root).resolve()
            if folder.is_relative_to(root) or root.is_relative_to(folder):
                raise ValueError('請選擇連接暫存區以外的工作區與保存位置。')

    def start(self):
        if self.server.isListening():
            return
        self.ensure_store()
        self.directory.mkdir(parents=True, exist_ok=True)
        paths.root_path(self.directory)
        # Do not remove another live endpoint. Existing instance/data lock wins.
        if not self.server.listen(self.pipe_name):
            raise ValueError('Chrome 連接無法啟動：' + self.server.errorString())
        try:
            atomic_json(self.directory / (self.instance_id + '.json'), dict(
                version=1, instance_id=self.instance_id, pipe_name=self.pipe_name, token=self.token,
                display_name=self.workspace.root.name, updated_at=time.time()))
        except Exception:
            self.server.close()
            raise
        self.error = ''

    def set_enabled(self, enabled):
        if self.writing:
            raise ValueError('正在保存對話，請稍候。')
        if enabled:
            self.start()
        else:
            self.stop()
        updated = dict(self.settings, enabled=bool(enabled))
        try:
            atomic_json(self.settings_path, updated)
        except Exception:
            if enabled:
                self.stop()
            elif self.settings['enabled']:
                self.start()
            raise
        self.settings = updated
        self.changed.emit()

    def set_destination(self, path):
        self.check_isolation(path)
        destination = Destination.choose(path)
        updated = dict(self.settings, destination=str(destination.path))
        atomic_json(self.settings_path, updated)
        self.settings = updated
        self.changed.emit()

    def accept_connections(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if len(self.clients) >= 8:
                socket.abort()
                socket.deleteLater()
                continue
            socket.setReadBufferSize(512 * 1024 + 1)
            timer = QTimer(socket)
            timer.setSingleShot(True)
            timer.setInterval(20000)
            timer.timeout.connect(socket.abort)
            self.clients[socket] = (bytearray(), timer)
            socket.readyRead.connect(lambda s=socket: self.read_request(s))
            socket.disconnected.connect(lambda s=socket: self.drop_client(s))
            timer.start()

    def drop_client(self, socket):
        self.clients.pop(socket, None)
        socket.deleteLater()

    def read_request(self, socket):
        if socket not in self.clients:
            return
        buffer, timer = self.clients[socket]
        buffer.extend(bytes(socket.readAll()))
        if len(buffer) > 512 * 1024:
            socket.abort()
            return
        if b'\n' not in buffer:
            return
        line, _, tail = bytes(buffer).partition(b'\n')
        # One operation per short-lived pipe connection.
        if tail:
            socket.abort()
            return
        timer.stop()
        request = {}
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError('連接請求無效。')
            token = request.get('token', '')
            if not isinstance(token, str) or not hmac.compare_digest(token, self.token):
                raise ValueError('請重新連接 PCS。')
            result = self.dispatch(request)
            response = dict(request_id=request.get('request_id'), ok=True, result=result)
        except (ValueError, OSError, KeyError, TypeError, RuntimeError, RecursionError) as error:
            response = dict(request_id=request.get('request_id') if isinstance(request, dict) else None,
                            ok=False, error=dict(code=getattr(error, 'code', 'request_failed'), message=str(error)))
        socket.write(json.dumps(response, ensure_ascii=False, allow_nan=False).encode() + b'\n')
        socket.disconnectFromServer()

    def dispatch(self, request):
        command = request.get('command')
        if command == 'hello':
            return dict(version=1, instance_id=self.instance_id, display_name=self.workspace.root.name,
                        ready=self.ready, capabilities=['save_destination_v1'], max_capture_bytes=16 * 1024 * 1024)
        if not self.ready:
            raise ValueError('PCS 尚未就緒，請稍後再試。')
        store = self.ensure_store()
        if command == 'destinations':
            return self.destinations.list(request.get('folder_id'))
        operation = request.get('operation_id')
        if command == 'begin':
            destination_id = request.get('destination_id')
            choice = None
            if destination_id is not None and store.get(operation) is None:
                choice = self.destinations.resolve(destination_id)
            result = store.begin(operation, request.get('size'), request.get('sha256'),
                                 destination_id=destination_id,
                                 destination=choice.destination if choice else None,
                                 destination_root=choice.root if choice else None)
        elif command == 'chunk':
            result = store.chunk(operation, request.get('index'), request.get('data'))
        elif command == 'finish':
            result = store.finish(operation)
            if result['state'] == 'previewing':
                if result.get('destination_id'):
                    result = self.start_direct(result['operation_id'])
                else:
                    QTimer.singleShot(0, self, lambda key=result['operation_id']: self.show_preview(key))
        elif command == 'status':
            if not self.writing:
                store.recover()
            result = store.get(operation)
        elif command == 'cancel':
            job = self.direct_jobs.get(operation_key(operation))
            if job is not None:
                job.cancel.set()
            result = store.get(operation)
            if result:
                dialog = self.dialogs.get(result['operation_id'])
                if dialog is not None and isValid(dialog):
                    dialog.reject()
            result = store.cancel(operation)
        elif command in ('open', 'reveal'):
            self.open_saved(operation, reveal=command == 'reveal')
            result = store.get(operation)
        else:
            raise ValueError('不支援這個操作，請更新擴充。')
        if command not in ('chunk', 'status'):
            self.changed.emit()
        return public_record(result)

    def start_direct(self, operation):
        store = self.store
        record = store.get(operation)
        if not record or record['state'] != 'previewing':
            return record
        try:
            choice = store.bound_choice(record)
            destination = self.destinations.check_bound(choice)
            store.accept(operation, destination)
        except (ValueError, OSError) as error:
            return store.fail_pending(operation, error)

        def write(cancel):
            entered_writer = False
            try:
                source = load_archive(record['source'], cancel)
                self.destinations.check_bound(choice)
                # Keep all selected ancestors stable through validation and the
                # existing writer, including the originally enrolled root.
                with pinned_parent(destination.path / '.connector-direct.guard'):
                    self.destinations.check_bound(choice)
                    entered_writer = True
                    result = import_selected(source, (0,), destination, cancel,
                                             operation_id=operation, on_started=store.started,
                                             on_reused=store.reused)
                    store.complete(operation, result)
            except Cancelled:
                store.complete(operation, ImportResult(cancelled=True))
            except Exception as error:
                # Publication or receipt failures require reconciliation. Only
                # an error before entering the writer is a known failed save.
                if not entered_writer:
                    store.complete(operation, error=str(error), error_code=getattr(error, 'code', 'request_failed'))
                raise

        job = DirectSaveJob(operation, write, self.direct_done)
        self.direct_jobs[operation] = job
        self.writing.add(operation)
        try:
            self.submit_direct_jobs()
        except Exception:
            self.direct_jobs.pop(operation, None)
            self.write_finished(operation)
            raise
        self.changed.emit()
        return store.get(operation)

    def submit_direct_jobs(self):
        # Legacy compact-dialog jobs also own the writer until their finished
        # callback. Keep direct requests queued rather than racing that owner.
        if self.writing - self.direct_jobs.keys():
            return
        for job in self.direct_jobs.values():
            if not job.submitted:
                self.direct_pool.start(job)
                job.submitted = True

    @Slot(str, str)
    def direct_finished(self, operation, error):
        self.direct_jobs.pop(operation, None)
        if error:
            self.error = error
        self.write_finished(operation)
        if isValid(self.workspace):
            self.workspace.refresh_tree()

    def show_preview(self, operation):
        if not self.ready:
            return
        current = self.dialogs.get(operation)
        if current is not None and isValid(current):
            current.show()
            current.raise_()
            current.activateWindow()
            return
        from .connector_dialog import ConnectorImportDialog
        try:
            record = self.store.get(operation)
            if not record or record['state'] != 'previewing' or record.get('destination_id'):
                return
            dialog = ConnectorImportDialog(self, record)
            self.dialogs[operation] = dialog
            dialog.destroyed.connect(lambda: self.dialogs.pop(operation, None))
            dialog.show()
            dialog.raise_()
            self.workspace.showNormal() if self.workspace.isMinimized() else None
            self.workspace.raise_()
            self.workspace.activateWindow()
        except (ValueError, OSError) as error:
            self.workspace.show_notice(str(error))

    def commit(self, dialog):
        operation = dialog.operation
        if self.writing:
            raise ValueError('正在保存另一份對話，請稍候。')
        destination = dialog.destination
        self.check_isolation(destination.path)
        self.store.accept(operation, destination)
        self.writing.add(operation)
        source = dialog.archive

        def write(cancel, progress):
            result = import_selected(source, (0,), destination, cancel, progress,
                                     operation_id=operation, on_started=self.store.started,
                                     on_reused=self.store.reused)
            # A receipt/ledger failure after publication must remain reconcilable;
            # never replace a durable writer success with a generic failed state.
            self.store.complete(operation, result)
            return result

        try:
            dialog.start_job(write, 'write')
        except Exception:
            self.write_finished(operation)
            raise
        self.changed.emit()

    def write_finished(self, operation):
        self.writing.discard(operation)
        self.submit_direct_jobs()
        try:
            if not self.writing:
                self.store.recover()
        except (ValueError, OSError) as error:
            self.error = str(error)
        self.changed.emit()

    def open_saved(self, operation, *, reveal=False):
        target = self.ensure_store().saved_path(operation)
        if reveal:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))
        else:
            if self.workspace.locations.root_for(target) is None:
                self.workspace.add_root(Path(self.store.get(operation)['destination']))
            tab = next((item for item in self.workspace.tabs_by_key.values() if item.path == target), None)
            if tab is None:
                tab = self.workspace.open_path(target, retained=True, new_tab=True)
            else:
                self.workspace.focus_tab(tab)
            if tab is not None:
                self.workspace.raise_()
                self.workspace.activateWindow()
            return tab

    def stop(self):
        if self.writing:
            raise ValueError('正在保存對話，請完成後再關閉。')
        self.server.close()
        for socket in list(self.clients):
            socket.abort()
        endpoint = self.directory / (self.instance_id + '.json')
        if endpoint.exists():
            try:
                value = json.loads(read_local(endpoint, 64 * 1024))
                if isinstance(value, dict) and value.get('token') == self.token:
                    endpoint.unlink()
            except (ValueError, OSError):
                pass
        for operation, dialog in list(self.dialogs.items()):
            if isValid(dialog):
                dialog.reject()
        if self.store is not None:
            for record in self.store.records():
                if record['state'] == 'receiving':
                    self.store.cancel(record['operation_id'])


def connector_for(workspace):
    current = getattr(workspace, 'chrome_connector', None)
    if current is None:
        current = workspace.chrome_connector = ConnectorService(workspace)
    return current
