"""Bounded transfer spool and durable operation ownership, independent of Qt."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from .conversation_archive import canonical, decode_json
from .conversation_import import Destination, RECEIPT
from .local_documents import paths
from .local_documents.directory_pin import pinned_parent
from .retained_files import read_local

MAX_CAPTURE = 16 * 1024 * 1024
MAX_CHUNK = 256 * 1024
MAX_SPOOL = 256 * 1024 * 1024
ACTIVE = {'receiving', 'previewing', 'committing'}


def operation_key(value):
    if not isinstance(value, str):
        raise ValueError('傳送編號無效。')
    return uuid.UUID(value).hex


def atomic_json(path, value):
    path = Path(path)
    content = canonical(value)
    with pinned_parent(path):
        if os.path.lexists(path) and paths.is_link(path):
            raise ValueError('設定位置無效。')
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class TransferStore:
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        self.directory.mkdir(parents=True, exist_ok=True)
        paths.root_path(self.directory)
        self.lock = threading.RLock()

    def path(self, operation, suffix='.json'):
        return self.directory / (operation_key(operation) + suffix)

    def get(self, operation):
        path = self.path(operation)
        if not path.exists():
            return None
        record = json.loads(read_local(path, 64 * 1024))
        if record.get('operation_id') != operation_key(operation):
            raise ValueError('傳送紀錄不一致。')
        return record

    def put(self, record):
        record['updated_at'] = time.time()
        atomic_json(self.path(record['operation_id']), record)
        return record

    def records(self):
        values = []
        ledgers = [path for path in self.directory.glob('*.json')
                   if re.fullmatch('[0-9a-f]{32}', path.stem)]
        if len(ledgers) > 1000:
            raise ValueError('傳送記錄超過容量，請先整理記錄。')
        for path in ledgers:
            try:
                record = self.get(path.stem)
                if record is not None:
                    values.append(record)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(values, key=lambda r: r.get('updated_at', 0), reverse=True)

    def _cleanup(self, record, *, part_only=False):
        if not part_only and record['state'] not in ('committed', 'cancelled'):
            return
        suffixes = ('.part',) if part_only else ('.part', '.source.json')
        for suffix in suffixes:
            path = self.path(record['operation_id'], suffix)
            try:
                if not os.path.lexists(path):
                    continue
                with pinned_parent(path):
                    if os.path.lexists(path) and not paths.is_link(path):
                        path.unlink()
            except (OSError, ValueError):
                pass  # Payload cleanup never changes the durable result.

    def _spool_bytes(self):
        total = 0
        for path in self.directory.iterdir():
            if re.fullmatch(r'[0-9a-f]{32}\.(?:part|source\.json)', path.name):
                if paths.is_link(path) or not path.is_file():
                    raise ValueError('連接暫存位置無效。')
                total += path.stat().st_size
        return total

    def begin(self, operation, size, digest):
        key = operation_key(operation)
        if type(size) is not int or not 0 < size <= MAX_CAPTURE:
            raise ValueError('對話太大，請減少選取的訊息。')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('傳送內容無效。')
        with self.lock:
            old = self.get(key)
            if old:
                if old['state'] == 'cancelled':
                    self._cleanup(old)
                    return old
                if old['size'] != size or old['sha256'] != digest:
                    raise ValueError('傳送內容已改變，請重新選取。')
                return old
            records = self.records()
            for record in records:
                self._cleanup(record)
            if len(records) >= 1000 or sum(r['state'] in ACTIVE for r in records) >= 4:
                raise ValueError('請先完成或取消目前的傳送。')
            reserved = sum(max(0, r['size'] - r['received']) for r in records if r['state'] == 'receiving')
            if self._spool_bytes() + reserved + size > MAX_SPOOL:
                raise ValueError('連接暫存空間已滿，請先完成目前的傳送。')
            record = dict(version=1, operation_id=key, size=size, sha256=digest,
                          state='receiving', received=0, next_index=0)
            # Create the operation before accepting bytes; no workspace writes.
            return self.put(record)

    def chunk(self, operation, index, encoded):
        if type(index) is not int or index < 0 or not isinstance(encoded, str) or len(encoded) > 4 * MAX_CHUNK // 3 + 8:
            raise ValueError('傳送片段無效。')
        try:
            block = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise ValueError('傳送片段無效。') from error
        if not block or len(block) > MAX_CHUNK:
            raise ValueError('傳送片段超過容量。')
        with self.lock:
            record = self.get(operation)
            if not record or record['state'] != 'receiving':
                raise ValueError('這次傳送已結束，請重新選取。')
            if index != record['next_index']:
                raise ValueError('傳送片段順序不符，請重新傳送。')
            if record['received'] + len(block) > record['size']:
                raise ValueError('傳送內容超過原定大小。')
            if self._spool_bytes() + len(block) > MAX_SPOOL:
                raise ValueError('連接暫存空間已滿，請先完成目前的傳送。')
            path = self.path(operation, '.part')
            with pinned_parent(path):
                if os.path.lexists(path) and paths.is_link(path):
                    raise ValueError('暫存位置無效。')
                # Crash between append and receipt must not append a duplicate.
                actual = path.stat().st_size if path.exists() else 0
                if actual != record['received']:
                    raise ValueError('上次傳送未完成，請重新選取。')
                with path.open('ab') as stream:
                    stream.write(block)
                    stream.flush()
                    os.fsync(stream.fileno())
            record.update(received=actual + len(block), next_index=index + 1)
            return self.put(record)

    def finish(self, operation):
        from .connector_capture import validate_capture
        with self.lock:
            record = self.get(operation)
            if not record:
                raise ValueError('找不到這次傳送。')
            if record['state'] != 'receiving':
                return record
            data = read_local(self.path(operation, '.part'), MAX_CAPTURE)
            if len(data) != record['size'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError('對話傳送不完整，請重新傳送。')
            capture = decode_json(data)
            normalized = validate_capture(capture)
            source = self.path(operation, '.source.json')
            if self._spool_bytes() + len(canonical(normalized)) > MAX_SPOOL:
                raise ValueError('連接暫存空間已滿，請先完成目前的傳送。')
            atomic_json(source, normalized)
            original_hash = hashlib.sha256(canonical(normalized)).hexdigest()
            record.update(state='previewing', title=normalized['title'],
                          captured_at=capture.get('captured_at'), source=str(source),
                          message_count=len(capture['messages']), scope=capture['scope'],
                          source_id=normalized['conversation_id'], original_hash=original_hash,
                          content_hash=hashlib.sha256(canonical([original_hash, {}])).hexdigest())
            self.put(record)
            self._cleanup(record, part_only=True)
            return record

    def accept(self, operation, destination):
        destination.verify()
        with self.lock:
            record = self.get(operation)
            if not record or record['state'] != 'previewing':
                raise ValueError('這份預覽已結束。')
            raw = decode_json(read_local(self.path(operation, '.source.json')))
            if hashlib.sha256(canonical(raw)).hexdigest() != record['original_hash']:
                raise ValueError('預覽來源已改變，請重新傳送。')
            record.update(state='committing', destination=str(destination.path),
                          destination_identity=list(destination.identity))
            return self.put(record)

    def started(self, operation, folder):
        with self.lock:
            record = self.get(operation)
            if not record or record['state'] != 'committing' or record.get('owned_path'):
                raise ValueError('保存操作已失效。')
            Destination(Path(record['destination']), tuple(record['destination_identity'])).verify()
            folder = paths.root_path(folder)
            if folder.parent != Path(record['destination']):
                raise ValueError('保存位置不一致。')
            info = folder.stat()
            record.update(owned_path=str(folder), owned_identity=[info.st_dev, info.st_ino], reused=False)
            self.put(record)

    def _receipt(self, record, folder):
        """Verify source and required files, while allowing edited Markdown."""
        receipt = decode_json(read_local(folder / RECEIPT, 4 * 1024 * 1024))
        if (not isinstance(receipt, dict) or receipt.get('version') != 1
                or receipt.get('state') != 'complete'
                or any(receipt.get(key) != record[key] for key in ('source_id', 'original_hash', 'content_hash'))):
            raise ValueError('保存回執與本次來源不一致。')
        files = receipt.get('files')
        if (not isinstance(files, list) or any(not isinstance(name, str) for name in files)
                or len(files) != len(set(files))
                or not {'原始對話.json', '對話.md'} <= set(files)):
            raise ValueError('保存回執的文件清單不完整。')
        for name in files:
            if not paths.checked(folder, name).is_file():
                raise ValueError('已保存文件不存在。')
        original = decode_json(read_local(paths.checked(folder, '原始對話.json')))
        if hashlib.sha256(canonical(original)).hexdigest() != record['original_hash']:
            raise ValueError('已保存來源與本次預覽不一致。')
        return receipt

    def reused(self, operation, folder):
        """Bind a matching old receipt before the writer reports a skipped copy."""
        with self.lock, pinned_parent(Path(folder) / RECEIPT):
            record = self.get(operation)
            if not record or record['state'] != 'committing' or record.get('owned_path'):
                raise ValueError('保存操作已失效。')
            Destination(Path(record['destination']), tuple(record['destination_identity'])).verify()
            folder = paths.root_path(folder)
            if folder.parent != Path(record['destination']):
                raise ValueError('保存位置不一致。')
            receipt = self._receipt(record, folder)
            old_operation = receipt.get('operation')
            if old_operation is not None and operation_key(old_operation) != old_operation:
                raise ValueError('既有保存操作識別無效。')
            info = folder.stat()
            record.update(owned_path=str(folder), owned_identity=[info.st_dev, info.st_ino],
                          reused=True, receipt_operation=old_operation)
            self.put(record)

    def _completion(self, record):
        folder = Path(record['owned_path'])
        with pinned_parent(folder / RECEIPT):
            Destination(Path(record['destination']), tuple(record['destination_identity'])).verify()
            folder = paths.root_path(folder)
            info = folder.stat()
            if [info.st_dev, info.st_ino] != record['owned_identity'] or folder.parent != Path(record['destination']):
                raise ValueError('保存位置已改變。')
            receipt = self._receipt(record, folder)
            expected = record['receipt_operation'] if record.get('reused') else record['operation_id']
            if receipt.get('operation') != expected:
                raise ValueError('保存回執不屬於這次操作。')
            return folder / '對話.md', receipt.get('operation')

    def complete(self, operation, result=None, error=''):
        with self.lock:
            record = self.get(operation)
            completed = (result.completed + result.skipped) if result else []
            if completed:
                target, receipt_operation = self._completion(record)
                if (len(completed) != 1 or Path(completed[0]) != target
                        or bool(result.skipped) != bool(record.get('reused'))):
                    raise ValueError('保存結果與本次操作不一致。')
                record.update(state='committed', path=str(target), title=result.titles.get(str(target), record.get('title', '對話')),
                              receipt_operation=receipt_operation)
                record.pop('message', None)
            else:
                detail = error or ('；'.join(str(row[2]) for row in result.failed) if result else '')
                record.update(state='cancelled' if result and result.cancelled else 'failed', message=detail or '未完成保存。')
            self.put(record)
            self._cleanup(record)
            return record

    def cancel(self, operation):
        with self.lock:
            record = self.get(operation)
            if record is None:
                if len(self.records()) >= 1000:
                    raise ValueError('傳送記錄已滿。')
                record = dict(version=1, operation_id=operation_key(operation), size=0, sha256='0' * 64,
                              state='cancelled', received=0, next_index=0)
                self.put(record)
            if record and record['state'] in ('receiving', 'previewing'):
                record.update(state='cancelled')
                self.put(record)
            if record:
                self._cleanup(record)
            return record

    def recover(self):
        """Reconcile only the operation's pinned output, never a title search."""
        with self.lock:
            for record in self.records():
                if record['state'] != 'committing' and not (record['state'] == 'unknown' and record.get('owned_path')):
                    continue
                try:
                    target, receipt_operation = self._completion(record)
                    record.update(state='committed', path=str(target), receipt_operation=receipt_operation)
                    record.pop('message', None)
                except (ValueError, OSError, KeyError):
                    record.update(state='unknown', message='上次保存中斷，請查看保存位置。')
                self.put(record)
                self._cleanup(record)

    def saved_path(self, operation):
        with self.lock:
            record = self.get(operation)
            if not record or record['state'] != 'committed':
                raise ValueError('這份對話尚未保存完成。')
            Destination(Path(record['destination']), tuple(record['destination_identity'])).verify()
            folder = paths.root_path(record['owned_path'])
            info = folder.stat()
            if [info.st_dev, info.st_ino] != record['owned_identity'] or folder.parent != Path(record['destination']):
                raise ValueError('保存位置已改變，請從工作區查找文件。')
            target = paths.checked(folder, '對話.md')
            if not target.is_file():
                raise ValueError('文件已移動，請從工作區查找。')
            return target


def public_record(record):
    if record is None:
        return dict(state='unknown')
    return {key: record[key] for key in ('operation_id', 'state', 'title', 'path', 'message', 'received', 'next_index') if key in record}
