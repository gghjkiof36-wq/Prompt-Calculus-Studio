"""Read-only, bounded ChatGPT export reader. No Qt or network dependencies."""
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from threading import Event
from zipfile import ZipFile, BadZipFile

from .local_documents import paths
from .local_documents.directory_pin import pinned_parent
from .local_documents.windows import reader

MIB = 1024 * 1024


@dataclass(frozen=True)
class Limits:
    source: int = 256*MIB
    expanded: int = 512*MIB
    member: int = 256*MIB
    members: int = 20_000
    conversations: int = 10_000
    nodes: int = 100_000
    branches: int = 2048
    path_steps: int = 2_000_000
    output: int = 512*MIB
    volume: int = 7*MIB


class Cancelled(Exception):
    pass


def checkpoint(cancel):
    if cancel is not None and cancel.is_set():raise Cancelled('已取消')


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:raise ValueError('JSON 有重複欄位：'+key)
        result[key] = value
    return result


def decode_json(raw):
    objects = 0
    def object_hook(pairs):
        nonlocal objects
        objects += 1
        if objects > 500_000:raise ValueError('JSON 的物件數超過限制。')
        return unique_object(pairs)
    try:
        return json.loads(raw, object_pairs_hook=object_hook,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('JSON 含無效數值：'+value)))
    except (UnicodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError('JSON 格式無法讀取：'+str(error)) from error


def canonical(raw):
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


@dataclass
class Conversation:
    raw: dict
    key: str
    digest: str
    title: str
    leaves: tuple
    warnings: list = field(default_factory=list)

    def path(self, leaf):
        result = []
        while leaf is not None:
            result.append(leaf)
            leaf = self.raw['mapping'][leaf].get('parent')
        return tuple(reversed(result))

    @property
    def current(self):
        return self.raw.get('current_node')


def conversation(raw, limits):
    if not isinstance(raw, dict) or not isinstance(raw.get('mapping'), dict) or not raw['mapping']:
        raise ValueError('不是支援的 ChatGPT 對話：需要非空的 mapping 訊息關係。')
    mapping = raw['mapping']
    if len(mapping) > limits.nodes:raise ValueError('對話節點超過限制。')
    children = {key: [] for key in mapping}
    roots = []
    for key, node in mapping.items():
        if not isinstance(key, str) or not isinstance(node, dict) or node.get('id', key) != key:
            raise ValueError('訊息節點的識別不一致。')
        parent = node.get('parent')
        if parent is None:roots.append(key)
        elif not isinstance(parent, str) or parent not in mapping:
            raise ValueError('訊息 '+key+' 找不到父節點。')
        else:children[parent].append(key)
        if node.get('message') is not None and not isinstance(node['message'], dict):
            raise ValueError('訊息 '+key+' 的內容結構不支援。')
    if not roots:raise ValueError('對話沒有根節點，可能存在循環。')
    for key, node in mapping.items():
        declared = node.get('children')
        if declared is not None:
            if (not isinstance(declared, list) or any(not isinstance(v, str) for v in declared)
                    or len(set(declared)) != len(declared) or set(declared) != set(children[key])):
                raise ValueError('訊息 '+key+' 的父子關係不一致。')
            children[key] = declared
    pending = [(key, 1) for key in reversed(roots)]
    visited, leaves, steps = set(), [], 0
    while pending:
        key, depth = pending.pop()
        if key in visited:raise ValueError('對話含循環訊息關係。')
        visited.add(key)
        if not children[key]:
            leaves.append(key)
            steps += depth
            if len(leaves) > limits.branches or steps > limits.path_steps:
                raise ValueError('對話分支展開超過限制，沒有截斷匯入。')
        pending.extend((child, depth+1) for child in reversed(children[key]))
    if len(visited) != len(mapping):raise ValueError('對話含無法連到根節點的循環。')
    if raw.get('current_node') is not None and raw['current_node'] not in mapping:
        raise ValueError('來源目前路徑指向不存在的訊息。')
    digest = hashlib.sha256(canonical(raw)).hexdigest()
    key = raw.get('conversation_id') or raw.get('id')
    warnings = []
    if not isinstance(key, str) or not key:
        key = 'content-'+digest
        warnings.append('來源沒有對話識別，使用內容識別判斷重複。')
    title = raw.get('title')
    return Conversation(raw, key, digest, title if isinstance(title, str) and title.strip() else '未命名對話',
                        tuple(leaves), warnings)


def records(value):
    if isinstance(value, list):return value
    if isinstance(value, dict):
        if value.get('provider') == 'chatgpt' and 'version' in value and 'messages' in value:
            from .connector_capture import validate_capture
            return [validate_capture(value)]
        if 'mapping' in value:return [value]
        if isinstance(value.get('conversations'), list):return value['conversations']
    raise ValueError('不支援這份 JSON；請選擇 ChatGPT 的 conversations.json 或其 ZIP 封存。')


def read_chunks(stream, limit, cancel, progress=None):
    chunks, total = [], 0
    while True:
        checkpoint(cancel)
        block = stream.read(MIB)
        if not block:break
        total += len(block)
        if total > limit:raise ValueError('檔案展開後超過容量限制。')
        chunks.append(block)
        if progress:progress(total)
    return b''.join(chunks)


def fingerprint(stream, cancel):
    digest = hashlib.sha256()
    stream.seek(0)
    while True:
        checkpoint(cancel)
        block = stream.read(MIB)
        if not block:break
        digest.update(block)
    stream.seek(0)
    return digest.hexdigest()


def safe_source(path, limits):
    path = Path(path).absolute()
    paths.root_path(path.parent)
    if paths.is_link(path):raise ValueError('來源不可是連結或重解析點。')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limits.source:
        raise ValueError('來源不是一般檔案，或超過 256 MiB。')
    return path, identity(info)


def zip_members(archive, limits):
    result, folded, expanded = {}, set(), 0
    infos = archive.infolist()
    if len(infos) > limits.members:raise ValueError('封存檔案數超過限制。')
    for info in infos:
        raw_name = info.orig_filename.rstrip('/') if info.is_dir() else info.orig_filename
        paths.relative_parts(raw_name)
        name = info.filename.rstrip('/') if info.is_dir() else info.filename
        paths.relative_parts(name)
        if name.casefold() in folded:raise ValueError('封存內有重名或大小寫衝突：'+name)
        folded.add(name.casefold())
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or info.flag_bits & 1:raise ValueError('封存含連結或加密項目：'+name)
        if info.file_size > limits.member:raise ValueError('封存項目過大：'+name)
        expanded += info.file_size
        if expanded > limits.expanded:raise ValueError('封存展開後超過 512 MiB。')
        if not info.is_dir():result[name] = info
    file_names = {n.casefold() for n in result}
    for name in result:
        if any(parent.as_posix().casefold() in file_names
               for parent in PurePosixPath(name).parents if str(parent) != '.'):
            raise ValueError('封存的檔案與資料夾路徑衝突：'+name)
    return result


@dataclass
class Archive:
    path: Path
    stamp: tuple
    digest: str
    conversations: list
    members: tuple
    limits: Limits
    conversation_members: frozenset = frozenset()

    def verify(self, stream, cancel, *, full=True):
        path, stamp = safe_source(self.path, self.limits)
        if stamp != self.stamp or identity(os.fstat(stream.fileno())) != self.stamp:
            raise ValueError('來源檔案已改變，請重新選擇封存。')
        if full and fingerprint(stream, cancel) != self.digest:
            raise ValueError('來源內容已改變，請重新選擇封存。')
        if identity(path.stat()) != self.stamp:raise ValueError('來源在讀取期間被替換。')

    def attachment(self, reference):
        value = str(reference)
        for prefix in ('file-service://', 'sandbox:/mnt/data/', 'attachment://'):
            if value.startswith(prefix):value = value[len(prefix):]
        # File IDs and explicit exported filenames only; never match by message title.
        names = self.members + tuple(self.conversation_members)
        matches = [value] if value in names else [name for name in names if PurePosixPath(name).name == value or
                  value.startswith('file-') and PurePosixPath(name).name.startswith((value+'-', value+'_', value+'.'))]
        # Retain carrier matches for an explicit explanation and ambiguity
        # checks, but never let an attachment reference copy the conversation set.
        if matches and all(name in self.conversation_members for name in matches):
            return None, '對話來源檔不作為附件匯入；所選對話另存於原始對話.json'
        if len(matches) == 1:return matches[0], ''
        return None, '有多個可能附件，未猜測配對' if matches else '封存未提供這個附件'


def load_archive(path, cancel=None, progress=lambda text: None, limits=Limits()):
    cancel = cancel or Event()
    path, stamp = safe_source(path, limits)
    if path.suffix.lower() not in ('.json', '.zip'):raise ValueError('請選擇 ZIP 或 JSON 對話封存。')
    conversations, seen, members, sources = [], set(), (), frozenset()
    nodes = 0
    with pinned_parent(path), reader(path) as stream:
        if identity(os.fstat(stream.fileno())) != stamp:raise ValueError('來源檔案在開啟時被替換。')
        digest = fingerprint(stream, cancel)
        def add(raw):
            nonlocal nodes
            value = decode_json(raw)
            if isinstance(value, dict) and value.get('provider') == 'chatgpt' and 'version' in value and 'messages' in value:
                from .connector_capture import MAX_CAPTURE_BYTES
                if len(raw) > MAX_CAPTURE_BYTES:
                    raise ValueError('擷取資料超過 16 MiB，請減少選取訊息。')
            for record in records(value):
                checkpoint(cancel)
                item = conversation(record, limits)
                pair = item.key, item.digest
                if pair not in seen:
                    nodes += len(item.raw['mapping'])
                    if nodes > limits.nodes:raise ValueError('封存的訊息節點總數超過 100,000。')
                    seen.add(pair)
                    conversations.append(item)
                    if len(conversations) > limits.conversations:raise ValueError('對話數量超過限制。')
        progress('正在讀取對話…')
        if path.suffix.lower() == '.zip':
            try:
                with ZipFile(stream) as archive:
                    entries = zip_members(archive, limits)
                    sources = frozenset(name for name in entries if re.fullmatch(r'conversations(?:[-_]\d+)?\.json', PurePosixPath(name).name, re.I))
                    if not sources:raise ValueError('ZIP 裡找不到 conversations.json 或 conversations-數字.json。')
                    members = tuple(name for name in entries if name not in sources)
                    for name in sorted(sources):
                        progress('正在讀取 '+name+'…')
                        with archive.open(name) as member:
                            add(read_chunks(member, limits.member, cancel))
            except (BadZipFile, RuntimeError, NotImplementedError) as error:
                raise ValueError('ZIP 無法讀取：'+str(error)) from error
        else:add(read_chunks(stream, limits.source, cancel))
        result = Archive(path, stamp, digest, conversations, members, limits, sources)
        result.verify(stream, cancel, full=False)
    if not conversations:raise ValueError('封存沒有可匯入的對話。')
    progress(f'已讀取 {len(conversations)} 段對話')
    return result
