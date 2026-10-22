"""Qt-free service boundary tests; every provider call is synthetic/offline."""
import builtins
from dataclasses import FrozenInstanceError
import importlib
import io
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from prompt_calculus_studio import civitai
from prompt_calculus_studio.civitai_adapter import CivitAIBrowseAdapter
from prompt_calculus_studio.service_adapters import (
    ServiceError, ServiceFailure, ServiceRequest, ServiceResult,
)


def request(operation="search", *, scope=None, input=None, **kwargs):
    return ServiceRequest("civitai", operation,
                          scope={"page": "synthetic-page", "epoch": 2, "serial": 3}
                          if scope is None else scope,
                          input={} if input is None else input, **kwargs)


class Response(io.BytesIO):
    def __init__(self, payload, *, raw=False, cancel=None):
        super().__init__(payload if raw else json.dumps(payload).encode("utf-8"))
        self.cancel = cancel
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        value = super().read(size)
        if self.cancel is not None:
            self.cancel.set()
        return value


class ServiceBoundaryTests(unittest.TestCase):
    def test_request_freezes_nested_inputs_and_keeps_unicode_exact(self):
        source = {"query": "e\u0301 中文", "types": ("LORA",), "nested": [{"x": 1}]}
        scope = {"owner": ["A"], "epoch": 1}
        frozen = request(scope=scope, input=source)
        source["nested"][0]["x"] = 9
        scope["owner"].append("B")
        frozen.input["nested"][0]["x"] = 10
        frozen.scope["owner"].append("C")
        self.assertEqual(frozen.input, {"query": "e\u0301 中文", "types": ["LORA"], "nested": [{"x": 1}]})
        self.assertEqual(frozen.scope, {"owner": ["A"], "epoch": 1})
        with self.assertRaises(FrozenInstanceError):
            frozen.operation = "version"
        self.assertEqual(str(UUID(frozen.request_id)), frozen.request_id)
        self.assertNotEqual(frozen.request_id, request().request_id)

    def test_request_rejects_non_json_lossy_keys_and_cycles(self):
        cycle = []; cycle.append(cycle)
        for value in ({1: "number key"}, {"x": {1, 2}}, {"x": float("nan")},
                      {"x": float("inf")}, {"x": object()}, cycle):
            with self.subTest(type=type(value).__name__), self.assertRaises((TypeError, ValueError)):
                request(input=value)
        with self.assertRaises(ValueError):
            request(request_id="not-a-uuid")

    def test_result_freezes_value_and_returns_independent_nested_outputs(self):
        expected = request()
        source = {"items": [{"id": 1, "tags": ["original"]}], "metadata": {}}
        result = ServiceResult.success(expected, source)
        source["items"][0]["tags"].append("caller edit")
        first = result.value_for(expected)
        first["items"][0]["tags"].append("UI edit")
        self.assertEqual(result.value_for(expected)["items"][0]["tags"], ["original"])
        self.assertIs(result.request, expected)
        with self.assertRaises(FrozenInstanceError):
            result.request = request()
        self.assertIsNone(ServiceResult.success(expected, None).value_for(expected))

    def test_complete_request_correlation_rejects_all_mismatches(self):
        expected = request(input={"query": "first"})
        result = ServiceResult.success(expected, {"items": []})
        matching = ServiceRequest(expected.service, expected.operation,
                                  scope=expected.scope, input=expected.input,
                                  request_id=expected.request_id)
        self.assertEqual(result.value_for(matching), {"items": []})
        changed = [request(input=expected.input),
                   ServiceRequest("other", "search", scope=expected.scope,
                                  input=expected.input, request_id=expected.request_id),
                   ServiceRequest("civitai", "version", scope=expected.scope,
                                  input=expected.input, request_id=expected.request_id),
                   request(scope={"page": "other"}, input=expected.input,
                           request_id=expected.request_id),
                   request(input={"query": "second"}, request_id=expected.request_id),
                   None]
        for other in changed:
            with self.subTest(other=other), self.assertRaises(ServiceFailure) as caught:
                result.value_for(other)
            self.assertEqual(caught.exception.error.kind, "correlation")

    def test_error_unwrap_preserves_safe_metadata_and_string(self):
        expected = request()
        error = ServiceError("http", "CivitAI 暫時限制查詢頻率，請稍後再試。", 429)
        result = ServiceResult.failure(expected, error)
        with self.assertRaises(ServiceFailure) as caught:
            result.value_for(expected)
        self.assertIs(caught.exception.error, error)
        self.assertEqual(str(caught.exception), error.message)
        self.assertEqual(caught.exception.error.status, 429)
        with self.assertRaises(ValueError):
            ServiceResult(expected)
        with self.assertRaises(ValueError):
            ServiceResult(expected, value=None, error=error)

    def test_both_modules_import_when_qt_imports_are_blocked(self):
        # Load in an isolated package so already-imported modules cannot hide a
        # transitive Qt dependency. This starts neither a process nor an app.
        name = "_service_adapter_qt_free_test"
        package = ModuleType(name)
        package.__path__ = [str(Path(civitai.__file__).parent)]
        original = builtins.__import__

        def blocked(module, *args, **kwargs):
            if module.split(".")[0] in {"PySide6", "PyQt5", "PyQt6"}:
                raise AssertionError("Qt imported by service boundary")
            return original(module, *args, **kwargs)

        with patch.dict(sys.modules, {name: package}), patch("builtins.__import__", blocked):
            loaded = importlib.import_module(name + ".civitai_adapter")
            self.assertTrue(callable(loaded.CivitAIBrowseAdapter))


class CivitAIAdapterTests(unittest.TestCase):
    def test_three_operations_delegate_once_with_frozen_arguments(self):
        original = {"query": "first", "types": ("LORA",), "limit": 30}
        cases = [("connection", {}, "test_connection", (), {}),
                 ("search", original, "search_models", (),
                  {"query": "first", "types": ["LORA"], "limit": 30}),
                 ("version", {"version_id": 22}, "version", (22,), {})]
        for operation, args, method, positional, keywords in cases:
            with self.subTest(operation=operation):
                frozen = request(operation, input=args)
                client = Mock()
                getattr(client, method).return_value = {"raw": {"keep": [1]}}
                factory = Mock(return_value=client)
                cancel = threading.Event()
                result = CivitAIBrowseAdapter(factory).execute(frozen, cancel)
                factory.assert_called_once_with(cancel)
                getattr(client, method).assert_called_once_with(*positional, **keywords)
                self.assertEqual(len(client.mock_calls), 1)
                self.assertEqual(result.value_for(frozen), {"raw": {"keep": [1]}})

    def test_unsupported_operations_and_bad_shapes_never_create_a_client(self):
        factory = Mock()
        adapter = CivitAIBrowseAdapter(factory)
        invalid = [ServiceRequest("other", "search"), request("download"),
                   request("connection", input={"token": "synthetic-only"}),
                   request("search", input={"unknown": 1}),
                   request("search", input=["bad"]), request("version")]
        for frozen in invalid:
            result = adapter.execute(frozen)
            with self.assertRaises(ServiceFailure):
                result.value_for(frozen)
        factory.assert_not_called()

    def test_pre_cancel_skips_factory_and_factory_cancel_skips_client(self):
        frozen = request()
        cancel = threading.Event(); cancel.set()
        factory = Mock()
        result = CivitAIBrowseAdapter(factory).execute(frozen, cancel)
        factory.assert_not_called()
        self.assertEqual(result.error.kind, "cancelled")
        cancel.clear()
        client = Mock()

        def make(event):
            event.set()
            return client

        result = CivitAIBrowseAdapter(make).execute(frozen, cancel)
        self.assertEqual(result.error.kind, "cancelled")
        self.assertEqual(client.mock_calls, [])

    def test_late_cancel_discards_returned_value(self):
        frozen = request()
        cancel = threading.Event()

        def search_models(**_):
            cancel.set()
            return {"items": [{"name": "late result"}]}

        adapter = CivitAIBrowseAdapter(lambda event: SimpleNamespace(search_models=search_models))
        result = adapter.execute(frozen, cancel)
        with self.assertRaises(ServiceFailure) as caught:
            result.value_for(frozen)
        self.assertEqual(caught.exception.error.kind, "cancelled")
        self.assertEqual(str(caught.exception), "已取消 CivitAI 連線。")

    def test_factory_and_unexpected_client_errors_do_not_expose_secret(self):
        secret = "synthetic-secret-must-not-escape"
        frozen = request("connection")
        for factory in (Mock(side_effect=RuntimeError(secret)),
                        Mock(side_effect=ValueError(secret)),
                        Mock(return_value=SimpleNamespace(test_connection=Mock(side_effect=RuntimeError(secret))))):
            with self.subTest(factory_type=type(factory).__name__):
                adapter = CivitAIBrowseAdapter(factory)
                result = adapter.execute(frozen)
                self.assertEqual(result.error.kind, "unexpected")
                self.assertNotIn(secret, repr(adapter) + repr(frozen) + repr(result))
                with self.assertRaises(ServiceFailure) as caught:
                    result.value_for(frozen)
                self.assertNotIn(secret, str(caught.exception))

    def test_non_json_provider_value_is_a_response_error(self):
        frozen = request("connection")
        for value in ({"bad": object()}, {"bad": float("nan")}, {1: "bad key"}):
            adapter = CivitAIBrowseAdapter(lambda event: SimpleNamespace(test_connection=lambda: value))
            result = adapter.execute(frozen)
            self.assertEqual(result.error.kind, "response")


class CivitAIRealClientTests(unittest.TestCase):
    def adapter(self, token="synthetic-token", **kwargs):
        return CivitAIBrowseAdapter(lambda cancel: civitai.CivitAIClient(token, cancel=cancel, **kwargs))

    def test_search_keeps_real_query_headers_limits_and_raw_response(self):
        value = {"items": [{"id": 11, "modelVersions": [{"id": 22, "files": []}]}],
                 "metadata": {"nextCursor": "cursor-2"}, "extra": {"kept": True}}
        response = Response(value)
        frozen = request(input={"query": " hero ", "types": ("LORA",),
                                "base_models": ("Illustrious",), "limit": 5,
                                "cursor": "cursor-1", "nsfw": False})
        with patch.object(civitai, "_open_api", return_value=response) as opened:
            result = self.adapter().execute(frozen)
        self.assertEqual(result.value_for(frozen), value)
        self.assertTrue(response.closed)
        self.assertEqual(response.read_sizes, [32 * 1024 * 1024 + 1])
        opened.assert_called_once()
        wire = opened.call_args.args[0]
        query = parse_qs(urlsplit(wire.full_url).query)
        self.assertEqual(query["query"], ["hero"])
        self.assertEqual(query["types"], ["LORA"])
        self.assertEqual(query["baseModels"], ["Illustrious"])
        self.assertEqual(query["cursor"], ["cursor-1"])
        self.assertEqual(query["nsfw"], ["false"])
        self.assertEqual(wire.get_header("Authorization"), "Bearer synthetic-token")
        self.assertNotIn("synthetic-token", repr(result) + repr(frozen))

    def test_connection_preserves_authenticated_and_public_modes(self):
        for token in ("", "synthetic-token"):
            with self.subTest(authenticated=bool(token)):
                response = Response({"username": "tester"} if token else {"items": []})
                frozen = request("connection")
                with patch.object(civitai, "_open_api", return_value=response) as opened:
                    value = self.adapter(token).execute(frozen).value_for(frozen)
                self.assertEqual(value, {"connected": True, "authenticated": bool(token),
                                         "username": "tester" if token else ""})
                wire = opened.call_args.args[0]
                self.assertEqual(urlsplit(wire.full_url).path, "/api/v1/me" if token else "/api/v1/models")
                self.assertEqual(wire.get_header("Authorization"), "Bearer " + token if token else None)
                self.assertTrue(response.closed)

    def test_version_keeps_raw_fields_for_ui_normalization(self):
        value = {"id": 22, "modelId": 11, "files": [{"id": 33}], "images": [], "extra": [1]}
        response = Response(value)
        frozen = request("version", input={"version_id": 22})
        with patch.object(civitai, "_open_api", return_value=response) as opened:
            result = self.adapter().execute(frozen)
        self.assertEqual(result.value_for(frozen), value)
        self.assertTrue(opened.call_args.args[0].full_url.endswith("/model-versions/22"))
        self.assertTrue(response.closed)

    def test_existing_input_errors_reach_worker_without_network(self):
        cases = [(request("version", input={"version_id": bad}), "CivitAI 版本 ID 無效。")
                 for bad in (True, 0, -1, "22")]
        cases += [(request(input={"limit": bad}), "CivitAI 每頁數量必須介於 1–100。")
                  for bad in (0, 101)]
        with patch.object(civitai, "_open_api") as opened:
            for frozen, message in cases:
                with self.subTest(operation=frozen.operation), self.assertRaises(ServiceFailure) as caught:
                    self.adapter().execute(frozen).value_for(frozen)
                self.assertEqual(str(caught.exception), message)
                self.assertEqual(caught.exception.error.kind, "input")
            opened.assert_not_called()

    def test_existing_http_and_network_errors_stay_safe_and_do_not_retry(self):
        secret = "synthetic-transport-secret"
        cases = [(HTTPError("https://example.invalid/" + secret, 401, secret, {}, None),
                  "http", 401, "CivitAI Token 無效或已失效。"),
                 (HTTPError("https://example.invalid/" + secret, 429, secret, {}, None),
                  "http", 429, "CivitAI 暫時限制查詢頻率，請稍後再試。"),
                 (URLError(secret), "network", None, "目前無法連線 CivitAI；本地素材仍可照常使用。")]
        for error, kind, status, message in cases:
            with self.subTest(kind=kind, status=status):
                frozen = request()
                with patch.object(civitai, "_open_api", side_effect=error) as opened:
                    result = self.adapter().execute(frozen)
                opened.assert_called_once()
                self.assertEqual(result.error, ServiceError(kind, message, status))
                with self.assertRaises(ServiceFailure) as caught:
                    result.value_for(frozen)
                self.assertNotIn(secret, str(caught.exception) + repr(result))

    def test_bad_large_and_cancelled_real_responses_are_closed(self):
        cases = [(b"bad json", 1024, None, "response", "CivitAI 回傳了無法讀取的資料。"),
                 (b"x" * 20, 10, None, "response", "CivitAI 回應過大，已停止讀取。"),
                 (b'{"items": []}', 1024, threading.Event(), "cancelled", "已取消 CivitAI 連線。"),
                 (b'{"items": {}}', 1024, None, "response", "CivitAI 搜尋結果格式無效。")]
        for raw, limit, cancel, kind, message in cases:
            with self.subTest(kind=kind, message=message):
                response = Response(raw, raw=True, cancel=cancel)
                frozen = request()
                with patch.object(civitai, "_open_api", return_value=response) as opened:
                    result = self.adapter(max_response=limit).execute(frozen, cancel)
                opened.assert_called_once()
                self.assertTrue(response.closed)
                self.assertEqual(response.read_sizes, [limit + 1])
                self.assertEqual(result.error.kind, kind)
                self.assertEqual(result.error.message, message)


if __name__ == "__main__":
    unittest.main()
