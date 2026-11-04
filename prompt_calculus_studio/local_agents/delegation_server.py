"""Same-user short-lived IPC. No Chrome commands, origins or tokens are accepted."""
import hashlib
import hmac
import json
import secrets

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer

from ..local_ai.registry import atomic_json
from .delegation_protocol import MAX_REQUEST, MAX_RESPONSE, DelegationError, discovery_directory, encode, validate


class DelegationServer(QObject):
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.instance_id = hashlib.sha256(str(controller.workspace.data.resolve()).casefold().encode()).hexdigest()[:24]
        self.pipe_name = 'pcs-agent-' + self.instance_id
        self.directory = discovery_directory()
        self.token = self.connection_id = ''
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.setMaxPendingConnections(8)
        self.server.newConnection.connect(self.accept)
        self.clients = {}

    def start(self):
        if self.server.isListening():
            return
        if not self.server.listen(self.pipe_name):
            raise DelegationError('instance_busy', '這個工作區的本機委派入口已在使用。')
        self.token, self.connection_id = secrets.token_hex(32), secrets.token_hex(16)
        try:
            atomic_json(self.directory/(self.instance_id+'.json'), dict(version=1, instance_id=self.instance_id,
                        connection_id=self.connection_id, pipe_name=self.pipe_name, token=self.token,
                        display_name=self.controller.workspace.root.name[:200]))
        except (OSError, ValueError):
            self.server.close()
            self.token = self.connection_id = ''
            raise

    def stop(self):
        if self.server.isListening():
            self.server.close()
            path = self.directory/(self.instance_id+'.json')
            try:
                if json.loads(path.read_text(encoding='utf-8')).get('connection_id') == self.connection_id:
                    path.unlink()
            except (OSError, ValueError):
                pass
        for socket in list(self.clients):
            socket.abort()
        self.token = self.connection_id = ''

    def accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if len(self.clients) >= 8:
                socket.abort(); socket.deleteLater(); continue
            socket.setReadBufferSize(MAX_REQUEST+1)
            timer = QTimer(socket)
            timer.setSingleShot(True)
            timer.setInterval(5000)
            timer.timeout.connect(socket.abort)
            self.clients[socket] = bytearray(), timer
            socket.readyRead.connect(lambda s=socket: self.read(s))
            socket.disconnected.connect(lambda s=socket: self.drop(s))
            timer.start()

    def drop(self, socket):
        self.clients.pop(socket, None)
        socket.deleteLater()

    def dispatch(self, request):
        validate(request)
        token = request['token']
        if (not self.token or not isinstance(token, str) or not hmac.compare_digest(token, self.token)
                or request['connection_id'] != self.connection_id):
            raise DelegationError('connection_expired', '委派連線已變更，請重新查詢 PCS 實例。')
        if request['instance_id'] != self.instance_id:
            raise DelegationError('wrong_instance', '委派目標與這個 PCS 工作區不符。')
        return self.controller.dispatch(request)

    def read(self, socket):
        if socket not in self.clients:
            return
        buffer, timer = self.clients[socket]
        buffer.extend(bytes(socket.readAll()))
        if len(buffer) > MAX_REQUEST:
            socket.abort(); return
        if b'\n' not in buffer:
            return
        line, _, tail = buffer.partition(b'\n')
        if tail:
            socket.abort(); return
        request_id = None
        try:
            request = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            validate(request)
            request_id = request['request_id']
            result = self.dispatch(request)
            response = dict(request_id=request_id, ok=True, result=result)
            if len(encode(response)) > MAX_RESPONSE:
                raise DelegationError('response_too_large', '委派結果超過回傳上限，原對話仍保留。')
        except (ValueError, OSError, KeyError, TypeError, RecursionError) as exc:
            # Never include arbitrary request content or connection credentials.
            response = dict(request_id=request_id, ok=False, error=dict(
                code=getattr(exc, 'code', 'request_failed'),
                message=str(exc) if isinstance(exc, DelegationError) else '無法完成委派操作，請檢查 PCS。'))
        socket.readyRead.disconnect()
        socket.write(encode(response)+b'\n')
        socket.disconnectFromServer()
        # Keep the original deadline until the short response drains.
