"""Immutable display-only identities and budgets. This module is Qt-free.

Receipt identity fences delivery; DecodeKey deliberately omits the consumer so
two cards requesting the same pixels share one read. Neither is persisted.
"""
from dataclasses import dataclass, field
from pathlib import Path
from os import stat as file_stat


# REV3 uses existing receipt dimensions, or the caller's encoded-size stamp.
# No extra header read for classification. See qa/.../p6-1/rev3/REPORT.md.
WORKER_ENCODED_BYTES = 2048
WORKER_BMP_BYTES = 32 * 1024
WORKER_SOURCE_PIXELS = 256 * 256
# 2/4/8 still lose on 512px source cards (notably WebP); 16 is measured
# in rev4/batch-16-warm. Counts below this retain the same-turn legacy path.
WORKER_BATCH_MIN = 16


def worker_batch(paths, sources=None):
    """Classify one display load, never individual arrivals across event turns.

    The small/one-image legacy path does not enter the scheduler. Header sizes
    come from existing receipts; missing metadata uses one cheap stat only.
    """
    if len(paths) < WORKER_BATCH_MIN:
        return set()
    sources = sources if sources is not None else [None] * len(paths)
    selected = {i for i, (path, source) in enumerate(zip(paths, sources))
                if path and use_worker(path, source=source)}
    return selected if len(selected) >= WORKER_BATCH_MIN else set()


def live_result(items, pending):
    """Only the still-owned D-B acceptance refresh; history stays independent."""
    return bool(pending) and any(item.get('reference', {}).get('attempt') in pending for item in items)


def decoded_bytes(source):
    """Conservative codec working-set estimate from an existing receipt only."""
    if source is not None:
        width,height=source.get('width',0),source.get('height',0)
        if type(width) is int and type(height) is int and width>0 and height>0:
            return width*height*8
    return 0


def use_worker(path, size=None, source=None):
    # Imported sources and D-B receipts already carry validated header sizes.
    # Do not stat or reopen a header just to classify those immutable sources.
    if source is not None:
        width,height=source.get('width',0),source.get('height',0)
        if type(width) is int and type(height) is int and width>0 and height>0:
            return width*height>WORKER_SOURCE_PIXELS
    if size is None:
        try:
            size = file_stat(path).st_size
        except OSError:
            return False
    limit = WORKER_BMP_BYTES if str(path).lower().endswith('.bmp') else WORKER_ENCODED_BYTES
    return size > limit


def stamp(path):
    try:
        stat = Path(path).stat()
        return stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns, stat.st_ino
    except OSError:
        return None


@dataclass(frozen=True)
class Limits:
    workers: int = 4
    decoding_bytes: int = 64 * 1024 * 1024
    pending_count: int = 256
    pending_bytes: int = 256 * 1024 * 1024
    completed_count: int = 16
    completed_bytes: int = 128 * 1024 * 1024
    cache_bytes: int = 64 * 1024 * 1024
    apply_ms: float = 2.

    def __post_init__(self):
        if not (1 <= self.workers <= self.completed_count and
                self.completed_bytes >= self.workers * 2048 * 2048 * 8 and
                self.pending_count > 0 and self.pending_bytes > 0 and
                self.cache_bytes >= 0 and self.apply_ms > 0 and self.decoding_bytes > 0):
            raise ValueError('Invalid preview budgets')


@dataclass(frozen=True)
class DecodeKey:
    scope: tuple
    path: str
    version: tuple | None
    source: str
    width: int
    height: int
    dpr: float = 1.
    variant: str = 'fit'
    # Admission cost is not part of pixel identity. Missing metadata reserves
    # 32 MiB; a larger-than-budget image runs alone, with Qt's existing 128 MiB
    # per-reader allocation limit. No quality/target-size change is involved.
    raw_bytes: int = field(default=0, compare=False)

    def __post_init__(self):
        if not (1 <= self.width <= 2048 and 1 <= self.height <= 2048 and
                0 < self.dpr <= 8 and self.variant in ('fit', 'shrink', 'legacy-icon', 'legacy-thumb') and
                type(self.raw_bytes) is int and self.raw_bytes >= 0):
            raise ValueError('Unsupported preview transform')

    @property
    def reservation(self):
        return (self.version[1] if self.version else 0) + self.width * self.height * 8


@dataclass(frozen=True)
class Identity:
    scope: tuple
    consumer: int
    serial: int
    card: object
    key: DecodeKey
