"""Qt-free request/result boundary for external services.

This module owns snapshots and correlation, not credentials, scheduling, retries
or destination freshness. Callers still apply their existing owner/epoch guards.
JSON snapshots retain strings exactly; tuples are accepted as JSON arrays.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from threading import Event
from typing import Protocol
from uuid import UUID, uuid4


def _snapshot(value, *, sort_keys=False):
    """Reject lossy object keys; JSON itself checks values and non-finite floats."""
    active = set()

    def check_keys(item):
        if isinstance(item, (dict, list, tuple)):
            if id(item) in active:
                raise ValueError("Service payload must not contain a cycle.")
            active.add(id(item))
            try:
                if isinstance(item, dict):
                    if any(not isinstance(key, str) for key in item):
                        raise TypeError("Service JSON object keys must be strings.")
                    values = item.values()
                else:
                    values = item
                for child in values:
                    check_keys(child)
            finally:
                active.remove(id(item))

    check_keys(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), sort_keys=sort_keys)


@dataclass(frozen=True, slots=True, init=False)
class ServiceRequest:
    """Immutable correlation and JSON snapshots; accessors return fresh copies.

    scope is caller-owned identity (for example page, token epoch and serial).
    Neither scope nor input should contain credentials or executable objects.
    """
    service: str
    operation: str
    request_id: str
    _scope_json: str = field(repr=False)
    _input_json: str = field(repr=False)

    def __init__(self, service, operation, *, scope=None, input=None,
                 request_id=None):
        if not isinstance(service, str) or not service:
            raise ValueError("Service name must be a non-empty string.")
        if not isinstance(operation, str) or not operation:
            raise ValueError("Service operation must be a non-empty string.")
        ident = str(uuid4()) if request_id is None else str(UUID(request_id))
        object.__setattr__(self, "service", service)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "request_id", ident)
        object.__setattr__(self, "_scope_json", _snapshot(scope, sort_keys=True))
        object.__setattr__(self, "_input_json", _snapshot(input, sort_keys=True))

    @property
    def scope(self):
        return json.loads(self._scope_json)

    @property
    def input(self):
        return json.loads(self._input_json)


@dataclass(frozen=True, slots=True)
class ServiceError:
    """Provider-sanitized display message and small, non-secret metadata."""
    kind: str
    message: str
    status: int | None = None

    def __post_init__(self):
        if not isinstance(self.kind, str) or not self.kind:
            raise ValueError("Service error kind must be a non-empty string.")
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("Service error message must be a non-empty string.")
        if self.status is not None and type(self.status) is not int:
            raise TypeError("Service error status must be an integer or None.")


class ServiceFailure(Exception):
    """Unwrap inside the worker so existing Jobs.failed handles failures."""
    def __init__(self, error: ServiceError):
        super().__init__(error.message)
        self.error = error


_MISSING = object()


@dataclass(frozen=True, slots=True, init=False)
class ServiceResult:
    request: ServiceRequest
    error: ServiceError | None
    _value_json: str | None = field(repr=False)

    def __init__(self, request, *, value=_MISSING, error=None):
        if not isinstance(request, ServiceRequest):
            raise TypeError("Service result requires a ServiceRequest.")
        if (value is _MISSING) == (error is None):
            raise ValueError("Service result requires exactly one value or error.")
        if error is not None and not isinstance(error, ServiceError):
            raise TypeError("Service result error must be a ServiceError.")
        frozen_value = None if error is not None else _snapshot(value)
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "error", error)
        object.__setattr__(self, "_value_json", frozen_value)

    @classmethod
    def success(cls, request, value):
        return cls(request, value=value)

    @classmethod
    def failure(cls, request, error):
        return cls(request, error=error)

    def value_for(self, request):
        """Return an independent value only for the complete expected request."""
        if not isinstance(request, ServiceRequest) or request != self.request:
            raise ServiceFailure(ServiceError(
                "correlation", "外部服務回覆與目前要求不符，已忽略。"))
        if self.error is not None:
            raise ServiceFailure(self.error)
        return json.loads(self._value_json)


class Adapter(Protocol):
    def execute(self, request: ServiceRequest,
                cancel: Event | None = None) -> ServiceResult: ...
