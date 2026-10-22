"""CivitAI browsing adapter; transport and normalization stay in civitai.py."""
from __future__ import annotations

from .civitai import CivitAIError
from .service_adapters import ServiceError, ServiceRequest, ServiceResult


_CANCELLED = ServiceError("cancelled", "已取消 CivitAI 連線。")
_UNEXPECTED = ServiceError("unexpected", "CivitAI 讀取失敗，請稍後再試。")
_BAD_RESPONSE = ServiceError("response", "CivitAI 回傳了無法讀取的資料。")
_SAFE_INPUT_ERRORS = frozenset({
    "CivitAI 版本 ID 無效。",
    "CivitAI 每頁數量必須介於 1–100。",
})
_SEARCH_KEYS = frozenset({
    "query", "types", "base_models", "sort", "period", "limit", "cursor", "nsfw",
})


def _cancelled(cancel):
    return cancel is not None and cancel.is_set()


class CivitAIBrowseAdapter:
    """Create one client per execution using client_factory(cancel).

    The UI captures its frozen token in that factory and can retain its existing
    CivitAIClient mock/injection point. Credentials and the factory never enter
    a request, result or repr. Only the three current read operations are exposed.
    """
    __slots__ = ("_client_factory",)

    def __init__(self, client_factory):
        if not callable(client_factory):
            raise TypeError("CivitAI adapter requires a client factory.")
        self._client_factory = client_factory

    def __repr__(self):
        return "CivitAIBrowseAdapter()"

    def execute(self, request: ServiceRequest, cancel=None) -> ServiceResult:
        if not isinstance(request, ServiceRequest):
            raise TypeError("CivitAI adapter requires a ServiceRequest.")
        if _cancelled(cancel):
            return ServiceResult.failure(request, _CANCELLED)
        if request.service != "civitai" or request.operation not in {
                "connection", "search", "version"}:
            return ServiceResult.failure(request, ServiceError(
                "request", "不支援的 CivitAI 讀取操作。"))
        arguments = request.input
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return ServiceResult.failure(request, ServiceError(
                "input", "CivitAI 查詢參數無效。"))
        if ((request.operation == "connection" and arguments)
                or (request.operation == "search" and arguments.keys() - _SEARCH_KEYS)):
            return ServiceResult.failure(request, ServiceError(
                "input", "CivitAI 查詢參數無效。"))
        if request.operation == "version" and arguments.keys() != {"version_id"}:
            return ServiceResult.failure(request, ServiceError(
                "input", "CivitAI 版本 ID 無效。"))
        try:
            client = self._client_factory(cancel)
        except Exception:
            # Factories may contain credentials; their exception text is not UI text.
            return ServiceResult.failure(request, _CANCELLED if _cancelled(cancel) else _UNEXPECTED)
        if _cancelled(cancel):
            return ServiceResult.failure(request, _CANCELLED)
        try:
            if request.operation == "connection":
                value = client.test_connection()
            elif request.operation == "search":
                value = client.search_models(**arguments)
            else:
                value = client.version(arguments["version_id"])
        except CivitAIError as exc:
            # CivitAIClient already replaces URLs, headers and response bodies with
            # its fixed, actionable messages. Preserve that existing error contract.
            error = ServiceError(exc.kind, str(exc), exc.status)
        except ValueError as exc:
            message = str(exc)
            if message == _CANCELLED.message:
                error = _CANCELLED
            elif message in _SAFE_INPUT_ERRORS:
                error = ServiceError("input", message)
            else:
                error = ServiceError("input", "CivitAI 查詢參數無效。")
        except Exception:
            error = _UNEXPECTED
        else:
            if _cancelled(cancel):
                return ServiceResult.failure(request, _CANCELLED)
            try:
                result = ServiceResult.success(request, value)
            except (TypeError, ValueError, RecursionError):
                return ServiceResult.failure(request, _CANCELLED if _cancelled(cancel) else _BAD_RESPONSE)
            return ServiceResult.failure(request, _CANCELLED) if _cancelled(cancel) else result
        return ServiceResult.failure(request, _CANCELLED if _cancelled(cancel) else error)
