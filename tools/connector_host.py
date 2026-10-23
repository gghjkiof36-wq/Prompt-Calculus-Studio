"""Chrome native-messaging transport for PCS; deliberately has no Qt dependency."""
from __future__ import annotations

import base64
import binascii
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import time
from typing import BinaryIO, Callable


HOST_NAME = "com.promptcalculus.studio"
EXTENSION_ID = "migmpicacfjoipmfcgdjocgdbplgldan"
ALLOWED_ORIGIN = f"chrome-extension://{EXTENSION_ID}/"
MAX_FRAME_BYTES = 512 * 1024
MAX_PIPE_BYTES = 4 * 1024 * 1024
MAX_CAPTURE_BYTES = 16 * 1024 * 1024
MAX_CHUNK_BYTES = 256 * 1024
PIPE_TIMEOUT_SECONDS = 15.0
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_PIPE_NAME = re.compile(r"pcs-connector-[0-9a-f]{24}\Z")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_COMMANDS = frozenset({"hello", "begin", "chunk", "finish", "status", "open", "reveal", "cancel"})


class ConnectorError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ConnectorError("invalid_json", "連線資料格式不正確。") from exc


def _reject_json_constant(value: str):
    raise ValueError(f"Non-JSON number: {value}")


def _read_exact(stream: BinaryIO, length: int, *, eof_ok: bool = False) -> bytes | None:
    result = bytearray()
    while len(result) < length:
        part = stream.read(length - len(result))
        if not part:
            if not result and eof_ok:
                return None
            raise ConnectorError("invalid_frame", "連線資料不完整。")
        result.extend(part)
    return bytes(result)


def read_frame(stream: BinaryIO) -> dict | None:
    header = _read_exact(stream, 4, eof_ok=True)
    if header is None:
        return None
    length = struct.unpack("<I", header)[0]
    if not 0 < length <= MAX_FRAME_BYTES:
        raise ConnectorError("frame_too_large", "傳送內容超過單次大小限制。")
    raw = _read_exact(stream, length)
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ConnectorError("invalid_frame", "無法讀取傳送內容。") from exc
    if not isinstance(value, dict):
        raise ConnectorError("invalid_request", "傳送內容格式不正確。")
    return value


def write_frame(stream: BinaryIO, value: dict) -> None:
    raw = _json_bytes(value)
    if len(raw) > MAX_FRAME_BYTES:
        raise ConnectorError("response_too_large", "PCS 回應超過大小限制。")
    stream.write(struct.pack("<I", len(raw)))
    stream.write(raw)
    stream.flush()


def discovery_directory() -> Path:
    override = os.environ.get("PCS_CONNECTOR_DISCOVERY")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise ConnectorError("configuration_unavailable", "找不到 PCS 連線設定。")
    return Path(local) / "PromptCalculusStudio" / "Connector" / "instances"


def discover_instances(directory: Path | None = None) -> list[dict]:
    directory = directory if directory is not None else discovery_directory()
    result: list[dict] = []
    try:
        candidates = sorted(directory.glob("*.json"))[:32]
    except OSError:
        return result
    seen: set[str] = set()
    for path in candidates:
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or path.is_symlink():
                continue
            if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                continue
            if info.st_size > 16 * 1024:
                continue
            with path.open("rb") as stream:
                raw = stream.read(16 * 1024 + 1)
            if len(raw) > 16 * 1024:
                continue
            item = json.loads(raw.decode("utf-8"))
            if not isinstance(item, dict) or type(item.get("version")) is not int or item["version"] != 1:
                continue
            identity, pipe, token, label = (item.get(key) for key in ("instance_id", "pipe_name", "token", "display_name"))
            if not isinstance(identity, str) or not _IDENTIFIER.fullmatch(identity) or identity in seen:
                continue
            if not isinstance(pipe, str) or not _PIPE_NAME.fullmatch(pipe):
                continue
            if not isinstance(token, str) or not 32 <= len(token) <= 256 or not token.isascii():
                continue
            if not isinstance(label, str) or not 1 <= len(label) <= 200:
                continue
            result.append({"instance_id": identity, "pipe_name": pipe, "token": token, "display_name": label})
            seen.add(identity)
        except (OSError, ValueError, UnicodeError, RecursionError):
            continue
    return result


def pipe_request(pipe_name: str, request: dict, timeout: float = PIPE_TIMEOUT_SECONDS) -> dict:
    """Exchange one LF-delimited JSON document with Qt's byte-mode named pipe.

    Overlapped Win32 I/O gives reads and writes the same bounded deadline. The
    SECURITY_IDENTIFICATION flag prevents the server impersonating this host.
    """
    if os.name != "nt":
        raise ConnectorError("platform_unavailable", "此連線工具需要 Windows。")
    if not _PIPE_NAME.fullmatch(pipe_name):
        raise ConnectorError("invalid_instance", "PCS 連線設定不正確。")
    raw = _json_bytes(request) + b"\n"
    if len(raw) > MAX_PIPE_BYTES:
        raise ConnectorError("request_too_large", "傳送內容超過大小限制。")

    class Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(Overlapped)]
    kernel.WriteFile.argtypes = kernel.ReadFile.argtypes
    kernel.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(Overlapped),
                                         ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    kernel.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(Overlapped)]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    deadline = time.monotonic() + max(0.01, timeout)
    name = "\\\\.\\pipe\\" + pipe_name
    invalid_handle = ctypes.c_void_p(-1).value

    def remaining_ms() -> int:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConnectorError("timeout", "PCS 暫時沒有回應，請稍後查看傳送狀態。")
        return max(1, int(remaining * 1000))

    while True:
        remaining_ms()
        handle = kernel.CreateFileW(name, 0xC0000000, 0, None, 3, 0x40000000 | 0x00110000, None)
        if handle != invalid_handle:
            break
        error = ctypes.get_last_error()
        if error != 231:  # ERROR_PIPE_BUSY
            raise ConnectorError("instance_unavailable", "無法連上 PCS，請開啟 PCS 後再試。")
        if not kernel.WaitNamedPipeW(name, remaining_ms()):
            if ctypes.get_last_error() == 121:
                raise ConnectorError("timeout", "PCS 暫時沒有回應，請稍後查看傳送狀態。")
            raise ConnectorError("instance_unavailable", "無法連上 PCS，請開啟 PCS 後再試。")

    def transfer(buffer: ctypes.Array, length: int, *, writing: bool) -> int:
        event = kernel.CreateEventW(None, True, False, None)
        if not event:
            raise ConnectorError("instance_unavailable", "無法建立 PCS 連線。")
        overlapped = Overlapped(hEvent=event)
        transferred = wintypes.DWORD()
        pending = False
        try:
            operation = kernel.WriteFile if writing else kernel.ReadFile
            ok = operation(handle, buffer, length, ctypes.byref(transferred), ctypes.byref(overlapped))
            if not ok:
                if ctypes.get_last_error() != 997:  # ERROR_IO_PENDING
                    raise ConnectorError("instance_unavailable", "PCS 連線已中斷。")
                pending = True
                wait_result = kernel.WaitForSingleObject(event, remaining_ms())
                if wait_result == 258:
                    raise ConnectorError("timeout", "PCS 暫時沒有回應，請稍後查看傳送狀態。")
                if wait_result != 0:
                    raise ConnectorError("instance_unavailable", "PCS 連線已中斷。")
                if not kernel.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(transferred), False):
                    raise ConnectorError("instance_unavailable", "PCS 連線已中斷。")
                pending = False
            return transferred.value
        finally:
            if pending:
                kernel.CancelIoEx(handle, ctypes.byref(overlapped))
                # Keep OVERLAPPED/buffer alive until cancellation has completed.
                kernel.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(transferred), True)
            kernel.CloseHandle(event)

    try:
        sent = 0
        while sent < len(raw):
            remaining_ms()
            piece = raw[sent:]
            count = transfer(ctypes.create_string_buffer(piece), len(piece), writing=True)
            if not count:
                raise ConnectorError("instance_unavailable", "PCS 連線已中斷。")
            sent += count
        received = bytearray()
        while True:
            remaining_ms()
            buffer = ctypes.create_string_buffer(65536)
            count = transfer(buffer, 65536, writing=False)
            if not count:
                raise ConnectorError("instance_unavailable", "PCS 連線已中斷。")
            received.extend(buffer.raw[:count])
            if len(received) > MAX_PIPE_BYTES:
                raise ConnectorError("response_too_large", "PCS 回應超過大小限制。")
            if b"\n" in received:
                line, _, extra = received.partition(b"\n")
                if extra.strip():
                    raise ConnectorError("invalid_response", "無法讀取 PCS 回應。")
                try:
                    result = json.loads(line.decode("utf-8"), parse_constant=_reject_json_constant)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise ConnectorError("invalid_response", "無法讀取 PCS 回應。") from exc
                if not isinstance(result, dict):
                    raise ConnectorError("invalid_response", "無法讀取 PCS 回應。")
                return result
    finally:
        kernel.CloseHandle(handle)


def _identifier(value: object) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


def validate_request(request: dict) -> dict:
    if not _identifier(request.get("request_id")):
        raise ConnectorError("invalid_request", "傳送識別碼不正確。")
    command = request.get("command")
    if not isinstance(command, str) or command not in _COMMANDS:
        raise ConnectorError("invalid_command", "不支援這個操作。")
    allowed = {"request_id", "command", "instance_id"}
    if "instance_id" in request and not _identifier(request["instance_id"]):
        raise ConnectorError("invalid_instance", "PCS 識別碼不正確。")
    if command != "hello":
        allowed.add("operation_id")
        if not _identifier(request.get("operation_id")):
            raise ConnectorError("invalid_request", "傳送識別碼不正確。")
    if command == "begin":
        allowed.update({"sha256", "size"})
        digest, size = request.get("sha256"), request.get("size")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ConnectorError("invalid_request", "傳送內容識別碼不正確。")
        if type(size) is not int or size <= 0:
            raise ConnectorError("invalid_request", "傳送內容大小不正確。")
        if size > MAX_CAPTURE_BYTES:
            raise ConnectorError("capture_too_large", "對話大小超過 16 MiB。")
    elif command == "chunk":
        allowed.update({"index", "data"})
        if type(request.get("index")) is not int or not 0 <= request["index"] < 65536:
            raise ConnectorError("invalid_request", "傳送段落不正確。")
        data = request.get("data")
        if not isinstance(data, str) or not 0 < len(data) <= ((MAX_CHUNK_BYTES + 2) // 3) * 4:
            raise ConnectorError("invalid_request", "傳送段落大小不正確。")
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ConnectorError("invalid_request", "傳送段落格式不正確。") from exc
        if not 0 < len(decoded) <= MAX_CHUNK_BYTES:
            raise ConnectorError("invalid_request", "傳送段落大小不正確。")
    if set(request) - allowed:
        raise ConnectorError("invalid_request", "傳送內容含有不支援的欄位。")
    return dict(request)


def _public_instance(item: dict) -> dict:
    return {key: item[key] for key in ("instance_id", "display_name")}


class ConnectorHost:
    def __init__(self, directory: Path | None = None, exchange: Callable = pipe_request):
        self.directory = directory
        self.exchange = exchange

    def _live_instances(self, instances: list[dict], request_id: str) -> list[dict]:
        live = []
        for item in instances:
            try:
                probe = self.exchange(item["pipe_name"], {"request_id": request_id, "command": "hello", "token": item["token"]}, 0.3)
                details = probe.get("result")
                if (probe.get("request_id") == request_id and probe.get("ok") is True
                        and isinstance(details, dict) and details.get("ready") is True
                        and details.get("instance_id") == item["instance_id"]):
                    live.append(item)
            except (ConnectorError, OSError):
                continue
        return live

    def handle(self, request: dict) -> dict:
        request_id = request.get("request_id") if _identifier(request.get("request_id")) else None
        try:
            validated = validate_request(request)
            instances = discover_instances(self.directory)
            if validated["command"] == "hello":
                live = self._live_instances(instances, request_id)
                return {"request_id": request_id, "ok": True, "result": {"version": 1, "instances": [_public_instance(item) for item in live], "max_capture_bytes": MAX_CAPTURE_BYTES}}
            selected = validated.pop("instance_id", None)
            if selected is None:
                instances = self._live_instances(instances, request_id)
                if not instances:
                    raise ConnectorError("instance_unavailable", "請先開啟 PCS。")
                if len(instances) > 1:
                    raise ConnectorError("instance_required", "請選擇要接收對話的 PCS。")
                item = instances[0]
            else:
                item = next((entry for entry in instances if entry["instance_id"] == selected), None)
                if item is None:
                    raise ConnectorError("instance_unavailable", "請先開啟選取的 PCS。")
            validated["token"] = item["token"]
            response = self.exchange(item["pipe_name"], validated, PIPE_TIMEOUT_SECONDS)
            if response.get("request_id") != request_id or type(response.get("ok")) is not bool:
                raise ConnectorError("invalid_response", "無法讀取 PCS 回應。")
            # Pass only protocol response fields, never server debug/config data.
            if response["ok"]:
                return {"request_id": request_id, "ok": True, "result": response.get("result")}
            error = response.get("error")
            if not isinstance(error, dict) or not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str):
                raise ConnectorError("invalid_response", "無法讀取 PCS 回應。")
            return {"request_id": request_id, "ok": False, "error": {"code": error["code"], "message": error["message"]}}
        except ConnectorError as exc:
            return {"request_id": request_id, "ok": False, "error": {"code": exc.code, "message": exc.message}}
        except (OSError, ValueError, TypeError, RecursionError):
            return {"request_id": request_id, "ok": False, "error": {"code": "connection_error", "message": "無法連上 PCS，請稍後再試。"}}


def serve(input_stream: BinaryIO, output_stream: BinaryIO, host: ConnectorHost | None = None) -> int:
    host = host if host is not None else ConnectorHost()
    while True:
        try:
            request = read_frame(input_stream)
            if request is None:
                return 0
        except ConnectorError as exc:
            write_frame(output_stream, {"request_id": None, "ok": False, "error": {"code": exc.code, "message": exc.message}})
            return 1
        response = host.handle(request)
        try:
            write_frame(output_stream, response)
        except ConnectorError as exc:
            write_frame(output_stream, {"request_id": response["request_id"], "ok": False, "error": {"code": exc.code, "message": exc.message}})


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments or arguments[0] != ALLOWED_ORIGIN:
        return 2
    if os.name == "nt":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    try:
        return serve(sys.stdin.buffer, sys.stdout.buffer)
    except (OSError, BrokenPipeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
