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
        if self.closed:return
        self.closed = True
        self.client.shutdown()
        self.persist()
        self.store.close()
