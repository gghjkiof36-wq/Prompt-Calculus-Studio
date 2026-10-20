"""One FIFO execution lane for every file in a workspace."""
import sqlite3

from PySide6.QtCore import QObject, QTimer

from .workspace_runtime import unfinished


class WorkspaceScheduler(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.runtime = workspace.runtimes
        self.clients = {}
        self.owner = None
        self.ready = False
        self.stopped = False
        self.refreshing = False
        self.waiting = []
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def attach(self, client):
        token = getattr(client.window, 'runtime_token', None)
        if token:
            self.clients[token] = client

    def runnable(self, client):
        if client.stopped:
            return False
        chain = client.input_flow.chain
        flow = client.input_flow
        runs = getattr(chain, 'runs', lambda active: [])(True)
        return bool(getattr(chain, 'preparing', None) or getattr(chain, 'applying', None)
                    or getattr(chain, 'apply_queue', None)
                    or any(r['status'] == 'running' for r in runs)
                    or client.queue.preparing or client.queue.dispatching
                    or client.generation.batch or client.run_id
                    or flow.current or flow.direct.current or flow.direct.pending)

    def acquire_operation(self, client):
        """Immediate explicit frontend actions cannot jump ahead of another file."""
        self.refresh()
        token = getattr(client.window, 'runtime_token', None)
        if not token or not self.ready or self.stopped or self.runtime.errors:
            return False
        if self.owner not in (None, token) or any(key != token for key in self.waiting):
            return False
        if any(key != token and self.pending(other) for key, other in self.clients.items()
               if not other.stopped):
            return False
        self.owner = token
        return True

    def pending(self, client):
        return unfinished(client.window.store.db)

    def claim(self, client):
        token = getattr(client.window, 'runtime_token', None)
        if not token or self.stopped or not self.ready or self.runtime.errors:
            return False
        if token != self.owner and token not in self.waiting:
            try:
                with self.runtime.db:
                    self.runtime.db.execute("""INSERT OR IGNORE INTO tickets
                        VALUES (?,(SELECT COALESCE(MAX(position),0)+1 FROM tickets))""", (token,))
                self.waiting.append(token)
                self.waiting.sort(key=lambda key: self.runtime.db.execute(
                    'SELECT position FROM tickets WHERE token=?', (key,)).fetchone()[0])
            except sqlite3.Error as exc:
                self.runtime.errors[token] = str(exc)
                client.window.notice('排程無法保存，這次未送出：'+str(exc))
                return False
        self.refresh()
        return self.owner == token

    def owns(self, client):
        return (not self.stopped and not self.runtime.errors
                and self.owner == getattr(client.window, 'runtime_token', None))

    def refresh(self):
        if self.stopped or not self.ready or self.refreshing:
            return
        self.refreshing = True
        before = (self.owner, tuple(self.waiting))
        try:
            pending = {key for key, client in self.clients.items()
                       if not client.stopped and self.pending(client)}
            if self.owner:
                client = self.clients.get(self.owner)
                if client is None or (self.owner not in pending and not self.runnable(client)):
                    self.finish(self.owner)
                    self.owner = None
            for token in list(self.waiting):
                client = self.clients.get(token)
                if client is None or not self.runnable(client):
                    self.waiting.remove(token)
                    self.finish(token)
            # Recovery may find multiple old uncertain owners. All can query
            # receipts, but none can submit until every other owner is settled.
            if not self.owner and not self.runtime.errors:
                if len(pending) == 1:
                    self.owner = next(iter(pending))
                    if self.owner in self.waiting:
                        self.waiting.remove(self.owner)
                elif not pending:
                    self.owner = next((key for key in self.waiting
                                       if self.clients[key].connected), None)
                    if self.owner:
                        self.waiting.remove(self.owner)
            if self.owner and pending - {self.owner}:
                # No new work during reconciliation of historical overlap.
                self.owner = None
        except (sqlite3.Error, ValueError, KeyError) as exc:
            self.runtime.errors['scheduler'] = str(exc)
            self.owner = None
        finally:
            self.refreshing = False
        if before != (self.owner, tuple(self.waiting)):
            for client in self.clients.values():
                if not client.stopped:
                    client.stateChanged.emit()
            if self.owner:
                self.clients[self.owner].input_flow.chain.later()
                QTimer.singleShot(0, self.clients[self.owner].queue.pump)
                QTimer.singleShot(0, self.clients[self.owner].input_flow.direct.pump)

    def finish(self, token):
        with self.runtime.db:
            self.runtime.db.execute('DELETE FROM tickets WHERE token=?', (token,))

    def description(self, client):
        token = getattr(client.window, 'runtime_token', None)
        if self.runtime.errors:
            return '任務紀錄需要檢查，已暫停新的生成'
        if token in self.waiting:
            owner = self.clients.get(self.owner)
            name = getattr(owner.window, 'title', '前次任務') if owner else '前次任務'
            return f'排隊第 {self.waiting.index(token)+1} 位 · 等待「{name}」結束'
        if self.owner == token:
            return '執行中 · 其他工作流依序等待' if self.runnable(client) else '正在核對前次任務'
        return ''

    def stop(self):
        self.stopped = True
        self.timer.stop()
