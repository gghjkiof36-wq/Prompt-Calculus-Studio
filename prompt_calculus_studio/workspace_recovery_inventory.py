"""Bounded, read-only discovery of missing documents in this workspace's cache."""
import json
import os
from pathlib import Path
import re
import stat

from .local_documents import paths
from .workspace_recovery import key


MAX_ENTRIES = 512
MAX_SCAN_BYTES = 128 * 1024 * 1024
_CACHE_NAME = re.compile(r'[0-9a-f]{64}\.json\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_KINDS = {'.md': 'md', '.pcs-category': 'category', '.pcs-workflow': 'workflow'}


def _missing_original(value, record_key):
    location = value.get('path')
    if not isinstance(location, str) or len(location) > 32767:
        return None
    original = Path(location)
    if (not original.is_absolute() or '..' in original.parts or key(original) != record_key
            or any(part.casefold() == '.obsidian' for part in original.parts)):
        return None
    expected = _KINDS.get(original.suffix.casefold())
    if expected is None or value.get('kind') != expected:
        return None
    digest = value.get('sha')
    if not isinstance(digest, str) or not _SHA.fullmatch(digest):
        return None
    try:
        paths.relative_parts(original.name)
        # lstat distinguishes a missing file from a broken symlink. Checking
        # ancestors never enumerates or reads the user's content directories.
        try:
            original.lstat()
            return None
        except (FileNotFoundError, NotADirectoryError):
            pass
        for parent in original.parents:
            try:
                if paths.is_link(parent):
                    return None
            except (FileNotFoundError, NotADirectoryError):
                continue
        # A disconnected drive or a former parent replaced by a regular file
        # still has a recoverable draft. Only the new destination must exist.
        return original
    except (OSError, ValueError):
        pass
    return None


def missing_records(recovery):
    """Return unique missing paths; never create, remove or adopt cached data.

    Both locations are owned by DraftCache for this workspace. The byte and
    entry budgets include rejected records, so malformed caches cannot cause
    unbounded startup work. Content is validated again by the recovery writer.
    """
    remaining, entries, records = MAX_SCAN_BYTES, 0, {}
    for folder in recovery.cache.roots:
        if entries >= MAX_ENTRIES or remaining <= 0:
            break
        try:
            root = paths.root_path(folder)
            with os.scandir(root) as listing:
                for entry in listing:
                    if entries >= MAX_ENTRIES or remaining <= 0:
                        break
                    entries += 1
                    if not _CACHE_NAME.fullmatch(entry.name):
                        continue
                    path = root / entry.name
                    try:
                        before = path.lstat()
                        if (not stat.S_ISREG(before.st_mode) or paths.is_link(path)
                                or before.st_size < 1 or before.st_size + 1 > remaining):
                            continue
                        remaining -= before.st_size + 1
                        raw, after = paths.read_bytes(path, before.st_size)
                        if ((before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                                or paths.is_link(path)):
                            continue
                        envelope = json.loads(raw)
                        if not isinstance(envelope, dict):
                            continue
                        record_key, value = envelope.get('key'), envelope.get('value')
                        if (not isinstance(record_key, str) or not record_key.startswith('document:')
                                or recovery.cache.name(record_key) != entry.name or not isinstance(value, dict)):
                            continue
                        previous = records.get(record_key)
                        if previous is None or after.st_mtime_ns > previous[0]:
                            records[record_key] = after.st_mtime_ns, value
                    except (OSError, ValueError, TypeError, RecursionError):
                        continue
        except (OSError, ValueError):
            continue
    ordered = sorted(records.items(), key=lambda item: (-item[1][0], item[0]))
    return [original for record_key, (_, value) in ordered
            if (original := _missing_original(value, record_key)) is not None]
