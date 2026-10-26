"""Small engine boundary; no Qt, executable paths or process handles."""
from __future__ import annotations

import socket
from threading import Event, Lock
from typing import Callable, Protocol


class Cancelled(Exception):
    pass


class Cancellation:
    """Thread-safe request cancellation, including a currently blocked socket."""
    def __init__(self):
        self.event = Event()
        self._lock = Lock()
        self._socket = None

    def check(self):
        if self.event.is_set():
            raise Cancelled()

    def bind(self, sock):
        with self._lock:
            self._socket = sock
            cancelled = self.event.is_set()
        if cancelled:
            self.cancel()
            raise Cancelled()

    def unbind(self):
        with self._lock:
            self._socket = None

    def cancel(self):
        self.event.set()
        with self._lock:
            sock = self._socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class InferenceRuntime(Protocol):
    """Blocking engine operations called only by the dedicated worker.

    An implementation must retain ownership until stop confirms child exit.
    ensure_idle must prove inference stopped or unload the owned engine. It
    returns True only when the same model remains ready for another request.
    Generate returns (completed|limited, full_text), and may raise after deltas;
    the service retains those deltas for cancellation/error receipts.
    """
    model: dict | None
    settings: dict | None

    @property
    def alive(self) -> bool: ...

    def start(self, model: dict, settings: dict, cancel: Cancellation) -> None: ...

    def stop(self) -> None: ...

    def generate(self, messages: list[dict], params: dict, cancel: Cancellation,
                 on_delta: Callable[[str], None]) -> tuple[str, str]: ...

    def ensure_idle(self, timeout: float = 3) -> bool: ...


RuntimeFactory = Callable[[], InferenceRuntime]
