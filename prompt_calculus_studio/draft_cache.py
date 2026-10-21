"""Local recovery records, independent of portable document validation."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


class DraftCache:
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        identity = hashlib.sha256(str(self.directory).casefold().encode()).hexdigest()
        self.roots = (self.directory/'recovery', Path(tempfile.gettempdir())/'pcs-recovery'/identity)

    @staticmethod
    def name(key):
        return hashlib.sha256(key.encode('utf-8')).hexdigest()+'.json'

    def read(self, key):
        records = []
        for root in self.roots:
            path = root/self.name(key)
            try:
                from .local_documents.paths import root_path, is_link
                root_path(root)
                if is_link(path) or path.stat().st_size > 128*1024*1024:continue
                value = json.loads(path.read_text(encoding='utf-8'))
                if value.get('key') == key and isinstance(value.get('value'),dict):
                    records.append((path.stat().st_mtime_ns, value['value']))
            except (OSError, ValueError, KeyError, TypeError, AttributeError):pass
        return max(records, key=lambda record: record[0])[1] if records else None

    def write(self, key, value):
        raw = json.dumps(dict(key=key, value=value), ensure_ascii=False, allow_nan=False).encode('utf-8')
        last = None
        for root in self.roots:
            temporary = None
            try:
                root.mkdir(parents=True, exist_ok=True)
                from .local_documents.paths import root_path
                root_path(root)
                with tempfile.NamedTemporaryFile(dir=root, prefix='.draft-', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(raw);stream.flush();os.fsync(stream.fileno())
                target = root/self.name(key)
                os.replace(temporary, target)
                return target
            except (OSError, ValueError) as error:last = error
            finally:
                if temporary is not None:
                    try:temporary.unlink(missing_ok=True)
                    except OSError:pass
        raise OSError('無法保存恢復副本：'+str(last))

    def clear(self, key):
        for root in self.roots:
            try:
                from .local_documents.paths import root_path
                root_path(root)
                (root/self.name(key)).unlink(missing_ok=True)
            except (OSError,ValueError):pass
