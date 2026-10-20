"""Durable, local execution owners; portable files never contain task journals."""
import copy
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import uuid

from .core import Storage


def path_key(path):
    return str(Path(path).absolute()).casefold()


def unfinished(db):
    """Accepted/uncertain work, including input-only mutations, holds the lane."""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'generation_jobs' in tables:
        if db.execute("""SELECT 1 FROM generation_jobs WHERE json_extract(body,'$.state')
                         IN ('submitting','queued','running','unconfirmed') LIMIT 1""").fetchone():
            return True
    if 'stage_journal' in tables:
        if db.execute("""SELECT 1 FROM stage_journal WHERE kind='input'
                         AND json_extract(body,'$.sent')=1
                         AND COALESCE(json_extract(body,'$.ended'),0)=0 LIMIT 1""").fetchone():
            return True
    if 'work_queue' in tables:
        if db.execute("""SELECT 1 FROM work_queue WHERE json_extract(body,'$.state')
                         IN ('submitting','queued','running','unconfirmed','results_pending') LIMIT 1""").fetchone():
            return True
    if 'workspace_operations' in tables:
        if db.execute("SELECT 1 FROM workspace_operations WHERE ended=0 LIMIT 1").fetchone():
            return True
    return False


class WorkspaceRuntime:
    def __init__(self, workspace):
        self.workspace = workspace
        self.directory = workspace.data/'documents'
        self.directory.mkdir(exist_ok=True)
        self.executions = {}
        self.errors = {}
        self.closed = False
        try:
            self.db = sqlite3.connect(workspace.data/'execution-index.sqlite3')
            with self.db:
                self.db.execute('CREATE TABLE IF NOT EXISTS owners (token TEXT PRIMARY KEY, body TEXT NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS tickets (token TEXT PRIMARY KEY, position INTEGER NOT NULL)')
            # Validate every binding before permitting new submissions. A bad
            # index is never replaced with an empty journal.
            self.rows()
        except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise ValueError('任務紀錄無法讀取；原資料已保留：'+str(exc)) from exc

    def rows(self):
        values = []
        for token, raw in self.db.execute('SELECT token,body FROM owners ORDER BY rowid'):
            value = json.loads(raw)
            if str(uuid.UUID(token)) != token or value['token'] != token:
                raise ValueError('任務身分紀錄無效。')
            if not isinstance(value['receipt']['sha256'], str):
                raise ValueError('文件配對紀錄無效。')
            values.append(value)
        return values

    def write(self, value):
        try:
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO owners VALUES (?,?)',
                                (value['token'], json.dumps(value, ensure_ascii=False)))
            self.errors.pop(value['token'], None)
        except sqlite3.Error as exc:
            self.errors[value['token']] = str(exc)
            raise OSError('任務配對紀錄無法儲存：'+str(exc)) from exc

    def metadata(self, document):
        return dict(token=document.runtime_token, path=str(document.path),
                    document_id=document.key, receipt=asdict(document.receipt),
                    document=copy.deepcopy(document.document),
                    runtime_document=copy.deepcopy(document.runtime_document),
                    identity_maps=copy.deepcopy(document.identity_maps),
                    baseline=copy.deepcopy(document.baseline),
                    opening_settings=copy.deepcopy(document.opening_settings))

    def remember(self, document):
        self.write(self.metadata(document))

    def retire_content(self, document):
        """Keep old tasks, but never restore a Save As draft over its source."""
        value = self.metadata(document)
        value['recovery_only'] = True
        self.write(value)

    def acquire(self, document, *, fresh=False):
        previous = None if fresh else next((r for r in reversed(self.rows())
            if path_key(r['path']) == path_key(document.path) and r['document_id'] == document.key
            and r['receipt']['sha256'] == document.receipt.sha256), None)
        if previous and previous.get('recovery_only'):
            previous = None
        document.runtime_token = previous['token'] if previous else str(uuid.uuid4())
        document.data = self.directory/document.runtime_token
        document.data.mkdir(exist_ok=True)
        if previous:
            document.runtime_document = previous['runtime_document']
            document.identity_maps = previous.get('identity_maps')
            document.baseline = previous.get('baseline')
            document.opening_settings = previous.get('opening_settings')
            execution = self.executions.get(document.runtime_token)
            if execution:
                document.state = execution.state
                document.execution = execution
            elif (document.data/'studio.sqlite3').is_file():
                store = None
                try:
                    store = Storage(document.data)
                    if store.db.execute('SELECT 1 FROM document WHERE id=1').fetchone():
                        document.state = store.load_current(multi=True)
                    elif previous.get('baseline') is not None:
                        raise ValueError('任務資料庫缺少原文件狀態。')
                except (sqlite3.Error, ValueError) as exc:
                    self.errors[document.runtime_token] = str(exc)
                    raise ValueError('任務資料無法讀取，原紀錄已保留：'+str(exc)) from exc
                finally:
                    if store is not None:
                        store.close()
            elif previous.get('baseline') is not None:
                self.errors[document.runtime_token] = '任務資料庫已不存在。'
                raise ValueError('這份文件的任務資料庫已不存在，未建立空白替代資料。')
        else:
            self.remember(document)
        return document.state is not None

    def relocate(self, source, target):
        """Update unopened owners too; the caller restores this snapshot on rollback."""
        before = self.rows()
        changes = []
        for value in before:
            try:
                relative = Path(value['path']).relative_to(source)
            except ValueError:
                continue
            value = copy.deepcopy(value)
            value['path'] = str(target/relative)
            value['receipt']['path'] = value['path']
            changes.append(value)
        try:
            with self.db:
                for value in changes:
                    self.db.execute('UPDATE owners SET body=? WHERE token=?',
                                    (json.dumps(value, ensure_ascii=False), value['token']))
        except sqlite3.Error as exc:
            raise OSError('任務位置紀錄無法更新：'+str(exc)) from exc
        return before

    def restore_bindings(self, values):
        with self.db:
            for value in values:
                self.db.execute('UPDATE owners SET body=? WHERE token=?',
                                (json.dumps(value, ensure_ascii=False), value['token']))

    def recover(self):
        """Monitor pending owners even when their tab/file is no longer open."""
        from .document_execution import DocumentExecution
        from .workspace_connection import WorkspaceComfyClient
        for value in self.rows():
            token = value['token']
            if token in self.executions:
                continue
            if not (self.directory/token/'studio.sqlite3').is_file():
                if value.get('baseline') is not None:
                    self.errors[token] = '任務資料庫已不存在。'
                continue
            store = None
            try:
                store = Storage(self.directory/token)
                if not unfinished(store.db):
                    store.close()
                    continue
                state = store.load_current(multi=True)
                execution = DocumentExecution(None, None, WorkspaceComfyClient,
                    workspace=self.workspace, store=store, state=state, metadata=value)
                self.executions[token] = execution
            except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
                if store is not None:
                    store.close()
                self.errors[token] = str(exc)
        if self.errors:
            self.workspace.show_notice('部分任務紀錄需要檢查，已保留資料並暫停新的生成。')

    def history_owner(self, value):
        """Open retained history on demand without restoring it into a file."""
        from .document_execution import DocumentExecution
        from .workspace_connection import WorkspaceComfyClient
        token = value['token']
        if token in self.executions:
            return self.executions[token]
        if not (self.directory/token/'studio.sqlite3').is_file():
            raise ValueError('任務資料庫已不存在，原配對紀錄仍保留。')
        store = Storage(self.directory/token)
        try:
            state = store.load_current(multi=True)
            execution = DocumentExecution(None, None, WorkspaceComfyClient,
                workspace=self.workspace, store=store, state=state, metadata=value)
            self.executions[token] = execution
            return execution
        except Exception:
            store.close()
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        for execution in self.executions.values():
            execution.close()
        self.db.close()
