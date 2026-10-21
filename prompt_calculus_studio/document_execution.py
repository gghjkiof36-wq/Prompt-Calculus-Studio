"""A workflow execution owner outlives every view and never follows focus."""
import copy
from pathlib import Path
from PySide6.QtCore import QObject


class DocumentExecution(QObject):
    def __init__(self, document, host, client_type, *, workspace=None, store=None, state=None, metadata=None):
        workspace = workspace or getattr(host, 'workspace_owner', None)
        super().__init__(workspace or document)
        self.workspace_owner = workspace
        self.document_id = document.key if document else metadata['document_id']
        self.runtime_token = document.runtime_token if document else metadata['token']
        self._title = document.title if document else Path(metadata['path']).stem
        self.document = document
        self.view_host = host
        self.store = host.store if host else store
        self._state = state
        self.closed = False
        self.client = client_type(self)
        self.received = []
        self.client.resultsReceived.connect(self.receive_results)

    @property
    def state(self):
        return self.document.state if self.document is not None else self._state

    @state.setter
    def state(self, value):
        if self.document is not None:self.document.state = value
        else:self._state = value

    @property
    def comfy(self):return self.client

    @property
    def title(self):return self.document.title if self.document else self._title

    def __getattr__(self, name):
        # Existing runners can reach their document's UI while it is attached.
        # A detached identity has no editable UI to accidentally mutate.
        host = self.__dict__.get('view_host')
        if host is not None:return getattr(host, name)
        raise AttributeError(name)

    def notice(self, message):
        if self.view_host is not None:self.view_host.notice(message)
        elif self.workspace_owner is not None:self.workspace_owner.show_notice(self.title+'：'+message)

    def error(self, message):self.notice(message)

    def changed(self, scope='generation', refresh=True):
        if scope == 'settings':self.persist()
        elif self.document is not None:self.document.view_changed(self.view_host)
        else:self.persist()

    def persist(self):
        self.store.save(self.state)
        return True

    def receive_results(self, values):
        self.received.extend(copy.deepcopy(values))
        if self.document is not None:
            for host in self.document.hosts:host.recent.receive_results(values)

    def detach(self):
        self._state = copy.deepcopy(self.state)
        self.document = None
        self.view_host = None
        self.persist()

    def attach(self, document, host):
        document.state = self.state
        self.document = document
        self.view_host = host

    def close(self):
        if self.closed or self.__dict__.get('_closing', False):return
        from .close_errors import CloseError
        from .draft_cache import DraftCache
        failures = []
        self._closing = True
        try:
            if not self.__dict__.get('_client_closed', False):
                try:
                    self.client.shutdown()
                    self._client_closed = True
                except Exception as error:failures.append(('停止連線', error))
            if not self.__dict__.get('_state_saved', False):
                try:
                    self.persist()
                    # A client whose shutdown failed may still change state.
                    self._state_saved = self.__dict__.get('_client_closed', False)
                    self._state_preserved = True
                except Exception as error:
                    failures.append(('保存文件', error))
                    self._state_preserved = False
                    try:
                        self.close_recovery_path = str(DraftCache(self.store.directory).write(
                            'execution-close-state', dict(token=self.runtime_token, state=copy.deepcopy(self.state))))
                        self._state_preserved = True
                    except Exception as recovery_error:failures.append(('保存執行狀態副本', recovery_error))
                    recovery = getattr(self.workspace_owner, 'close_recovery', None)
                    if self.document is not None and recovery is not None:
                        try:
                            from types import SimpleNamespace
                            location = recovery.preserve(SimpleNamespace(document=self.document,
                                path=self.document.path, title=self.document.title))
                            if location is None:raise OSError('文件恢復副本未能寫入。')
                            self._state_preserved = True
                            self.document_recovery_path = location
                        except Exception as recovery_error:failures.append(('保存文件恢復副本', recovery_error))
            # Shutdown can use the journal. Keep it available for a failed
            # client's retry. If all preservation failed, retain this owner and
            # store for a later save; unrelated owners must still finish.
            if (self.__dict__.get('_client_closed', False) and self.__dict__.get('_state_preserved', False)
                    and not self.__dict__.get('_store_closed', False)):
                try:
                    self.store.close()
                    self._store_closed = True
                except Exception as error:failures.append(('關閉文件資料庫', error))
            self.closed = self.__dict__.get('_client_closed', False) and self.__dict__.get('_store_closed', False)
            self.close_errors = [*self.__dict__.get('close_errors', []), *failures]
        finally:
            self._closing = False
        if failures:raise CloseError(failures) from failures[0][1]
