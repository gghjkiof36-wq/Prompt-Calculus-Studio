"""Explicit ownership for the synthetic receipt Service's connection contexts."""
from contextlib import contextmanager
from test_comfy_integration import Service


class ReceiptService(Service):
    def __init__(self, *args, **kwargs):
        self._connections = set()
        self._closed = False
        super().__init__(*args, **kwargs)

    @contextmanager
    def connect(self):
        if self._closed:
            raise AssertionError('Receipt Service used after fixture shutdown')
        with super().connect() as connection:
            self._connections.add(connection)
            try:
                yield connection
            finally:
                self._connections.remove(connection)

    def close(self):
        # Production Service.connect already closes every connection in finally.
        # A retained context is a fixture failure, not a reason to hide a lock.
        if self._connections:
            raise AssertionError(f'Receipt Service still has {len(self._connections)} open connections')
        self._closed = True
