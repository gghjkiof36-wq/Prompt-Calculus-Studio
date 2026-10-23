from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import io
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid

from tools import connector_host as host
from tools import install_chrome_connector as installer
from tools import build_connector as builder


class PartialRead(io.BytesIO):
    def read(self, size=-1):
        return super().read(min(size, 3))


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def config(self, identity="one", pipe=None, **updates):
        item = {"version": 1, "instance_id": identity,
                "pipe_name": pipe or "pcs-connector-" + ("a" if identity == "one" else "b") * 24,
                "token": "secret_" + "a" * 40, "display_name": f"工作區 {identity}", "updated_at": "2026-10-23T12:00:00Z"}
        item.update(updates)
        (self.directory / f"{identity}.json").write_text(json.dumps(item), encoding="utf-8")
        return item

    def exchange(self, pipe, request, timeout):
        identity = "one" if pipe.endswith("a" * 24) else "two"
        return {"request_id": request["request_id"], "ok": True,
                "result": {"instance_id": identity, "ready": True, "operation_id": request.get("operation_id")}}

    def test_utf8_native_framing_and_partial_reads(self):
        value = {"request_id": "r1", "內容": "京都旅行 🌸"}
        output = io.BytesIO()
        host.write_frame(output, value)
        wire = output.getvalue()
        self.assertEqual(struct.unpack("<I", wire[:4])[0], len(wire[4:]))
        self.assertEqual(host.read_frame(PartialRead(wire)), value)
        self.assertIsNone(host.read_frame(io.BytesIO()))

    def test_invalid_truncated_or_oversized_frames_are_bounded(self):
        for payload in (b"\x01", struct.pack("<I", host.MAX_FRAME_BYTES + 1),
                        struct.pack("<I", 10) + b"{}", struct.pack("<I", 1) + b"\xff",
                        struct.pack("<I", 2) + b"[]"):
            with self.subTest(payload=payload[:10]), self.assertRaises(host.ConnectorError):
                host.read_frame(io.BytesIO(payload))

    def test_invalid_unicode_and_non_json_numbers_do_not_pollute_stdout(self):
        for result in ({"text": "\ud800"}, {"value": float("nan")}):
            output = io.BytesIO()
            with self.assertRaises(host.ConnectorError):
                host.write_frame(output, result)
            self.assertEqual(output.getvalue(), b"")
        raw = b'{"value":NaN}'
        with self.assertRaises(host.ConnectorError):
            host.read_frame(io.BytesIO(struct.pack("<I", len(raw)) + raw))

    def test_discovery_ignores_invalid_or_redirected_config(self):
        self.config()
        self.config("two", pipe="\\\\.\\pipe\\anything")
        self.config("three", version=True)
        self.config("four", token="short")
        (self.directory / "broken.json").write_bytes(b"invalid")
        (self.directory / "huge.json").write_bytes(b" " * (16 * 1024 + 1))
        self.assertEqual([item["instance_id"] for item in host.discover_instances(self.directory)], ["one"])

    def test_hello_hides_token_and_pipe_and_filters_closed_instances(self):
        self.config()
        self.config("two")
        def exchange(pipe, request, timeout):
            if pipe.endswith("b" * 24):
                raise host.ConnectorError("instance_unavailable", "closed")
            return self.exchange(pipe, request, timeout)
        result = host.ConnectorHost(self.directory, exchange).handle({"request_id": "r1", "command": "hello"})
        self.assertEqual(result["result"]["instances"], [{"instance_id": "one", "display_name": "工作區 one"}])
        self.assertNotIn("token", json.dumps(result))
        self.assertNotIn("pcs-connector-", json.dumps(result))

    def test_empty_hello_is_success_and_no_command_auto_replay(self):
        exchange = mock.Mock(side_effect=host.ConnectorError("timeout", "timeout"))
        handler = host.ConnectorHost(self.directory, exchange)
        self.assertEqual(handler.handle({"request_id": "r1", "command": "hello"})["result"]["instances"], [])
        self.config()
        response = handler.handle({"request_id": "r2", "command": "finish", "operation_id": "op", "instance_id": "one"})
        self.assertEqual(response["error"]["code"], "timeout")
        self.assertEqual(exchange.call_count, 1)

    def test_destination_capability_comes_from_live_service_and_is_allowlisted(self):
        self.config(capabilities=['save_destination_v1'])
        request = {'request_id': 'hello1', 'command': 'hello'}
        legacy = host.ConnectorHost(self.directory, self.exchange).handle(request)
        self.assertNotIn('capabilities', legacy['result']['instances'][0])
        def exchange(pipe, value, timeout):
            result = self.exchange(pipe, value, timeout)
            result['result']['capabilities'] = ['save_destination_v1', 'untrusted-debug-capability']
            return result
        result = host.ConnectorHost(self.directory, exchange).handle(request)
        self.assertEqual(result['result']['instances'][0]['capabilities'], ['save_destination_v1'])

    def test_destinations_and_begin_only_allow_opaque_ids(self):
        self.config()
        query = dict(request_id='folders', command='destinations', instance_id='one')
        identity = 'a' * 32
        self.assertEqual(host.validate_request(query), query)
        host.validate_request(dict(query, folder_id=identity))
        begin = dict(request_id='begin', command='begin', operation_id='op',
                     instance_id='one', size=1, sha256='b' * 64, destination_id=identity)
        host.validate_request(begin)
        for value in (dict(query, folder_id='C:/private'), dict(query, path='C:/private'),
                      dict(query, folder_id=None), dict(query, operation_id='op'),
                      dict(begin, destination_id='../../escape'), dict(begin, destination_id=None),
                      {k: v for k, v in query.items() if k != 'instance_id'}):
            with self.subTest(value=value), self.assertRaises(host.ConnectorError):
                host.validate_request(value)
        exchange = mock.Mock(side_effect=self.exchange)
        self.assertTrue(host.ConnectorHost(self.directory, exchange).handle(dict(query, folder_id=identity))['ok'])
        self.assertEqual(exchange.call_args.args[1]['folder_id'], identity)
        self.assertNotIn('operation_id', exchange.call_args.args[1])

    def test_single_auto_route_and_explicit_multi_instance_route(self):
        config = self.config()
        exchange = mock.Mock(side_effect=self.exchange)
        handler = host.ConnectorHost(self.directory, exchange)
        request = {"request_id": "r1", "command": "status", "operation_id": "op"}
        self.assertTrue(handler.handle(request)["ok"])
        sent = exchange.call_args.args[1]
        self.assertEqual(sent["token"], config["token"])
        self.assertNotIn("instance_id", sent)
        self.config("two")
        self.assertEqual(handler.handle(request)["error"]["code"], "instance_required")
        result = handler.handle({**request, "instance_id": "two"})
        self.assertTrue(result["ok"])
        self.assertTrue(exchange.call_args.args[0].endswith("b" * 24))

    def test_stale_second_instance_does_not_prevent_auto_route(self):
        self.config()
        self.config("two")
        def exchange(pipe, request, timeout):
            if pipe.endswith("b" * 24):
                raise host.ConnectorError("instance_unavailable", "closed")
            return self.exchange(pipe, request, timeout)
        result = host.ConnectorHost(self.directory, exchange).handle({"request_id": "r1", "command": "status", "operation_id": "op"})
        self.assertTrue(result["ok"])

    def test_schema_rejects_paths_tokens_bad_hash_and_oversized_chunks(self):
        base = {"request_id": "r", "command": "begin", "operation_id": "op", "sha256": "a" * 64, "size": 10}
        for request in ({**base, "token": "injected"}, {**base, "path": "C:/private"},
                        {**base, "sha256": "oops"}, {**base, "size": True}, {**base, "size": host.MAX_CAPTURE_BYTES + 1},
                        {"request_id": "r", "command": "open", "operation_id": "../../private"},
                        {"request_id": "r", "command": "chunk", "operation_id": "op", "index": 0, "data": "!"},
                        {"request_id": "r", "command": "chunk", "operation_id": "op", "index": -1, "data": "YQ=="},
                        {"request_id": "r", "command": "chunk", "operation_id": "op", "index": 0,
                         "data": base64.b64encode(b"a" * (host.MAX_CHUNK_BYTES + 1)).decode()}):
            with self.subTest(command=request["command"]), self.assertRaises(host.ConnectorError):
                host.validate_request(request)
        host.validate_request(base)
        host.validate_request({"request_id": "r", "command": "cancel", "operation_id": "op"})
        host.validate_request({"request_id": "r", "command": "chunk", "operation_id": "op", "index": 0,
                               "data": base64.b64encode(b"a" * host.MAX_CHUNK_BYTES).decode()})

    def test_origin_is_exact_and_checked_before_stdio(self):
        for arguments in ([], [host.ALLOWED_ORIGIN.rstrip("/")], [host.ALLOWED_ORIGIN + "extra"],
                          ["chrome-extension://" + "a" * 32 + "/"]):
            self.assertEqual(host.main(arguments), 2)

    def test_persistent_stream_and_too_large_response(self):
        incoming = io.BytesIO()
        for identity in ("one", "two"):
            host.write_frame(incoming, {"request_id": identity, "command": "hello"})
        incoming.seek(0)
        fake = mock.Mock()
        fake.handle.side_effect = [{"request_id": "one", "ok": True, "result": "a" * host.MAX_FRAME_BYTES},
                                   {"request_id": "two", "ok": True, "result": {}}]
        output = io.BytesIO()
        self.assertEqual(host.serve(incoming, output, fake), 0)
        output.seek(0)
        self.assertEqual(host.read_frame(output)["error"]["code"], "response_too_large")
        self.assertEqual(host.read_frame(output)["request_id"], "two")
        self.assertIsNone(host.read_frame(output))

    def test_server_response_requires_matching_request_and_typed_error(self):
        self.config()
        request = {"request_id": "r", "command": "status", "operation_id": "op", "instance_id": "one"}
        for response in ({"request_id": "other", "ok": True}, {"request_id": "r", "ok": 1},
                         {"request_id": "r", "ok": False, "error": "oops"}):
            result = host.ConnectorHost(self.directory, lambda *args: response).handle(request)
            self.assertEqual(result["error"]["code"], "invalid_response")


@unittest.skipUnless(os.name == "nt", "Windows named-pipe transport")
class WindowsPipeTests(unittest.TestCase):
    def run_server(self, reply: bytes, *, delay: float = 0):
        name = "pcs-connector-" + uuid.uuid4().hex[:24]
        ready = threading.Event()
        received = []
        failures = []
        def server():
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                                wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
            kernel.CreateNamedPipeW.restype = wintypes.HANDLE
            kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
            kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            kernel.WriteFile.argtypes = kernel.ReadFile.argtypes
            kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
            kernel.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.CreateNamedPipeW("\\\\.\\pipe\\" + name, 3, 0, 1, 65536, 65536, 1000, None)
            try:
                if handle == ctypes.c_void_p(-1).value:
                    raise OSError(ctypes.get_last_error(), "CreateNamedPipeW")
                ready.set()
                connected = kernel.ConnectNamedPipe(handle, None)
                if not connected and ctypes.get_last_error() != 535:
                    raise OSError(ctypes.get_last_error(), "ConnectNamedPipe")
                data = bytearray()
                while b"\n" not in data:
                    buffer = ctypes.create_string_buffer(4096)
                    count = wintypes.DWORD()
                    if not kernel.ReadFile(handle, buffer, 4096, ctypes.byref(count), None):
                        raise OSError(ctypes.get_last_error(), "ReadFile")
                    data.extend(buffer.raw[:count.value])
                received.append(json.loads(data.decode("utf-8")))
                time.sleep(delay)
                buffer = ctypes.create_string_buffer(reply)
                count = wintypes.DWORD()
                if kernel.WriteFile(handle, buffer, len(reply), ctypes.byref(count), None):
                    kernel.FlushFileBuffers(handle)
            except BaseException as exc:
                failures.append(exc)
            finally:
                ready.set()
                kernel.DisconnectNamedPipe(handle)
                kernel.CloseHandle(handle)
        worker = threading.Thread(target=server, daemon=True)
        worker.start()
        self.assertTrue(ready.wait(2))
        def cleanup():
            worker.join(3)
            self.assertFalse(worker.is_alive(), "synthetic pipe worker did not exit")
            self.assertFalse(failures, failures)
        self.addCleanup(cleanup)
        return name, received

    def test_real_win32_pipe_byte_json_exchange_without_qt(self):
        response = {"request_id": "r", "ok": True, "result": {"title": "京都 🌸"}}
        name, received = self.run_server(json.dumps(response, ensure_ascii=False).encode() + b"\n")
        request = {"request_id": "r", "command": "hello", "token": "secret", "text": "你好"}
        self.assertEqual(host.pipe_request(name, request, 2), response)
        self.assertEqual(received, [request])

    def test_pipe_read_timeout_cancels_pending_io(self):
        name, _ = self.run_server(b'{"request_id":"r","ok":true}\n', delay=0.3)
        start = time.monotonic()
        with self.assertRaises(host.ConnectorError) as captured:
            host.pipe_request(name, {"request_id": "r"}, 0.06)
        self.assertEqual(captured.exception.code, "timeout")
        self.assertLess(time.monotonic() - start, 1)

    def test_missing_pipe_and_invalid_response(self):
        with self.assertRaises(host.ConnectorError) as captured:
            host.pipe_request("pcs-connector-" + uuid.uuid4().hex[:24], {}, 0.1)
        self.assertEqual(captured.exception.code, "instance_unavailable")
        name, _ = self.run_server(b"invalid\n")
        with self.assertRaises(host.ConnectorError) as captured:
            host.pipe_request(name, {}, 1)
        self.assertEqual(captured.exception.code, "invalid_response")


class FakeRegistry:
    HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_SZ = 1, 2, 4, 1
    KEY_WOW64_32KEY = 0x200
    def __init__(self, previous=("old-manifest", 1), fail_once=False):
        self.value = previous
        self.fail_once = fail_once
        self.deleted = False
    def OpenKey(self, *args):
        return self
    def CreateKeyEx(self, *args):
        return self
    def QueryValueEx(self, *args):
        if self.value is None:
            raise FileNotFoundError()
        return self.value
    def SetValueEx(self, key, name, reserved, kind, value):
        self.value = (value, kind)
        if self.fail_once:
            self.fail_once = False
            raise PermissionError("synthetic registry failure")
    def DeleteValue(self, *args):
        self.value = None
    def DeleteKeyEx(self, *args, **kwargs):
        self.deleted = True
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


@unittest.skipUnless(os.name == "nt", "Windows installer")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "pcs-connector-host.exe"
        self.source.write_bytes(b"MZsynthetic-host")
        self.destination = self.directory / "installed"

    def test_install_uses_fixed_origin_and_immutable_runtime_and_preserves_old(self):
        registry = FakeRegistry()
        with mock.patch.dict(sys.modules, {"winreg": registry}):
            manifest = installer.install(self.source, host.EXTENSION_ID, self.destination)
            document = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(document["allowed_origins"], [host.ALLOWED_ORIGIN])
            deployed = Path(document["path"])
            self.assertEqual(deployed.read_bytes(), self.source.read_bytes())
            self.assertNotEqual(deployed, self.source)
            self.assertEqual(registry.value, (str(manifest), registry.REG_SZ))
            self.assertEqual(installer.install(self.source, host.EXTENSION_ID, self.destination), manifest)
            self.source.write_bytes(b"MZnew-version")
            changed = installer.install(self.source, host.EXTENSION_ID, self.destination)
            self.assertNotEqual(changed, manifest)
            self.assertTrue(manifest.exists())
            self.assertEqual(deployed.read_bytes(), b"MZsynthetic-host")

    def test_invalid_id_or_corrupt_executable_never_registers(self):
        with mock.patch.object(installer, "_register") as register:
            with self.assertRaises(installer.InstallError):
                installer.install(self.source, "a" * 32, self.destination)
            self.source.write_bytes(b"not executable")
            with self.assertRaises(installer.InstallError):
                installer.install(self.source, host.EXTENSION_ID, self.destination)
            register.assert_not_called()

    def test_registration_failure_restores_previous_value(self):
        registry = FakeRegistry(fail_once=True)
        with mock.patch.dict(sys.modules, {"winreg": registry}):
            with self.assertRaises(installer.InstallError):
                installer.install(self.source, host.EXTENSION_ID, self.destination)
        self.assertEqual(registry.value, ("old-manifest", 1))

    def test_corrupt_existing_deployment_is_not_overwritten(self):
        with mock.patch.object(installer, "_register") as register:
            manifest = installer.install(self.source, host.EXTENSION_ID, self.destination)
            deployed = Path(json.loads(manifest.read_text(encoding="utf-8"))["path"])
            deployed.write_bytes(b"damaged")
            register.reset_mock()
            with self.assertRaises(installer.InstallError):
                installer.install(self.source, host.EXTENSION_ID, self.destination)
            self.assertEqual(deployed.read_bytes(), b"damaged")
            register.assert_not_called()

    def test_redirected_destination_is_rejected(self):
        self.destination.mkdir()
        with mock.patch.object(Path, "is_symlink", autospec=True, side_effect=lambda path: path == self.destination), mock.patch.object(installer, "_register") as register:
            with self.assertRaises(installer.InstallError):
                installer.install(self.source, host.EXTENSION_ID, self.destination)
            register.assert_not_called()


class PackageReuseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "source"
        self.package = self.directory / "package"
        self.staging = self.directory / "staging"
        self.package.mkdir()
        self.staging.mkdir()
        self.manifest = {"source_files": {}, "files": {}, "python": "synthetic-python"}
        for binary, source in builder.NATIVE_SOURCES.items():
            path = self.root / source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source.encode())
            self.manifest["source_files"][source] = hashlib.sha256(path.read_bytes()).hexdigest()
            (self.package / binary).write_bytes(b"MZ" + binary.encode())
            self.manifest["files"][binary] = hashlib.sha256((self.package / binary).read_bytes()).hexdigest()
        (self.package / "licenses").mkdir()
        for name in ("Python-LICENSE.txt", "PyInstaller-LICENSE.txt", "OpenSSL-LICENSE.txt"):
            relative = "licenses/" + name
            (self.package / relative).write_bytes(name.encode())
            self.manifest["files"][relative] = hashlib.sha256((self.package / relative).read_bytes()).hexdigest()
        (self.package / "PACKAGE_MANIFEST.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_reuse_preserves_exact_verified_binaries_and_runtime_notices(self):
        with mock.patch.object(builder, "ROOT", self.root):
            identity = builder.reuse_native(self.package, self.staging)
        self.assertEqual(identity["python"], "synthetic-python")
        for relative, digest in self.manifest["files"].items():
            self.assertEqual(hashlib.sha256((self.staging / relative).read_bytes()).hexdigest(), digest)

    def test_source_drift_and_binary_corruption_require_rebuild(self):
        source = self.root / "tools/connector_host.py"
        original = source.read_bytes()
        source.write_bytes(b"changed source")
        with mock.patch.object(builder, "ROOT", self.root), self.assertRaises(ValueError):
            builder.reuse_native(self.package, self.staging)
        self.assertEqual(list(self.staging.iterdir()), [])
        source.write_bytes(original)
        (self.package / "pcs-connector-host.exe").write_bytes(b"corrupted")
        with mock.patch.object(builder, "ROOT", self.root), self.assertRaises(ValueError):
            builder.reuse_native(self.package, self.staging)
        self.assertEqual(list(self.staging.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
