"""Bounded QImage workers and a GUI-only, generation-fenced display adapter.

No worker captures a document, widget, SQL owner or mutable collection. Active
read() calls finish cooperatively; window close polls the pool without joining.
"""
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from itertools import count
import threading
from time import perf_counter
import weakref

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot, Qt, QEvent
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from .preview_requests import DecodeKey, Identity, Limits, stamp

_serials = count(1)
_reader_headers = threading.Lock()


def scope(window, store):
    return (id(getattr(window, 'store', store)), id(getattr(window, 'canvas_edit_context', None)),
            str(getattr(window, 'state', {}).get('workspace', '')))


def decode(key):
    """Preserve the caller's old size/EXIF rules, and reject a replaced file."""
    if key.version is None:
        return QImage(), 'changed'
    # First-use Qt codec discovery can reenter QApplication.notify. Serialize
    # header/plugin setup with a Python lock (which releases the GIL while
    # waiting), avoiding a Qt plugin-lock/GIL inversion between workers.
    with _reader_headers:
        reader = QImageReader(key.path)
        reader.setAutoTransform(key.variant not in ('legacy-icon', 'legacy-thumb'))
        reader.setAllocationLimit(128)
        size = reader.size()
        if key.variant != 'legacy-thumb' and size.isValid() and (key.variant != 'shrink' or
                              size.width() > key.width or size.height() > key.height):
            size.scale(key.width, key.height, Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(size)
    image = reader.read()
    reader = None
    if key.variant == 'legacy-thumb' and not image.isNull():
        image = image.scaled(key.width,key.height,Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    if stamp(key.path) != key.version:
        return QImage(), 'changed'
    return image, 'failed' if image.isNull() else 'ready'


@dataclass
class Work:
    key: DecodeKey
    number: int
    visible: bool
    consumer: str
    decode_charge: int = 0
    created: float = field(default_factory=perf_counter)
    cancel: threading.Event = field(default_factory=threading.Event)
    receivers: dict = field(default_factory=dict)


class Sink(QObject):
    finished = Signal(object)


class WorkQueue:
    """GUI-owned queues. Reserve completion space BEFORE starting a reader."""
    def __init__(self):
        self.pending, self.running = OrderedDict(), {}
        self.pending_bytes = self.reserved_bytes = self.reserved_count = 0
        self.decoding_bytes = 0

    def release(self, work):
        self.reserved_count -= 1
        self.reserved_bytes -= work.key.width * work.key.height * 8


class Read(QRunnable):
    """One read, one queued signal, then return. Never wait for scheduler work."""
    def __init__(self, work, reader, sink):
        super().__init__()
        # Python owns the wrapper. Retire it on the GUI thread only after the
        # pool reports idle, not while C++ might still be returning from run().
        self.setAutoDelete(False)
        self.work, self.reader, self.sink = work, reader, sink
        self.done = threading.Event()

    def run(self):
        started = perf_counter()
        image, status = QImage(), 'cancelled'
        try:
            if not self.work.cancel.is_set():
                image, status = self.reader(self.work.key)
        except Exception:
            status = 'failed'
        finally:
            result = (self.work, image, status, (started-self.work.created)*1000,
                      (perf_counter()-started)*1000)
            self.done.set()
            self.sink.finished.emit(result)


class PreviewLoader(QObject):
    capacity = Signal()

    def __init__(self, limits=None, reader=decode):
        super().__init__(QApplication.instance())
        self.limits = limits or Limits()
        self.sink = Sink(self)
        self.sink.finished.connect(self.completed, Qt.ConnectionType.QueuedConnection)
        self.counts = dict(requests=0, hits=0, merged=0, decodes=0, dropped=0,
                           peak_pending=0, peak_pending_bytes=0, peak_completed=0,
                           peak_completed_bytes=0, apply_max_ms=0.)
        self.counts['peak_decoding_bytes'] = 0
        self.queue = WorkQueue()
        self.reader = reader
        self.jobs, self.cache = {}, OrderedDict()
        self.cache_bytes = 0
        self.closed = self.draining = False
        self.deliveries = deque()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.drain)
        self.recorder = getattr(QApplication.instance(), 'stall_recorder', None)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(self.limits.workers)
        self.runnables = {}
        self.pump_timer = QTimer(self)
        self.pump_timer.setSingleShot(True)
        self.pump_timer.timeout.connect(self.pump)

    @property
    def pending(self):
        return dict(self.queue.pending)

    @property
    def running(self):
        return dict(self.queue.running)

    @property
    def pending_bytes(self):
        return self.queue.pending_bytes

    def record(self, phase, work=None, **values):
        if self.recorder is not None:
            self.recorder.image(phase, consumer=work.consumer if work else 'result',
                                request=work.number if work else 0, **values)

    def request(self, identity, consumer, receiver, visible=True):
        if self.closed:
            return False
        key = identity.key
        self.counts['requests'] += 1
        if key in self.cache:
            image = self.cache.pop(key)
            self.cache[key] = image
            self.counts['hits'] += 1
            self.record('request', request_count=1)
            self.record('cache', hit=1)
            receiver(identity, image, 'ready')
            return True
        work = self.jobs.get(key)
        if work is not None:
            work.receivers[identity] = receiver
            work.visible |= visible
            self.counts['merged'] += 1
            self.record('request', work, request_count=1)
            self.record('cache', work, merged=1)
            return True
        queue = self.queue
        if (len(queue.pending) >= self.limits.pending_count or
                queue.pending_bytes + key.reservation > self.limits.pending_bytes):
            return False
        work = Work(key, next(_serials), visible, consumer)
        work.receivers[identity] = receiver
        self.jobs[key] = work
        queue.pending[key] = work
        queue.pending_bytes += key.reservation
        self.counts['peak_pending'] = max(self.counts['peak_pending'],len(queue.pending))
        self.counts['peak_pending_bytes'] = max(self.counts['peak_pending_bytes'],queue.pending_bytes)
        self.pump()
        self.record('request', work, request_count=1)
        self.record('cache', work, miss=1, bytes=key.reservation)
        return True

    def cancel(self, consumer):
        queue = self.queue
        for key,work in list(self.jobs.items()):
            work.receivers = {i:r for i,r in work.receivers.items() if i.consumer != consumer}
            if not work.receivers and key in queue.pending:
                queue.pending.pop(key)
                queue.pending_bytes -= key.reservation
                self.jobs.pop(key, None)
                work.cancel.set()
        # Delivery batches also use their live receiver mapping. Cancelled
        # consumers cannot be resurrected by an already dequeued completion.

    @Slot()
    def pump(self):
        queue = self.queue
        # A done flag precedes C++'s return from run(); activeThreadCount is the
        # extra fence needed before deleting a non-auto-deleted QRunnable.
        if not self.pool.activeThreadCount():
            self.runnables = {n:r for n,r in self.runnables.items() if not r.done.is_set()}
        if self.closed:
            if self.runnables:
                self.pump_timer.start(1)
            return
        while queue.pending and len(queue.running) < self.limits.workers:
            # Bound retired native wrappers too. Pause dispatch briefly until
            # the current batch returns; never wait on the GUI thread.
            if len(self.runnables) >= self.limits.completed_count + self.limits.workers:
                self.pump_timer.start(1)
                break
            key = next((k for k,w in queue.pending.items() if w.visible), next(iter(queue.pending)))
            size = key.width * key.height * 8
            charge = min(self.limits.decoding_bytes, key.raw_bytes or 32*1024*1024)
            if (queue.reserved_count >= self.limits.completed_count or
                    queue.reserved_bytes + size > self.limits.completed_bytes or
                    queue.decoding_bytes + charge > self.limits.decoding_bytes):
                break
            work = queue.pending.pop(key)
            queue.pending_bytes -= key.reservation
            queue.running[key] = work
            work.decode_charge = charge
            queue.decoding_bytes += charge
            self.counts['peak_decoding_bytes'] = max(self.counts['peak_decoding_bytes'],queue.decoding_bytes)
            queue.reserved_count += 1
            queue.reserved_bytes += size
            self.counts['peak_completed'] = max(self.counts['peak_completed'],queue.reserved_count)
            self.counts['peak_completed_bytes'] = max(self.counts['peak_completed_bytes'],queue.reserved_bytes)
            self.counts['decodes'] += 1
            job = Read(work, self.reader, self.sink)
            self.runnables[work.number] = job
            self.pool.start(job)

    @Slot(object)
    def completed(self, result):
        work,image,status,queued,elapsed = result
        self.queue.running.pop(work.key, None)
        self.queue.decoding_bytes -= work.decode_charge
        self.record('decode',work,duration_ms=elapsed,queue_ms=queued,
                    bytes=image.sizeInBytes(),pixels=image.width()*image.height(),worker=1)
        if self.closed:
            self.queue.release(work)
            self.jobs.pop(work.key, None)
            self.pump()
            return
        if status == 'ready' and image.sizeInBytes() <= self.limits.cache_bytes:
            prior = self.cache.pop(work.key, None)
            if prior is not None:
                self.cache_bytes -= prior.sizeInBytes()
            self.cache[work.key] = image
            self.cache_bytes += image.sizeInBytes()
            while self.cache_bytes > self.limits.cache_bytes:
                self.cache_bytes -= self.cache.popitem(last=False)[1].sizeInBytes()
        self.deliveries.append((work,image,status))
        # Merge queued notifications before applying, bounded to 2 ms.
        if not self.timer.isActive():
            self.timer.start(0)
        self.pump()

    @Slot()
    def drain(self):
        if self.draining:
            return
        self.draining = True
        started = perf_counter()
        queue = self.queue
        try:
            while self.deliveries and not self.closed:
                work,image,status = self.deliveries[0]
                if work.receivers:
                    identity = next(iter(work.receivers))
                    receiver = work.receivers.pop(identity)
                    before = perf_counter()
                    receiver(identity,image,status)
                    elapsed = (perf_counter()-before)*1000
                    self.counts['apply_max_ms'] = max(self.counts['apply_max_ms'],elapsed)
                    self.record('apply',work,duration_ms=elapsed,bytes=image.sizeInBytes())
                if not work.receivers:
                    self.deliveries.popleft()
                    self.jobs.pop(work.key, None)
                    queue.release(work)
                if (perf_counter()-started)*1000 >= self.limits.apply_ms:
                    break
            if self.deliveries:
                self.timer.start(0)
            if not self.closed:
                self.pump()
                self.capacity.emit()
        finally:
            self.draining = False

    def shutdown(self):
        self.closed = True
        self.timer.stop()
        queue = self.queue
        for work in self.jobs.values():
            work.cancel.set()
            work.receivers.clear()
        for key in queue.pending:
            self.jobs.pop(key, None)
        queue.pending.clear()
        queue.pending_bytes = 0
        while self.deliveries:
            work,_,_ = self.deliveries.popleft()
            queue.release(work)
            self.jobs.pop(work.key, None)
        # Only short jobs have entered the pool. A queued-but-not-started job
        # is removed safely because autoDelete is false (no Qt ABA hazard).
        for number,job in list(self.runnables.items()):
            if not job.done.is_set() and self.pool.tryTake(job):
                queue.running.pop(job.work.key, None)
                queue.decoding_bytes -= job.work.decode_charge
                self.jobs.pop(job.work.key, None)
                queue.release(job.work)
                del self.runnables[number]
        self.cache.clear()
        self.cache_bytes = 0
        self.pump()
        return bool(self.running or self.runnables or self.pool.activeThreadCount())


def loader_for(window, store):
    # Store-scoped fallback supports the existing shared helper constructor.
    host = window if window is not None else store
    loader = getattr(host, '_preview_loader', None)
    if loader is None:
        loader = PreviewLoader()
        host._preview_loader = loader
    return loader


def close_window(window):
    loaders = {getattr(host, '_preview_loader', None) for host in (window, window.store)} - {None}
    return any([loader.shutdown() for loader in loaders])


class PreviewAdapter(QObject):
    """One consumer generation. Slots and source descriptors stay GUI-owned."""
    ready = Signal(object, object, str)

    def __init__(self, parent, window, store, consumer='result'):
        super().__init__(parent)
        self.window, self.store, self.consumer = window, store, consumer
        self.loader = loader_for(window, store)
        self.number, self.serial = next(_serials), 0
        self.specs, self.waiting = [], deque()
        self.prepared = None
        self.versions = {}
        self.current_scope = scope(window, store)
        self.hidden = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.submit)
        self.loader.capacity.connect(self.submit)
        parent.installEventFilter(self)
        loader, number = self.loader, self.number
        parent.destroyed.connect(lambda *_: loader.cancel(number))

    def cancel(self):
        self.serial += 1
        self.timer.stop()
        self.waiting.clear()
        self.prepared = None
        self.versions.clear()
        self.loader.cancel(self.number)

    def load(self, specs, versions=None):
        """(slot, path, source digest, width, height, transform), in slot order."""
        self.cancel()
        self.current_scope = scope(self.window, self.store)
        self.specs = list(specs)
        self.versions = dict(versions or {})
        self.waiting = deque(self.specs)
        self.submit()

    def submit(self):
        if not self.waiting:
            return
        if (self.hidden or self.loader.closed or
                (self.window is not None and self.window.is_closed())):
            return
        if scope(self.window, self.store) != self.current_scope:
            self.cancel()
            return
        started = perf_counter()
        visible = self.visible()
        while self.waiting:
            spec = self.waiting[0]
            slot, path, source, width, height, variant = spec[:6]
            # Backpressure retries this same immutable request. Do not stat it
            # on every capacity broadcast; decode and delivery both recheck it.
            if self.prepared is None:
                version = self.versions[slot] if slot in self.versions else stamp(path)
                key = DecodeKey(self.current_scope, str(path), version, str(source), width, height,
                                self.parent().devicePixelRatioF() if hasattr(self.parent(), 'devicePixelRatioF') else 1., variant,
                                spec[6] if len(spec)>6 else 0)
                self.prepared = Identity(self.current_scope, self.number, self.serial, slot, key)
            identity = self.prepared
            key = identity.key
            ref = weakref.ref(self)

            def receive(identity, image, status, ref=ref):
                adapter = ref()
                if adapter is not None and isValid(adapter):
                    adapter.receive(identity, image, status)

            if not self.loader.request(identity, self.consumer, receive, visible):
                # An oversized source cannot ever enter the queue. Report the
                # existing unreadable state, without changing decode quality.
                if key.reservation > self.loader.limits.pending_bytes:
                    self.waiting.popleft()
                    self.prepared = None
                    self.ready.emit(slot, QImage(), 'failed')
                    continue
                break
            self.waiting.popleft()
            self.prepared = None
            if (perf_counter()-started)*1000 >= self.loader.limits.apply_ms:
                self.timer.start(0)
                break

    def visible(self):
        widget = self.parent()
        if not hasattr(widget, 'isVisible') or not widget.isVisible():
            return False
        while widget is not None:
            proxy = widget.graphicsProxyWidget() if hasattr(widget, 'graphicsProxyWidget') else None
            if proxy is not None and proxy.scene() is not None:
                return any(view.isVisible() and view.mapToScene(view.viewport().rect()).boundingRect().intersects(proxy.sceneBoundingRect())
                           for view in proxy.scene().views())
            widget = widget.parentWidget() if hasattr(widget, 'parentWidget') else None
        return True

    def receive(self, identity, image, status):
        if (identity.serial != self.serial or identity.scope != self.current_scope or self.hidden or
                scope(self.window, self.store) != self.current_scope or self.loader.closed or
                (self.window is not None and self.window.is_closed())):
            self.loader.counts['dropped'] += 1
            self.loader.record('apply', dropped=1)
            return
        # Recheck just before displaying: file replacement after read must not
        # turn an otherwise current receipt into the wrong source pixels.
        if status == 'ready' and stamp(identity.key.path) != identity.key.version:
            image, status = QImage(), 'changed'
        self.ready.emit(identity.card, image, status)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Hide:
            self.hidden = True
            self.cancel()
        elif event.type() == QEvent.Type.Show and self.hidden:
            self.hidden = False
            if scope(self.window, self.store) == self.current_scope:
                self.load(self.specs)
            else:
                # Show is not a new source selection. A different document
                # must explicitly provide its own specs before work resumes.
                self.cancel()
                self.specs = []
        return False
