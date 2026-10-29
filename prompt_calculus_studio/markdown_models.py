"""Per-file text-assist model preferences, separate from Markdown and chat."""
import json
from pathlib import Path
import weakref

from PySide6.QtCore import QIODevice, QLockFile, QSaveFile
from shiboken6 import isValid

from .workspace_locations import path_key


_MISSING = object()
_MAX_BYTES = 2 * 1024 * 1024


def _read_mapping(path):
    if path.is_symlink():
        raise ValueError('文件模型設定不能是捷徑。')
    try:
        with path.open('rb') as stream:
            raw = stream.read(_MAX_BYTES + 1)
    except FileNotFoundError:
        return {}
    if len(raw) > _MAX_BYTES:
        raise ValueError('文件模型設定過大，無法讀取。')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise ValueError('文件模型設定無法讀取；原有設定已保留。') from error
    if (not isinstance(value, dict) or type(value.get('version')) is not int
            or value['version'] != 1 or not isinstance(value.get('models'), dict)):
        raise ValueError('文件模型設定版本或格式不受支援；原有設定已保留。')
    models = value['models']
    if any(not key or not Path(key).is_absolute() or not isinstance(model, str)
           or '\x00' in model for key, model in models.items()):
        raise ValueError('文件模型設定格式不正確；原有設定已保留。')
    return models


def _write_mapping(path, models):
    raw = json.dumps(dict(version=1, models=models), ensure_ascii=False, indent=2).encode('utf-8')
    if len(raw) > _MAX_BYTES:
        raise ValueError('文件模型設定過大，無法保存。')
    output = QSaveFile(str(path))
    output.setDirectWriteFallback(False)
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise ValueError('文件模型尚未保存，請確認設定資料夾可以寫入。')
    if output.write(raw) != len(raw):
        output.cancelWriting()
        raise ValueError('文件模型尚未保存，請稍後重試。')
    if not output.commit():
        raise ValueError('文件模型尚未保存，請稍後重試。')


def _stored_model(path, key, fallback, explicit=_MISSING):
    """Read and update under one short lock; never replace an unreadable file."""
    lock = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(path) + '.lock')
        if not lock.tryLock(0):
            raise ValueError('文件模型設定正在使用中，請稍後重試。')
        models = _read_mapping(path)
        previous = models.get(key, _MISSING)
        selected = explicit if explicit is not _MISSING else models.get(key, fallback)
        if previous != selected:
            models[key] = selected
            _write_mapping(path, models)
        return selected, previous
    except OSError as error:
        raise ValueError('文件模型設定無法讀取或保存；原有設定已保留。') from error
    finally:
        if lock is not None and lock.isLocked():
            lock.unlock()


class _Binding:
    def __init__(self, path):
        self.path = path
        self.key = None
        self.model = ''
        self.pending_error = None
        self.surfaces = weakref.WeakSet()
        self.titles = weakref.WeakSet()

    def attach(self, surface):
        self.surfaces.add(surface)
        title = surface.page.file_title
        if title not in self.titles:
            title.filenameChanged.connect(self.path_changed)
            self.titles.add(title)

    def resolve(self, surface, default='', explicit=_MISSING):
        key = path_key(surface.page.path)
        # An open shared buffer retains its choice when saved under a new path.
        fallback = self.model if self.key is not None else default
        if self.key is not None and key != self.key and explicit is _MISSING:
            explicit = self.model
        model, previous = _stored_model(self.path, key, fallback, explicit)
        self.key, self.model, self.pending_error = key, model, None
        return model, previous

    def path_changed(self, *_):
        if self.key is None:
            return
        for surface in tuple(self.surfaces):
            try:
                if path_key(surface.page.path) != self.key:
                    self.resolve(surface)
                return
            except RuntimeError:
                continue  # A closed Qt view may be awaiting Python collection.
            except ValueError as error:
                # File operations must finish normally. The next public call
                # retries the move and reports its ValueError through the UI.
                self.pending_error = error
                return


def _binding(surface):
    workspace = surface.page.file_title.workspace
    if workspace is None or not getattr(workspace, 'data', None):
        raise ValueError('這份文件沒有可保存模型設定的工作區。')
    path = Path(workspace.data) / 'md-models.json'
    buffer = surface.page.buffer
    binding = getattr(buffer, '_markdown_model_binding', None)
    if binding is None:
        binding = _Binding(path)
        buffer._markdown_model_binding = binding
    elif path_key(binding.path) != path_key(path):
        raise ValueError('這份文件的模型設定不屬於目前工作區。')
    binding.attach(surface)
    return binding


def _validate_model(service, model_id):
    if not isinstance(model_id, str):
        raise ValueError('請選擇有效的文字協作模型。')
    if model_id and not any(model.get('id') == model_id for model in service.models):
        raise ValueError('這份文件選用的模型已不在清單中，請從右上角「更多」重新選擇。')


def markdown_model_id(surface, service):
    """Return a registered ID or ''. A missing saved model raises ValueError.

    On first use only, snapshot the chat selection (including no selection).
    The persisted absolute path survives new buffer/document IDs on reopening.
    """
    default = service.selected_model or ''
    binding = _binding(surface)
    model, _ = binding.resolve(surface, default)
    _validate_model(service, model)
    return model


def set_markdown_model(surface, service, model_id):
    """Persist before notifying shared views; never select/load a chat model."""
    _validate_model(service, model_id)
    binding = _binding(surface)
    _, previous = binding.resolve(surface, explicit=model_id)
    if previous == model_id:
        return
    for page in tuple(surface.page.buffer.views):
        if not isValid(page):
            continue
        other = getattr(page, 'md_surface', None)
        assist = getattr(other, 'inline_assist', None)
        if assist is not None:
            assist.model_changed()
