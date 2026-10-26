"""Cancelable local GGUF discovery, separate from inference and its request slot."""
from __future__ import annotations

from dataclasses import dataclass, field
import copy
import os
from pathlib import Path
import stat
import sys
from threading import Event
import time

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from .registry import inspect_model


def default_model_folder(registry, *, source_root=None, executable=None, frozen=None):
    """Use verified application layout anchors, never a named user directory."""
    source = Path(source_root or Path(__file__).resolve().parents[2])
    executable = Path(executable or sys.executable)
    is_frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    if is_frozen:
        return executable.parent / 'models' / 'local-chat'
    # Managed source worktrees share the existing development model store.
    if source.parent.name == 'worktrees' and (source.parent.parent / 'repository').is_dir():
        return source.parent.parent / 'models' / 'local-chat'
    if source.name == 'repository' and (source.parent / 'models' / 'local-chat').is_dir():
        return source.parent / 'models' / 'local-chat'
    configured = registry.settings.get('runtime_path', '')
    if configured:
        engine = Path(configured)
        for parent in engine.parents:
            if parent.name == 'llama.cpp' and parent.parent.name == 'runtimes' and engine.is_file():
                return parent.parent.parent / 'models' / 'local-chat'
    if (source / 'prompt_calculus_studio').is_dir() and (source / 'run.py').is_file():
        return source / 'models' / 'local-chat'
    return registry.path.parent.parent / 'models' / 'local-chat'


@dataclass
class ScanResult:
    records: list = field(default_factory=list)
    checked: int = 0
    skipped: int = 0
    limit: str = ''
    error: str = ''
    cancelled: bool = False


def scan_folder(folder, cancel, progress, *, max_entries=50000, max_models=1000,
                max_depth=16, max_seconds=30):
    """Read headers only; caps bound work, with cooperative checks between I/O.

    Never follow directory reparse points or file links. A slow operating-system
    call may finish after cancellation; the owner retains the worker until exit.
    """
    result = ScanResult()
    root = Path(folder).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('找不到模型資料夾。')
    pending = [(root, 0)]
    deadline = time.monotonic() + max_seconds
    last_progress = 0.
    while pending:
        if cancel.is_set():
            return ScanResult(cancelled=True)
        if time.monotonic() >= deadline:
            result.limit = '資料夾較大，本次僅完成部分掃描。'
            return result
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if cancel.is_set():
                        return ScanResult(cancelled=True)
                    if result.checked >= max_entries or len(result.records) >= max_models or time.monotonic() >= deadline:
                        result.limit = '資料夾較大，本次僅完成部分掃描。'
                        return result
                    result.checked += 1
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                            result.skipped += 1
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            if depth < max_depth:
                                pending.append((Path(entry.path), depth + 1))
                            else:
                                result.limit = '部分子資料夾層數較深，尚未掃描。'
                        elif stat.S_ISREG(info.st_mode) and entry.name.lower().endswith('.gguf'):
                            path = Path(entry.path).resolve(strict=True)
                            if not path.is_relative_to(root):
                                result.skipped += 1
                                continue
                            result.records.append(inspect_model(path))
                    except (OSError, ValueError):
                        result.skipped += 1
                    now = time.monotonic()
                    if now - last_progress >= .2:
                        progress(result.checked, len(result.records))
                        last_progress = now
        except OSError as exc:
            if directory == root:
                raise ValueError('無法讀取模型資料夾，請確認路徑與存取權限。') from exc
            result.skipped += 1
    if cancel.is_set():
        return ScanResult(cancelled=True)
    result.records.sort(key=lambda item: os.path.normcase(item['path']))
    return result


class _ScanThread(QThread):
    result = Signal(int, object)
    progress = Signal(int, int, int)

    def __init__(self, epoch, folder, scanner, parent, *, create_default=False):
        super().__init__(parent)
        self.epoch, self.folder, self.scanner = epoch, folder, scanner
        self.cancel = Event()
        self.create_default = create_default

    def run(self):
        try:
            if self.create_default and not self.cancel.is_set():
                Path(self.folder).mkdir(parents=True, exist_ok=True)
            result = self.scanner(self.folder, self.cancel,
                                  lambda count, models: self.progress.emit(self.epoch, count, models))
        except (OSError, ValueError) as exc:
            result = ScanResult(error=str(exc))
        except Exception:
            result = ScanResult(error='模型資料夾掃描未完成，請重試。')
        self.result.emit(self.epoch, result)


class ModelLibrary(QObject):
    changed = Signal()

    def __init__(self, registry, parent=None, *, can_apply=lambda: True,
                 scanner=scan_folder, default_folder=None):
        super().__init__(parent)
        self.registry = registry
        self.default_folder = str(default_folder or default_model_folder(registry))
        self.state, self.message = 'idle', ''
        self._can_apply, self._scanner = can_apply, scanner
        self._worker = None
        self._epoch = 0
        self._queued = False
        self._pending = None
        self._baseline = []
        self._closing = False

    @property
    def folder(self):
        return self.registry.model_folder or self.default_folder

    @property
    def busy(self):
        return self._worker is not None or self._pending is not None or self._queued

    def _fail(self, message):
        self.state, self.message = 'error', str(message)
        self.changed.emit()
        return False

    def set_folder(self, folder):
        if self._closing:
            return self._fail('模型資料夾正在停止掃描。')
        try:
            path = Path(folder).expanduser()
            if not path.is_absolute():
                raise ValueError('請選擇完整的模型資料夾路徑。')
            # Directory access/resolve belongs to the worker: a disconnected
            # network location must not block the interface before scanning.
            normalized = os.path.normpath(str(path))
            if normalized != self.registry.model_folder:
                self.registry.update(model_folder=normalized)
        except (OSError, ValueError, TypeError) as exc:
            return self._fail(exc)
        return self._restart()

    def reset_folder(self):
        if self._closing:
            return self._fail('模型資料夾正在停止掃描。')
        try:
            if self.registry.model_folder:
                self.registry.update(model_folder='')
        except (OSError, ValueError) as exc:
            return self._fail(exc)
        return self._restart()

    def refresh(self):
        if self._closing:
            return self._fail('模型資料夾正在停止掃描。')
        if self.busy:
            return False
        return self._restart()

    def _restart(self):
        self._epoch += 1
        self._pending = None
        self._queued = True
        if self._worker is not None:
            self._worker.cancel.set()
            self.state, self.message = 'cancelling', '正在停止上次掃描…'
            self.changed.emit()
            return True
        return self._start()

    def _start(self):
        self._queued = False
        # Explicit refresh/reset may create the default directory on the worker;
        # merely opening a page never creates folders or discovers files.
        self._baseline = copy.deepcopy(self.registry.models)
        worker = _ScanThread(self._epoch, self.folder, self._scanner, self,
                             create_default=not self.registry.model_folder)
        self._worker = worker
        worker.result.connect(self._on_result, Qt.ConnectionType.QueuedConnection)
        worker.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        self.state, self.message = 'scanning', '正在掃描模型資料夾…'
        worker.start()
        self.changed.emit()
        return True

    def cancel(self):
        if not self.busy:
            return False
        self._epoch += 1
        self._pending, self._queued = None, False
        if self._worker is not None:
            self._worker.cancel.set()
        self.state = 'cancelling' if self._worker is not None else 'idle'
        self.message = '正在停止掃描…' if self._worker is not None else '已取消掃描。'
        self.changed.emit()
        return True

    @Slot(int, object)
    def _on_result(self, epoch, result):
        if epoch == self._epoch and not self._closing:
            self._pending = result

    @Slot(int, int, int)
    def _on_progress(self, epoch, count, models):
        if epoch == self._epoch and not self._closing:
            self.message = f'已檢查 {count} 項，找到 {models} 個模型…'
            self.changed.emit()

    @Slot()
    def _on_finished(self):
        worker = self.sender()
        if worker is not self._worker:
            return
        self._worker = None
        worker.deleteLater()
        if self._closing:
            self.state, self.message = 'idle', ''
        elif self._queued:
            self._start()
            return
        elif self._pending is not None:
            self.accept_pending()
            return
        else:
            self.state, self.message = 'idle', '已取消掃描。'
        self.changed.emit()

    @Slot()
    def accept_pending(self):
        if self._closing or self._worker is not None or self._pending is None:
            return
        result = self._pending
        if result.error or result.cancelled:
            self._pending = None
            if result.error:
                self._fail(result.error)
            else:
                self.state, self.message = 'idle', '已取消掃描。'
                self.changed.emit()
            return
        if not self._can_apply():
            if self.state != 'waiting':
                self.state, self.message = 'waiting', '掃描完成，待目前對話操作結束後更新模型清單。'
                self.changed.emit()
            return
        self._pending = None  # Reentrant changed signals cannot apply twice.
        try:
            added, updated = self.registry.merge_scanned(result.records, baseline=self._baseline)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self._fail(f'模型清單未更新：{exc}')
            return
        self.state = 'warning' if result.skipped or result.limit else 'idle'
        self.message = f'找到 {len(result.records)} 個模型；新增 {added} 個，更新 {updated} 個。'
        if result.skipped:
            self.message += f' {result.skipped} 項未支援或無法讀取，已略過。'
        if result.limit:
            self.message += ' ' + result.limit
        self.changed.emit()

    def shutdown(self):
        if not self._closing:
            self._closing = True
            self.cancel()
        return self._worker is None

    def resume_after_close_abort(self):
        if self._worker is not None:
            return False
        self._closing = False
        self.state, self.message = 'idle', ''
        return True
