"""Opt-in, content-free GUI stall records. No Qt import on the disabled path."""
import atexit
from datetime import datetime, timezone
import math
from pathlib import Path
import queue
import threading
import time


def threshold(value):
    import argparse
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError('stall threshold must be a positive finite number') from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('stall threshold must be a positive finite number')
    return number


def add_arguments(parser):
    parser.add_argument('--record-stalls', action='store_true', help='Record slow GUI events without document content.')
    parser.add_argument('--record-stalls-threshold', type=threshold, default=50., metavar='MS',
                        help='Stall threshold in milliseconds (default: 50; used with --record-stalls).')


def object_class(value):
    """Only built-in Qt/product type names; never objectName, repr or payload."""
    cls = type(value)
    if cls.__module__.startswith(('PySide6.', 'prompt_calculus_studio.')):
        name = cls.__name__
        if name.isidentifier() and len(name) <= 80:
            return name
    return 'unregistered'


class JsonlWriter:
    """Bounded producer queue; all encoding, rotation and disk I/O stay here.

    One active file plus two backups, each at most 10 MiB. Failure disables the
    writer, not the UI. No exception string (which may contain a path) is logged.
    """
    def __init__(self, data, *, max_bytes=10*1024*1024, backups=2, capacity=256):
        self.path = Path(data)/'logs'/('stalls-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'.jsonl')
        self.max_bytes = max_bytes
        self.backups = backups
        self.queue = queue.Queue(maxsize=capacity)
        self.stopping = threading.Event()
        self.closed = False
        self.dropped = 0
        self.failed = False
        self.thread = threading.Thread(target=self._run, name='PCS stall writer', daemon=True)
        self.thread.start()

    def submit(self, record):
        if self.closed or self.failed:
            return
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1

    def _write(self, record):
        import json
        # This serializer accepts only the fixed primitive record built below.
        encoded = (json.dumps(record, ensure_ascii=True, separators=(',', ':'))+'\n').encode('ascii')
        if len(encoded) > self.max_bytes:
            self.dropped += 1
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size + len(encoded) > self.max_bytes:
            for number in range(self.backups, 0, -1):
                target = self.path.with_suffix(f'.{number}.jsonl')
                source = self.path if number == 1 else self.path.with_suffix(f'.{number-1}.jsonl')
                if source.exists():
                    source.replace(target)
            if not self.backups:
                self.path.unlink()
        with self.path.open('ab') as stream:
            stream.write(encoded)

    def _run(self):
        reported = 0
        try:
            while not self.stopping.is_set() or not self.queue.empty():
                try:
                    record = self.queue.get(timeout=.1)
                except queue.Empty:
                    record = None
                if record is not None:
                    self._write(record)
                dropped = self.dropped
                if dropped != reported:
                    self._write(dict(schema=1, time=datetime.now(timezone.utc).isoformat(),
                                     duration_ms=0., event_type='Overflow', feature='recorder overflow',
                                     object_class='JsonlWriter', dropped_count=dropped-reported))
                    reported = dropped
        except OSError:
            self.failed = True

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stopping.set()
        # Normal shutdown drains the bounded queue. A stuck device must not
        # keep PCS alive indefinitely; the daemon is only a diagnostic writer.
        self.thread.join(timeout=2.)


class StallRecorder:
    def __init__(self, threshold_ms=50.):
        self.threshold = threshold(threshold_ms)/1000.
        self.owner = threading.get_ident()
        self.writer = None
        self.closed = False
        self.depth = 0
        self.best = None
        self.undo = []

    def start(self, data):
        if self.writer is None and not self.closed:
            self.writer = JsonlWriter(data)

    def image(self, phase, *, consumer='result', **values):
        """Fixed display labels and bounded numbers; never content or paths."""
        if self.closed or self.writer is None:
            return
        if phase not in ('request', 'cache', 'decode', 'apply'):
            return
        if consumer not in ('source', 'result', 'picker', 'legacy'):
            consumer = 'result'
        record = dict(schema=1, event_type='Image', feature='image '+phase, consumer=consumer)
        for key in ('request', 'request_count', 'hit', 'miss', 'merged', 'dropped',
                    'queue_ms', 'duration_ms', 'bytes', 'pixels', 'worker'):
            value = values.get(key)
            if type(value) is int or (type(value) is float and math.isfinite(value)):
                record[key] = min(2**53-1, max(0, value))
        self.writer.submit(record)

    def begin(self, event_type, class_name):
        if self.closed or self.writer is None or threading.get_ident() != self.owner:
            return None
        started = time.perf_counter()
        if not self.depth:
            self.started = started
            self.event_type = event_type
            self.class_name = class_name
            self.best = None
        self.depth += 1
        return started

    def end(self, started, feature=None, class_name=None):
        if started is None:
            return
        elapsed = time.perf_counter()-started
        if feature and (self.best is None or elapsed > self.best[0]):
            self.best = (elapsed, feature, class_name)
        self.depth -= 1
        if self.depth or self.closed:
            return
        elapsed = time.perf_counter()-self.started
        if elapsed < self.threshold:
            return
        best = self.best
        self.writer.submit(dict(schema=1, time=datetime.now(timezone.utc).isoformat(),
                                duration_ms=round(elapsed*1000., 3), event_type=self.event_type,
                                feature=best[1] if best else 'unregistered',
                                object_class=best[2] if best else self.class_name))

    def close(self):
        if self.closed:
            return
        self.closed = True
        for owner, name, original in reversed(self.undo):
            setattr(owner, name, original)
        self.undo.clear()
        if self.writer is not None:
            self.writer.close()


def create_application(argv, *, enabled=False, threshold_ms=50.):
    from PySide6.QtWidgets import QApplication
    if not enabled:
        return QApplication(argv)

    recorder = StallRecorder(threshold_ms)

    class RecordingApplication(QApplication):
        def notify(self, receiver, event):
            kind = event.type()
            # User event numbers have no registered payload or dynamic name.
            event_type = kind.name if int(kind) < 1000 else 'User'
            started = recorder.begin(event_type, object_class(receiver))
            try:
                return super().notify(receiver, event)
            finally:
                recorder.end(started)

    app = RecordingApplication(argv)
    app.stall_recorder = recorder
    from .performance_callbacks import install
    install(recorder)
    app.aboutToQuit.connect(recorder.close)
    atexit.register(recorder.close)
    return app


def start_recording(app, data):
    recorder = getattr(app, 'stall_recorder', None)
    if recorder is not None:
        recorder.start(data)


def stop_recording(app):
    recorder = getattr(app, 'stall_recorder', None)
    if recorder is not None:
        recorder.close()
