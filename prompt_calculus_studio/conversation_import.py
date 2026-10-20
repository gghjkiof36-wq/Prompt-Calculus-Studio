"""Exclusive local conversation publication with durable per-conversation receipts.

The new folder is marked started before payload writes and complete only after
all files are flushed. Failed/cancelled folders remain identifiable, never
overwritten or recursively cleaned. A retry gets a separate owned folder.
"""
from contextlib import nullcontext
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from zipfile import ZipFile

from .conversation_archive import Cancelled, MIB, checkpoint, canonical
from .conversation_markdown import attachment_plan, branch_files, index_markdown
from .local_documents import paths
from .local_documents.directory_pin import pinned_parent
from .local_documents.windows import reader
from .markdown_source import MAX_BYTES

RECEIPT = '匯入完成.json'


@dataclass(frozen=True)
class Destination:
    path: Path
    identity: tuple

    @classmethod
    def choose(cls, path):
        root = paths.root_path(path)
        info = root.stat()
        return cls(root, (info.st_dev, info.st_ino))

    def verify(self):
        root = paths.root_path(self.path)
        info = root.stat()
        if (info.st_dev, info.st_ino) != self.identity:
            raise ValueError('匯入位置已被替換，請重新選擇位置。')
        return root


@dataclass
class ImportResult:
    completed: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    cancelled: bool = False
    titles: dict = field(default_factory=dict)
    unprocessed: list = field(default_factory=list)


def safe_title(title):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', title).strip(' .')[:48] or '未命名對話'
    if paths.RESERVED.match(title):title = '對話-'+title
    return title


def existing(root, prefix, key, digest):
    for folder in root.iterdir():
        if not folder.name.startswith(prefix):continue
        if paths.is_link(folder):raise ValueError('匯入名稱已被連結占用：'+folder.name)
        if not folder.is_dir():continue
        receipt = paths.checked(root, folder.name+'/'+RECEIPT)
        if not receipt.exists():continue
        with pinned_parent(receipt):
            raw, _ = paths.read_bytes(receipt, 4*MIB)
            try:record = json.loads(raw)
            except (ValueError, UnicodeError):continue  # Interrupted final receipt.
            if not isinstance(record, dict):continue
            if record.get('source_id') == key and record.get('content_hash') == digest and record.get('state') == 'complete':
                for name in record['files']:
                    if not paths.checked(folder, name).is_file():
                        raise ValueError('先前匯入的檔案不完整：'+folder.name+'；請選擇另一個目的位置。')
                return folder/'對話.md'
    return None


def asset_hashes(archive, assets, cancel):
    result = {}
    for member in assets:
        checkpoint(cancel)
        digest = hashlib.sha256()
        with archive.open(member) as stream:
            while True:
                checkpoint(cancel)
                block = stream.read(MIB)
                if not block:break
                digest.update(block)
        result[member] = digest.hexdigest()
    return result


def import_selected(source, selected, destination, cancel, progress=lambda text: None):
    result = ImportResult()
    used = 0
    indices = tuple(dict.fromkeys(selected))
    if not indices or any(type(i) is not int or not 0 <= i < len(source.conversations) for i in indices):
        raise ValueError('請先選取要匯入的對話。')
    destination.verify()
    # Pin the complete source and destination chains before any payload I/O.
    with pinned_parent(source.path), reader(source.path) as stream, pinned_parent(destination.path/'import.guard'):
        source.verify(stream, cancel)
        root = destination.verify()
        zipped = ZipFile(stream) if source.path.suffix.lower() == '.zip' else nullcontext(None)
        with zipped as archive:
            for position, index in enumerate(indices, 1):
                folder = None
                item = source.conversations[index]
                try:
                    checkpoint(cancel)
                    progress(f'正在匯入 {position} / {len(indices)}：{item.title}')
                    links, assets, warnings = attachment_plan(source, item, cancel)
                    hashes = asset_hashes(archive, assets, cancel) if archive else {}
                    digest = hashlib.sha256(canonical([item.digest, hashes])).hexdigest()
                    prefix = safe_title(item.title)+'-'+hashlib.sha256(item.key.encode()).hexdigest()[:12]+'-'+digest[:12]
                    previous = existing(root, prefix, item.key, digest)
                    if previous:
                        result.skipped.append(previous)
                        result.titles[str(previous)] = item.title
                        continue
                    folder = paths.checked(root, prefix)
                    if folder.exists():
                        folder = paths.checked(root, prefix+'-重試-'+uuid.uuid4().hex[:8])
                    folder.mkdir()  # Exclusive directory creation; never merge.
                    files, volumes = [], []
                    with pinned_parent(folder/'receipt.guard'):
                        def write(name, content):
                            nonlocal used
                            checkpoint(cancel)
                            path = paths.checked(folder, name, new=True)
                            if path.suffix == '.md' and len(content) > MAX_BYTES:
                                raise ValueError('產生的 Markdown 超過單檔 8 MiB，沒有保存為完成。')
                            if used+len(content) > source.limits.output:
                                raise ValueError('本次匯入輸出超過 512 MiB，請縮減選取。')
                            with pinned_parent(path), path.open('xb') as output:
                                for start in range(0, len(content), MIB):
                                    checkpoint(cancel)
                                    output.write(content[start:start+MIB])
                                output.flush()
                                os.fsync(output.fileno())
                            used += len(content)
                            files.append(name)
                        write('匯入開始.json', canonical(dict(version=1, state='started', source_id=item.key,
                                                           content_hash=digest, operation=uuid.uuid4().hex)))
                        write('原始對話.json', json.dumps(item.raw, ensure_ascii=False, indent=2, allow_nan=False).encode())
                        if assets:
                            (folder/'assets').mkdir()
                            for member, name in assets.items():
                                checkpoint(cancel)
                                path = paths.checked(folder, name, new=True)
                                digest_check = hashlib.sha256()
                                with pinned_parent(path), path.open('xb') as output, archive.open(member) as asset:
                                    while True:
                                        checkpoint(cancel)
                                        block = asset.read(MIB)
                                        if not block:break
                                        used += len(block)
                                        if used > source.limits.output:raise ValueError('附件超過本次輸出容量。')
                                        digest_check.update(block)
                                        output.write(block)
                                    output.flush()
                                    os.fsync(output.fileno())
                                if digest_check.hexdigest() != hashes[member]:raise ValueError('附件讀取時已改變：'+member)
                                files.append(name)
                        for number, part, name, content in branch_files(item, links, source.limits, cancel):
                            write(name, content)
                            volumes.append((number, part, name))
                        write('對話.md', index_markdown(item, volumes, warnings, assets))
                        # reader excludes in-place writes; check replacement
                        # identity here instead of hashing the whole ZIP per note.
                        source.verify(stream, cancel, full=False)
                        checkpoint(cancel)
                        # The final durable marker is written last; cancellation
                        # after this point preserves this complete conversation.
                        receipt = canonical(dict(version=1, state='complete', source_id=item.key,
                                                 content_hash=digest, original_hash=item.digest,
                                                 files=list(files), attachments=hashes, warnings=warnings))
                        write(RECEIPT, receipt)
                    result.completed.append(folder/'對話.md')
                    result.titles[str(folder/'對話.md')] = item.title
                except Cancelled:
                    result.cancelled = True
                    if folder:result.failed.append((item.title, str(folder), '已取消；這個資料夾尚未完成。'))
                    result.unprocessed = [source.conversations[i].title for i in indices[position if folder else position-1:]]
                    break
                except (OSError, ValueError, KeyError, RuntimeError) as error:
                    result.failed.append((item.title, str(folder) if folder else '', str(error)))
                    result.unprocessed = [source.conversations[i].title for i in indices[position:]]
                    # Source and destination failures cannot be safely retried
                    # automatically for the remaining selected conversations.
                    break
    progress('已停止匯入' if result.cancelled else '匯入處理結束')
    return result
