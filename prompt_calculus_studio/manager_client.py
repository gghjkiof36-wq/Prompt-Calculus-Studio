"""Asynchronous local Manager adapter. No credentials, redirects or mutation retries."""
import json
from urllib.parse import urlencode

from PySide6.QtCore import QObject, Signal, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from .core import local_address
from .manager_protocol import (ACTIVE, MAX_BODY, Journal, enrich_legacy, installed_packages,
                               make_plan, queue_busy)


class ManagerClient(QObject):
    changed = Signal()

    def __init__(self, directory, parent=None, activity=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.activity = activity or (lambda: False)
        self.journal = Journal(directory / 'manager-operations.json')
        self.server = ''
        self.epoch = 0
        self.api = ''
        self.version = ''
        self.packages = []
        self.message = '連接 ComfyUI 後，查看已安裝套件'
        self.busy = False
        self.available = False
        self.can_update = False
        self.stopped = False
        self.polling = False
        self.replies = set()
        self.operation = None
        self.timer = QTimer(self)
        self.timer.setInterval(1800)
        self.timer.timeout.connect(self.poll)

    def set_server(self, server, enabled=True):
        value = local_address(server) if enabled else ''
        if value == self.server:
            return
        self._detach()
        self.server = value
        self.packages = []
        self.available = self.can_update = False
        self.api = self.version = ''
        self.message = '按「重新整理」讀取套件' if value else '請先連接 ComfyUI'
        self.changed.emit()

    def _detach(self):
        # Any accepted/submitting operation belongs to its original server forever.
        if self.server:
            for task in self.journal.unresolved(self.server):
                task['state'] = 'unknown'
            if self.journal.unresolved(self.server):
                try:
                    self.journal.save()
                except (OSError, ValueError):
                    pass
        self.epoch += 1
        self.timer.stop()
        for reply in tuple(self.replies):
            reply.abort()
        self.busy = self.polling = False
        self.operation = None

    def shutdown(self):
        self._detach()
        self.stopped = True

    def _save(self):
        try:
            self.journal.save()
            return True
        except (OSError, ValueError):
            self.journal.error = '操作紀錄無法儲存，請檢查資料位置'
            self.message = self.journal.error
            self.busy = self.can_update = False
            self.timer.stop()
            self.changed.emit()
            return False

    def request(self, route, done, failed, payload=None, text=False):
        if self.stopped or not self.server:
            failed('請先連接 ComfyUI', 0)
            return
        epoch = self.epoch
        request = QNetworkRequest(QUrl(self.server + route))
        request.setTransferTimeout(15000)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, 'application/json')
        request.setRawHeader(b'Accept', b'application/json, text/plain')
        reply = self.network.get(request) if payload is None else self.network.post(request, json.dumps(payload).encode())
        self.replies.add(reply)
        body = bytearray()

        def read():
            body.extend(bytes(reply.readAll()))
            if len(body) > MAX_BODY:
                reply.abort()

        def finish():
            read()
            self.replies.discard(reply)
            if epoch != self.epoch or self.stopped:
                reply.deleteLater()
                return
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0
            try:
                if len(body) > MAX_BODY:
                    failed('Manager 回應過大', status)
                elif 300 <= status < 400:
                    failed('Manager 網址重新導向，請檢查連線設定', status)
                elif reply.error() != QNetworkReply.NetworkError.NoError or not 200 <= status < 300:
                    # Do not echo remote HTML, paths or credentials from transport errors.
                    message = {403: 'Manager 拒絕此操作', 404: '此 Manager 版本未提供此功能',
                               409: 'Manager 正在處理其他操作', 400: 'Manager 不接受此操作'}.get(status, 'Manager 連線中斷，請重新查詢結果')
                    failed(message, status)
                else:
                    try:
                        result = body.decode('utf-8', errors='replace') if text else json.loads(body) if body else None
                    except (ValueError, UnicodeError):
                        result = body.decode('utf-8', errors='replace')
                    done(result)
            except (ValueError, TypeError, KeyError, OSError) as exc:
                failed(str(exc), status)
            finally:
                reply.deleteLater()
        reply.readyRead.connect(read)
        reply.finished.connect(finish)

    def refresh(self):
        if self.busy or not self.server:
            return
        self.busy = True
        self.message = '正在讀取 Manager…'
        self.changed.emit()

        def failed(message, status):
            self.busy = False
            self.available = self.can_update = False
            self.message = message
            self.changed.emit()

        def versioned(value):
            if not isinstance(value, str) or len(value) > 80 or '<' in value:
                failed('Manager 版本格式不符', 0)
                return
            self.api = 'v2'
            self.version = value.strip()
            self.request('/v2/customnode/installed', installed, failed)

        def legacy(_message, status):
            if status not in (404, 405):
                failed(_message, status)
                return
            self.api = 'legacy'
            self.version = 'Legacy'
            self.request('/customnode/installed', installed, failed)

        def installed(data):
            self.packages = installed_packages(data)
            self.available = True
            if self.api == 'legacy':
                self.request('/customnode/getlist?mode=cache&skip_update=true', enriched,
                             lambda *_: ready(None))
            else:
                self.request('/v2/manager/queue/status', ready, failed)

        def enriched(data):
            self.packages = enrich_legacy(self.packages, data)
            ready(None)

        def ready(queue):
            if self.api == 'v2':
                queue_busy(queue)  # Validate the required queue capability.
                query = urlencode({'client_id': self.journal.data['client_id']})
                self.request('/v2/manager/queue/history?' + query, history_capability, no_history)
            else:
                self.can_update = False
                finish_ready()

        def history_capability(data):
            if not isinstance(data, dict) or not isinstance(data.get('history'), dict):
                no_history('', 0)
                return
            self.journal.reconcile(self.server, data['history'])
            self.can_update = not self.journal.error
            finish_ready()

        def no_history(_message, _status):
            self.can_update = False
            self.busy = False
            self.message = '此 Manager 缺少操作結果查詢，請從 ComfyUI 更新'
            self.changed.emit()

        def finish_ready():
            self.busy = False
            self.message = self.journal.error or (f'{len(self.packages)} 個已安裝套件' if self.api == 'v2' else '此 Manager 版本的更新請在 ComfyUI 操作')
            self.changed.emit()
            if self.api == 'v2' and self.journal.unresolved(self.server):
                self.timer.start()
                self.poll()

        self.request('/v2/manager/version', versioned, legacy, text=True)

    def plan(self, selected=None, include_core=False):
        ids = selected if selected is not None else [pack['id'] for pack in self.packages]
        return make_plan(self.packages, ids, self.journal.pins(self.server), include_core)

    def _generation_gate(self):
        if not self.activity():
            return True
        self.busy = False
        self.message = '請等目前的生成工作結束再更新'
        self.changed.emit()
        return False

    def submit(self, plan):
        if not self._generation_gate():
            return False
        if not self.can_update or self.busy or self.journal.unresolved(self.server):
            self.message = '上次操作結果待確認' if self.journal.unresolved(self.server) else 'Manager 尚未就緒'
            self.changed.emit()
            return False
        # Revalidate identity/version at the final action, not the earlier preview.
        current = {pack['id']: pack for pack in self.packages}
        for task in plan:
            ident = task['package_id']
            if ident == '__comfyui__':
                continue
            pack = current.get(ident)
            if not pack or pack['version'] != task['before'] or not pack['enabled'] or ident in self.journal.pins(self.server):
                self.message = '更新範圍已變更，請重新選擇'
                self.changed.emit()
                return False
        self.busy = True
        self.message = '正在確認更新範圍…'
        self.changed.emit()

        def failed(message, _status):
            self.busy = False
            self.message = message
            self.changed.emit()

        def checked(data):
            if not self._generation_gate():
                return
            if queue_busy(data):
                failed('Manager 正在處理其他操作', 409)
                return
            try:
                self.operation = self.journal.begin(self.server, plan)
            except (OSError, ValueError):
                self._save()
                failed(self.journal.error or '操作紀錄無法儲存', 0)
                return
            self._enqueue(0)
        self.request('/v2/manager/queue/status', checked, failed)
        return True

    def _enqueue(self, index):
        operation = self.operation
        if operation is None:
            return
        if not self._generation_gate():
            return
        if index >= len(operation['tasks']):
            self._start_queue_guarded()
            return
        task = operation['tasks'][index]
        task['state'] = 'submitting'
        if not self._save():
            return
        self.message = f'正在送出 {index + 1} / {len(operation["tasks"])}'
        self.changed.emit()

        def accepted(_):
            task['state'] = 'queued'
            if self._save():
                self._enqueue(index + 1)

        def failed(message, status):
            task['state'] = 'failed' if 400 <= status < 500 else 'unknown'
            task['message'] = message
            if not self._save():
                return
            self.busy = False
            self.message = message
            self.timer.start()
            self.changed.emit()
            # Previously queued tasks remain queued. Never reset another client's queue.
            self.poll()

        wire = dict(kind=task['kind'], params=task['params'], ui_id=task['ui_id'],
                    client_id=self.journal.data['client_id'])
        self.request('/v2/manager/queue/task', accepted, failed, payload=wire)

    def _started(self, _):
        self.busy = False
        self.message = '正在更新…'
        self.timer.start()
        self.changed.emit()
        self.poll()

    def _start_failed(self, message, _status):
        self.busy = False
        self.message = '開始狀態待確認 · 重新查詢'
        self.timer.start()
        self.changed.emit()
        self.poll()

    def poll(self):
        if self.polling or self.busy or self.api != 'v2' or not self.server:
            return
        if not self.journal.unresolved(self.server):
            self.timer.stop()
            return
        self.polling = True
        query = urlencode({'client_id': self.journal.data['client_id']})

        def failed(message, _status):
            self.polling = False
            self.message = message
            self.timer.stop()  # User can explicitly query again; no background flood.
            self.changed.emit()

        def history(data):
            if not isinstance(data, dict) or not isinstance(data.get('history'), dict):
                failed('Manager 操作紀錄格式不符', 0)
                return
            self.journal.reconcile(self.server, data['history'])
            self.request('/v2/manager/queue/status?' + query, status, failed)

        def status(data):
            active = queue_busy(data)
            self.polling = False
            unresolved = self.journal.unresolved(self.server)
            if not unresolved:
                self.timer.stop()
                self.message = '操作完成 · 查看下方結果'
            elif active:
                self.message = '正在處理…' if data.get('is_processing') else '已排入工作 · 尚未開始'
            else:
                for task in unresolved:
                    task['state'] = 'unknown'
                self._save()
                self.message = '操作結果待確認'
                self.timer.stop()
            self.changed.emit()
        self.request('/v2/manager/queue/history?' + query, history, failed)

    def resume_queue(self):
        """Explicitly start Manager's shared queue; never resubmit any task."""
        if self.busy or self.api != 'v2' or not self.journal.unresolved(self.server):
            return
        if not self._generation_gate():
            return
        self.busy = True
        self._start_queue_guarded()

    def _start_queue_guarded(self):
        # Manager start_worker operates on a GLOBAL queue. The official HTTP API
        # exposes per-client counts, not an atomic compare-and-start primitive.
        # Compare own and global pending work immediately before starting. This
        # detects existing foreign work; it cannot lock out another UI afterwards.
        query = urlencode({'client_id': self.journal.data['client_id']})

        def owned(data):
            queue_busy(data)
            if data.get('client_id') != self.journal.data['client_id']:
                self._start_failed('Manager 工作來源無法確認', 0)
                return
            self.request('/v2/manager/queue/status', lambda global_data: checked(data, global_data), self._start_failed)

        def checked(own, global_data):
            if not self._generation_gate():
                return
            queue_busy(global_data)
            if global_data.get('is_processing') or global_data.get('in_progress_count', 0) > 0:
                self.busy = False
                self.poll()
                return
            pending = own.get('pending_count')
            if type(pending) is not int or pending != global_data.get('pending_count'):
                self.busy = False
                self.message = 'Manager 有其他待處理工作，請從 ComfyUI 啟動佇列'
                self.changed.emit()
                return
            if pending < 1:
                self.busy = False
                self.poll()
                return
            self.request('/v2/manager/queue/start', self._started, self._start_failed, payload={})
        self.request('/v2/manager/queue/status?' + query, owned, self._start_failed)
