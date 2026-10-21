"""Keep failed saves recoverable while allowing file tabs to close."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import sqlite3

from .draft_cache import DraftCache


def key(path):
    return 'document:'+str(Path(path).absolute()).casefold()


def state_digest(state):
    raw = json.dumps(state, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


class CloseRecovery:
    def __init__(self, workspace):
        self.workspace = workspace
        self.cache = DraftCache(workspace.data)
        self.memory = {}

    def preserve(self, tab):
        document = tab.document
        if document:
            value = dict(kind=document.kind, sha=document.receipt.sha256,
                         state=copy.deepcopy(document.state), baseline=copy.deepcopy(document.baseline),
                         runtime_document=copy.deepcopy(document.runtime_document),
                         identity_maps=copy.deepcopy(document.identity_maps),
                         opening_settings=copy.deepcopy(document.opening_settings))
            # Runtime directories own attachments; closing a view does not delete them.
            value['data'] = str(document.data)
            # Record the committed predecessor, not a wall-clock timestamp.
            # A later runtime commit must never be replaced by this cache.
            store = getattr(document.host, 'store', None)
            if store is not None:
                from .state_loading import prepare_state
                try:
                    value['runtime_base_sha256'] = state_digest(prepare_state(store.load(), multi=True))
                except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
                    # Still preserve the edit. Without an ordering proof a
                    # conflicting runtime will keep priority during restore.
                    pass
        else:
            page = tab.widget
            try:raw = page.encoded()
            except ValueError:raw = page.buffer.snapshot().text.encode('utf-8')
            value = dict(kind='md', sha=getattr(page.buffer, 'recovery_source_sha', None) or hashlib.sha256(page.baseline).hexdigest(),
                         raw=base64.b64encode(raw).decode('ascii'))
        value['path'] = str(tab.path)
        self.memory[key(tab.path)] = value
        try:
            location = self.cache.write(key(tab.path), value)
            self.workspace.show_notice('已保留「'+tab.title+'」的恢復副本。')
            return str(location)
        except (OSError, ValueError) as error:
            self.workspace.show_notice(str(error))
            return None

    def get(self, path):
        return self.memory.get(key(path)) or self.cache.read(key(path))

    def clear(self, path):
        self.memory.pop(key(path), None)
        self.cache.clear(key(path))

    def restore_markdown(self, page):
        value = self.get(page.path)
        if not value or value.get('kind') != 'md':return
        from .markdown_source import SourceEdit, SourceLedger
        try:
            raw = base64.b64decode(value['raw'], validate=True)
            ledger = SourceLedger(raw)
            text = ledger.text_of(ledger.pieces)
            if not isinstance(value['sha'],str):return
        except (ValueError,KeyError,TypeError):return
        # The failed-save buffer resumes, including its encoding and line endings.
        def apply():
            if waiting:page.buffer.opened.disconnect(apply)
            snapshot = page.buffer.snapshot()
            page.buffer.apply(snapshot, [SourceEdit(0, len(snapshot.text), text)])
            page.buffer.ledger = ledger
            page.buffer.recovery_source_sha = value['sha']
            page.document().setModified(True)
            if value['sha'] != hashlib.sha256(page.baseline).hexdigest():
                page.error = page.buffer.external_error = '原檔已變更；恢復的編輯尚未覆寫原檔。'
                page.operation_error = True
            self.workspace.show_notice('已恢復「'+page.path.stem+'」的編輯。')
        waiting = page.buffer.opening
        if waiting:page.buffer.opened.connect(apply)
        else:apply()

    def restore_document(self, document):
        value = self.get(document.path)
        if not value or value.get('kind') != document.kind:return
        if not all(isinstance(value.get(name),dict) for name in
                   ('state','baseline','runtime_document','opening_settings')):return
        if 'identity_maps' not in value or (value['identity_maps'] is not None
                                           and not isinstance(value['identity_maps'], dict)):return
        if not isinstance(value.get('sha'),str) or not isinstance(value.get('data'),str):return
        if value['sha'] != document.receipt.sha256:
            document.recovery_paths = [str(root/self.cache.name(key(document.path))) for root in self.cache.roots
                                       if (root/self.cache.name(key(document.path))).is_file()]
            self.workspace.show_notice('「'+document.title+'」另有恢復副本；原檔已變更。')
            return
        from .local_documents import schema
        from .state_loading import prepare_state
        try:
            schema.validate(value['runtime_document'])
            state = prepare_state(value['state'], multi=True)
        except (ValueError, TypeError, KeyError) as error:
            self.workspace.show_notice('無法恢復「'+document.title+'」的編輯：'+str(error))
            return
        if getattr(document, 'runtime_restored', False) and document.state != state:
            if value.get('runtime_base_sha256') != state_digest(document.state):
                document.recovery_paths = [str(root/self.cache.name(key(document.path))) for root in self.cache.roots
                                           if (root/self.cache.name(key(document.path))).is_file()]
                self.workspace.show_notice('「'+document.title+'」保留目前本機稿；另有內容不同的恢復副本。')
                return
        document.state = state
        for name in ('baseline', 'runtime_document', 'identity_maps', 'opening_settings'):
            setattr(document, name, copy.deepcopy(value[name]))
        # Reuse only a known runtime directory below this workspace's document root.
        source = Path(value['data']).absolute()
        if source.parent == self.workspace.runtimes.directory and source.is_dir():
            from .local_documents import assets, paths
            for ref, _, _ in assets.references(document.document):
                try:
                    old = paths.checked(source, ref)
                    new = paths.checked(document.data, ref)
                    if old.is_file() and not new.exists():
                        new.parent.mkdir(parents=True, exist_ok=True)
                        new.write_bytes(old.read_bytes())
                except (OSError, ValueError):pass
        self.workspace.show_notice('已恢復「'+document.title+'」的編輯。')
