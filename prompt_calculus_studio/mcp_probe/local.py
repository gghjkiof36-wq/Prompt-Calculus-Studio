"""Pair a single synthetic Windows owner with a bounded same-user named pipe."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from uuid import UUID, uuid4

import pywintypes
import win32crypt
from PySide6.QtCore import QCoreApplication, QIODevice, QLockFile, QSaveFile, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ..local_documents import paths
from ..retained_files import read_local
from .contracts import ProbeError, canonical
from .service import ProbeService

MAX_FRAME = 16384
DEADLINE_MS = 2000
PAIR_FILE = "a01-pair.bin"
ENTROPY = b"PCS.synthetic-mcp.pair.v1"
PAIR_SHAPE = re.compile(r"[0-9a-f]{32}")
SECRET_SHAPE = re.compile(r"[0-9a-f]{64}")

def instance_id(value: str):
    return str(UUID(value))

def mac(key: bytes, direction: bytes, payload: dict):
    return hmac.new(key, direction + canonical(payload), hashlib.sha256).hexdigest()

def paired_pipe(instance, boot):
    return "pcs-a01-" + instance + "-" + boot

def make_pair(directory, instance, boot, key):
    sealed = win32crypt.CryptProtectData(canonical(dict(instance=instance, boot=boot, key=key.hex())),
                                         "PCS A01 local pair", ENTROPY, None, None, 1)
    file = QSaveFile(str(directory / PAIR_FILE))
    if (not file.open(QIODevice.OpenModeFlag.WriteOnly) or file.write(sealed) != len(sealed)
            or not file.commit()):
        raise OSError("Could not write the isolated pairing record.")

def read_pair(directory, expected):
    path = directory / PAIR_FILE
    try:
        sealed = read_local(path, 4096)
        _, clear = win32crypt.CryptUnprotectData(sealed, ENTROPY, None, None, 1)
        data = json.loads(clear)
        if instance_id(data["instance"]) != expected:
            raise ProbeError("instance_mismatch", "The selected profile belongs to another PCS instance.")
        if not PAIR_SHAPE.fullmatch(data["boot"]) or not SECRET_SHAPE.fullmatch(data["key"]):
            raise ValueError
        return paired_pipe(expected, data["boot"]), bytes.fromhex(data["key"])
    except FileNotFoundError:
        raise ProbeError("service_offline", "Start the paired PCS probe instance.", retryable=True) from None
    except PermissionError:
        raise ProbeError("permission_denied", "This Windows user cannot access the pairing profile.") from None
    except (pywintypes.error, OSError, ValueError, KeyError, TypeError, RecursionError):
        raise ProbeError("authentication_failed", "The pairing record could not be authenticated.") from None

def failure(error):
    return {"schema_version": 1, "ok": False, "error": error.fault.model_dump()}

def exchange(directory: Path, expected: str, operation: str, arguments: dict):
    name, key = read_pair(directory, expected)
    rid = str(uuid4())
    request = dict(instance=expected, request_id=rid, operation=operation, arguments=arguments)
    wire = canonical({"body": request, "mac": mac(key, b"request:", request)}) + b"\n"
    if len(wire) > MAX_FRAME:
        raise ProbeError("invalid_arguments", "Tool input exceeds the probe frame limit.")
    sock = QLocalSocket()
    try:
        sock.setReadBufferSize(MAX_FRAME+1)
        sock.connectToServer(name)
        if not sock.waitForConnected(DEADLINE_MS):
            if sock.error() == QLocalSocket.LocalSocketError.SocketAccessError:
                raise ProbeError("permission_denied", "Windows denied access to this instance.")
            if sock.error() == QLocalSocket.LocalSocketError.SocketTimeoutError:
                raise ProbeError("transport_timeout", "The local connection timed out.", retryable=True)
            if sock.error() not in (QLocalSocket.LocalSocketError.ServerNotFoundError,
                                    QLocalSocket.LocalSocketError.ConnectionRefusedError):
                raise ProbeError("transport_error", "The named pipe could not be opened.", retryable=True)
            raise ProbeError("service_offline", "The paired PCS probe is offline.", retryable=True)
        if sock.write(wire) != len(wire) or (sock.bytesToWrite() and not sock.waitForBytesWritten(DEADLINE_MS)):
            raise ProbeError("transport_error", "PCS did not receive the complete request.", retryable=True)
        deadline = time.monotonic() + DEADLINE_MS/1000
        while not sock.canReadLine():
            remaining = int((deadline-time.monotonic())*1000)
            if remaining <= 0 or sock.bytesAvailable() > MAX_FRAME:
                raise ProbeError("transport_timeout", "No complete response within the limit.", retryable=True)
            if not sock.waitForReadyRead(remaining) and not sock.canReadLine():
                code = "transport_timeout" if time.monotonic() >= deadline-0.02 else "transport_error"
                raise ProbeError(code, "The local response was interrupted.", retryable=True)
        raw = bytes(sock.readLine(MAX_FRAME+1))
        if not raw.endswith(b"\n") or len(raw) > MAX_FRAME:
            raise ValueError
        envelope = json.loads(raw)
        body = envelope["body"]
        if not hmac.compare_digest(mac(key, b"reply:", body), envelope["mac"]):
            raise ProbeError("authentication_failed", "PCS could not authenticate this pairing.")
        if body["instance"] != expected or body["request_id"] != rid:
            raise ProbeError("instance_mismatch", "The response belongs to a different instance or request.")
        return body["outcome"]
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ProbeError("transport_error", "The local response is malformed.") from None
    finally:
        sock.abort()

class Owner:
    def __init__(self, service, server, key):
        self.service = service
        self.server = server
        self.key = key
        self.clients = set()
        server.newConnection.connect(self.accept)

    def accept(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            if len(self.clients) >= 8:
                sock.abort()
                sock.deleteLater()
                continue
            self.clients.add(sock)
            sock.setReadBufferSize(MAX_FRAME+1)
            sock.disconnected.connect(lambda peer=sock: self.release(peer))
            sock.readyRead.connect(lambda peer=sock: self.read(peer))
            QTimer.singleShot(DEADLINE_MS, sock, sock.abort)
            self.read(sock)

    def release(self, sock):
        self.clients.discard(sock)
        sock.deleteLater()

    def read(self, sock):
        if sock not in self.clients:
            return
        if sock.bytesAvailable() > MAX_FRAME:
            sock.abort()
            return
        if not sock.canReadLine():
            return
        # Consume exactly one request on this connection.
        self.clients.discard(sock)
        try:
            raw = bytes(sock.readLine(MAX_FRAME+1))
            if not raw.endswith(b"\n") or sock.bytesAvailable():
                raise ValueError
            envelope = json.loads(raw)
            body = envelope["body"]
            if not isinstance(body["request_id"], str):
                raise ValueError
            rid = str(UUID(body["request_id"]))
            if not hmac.compare_digest(mac(self.key, b"request:", body), envelope["mac"]):
                raise ProbeError("authentication_failed", "Invalid pairing proof.")
            if body["instance"] != self.service.instance:
                raise ProbeError("instance_mismatch", "Wrong target instance.")
            if not isinstance(body["operation"], str) or not isinstance(body["arguments"], dict):
                raise ProbeError("invalid_arguments", "Malformed operation or arguments.")
            data = self.service.dispatch(body["operation"], body["arguments"], rid)
            outcome = {"schema_version": 1, "ok": True, "data": data}
        except ProbeError as error:
            outcome = failure(error)
        except (ValueError, KeyError, TypeError, RecursionError):
            sock.abort()
            return
        except (sqlite3.Error, OSError):
            outcome = failure(ProbeError("storage_error", "The synthetic receipt could not be accessed.", retryable=True))
        reply = dict(request_id=rid, instance=self.service.instance, outcome=outcome)
        sock.write(canonical({"body": reply, "mac": mac(self.key, b"reply:", reply)}) + b"\n")
        sock.disconnectFromServer()

def run_owner(directory: Path, instance: str, minutes: int):
    app = QCoreApplication.instance() or QCoreApplication([])
    if not directory.exists():
        paths.root_path(directory.parent)
        directory.mkdir()
    paths.root_path(directory)
    allowed = {PAIR_FILE, "synthetic-jobs.sqlite3", "synthetic-jobs.sqlite3-journal", "owner.lock"}
    # The operator explicitly chooses a dedicated profile. Never open a PCS user database.
    if any(p.name not in allowed or not p.is_file() or paths.is_link(p) or p.stat().st_nlink != 1
           for p in directory.iterdir()):
        raise ValueError("Use an empty directory or an existing A01 profile.")
    lock = QLockFile(str(directory / "owner.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise ValueError("This probe profile is already in use.")
    with ExitStack() as stack:
        stack.callback(lock.unlock)
        service = ProbeService(directory, instance)
        stack.callback(service.db.close)
        server = QLocalServer()
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        server.setMaxPendingConnections(8)
        boot, key = uuid4().hex, secrets.token_bytes(32)
        owner = Owner(service, server, key)
        if not server.listen(paired_pipe(instance, boot)):
            raise OSError("Could not open the same-user PCS probe pipe.")
        stack.callback(server.close)
        make_pair(directory, instance, boot, key)
        print(json.dumps({"ready": True, "instance_id": instance, "pid": os.getpid(),
                          "lifetime_minutes": minutes}), flush=True)
        QTimer.singleShot(minutes * 60_000, app.quit)
        return app.exec()
