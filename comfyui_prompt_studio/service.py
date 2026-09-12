"""Local library reads, per-submission snapshots and lossless image collection."""
import copy
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from .shared.core import validate_state
from .shared.snapshots import portable_state, revision, validate_snapshot, image_snapshots
from .shared.pnginfo import png_metadata


class Service:
    def __init__(self, directory, output_root, temp_root, default_library=''):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.config_path = self.directory / 'settings.json'
        self.roots = {'output': Path(output_root).resolve(), 'temp': Path(temp_root).resolve()}
        self.lock = threading.RLock()
        self.default_library = default_library
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS collections (key TEXT PRIMARY KEY, path TEXT NOT NULL, hash TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL, created REAL NOT NULL)')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.directory / 'integration.sqlite3', timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def settings(self):
        if self.config_path.exists():
            value = json.loads(self.config_path.read_text(encoding='utf-8'))
        else:
            value = dict(library=self.default_library, destinations={})
        return value

    def configure(self, library=None, workspace=None, destination=None):
        with self.lock:
            config = self.settings()
            if library is not None:
                path = Path(library).expanduser()
                if path.is_dir():
                    path = path / 'studio.sqlite3'
                self.read_library(path)
                config['library'] = str(path.resolve())
            if destination is not None:
                target = Path(destination).expanduser()
                if not target.is_absolute() or not target.is_dir():
                    raise ValueError('收藏資料夾不存在；請選擇已有的絕對路徑。')
                config.setdefault('destinations', {})[str(workspace or '')] = str(target.resolve())
            temporary = self.config_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, self.config_path)
            return config

    def read_library(self, path=None):
        path = Path(path or self.settings().get('library', '')).expanduser()
        if path.is_dir():
            path = path / 'studio.sqlite3'
        if not path.is_file():
            raise ValueError('找不到桌面素材庫，請指定包含 studio.sqlite3 的資料夾。')
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
        try:
            row = connection.execute('SELECT body FROM document WHERE id=1').fetchone()
            if not row:
                raise ValueError('素材庫尚未保存資料。')
            state = validate_state(json.loads(row[0]))
            return dict(state=portable_state(state), library_id=str(uuid.uuid5(uuid.NAMESPACE_URL, str(path.resolve()).casefold())),
                        library_revision=revision(state))
        finally:
            connection.close()

    def prepare_prompt(self, data):
        """Runs on every queue submission, including cached node executions."""
        extra_data = data.get('extra_data')
        if not isinstance(extra_data, dict):
            return data
        extra = extra_data.get('extra_pnginfo')
        if not isinstance(extra, dict):
            return data
        # A loaded PNG may contain an earlier submission's envelope.
        extra.pop('prompt_studio', None)
        workflow = extra.get('workflow', {})
        graph = data.get('prompt', {})
        if not isinstance(workflow, dict) or not isinstance(graph, dict) or not isinstance(workflow.get('nodes', []), list):
            return data
        bindings, problems = [], []
        for node in workflow.get('nodes', []):
            if not isinstance(node, dict):
                continue
            properties = node.get('properties')
            binding = properties.get('prompt_studio') if isinstance(properties, dict) else None
            if not isinstance(binding, dict):
                continue
            node_id = str(node.get('id'))
            field = binding.get('field', 'text')
            actual_node = graph.get(node_id, {})
            # Muted/bypassed nodes are not output text sources in the submitted graph.
            if not isinstance(actual_node, dict) or not actual_node:
                continue
            inputs = actual_node.get('inputs')
            actual = inputs.get(field) if isinstance(inputs, dict) and field == 'text' else None
            if not isinstance(actual, str):
                problems.append(dict(node_id=node_id, reason='文字欄位已連線或不支援，未宣稱模組來源。'))
                continue
            try:
                snapshot = copy.deepcopy(validate_snapshot(binding['snapshot']))
                if snapshot['final_prompt'] != actual:
                    snapshot['state']['draft'] = actual
                    snapshot['state']['draft_base'] = snapshot['generated_prompt']
                    snapshot['final_prompt'] = actual
                    snapshot['manual_draft'] = True
                validate_snapshot(snapshot)
                bindings.append(dict(node_id=node_id, field=field,
                                     node_title=str(node.get('title') or node.get('type', '')),
                                     snapshot=snapshot))
            except (ValueError, KeyError, TypeError) as exc:
                problems.append(dict(node_id=node_id, reason=str(exc)))
        if bindings or problems:
            request_id = str(data.get('prompt_id') or uuid.uuid4())
            data['prompt_id'] = request_id
            envelope = dict(schema_version=1, submission_id=request_id, bindings=bindings, problems=problems)
            extra['prompt_studio'] = envelope
            with self.connect() as db:
                db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?)',
                    (request_id, json.dumps(envelope, ensure_ascii=False), time.time()))
                db.execute('DELETE FROM jobs WHERE id IN (SELECT id FROM jobs ORDER BY created DESC LIMIT -1 OFFSET 2000)')
        return data

    def source(self, image):
        root = self.roots.get(image.get('type', 'output'))
        if root is None:
            raise ValueError('只支援 ComfyUI output／temp 的 PNG 結果。')
        filename = image.get('filename', '')
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.lower().endswith('.png'):
            raise ValueError('請選擇原始 PNG 結果。')
        path = (root / image.get('subfolder', '') / filename).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('原始圖片已移動、刪除，或不在允許的結果目錄。')
        return path

    @staticmethod
    def digest(path):
        h = hashlib.sha256()
        with Path(path).open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        return h.hexdigest()

    def collect(self, image, workspace='', prompt_id='', destination=None):
        with self.lock:
            source = self.source(image)
            target_value = destination if destination is not None else self.settings().get('destinations', {}).get(str(workspace or ''))
            if not target_value:
                raise ValueError('請先設定這個工作區的收藏資料夾。')
            target = Path(target_value)
            if not target.is_absolute() or not target.is_dir():
                raise ValueError('收藏資料夾無法使用，未改存其他位置。')
            before = source.stat()
            with source.open('rb') as stream:
                if stream.read(8) != b'\x89PNG\r\n\x1a\n':
                    raise ValueError('來源不是有效的 PNG 檔案。')
            content_hash = self.digest(source)
            source_meta = png_metadata(source)
            if prompt_id:
                envelope = source_meta.get('raw', {}).get('prompt_studio')
                embedded_id = envelope.get('submission_id') if isinstance(envelope, dict) else None
                if embedded_id and embedded_id != prompt_id:
                    raise ValueError('來源圖片已被其他生成覆寫，請重新選擇結果。')
            key = hashlib.sha256((str(source) + '\0' + content_hash + '\0' + str(target.resolve())).encode()).hexdigest()
            with self.connect() as db:
                previous = db.execute('SELECT path,hash FROM collections WHERE key=?', (key,)).fetchone()
            if previous and Path(previous[0]).is_file() and self.digest(previous[0]) == previous[1]:
                return self.collection_result(Path(previous[0]), source_meta, True)
            if source.parent == target.resolve():
                destination = source
            else:
                destination = None
                try:
                    for number in range(100000):
                        candidate = target / (source.name if number == 0 else f'{source.stem}_{number:03}{source.suffix}')
                        try:
                            output = candidate.open('xb')
                            destination = candidate
                            break
                        except FileExistsError:
                            continue
                    if destination is None:
                        raise ValueError('同名圖片過多，請更換收藏資料夾。')
                    with output, source.open('rb') as input_stream:
                        for block in iter(lambda: input_stream.read(1024 * 1024), b''):
                            output.write(block)
                        output.flush()
                        os.fsync(output.fileno())
                    after = source.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or self.digest(destination) != content_hash:
                        raise ValueError('複製期間原始圖片變更，收藏未完成。')
                except Exception:
                    if destination is not None:
                        destination.unlink(missing_ok=True)
                    raise
            verified = png_metadata(destination)
            with self.connect() as db:
                db.execute('INSERT OR REPLACE INTO collections VALUES (?,?,?)', (key, str(destination), content_hash))
            return self.collection_result(destination, verified, False)

    @staticmethod
    def collection_result(path, meta, already):
        raw = meta.get('raw', {})
        return dict(path=str(path), already=already,
                    has_generation=isinstance(raw.get('prompt'), dict) and bool(raw['prompt']),
                    has_snapshot=bool(image_snapshots(meta)))

    def results(self, history):
        results = []
        for prompt_id, entry in reversed(list(history.items())):
            prompt = entry.get('prompt', [])
            extra = prompt[3] if len(prompt) > 3 and isinstance(prompt[3], dict) else {}
            envelope = extra.get('extra_pnginfo', {}).get('prompt_studio', {})
            workspaces = []
            for binding in envelope.get('bindings', []):
                state = binding.get('snapshot', {}).get('state', {})
                wid = state.get('workspace', '')
                name = next((w['name'] for w in state.get('workspaces', []) if w['id'] == wid), wid)
                if not any(w['id'] == wid for w in workspaces):
                    workspaces.append(dict(id=wid, name=name))
            for node_id, output in entry.get('outputs', {}).items():
                for image in output.get('images', []):
                    if isinstance(image, dict) and str(image.get('filename', '')).lower().endswith('.png'):
                        results.append(dict(prompt_id=prompt_id, node_id=node_id, image=image,
                                            workspaces=workspaces))
        return results[:120]

    @staticmethod
    def folders(value):
        if not value:
            roots = [f'{letter}:\\' for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' if Path(f'{letter}:\\').is_dir()] if os.name == 'nt' else ['/']
            return dict(path='', parent='', directories=roots)
        path = Path(value).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError('資料夾不存在。')
        path = path.resolve()
        folders = []
        for entry in path.iterdir():
            try:
                if entry.is_dir() and not entry.is_symlink():
                    folders.append(str(entry))
            except OSError:
                continue
        return dict(path=str(path), parent=str(path.parent) if path.parent != path else '',
                    directories=sorted(folders, key=str.casefold)[:500])
