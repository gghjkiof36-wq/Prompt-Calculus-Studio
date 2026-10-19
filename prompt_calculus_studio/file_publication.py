"""Recoverable byte publication, including non-cooperative external writers.

QSaveFile stages a candidate only. The final Windows replacement keeps the
actual displaced file; a preflight comparison is not a compare-and-swap.
"""
from dataclasses import dataclass
from contextlib import ExitStack
import os
import uuid

from PySide6.QtCore import QIODevice
from shiboken6 import delete, isValid
from .local_documents import paths, windows
from .local_documents.directory_pin import pinned_parent


@dataclass(frozen=True)
class WriteResult:
    status: str
    recovery_paths: tuple = ()
    error: str = ''
    uncertain: bool = False


class PublicationError(ValueError):
    def __init__(self, result):
        self.result = result
        super().__init__(result.error)


def _write(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _matches(stream, expected):
    for offset in range(0, len(expected), 65536):
        if stream.read(min(65536, len(expected)-offset)) != expected[offset:offset+65536]:
            return False
    return not stream.read(1)


def checked_replace(path, before, after, writer, *, max_bytes=None):
    """Publish exact bytes or retain a candidate and every displaced version.

There is deliberately no rollback after ReplaceFile: another writer may have
already published a newer version. An uncertain result must stop autosave and
must not be treated as an ordinary, side-effect-free transaction failure.
"""
    retained = []
    attempted = False
    conflict = False
    output = None
    protection = ExitStack()
    try:
        # Pin the original spelling before checked() can resolve any path.
        # Keep the pin through error diagnostics and recovery-copy cleanup.
        protection.enter_context(pinned_parent(path))
        path = paths.checked(path.parent, path.name)
        if max_bytes is not None and len(after) > max_bytes:
            raise ValueError('文件超過 8 MiB，尚未保存。')
        if os.name != 'nt':
            raise OSError('目前系統無法保留被替換的版本，尚未保存。')
        if before is None:
            unchanged = not path.exists()
        else:
            with path.open('rb') as stream:unchanged = _matches(stream, before)
        if not unchanged:
            conflict = True
            raise ValueError('檔案已被外部修改；本機稿尚未覆寫原檔。')
        prefix = path.name + '.recovery-' + uuid.uuid4().hex
        candidate, temporary, original, external = [path.with_name(prefix + '.' + role)
            for role in ('candidate', 'temporary', 'before', 'external')]
        retained = [candidate, temporary, original, external]
        output = writer(str(candidate))
        output.setDirectWriteFallback(False)
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        if output.write(after) != len(after):
            output.cancelWriting()
            raise OSError('無法完整保存本機稿。')
        if not output.commit():
            raise OSError(output.errorString() or '本機稿暫存失敗。')
        # Keep an independent, durable candidate even if native replacement
        # moves the temporary file and then fails partway through.
        _write(temporary, after)
        if before is None:
            paths.checked(path.parent, path.name, new=True)
            attempted = True
            # On Windows rename is exclusive, unlike replace. A destination
            # created since the preflight must remain untouched.
            os.rename(temporary, path)
            with windows.reader(path) as published:
                if not _matches(published, after):
                    raise ValueError('保存後文件再次被修改；已保留本機稿，未回滾外部內容。')
        else:
            with windows.reader(path) as source:
                if not _matches(source, before):
                    conflict = True
                    raise ValueError('檔案已被外部修改；本機稿尚未覆寫原檔。')
                _write(original, before)
                paths.checked(path.parent, path.name)
                attempted = True
                windows.replace(path, temporary, external)
                with windows.reader(external) as displaced:
                    if not _matches(displaced, before):
                        raise ValueError('保存時發現外部修改；兩份內容已保留，請檢視回復版本。')
                    paths.checked(path.parent, path.name)
                    with windows.reader(path) as published:
                        if not _matches(published, after):
                            raise ValueError('保存後文件再次被修改；已保留本機稿，未回滾外部內容。')
        # Successful autosaves do not accumulate recovery copies. Failure
        # cleanup is intentionally absent: even a failed ReplaceFile can have
        # moved the original to its backup name.
        for item in retained:
            try:
                item.unlink(missing_ok=True)
            except OSError:
                pass  # A leftover recovery copy cannot invalidate a save.
        return WriteResult('saved')
    except (OSError, ValueError) as error:
        copies = tuple(str(p) for p in retained if p.exists())
        message = str(error) or '保存未完成，本機稿已保留。'
        if copies:
            message += '\n保留的回復版本：\n' + '\n'.join(copies)
        result = WriteResult('conflict' if attempted or conflict else 'failed', copies, message, attempted)
        raise PublicationError(result) from error
    finally:
        try:
            # A staging failure can keep the writer alive in an exception
            # traceback. Destroy its native temporary file before unpinning
            # the directories, not later during Python garbage collection.
            if output is not None and isValid(output):
                delete(output)
        finally:
            protection.close()


def block_recovery(page, message, locations):
    """Keep all views of a partly applied multi-file operation stopped."""
    page.operation_error = True
    page.error = message
    page.recovery_paths = list(dict.fromkeys([*getattr(page, 'recovery_paths', ()), *map(str, locations)]))
    page.last_result = WriteResult('conflict', tuple(page.recovery_paths), message, True)
    page.document().setModified(True)
