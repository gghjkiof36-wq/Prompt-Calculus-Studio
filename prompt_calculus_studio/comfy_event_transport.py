"""Shared shadow channel: bounded background requests, no execution-state writes.

ShadowChannel is clock/transport injectable. QtChannel only adapts it to a worker
event loop; decoded pages and SQLite connections never cross into the GUI.
"""
import json
import logging
import math
import sqlite3
import threading
import time
from urllib.parse import urlencode, unquote

from . import comfy_event_protocol as p
from .comfy_event_inbox import EventInbox

HEARTBEAT = 8.0
DETECT = 12.0
EVIDENCE = 30.0
NO_PROGRESS = 120.0


def poll_evidence(route, data, value):
    """Small observations from existing replies, before any UI callback consumes them."""
    if not isinstance(value, dict): return []
    if route.startswith('/history/'):
        prompt = unquote(route[len('/history/'):])
        entry = value.get(prompt, {})
        status = entry.get('status', {}) if isinstance(entry, dict) else {}
        names = {row[0] for row in status.get('messages', []) if isinstance(row, list) and row and isinstance(row[0], str)}
        outcome = ('cancelled' if 'execution_interrupted' in names else
                   'error' if 'execution_error' in names or status.get('status_str') == 'error' else
                   'success' if status.get('completed') else None)
        return [(f'prompt:{prompt}', outcome)] if outcome else []
    if route not in ('workflow/native/status', 'workflow/native/recovery', 'workflow/native/cancel',
                     'workflow/queue/status', 'workflow/queue/recovery', 'workflow/queue/cancel'):
        return []
    ident = (data.get('attempt') if route.startswith('workflow/queue/') else data.get('id')) if isinstance(data, dict) else None
    if not ident: return []
    recovery = value.get('recovery', {})
    outcome = ('missing' if recovery.get('location') == 'missing' and recovery.get('fenced') else
               'cancelled' if value.get('cancel_confirmed') or
                   (value.get('state') == 'failed' and (value.get('terminal_action') == 'cancel' or route.endswith('/cancel'))) else
               'success' if value.get('state') == 'complete' else
               'error' if value.get('state') == 'failed' and value.get('terminal_action') != 'cancel' else None)
    if not outcome: return []
    entity = ('prompt:' + value['prompt_id'] if value.get('prompt_id') else
              ('queue_attempts:' if route.startswith('workflow/queue/') else 'native_operations:') + ident)
    return [(entity, outcome)]


class ShadowChannel:
    def __init__(self, inbox, send, identity, *, scope=None, clock=time.monotonic, notify=None, valid=None, reconcile_first=False):
        self.inbox, self.send, self.clock = inbox, send, clock
        self.identity = dict(identity)
        self.scope = p.scope(scope)
        self.notify = notify or (lambda value: None)
        self.valid = valid or (lambda: True)
        self.generation = 0
        self.serial = 0
        self.pending = {}
        self.stopped = False
        self.last_events = self.last_heartbeat = self.clock()
        self.next_heartbeat = self.next_events = self.next_snapshot = self.clock()
        self.retry_count = 0
        self.storage_retry = None
        self.storage_at = 0
        self.storage_failures = 0
        self.snapshot_token = self.snapshot_after = ''
        self.snapshot_as_of = None
        stream = inbox.stream(identity, self.scope)
        self.since = stream['received'] if stream else '0'
        self.need_snapshot = not stream or not stream['snapshot']
        self.needs_reconcile = self.need_snapshot
        self.causes = set()
        self.running = None
        self.progress = {}
        self.diagnosed = set()
        self.log_consistent = True
        self.publisher_alive = None
        self.queue_readable = None
        self.last_health = None
        self.last_error = ''
        self.renegotiate = False
        self.refresh_token = False
        self.next_negotiate = 0
        self.snapshot_completed = False
        self.watches = {}
        self.reconcile_first = reconcile_first
        self.snapshot_authorized = not reconcile_first

    def watch(self, entity, phase):
        if entity not in self.watches:
            self.watches[entity] = dict(phase=phase, started=self.clock(), state='waiting')
        elif self.watches[entity]['phase'] != phase:
            self.watches[entity] = dict(phase=phase, started=self.clock(), state='waiting')

    def _receipt(self, item):
        body = item.get('payload', {})
        entity = item.get('entity', '')
        kind = item.get('kind', '')
        from .comfy_event_inbox import terminal
        if terminal(item) or kind == 'operation.queued': self.watches.pop(entity, None)
        prompt = item.get('prompt_id')
        if prompt and terminal(item): self.watches.pop('prompt:' + prompt, None)
        if kind == 'native.executed' and prompt: self.watch('prompt:' + prompt, 'collecting')
        if kind == 'native.executing' and prompt: self.watches.pop('prompt:' + prompt, None)

    def cancel_requests(self):
        self.generation += 1
        handles = [row[1] for row in self.pending.values()]
        self.pending.clear()
        for handle in handles:
            if handle is not None: handle.abort()

    def stop(self):
        self.stopped = True
        self.cancel_requests()

    def set_scope(self, requested):
        requested = p.scope(requested)
        if requested == self.scope: return
        self.cancel_requests()
        self.scope = requested
        self.since = '0'
        self.require_snapshot('scope_changed', reset=True)

    def require_snapshot(self, reason, *, reset=False):
        self.needs_reconcile = True
        if reset or reason not in self.causes:
            self.inbox.diagnostic('reconcile_required', dict(identity=self.identity, reason=reason))
            self.causes.add(reason)
            self.need_snapshot = True
            self.snapshot_authorized = not self.reconcile_first
            self.snapshot_completed = False
            self.snapshot_token = self.snapshot_after = ''
            self.snapshot_as_of = None
            self.next_snapshot = self.clock()
            self.last_health = None  # A new cause needs a fresh targeted-probe authorization.

    def query(self, route, **extra):
        return route + '?' + urlencode(dict(server_instance=self.identity['server_instance'], epoch=self.identity['epoch'],
             scope=p.encode(self.scope).decode(), scope_digest=p.scope_digest(self.scope), **extra))

    def request(self, kind, route, receive, timeout=DETECT):
        if kind in self.pending or self.stopped: return
        generation = self.generation
        self.serial += 1
        serial = self.serial
        self.pending[kind] = (serial, None, self.clock() + timeout)
        def current():
            return self.valid() and not self.stopped and generation == self.generation and self.pending.get(kind, (None,))[0] == serial
        def done(value):
            if not current(): return
            self.pending.pop(kind)
            self._apply(lambda: receive(value))
        def failed(error):
            if not current(): return
            self.pending.pop(kind)
            self._failed(kind, error)
        handle = self.send(route, done, failed, self.clock() + timeout)
        if current(): self.pending[kind] = (serial, handle, self.clock() + timeout)

    def _apply(self, action):
        try:
            action()
        except (sqlite3.Error, OSError) as exc:
            # Keep this bounded page in memory. No subsequent GET or cursor
            # movement until the exact transaction succeeds (or process exits).
            self.last_error = 'inbox: ' + str(exc)
            self.cancel_requests()
            self.storage_retry = action
            self.storage_failures += 1
            self.storage_at = self.clock() + min(8, 2 ** (self.storage_failures - 1))
        except (ValueError, TypeError, KeyError) as exc:
            self.last_error = 'protocol: ' + str(exc)
            self.cancel_requests()
            self.renegotiate = True
            self.needs_reconcile = True
            self.next_negotiate = self.clock() + 1

    def _failed(self, kind, error):
        self.last_error = str(error)
        self.retry_count += 1
        backoff = min(8, 2 ** min(self.retry_count - 1, 3))
        self.next_events = self.next_snapshot = self.next_negotiate = self.clock() + backoff
        code = getattr(error, 'status_code', None)
        if code in (400, 409):
            self.cancel_requests()
            self.renegotiate = True
            self.needs_reconcile = True
        elif code == 403:
            self.cancel_requests()
            self.refresh_token = True
        if kind == 'snapshot':
            self.snapshot_token = self.snapshot_after = ''
            self.snapshot_as_of = None

    def _negotiate(self, value):
        identity = p.recognize(value)
        if identity is None:
            self.stop()
            return
        self.cancel_requests()
        self.identity = identity
        self.renegotiate = False
        self.since = '0'
        self.require_snapshot('identity_reset', reset=True)
        self.next_heartbeat = self.clock()

    def _configured(self, value):
        token = value.get('token')
        if not isinstance(token, str) or not token: raise ValueError('Missing channel token')
        self.notify(dict(token=token))  # Worker-local callback, never emitted to GUI.
        self.refresh_token = False
        self.renegotiate = True

    def _events(self, value):
        p.validate_header(value, self.identity, self.scope)
        if value.get('reset_required'):
            self.require_snapshot('cursor_expired', reset=True)
            return
        since = value['from_cursor']
        self.since = self.inbox.page(self.identity, self.scope, since, value)
        now = self.clock()
        self.last_events = now
        self.retry_count = 0
        self.next_events = now + (1 if not value['events'] or not value['log_consistent'] else .01)
        for item in value['events']:
            self._receipt(item)
            prompt = item.get('prompt_id')
            if prompt:
                self.progress[prompt] = now
                self.diagnosed.discard(prompt)
            if item.get('kind') == p.LOG_GAP:
                self.require_snapshot('log.gap:' + item['seq'])
        self._consistency(value)

    def _consistency(self, value):
        self.log_consistent = value['log_consistent']
        if not self.log_consistent: self.require_snapshot('inconsistent')
        else: self.causes.discard('inconsistent')

    def _heartbeat(self, value):
        p.validate_header(value, self.identity, self.scope)
        running = value.get('running')
        if running is not None and (not isinstance(running, list) or any(not isinstance(v, str) for v in running)):
            raise ValueError('Invalid running set')
        now = self.clock()
        self.last_heartbeat = now
        self.notify(dict(heartbeat=True, identity=dict(self.identity)))
        self.running = running
        self.queue_readable = value.get('queue_readable')
        self.publisher_alive = value.get('publisher_alive')
        for prompt in running or []: self.progress.setdefault(prompt, now)
        self.progress = {key:at for key,at in self.progress.items() if running is None or key in running}
        self._consistency(value)

    def _snapshot(self, value):
        self.inbox.snapshot_page(self.identity, self.scope, value, token=self.snapshot_token,
                                 after=self.snapshot_after, as_of=self.snapshot_as_of)
        self.snapshot_token, self.snapshot_after = value['snapshot_token'], value['next_after']
        self.snapshot_as_of = value['as_of_seq']
        for item in value['receipts']: self._receipt(item)
        if value['done']:
            self.since = self.inbox.stream(self.identity, self.scope)['received']
            self.need_snapshot = False
            self.snapshot_completed = True
            self.snapshot_token = self.snapshot_after = ''
            self.snapshot_as_of = None
            self.next_events = self.clock() + (1 if not value['log_consistent'] else .01)
        self.next_snapshot = self.clock() + .01

    def tick(self):
        if self.stopped or not self.valid(): return
        now = self.clock()
        if self.storage_retry is not None:
            if now < self.storage_at: return
            action = self.storage_retry
            self.storage_retry = None
            self._apply(action)
            if self.storage_retry is not None: return
            self.storage_failures = 0
        for kind, (_, handle, deadline) in list(self.pending.items()):
            if now >= deadline:
                self.pending.pop(kind, None)
                if handle is not None: handle.abort()
                self._failed(kind, TimeoutError('absolute request deadline'))
        age = max(now - self.last_events, now - self.last_heartbeat)
        state = 'unconfirmed' if age >= EVIDENCE else 'detected' if age >= DETECT else 'receiving'
        def record_health():
            if age >= DETECT: self.require_snapshot('no_response')
            else: self.causes.discard('no_response')
            for prompt, at in self.progress.items():
                if now - at >= NO_PROGRESS and prompt not in self.diagnosed:
                    self.inbox.diagnostic('no_progress', dict(identity=self.identity, prompt_id=prompt, seconds=now-at))
                    self.diagnosed.add(prompt)
            for entity, watch in self.watches.items():
                elapsed = now - watch['started']
                # A node output is not the terminal of the whole job. A running
                # prompt may spend hours on a subsequent node without failing.
                running = watch['phase'] == 'collecting' and entity.removeprefix('prompt:') in (self.running or [])
                watch['state'] = ('waiting' if running or elapsed < DETECT else
                                  'unconfirmed' if elapsed >= EVIDENCE else 'detected')
                if watch['state'] != 'waiting': self.require_snapshot('evidence:' + entity)
            health = dict(state=state, needs_reconcile=self.needs_reconcile,
                          shadow_snapshot_complete=self.snapshot_completed,
                          log_consistent=self.log_consistent, publisher_alive=self.publisher_alive,
                          queue_readable=self.queue_readable, running=self.running,
                          operations={key:dict(phase=value['phase'],state=value['state']) for key,value in self.watches.items()})
            if health != self.last_health:
                self.inbox.health(self.identity, self.scope, health)
                self.last_health = health
                self.notify(dict(health=health, identity=dict(self.identity)))
        self._apply(record_health)
        if self.storage_retry is not None: return
        if self.refresh_token:
            if now >= self.next_negotiate: self.request('config', 'config', self._configured, 6)
            return
        if self.renegotiate:
            if now >= self.next_negotiate: self.request('capability', 'events/capability', self._negotiate, 6)
            return
        if now >= self.next_heartbeat and 'heartbeat' not in self.pending:
            self.next_heartbeat = now + HEARTBEAT
            self.request('heartbeat', self.query('events/heartbeat'), self._heartbeat)
        if self.need_snapshot:
            # No event read can race the snapshot watermark commit.
            if self.snapshot_authorized and 'events' not in self.pending and now >= self.next_snapshot:
                self.request('snapshot', self.query('events/snapshot', snapshot_token=self.snapshot_token,
                             after=self.snapshot_after), self._snapshot, 6)
        elif now >= self.next_events:
            self.request('events', self.query('events', since=self.since, wait=8), self._events)

    def next_wakeup(self):
        """Next actionable deadline, after tick; no periodic idle Python pump."""
        if self.stopped or not self.valid():return None
        now=self.clock()
        if self.storage_retry is not None:return self.storage_at
        due=[row[2] for row in self.pending.values()]
        # These thresholds change health only once. HTTP responses and commands
        # separately wake the worker and may replace the deadlines beforehand.
        observed=min(self.last_events,self.last_heartbeat)
        if observed+DETECT>now:due.append(observed+DETECT)
        elif observed+EVIDENCE>now:due.append(observed+EVIDENCE)
        for prompt,observed in self.progress.items():
            if prompt not in self.diagnosed:due.append(observed+NO_PROGRESS)
        for entity,watch in self.watches.items():
            if watch['phase']=='collecting' and entity.removeprefix('prompt:') in (self.running or []):continue
            started=watch['started']
            if started+DETECT>now:due.append(started+DETECT)
            elif started+EVIDENCE>now:due.append(started+EVIDENCE)
        if self.refresh_token or self.renegotiate:
            kind='config' if self.refresh_token else 'capability'
            if kind not in self.pending:due.append(self.next_negotiate)
        else:
            if 'heartbeat' not in self.pending:due.append(self.next_heartbeat)
            if self.need_snapshot:
                if self.snapshot_authorized and 'events' not in self.pending and 'snapshot' not in self.pending:due.append(self.next_snapshot)
            elif 'events' not in self.pending:due.append(self.next_events)
        return min(due) if due else None


# Qt is only an adapter. Its worker owns request parsing, SQLite, timers and
# health evaluation even while the GUI is blocked for multiple seconds.
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot, Qt
from PySide6.QtNetwork import QNetworkAccessManager


class DeliveryFilter:
    """Worker-only transient observations; lifecycle authorization comes from GUI commit."""
    def __init__(self):
        self.running = {}
        self.nodes = {}
        self.display = {}

    def select(self, page, owner_info=None):
        owner=page['owner']; running=self.running.get(owner, set())
        selected=[]
        for event in page['events']:
            kind=event['kind'];prompt=event.get('prompt_id')
            if kind=='native.progress': continue
            if prompt in running and kind in ('native.execution_start','native.executing','native.executed','native.execution_cached'):
                if owner_info and any(event.get('scope',{}).get(key) not in (None,owner_info.get(key)) for key in ('library_id','workspace_id')):
                    continue
                node=event.get('payload',{}).get('node')
                if kind=='native.executing' and node is not None and self.nodes.get((owner,prompt))!=str(node):
                    self.nodes[owner,prompt]=str(node)
                    self.display.setdefault(owner,{})[prompt]=dict(prompt_id=prompt,node=str(node))
                continue
            selected.append(event)
        return dict(page,events=selected)

    def accepted(self, owner, prompts):
        self.running[owner]=set(prompts)
        self.nodes={key:node for key,node in self.nodes.items() if key[0]!=owner or key[1] in prompts}


class _Worker(QObject):
    finished = Signal()
    deliveryReady = Signal(object)
    healthChanged = Signal(object)
    def __init__(self, directory, url, token, identity):
        super().__init__()
        self.directory, self.url, self.token, self.identity = directory, url, token, identity
        self.epoch, self.stopped, self.replies = 0, False, set()
        self.core = self.inbox = None
        self.queued = []
        self.cancelled = threading.Event()
        self.owners = {}
        self.delivering = set()
        self.delivery_revision = -1
        self.prune_at = 0
        self.filter = DeliveryFilter()
        self.display_at = 0

    def notified(self, value):
        if 'token' in value: self.token = value['token']
        if 'health' in value or 'heartbeat' in value: self.healthChanged.emit(value)

    @Slot()
    def start(self):
        self.network = QNetworkAccessManager(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.cycle)
        self.retry_at = 0
        self.cycle()

    def cycle(self):
        if self.stopped: return
        if hasattr(self,'timer'):self.timer.stop()
        if self.core is None:
            if time.monotonic() < self.retry_at:
                self.wake(math.ceil((self.retry_at-time.monotonic())*1000));return
            try:
                self.inbox = EventInbox(self.directory)
                self.core = ShadowChannel(self.inbox, self.send, self.identity,
                                          notify=self.notified,
                                          valid=lambda: not self.cancelled.is_set(), reconcile_first=True)
            except (sqlite3.Error, OSError) as exc:
                logging.getLogger(__name__).warning('shadow inbox unavailable: %s', exc)
                self.retry_at = time.monotonic() + 8
                self.wake(8000);return
        while self.queued and self.core.storage_retry is None:
            value = self.queued.pop(0)
            self.core._apply(lambda value=value: self.apply_command(value))
        self.core.tick()
        if self.identity != self.core.identity:
            self.identity=dict(self.core.identity)
            self.delivering.clear()
            self.delivery_revision=-1
            self.filter = DeliveryFilter()
        if self.core.storage_retry is None and self.inbox.db.total_changes != self.delivery_revision:
            advanced = False
            for owner in self.owners:
                if owner in self.delivering: continue
                page = self.inbox.delivery(self.core.identity, self.core.scope, owner)
                if page is not None:
                    page = self.filter.select(page,self.owners[owner])
                    if not page['events'] and not page['snapshot']:
                        self.core._apply(lambda page=page:self.inbox.delivered(page))
                        if self.core.storage_retry is not None: break
                        advanced = True
                        # A bounded page may be followed by more inbox data.
                        self.wake(0)
                    else:
                        self.delivering.add(owner)
                        self.deliveryReady.emit(page)
            self.delivery_revision = -1 if advanced else self.inbox.db.total_changes
        if self.filter.display and time.monotonic()>=self.display_at:
            self.display_at=time.monotonic()+.2
            display=self.filter.display;self.filter.display={}
            self.healthChanged.emit(dict(identity=dict(self.core.identity),display={owner:list(items.values()) for owner,items in display.items()}))
        if time.monotonic() >= self.prune_at:
            self.prune_at = time.monotonic() + 60
            self.core._apply(lambda: self.inbox.prune(self.core.identity))
        if hasattr(self,'timer'):
            deadlines=[self.prune_at]
            if self.queued and self.core.storage_retry is None:deadlines.append(time.monotonic())
            due=self.core.next_wakeup()
            if due is not None:deadlines.append(due)
            if self.filter.display:deadlines.append(self.display_at)
            self.wake(max(1,math.ceil((min(deadlines)-time.monotonic())*1000)))

    def wake(self, milliseconds):
        if self.stopped:return
        if not hasattr(self,'timer'):
            QTimer.singleShot(milliseconds,self.cycle);return
        if not self.timer.isActive() or self.timer.remainingTime()>milliseconds:
            self.timer.start(max(0,milliseconds))

    def send(self, route, done, failed, deadline):
        from .comfy_client import ComfyClient
        def received(value):
            done(value)
            # Drain nonempty pages on the worker without waiting for its
            # health timer. A fixed 250ms pause per snapshot page can exhaust
            # the server's 60s snapshot lease. Core deadlines still enforce
            # empty-page, inconsistent-log and storage-error backoff.
            self.wake(15)
        def rejected(error):
            failed(error)
            self.wake(0)
        return ComfyClient.request(self, route, done=received, failed=rejected,
                                   deadline=deadline, max_bytes=p.MAX_PAGE_BYTES)

    @Slot(object)
    def command(self, value):
        if self.stopped: return
        # Ownership commands are bounded by the workspace's existing owners;
        # repeated polling evidence is coalesced before it reaches this queue.
        if len(self.queued) >= 1024:
            logging.getLogger(__name__).warning('shadow command queue full; diagnostic not accepted')
            return
        self.queued.append(value)
        self.wake(0)

    def apply_command(self, value):
        if value['kind'] == 'owner':
            self.inbox.owner(value['value'])
            self.owners[value['value']['token']] = value['value']
            self.core.require_snapshot('owner:' + value['value']['token'])
        elif value['kind'] == 'delivered':
            if value['page']['identity']!=self.core.identity:return
            self.inbox.delivered(value['page'])
            self.filter.accepted(value['page']['owner'],value.get('running',[]))
            self.delivering.discard(value['page']['owner'])
            self.delivery_revision = -1
        elif value['kind'] == 'redeliver':
            self.delivering.discard(value['owner'])
            self.delivery_revision = -1
        elif value['kind'] == 'reconcile':
            self.core.require_snapshot(value['reason'], reset=True)
        elif value['kind'] == 'snapshot_authorized':
            self.core.snapshot_authorized = True
        elif value['kind'] == 'reconciled':
            self.core.needs_reconcile = False
        elif value['kind'] == 'poll':
            for entity, outcome in value['evidence']:
                self.inbox.observe_poll(self.core.identity, entity, outcome, value['owner'])
        elif value['kind'] == 'scope': self.core.set_scope(value['scope'])
        elif value['kind'] == 'watch': self.core.watch(value['entity'], value['phase'])
        elif value['kind'] == 'diagnostic': self.inbox.diagnostic(value['name'], value['value'])

    @Slot()
    def stop(self):
        if self.stopped: return
        self.stopped = True
        self.epoch += 1
        if self.core: self.core.stop()
        for reply in list(self.replies): reply.abort()
        if hasattr(self, 'timer'): self.timer.stop()
        if self.inbox: self.inbox.close()
        self.finished.emit()


class QtChannel(QObject):
    command = Signal(object)
    stopping = Signal()
    deliveryReady = Signal(object)
    healthChanged = Signal(object)
    def __init__(self, directory, url, token, identity, parent=None):
        super().__init__(parent)
        self.url, self.token, self.identity = url, token, identity
        self.thread = QThread()
        self.worker = _Worker(directory, url, token, identity)
        self.worker.moveToThread(self.thread)
        self.worker.deliveryReady.connect(self.deliveryReady)
        self.worker.healthChanged.connect(self.healthChanged)
        self.thread.started.connect(self.worker.start)
        self.command.connect(self.worker.command)
        self.stopping.connect(self.worker.stop)
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.worker.finished.connect(self.worker.deleteLater)
        self._released = False
        self.thread.start()

    @property
    def running(self):
        if self._released: return False
        # finished is emitted before native thread teardown has completed.
        # Deleting QThread from that signal can wait for a Python callback
        # while the GUI holds the GIL. Only release after a nonblocking join.
        if not self.thread.wait(0): return True
        self._released = True
        self.thread.deleteLater()
        return False

    def stop(self):
        if self.running:
            self.worker.cancelled.set()
            self.stopping.emit()
