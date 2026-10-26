"""Small local model/settings registry. Weights remain at their original path."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import UUID, uuid4

from .settings import DEFAULT_SETTINGS, normalize_settings


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('本地 AI 設定檔不能是捷徑。')
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.local-ai-', suffix='.tmp', delete=False) as stream:
            name = stream.name
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def inspect_model(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != '.gguf':
        raise ValueError('請選擇既有的 GGUF 模型檔案。')
    split = re.search(r'-\d{5}-of-(\d{5})\.gguf$', path.name, re.IGNORECASE)
    if split and int(split.group(1)) > 1:
        raise ValueError('此版尚未支援分片模型，請選擇單一 GGUF 檔案。')
    with path.open('rb') as stream:
        header = stream.read(8)
    if len(header) != 8 or header[:4] != b'GGUF' or int.from_bytes(header[4:], 'little') not in (2, 3):
        raise ValueError('檔案不是支援的 GGUF 模型，或尚未下載完整。')
    stat = path.stat()
    return {'path': str(path), 'name': path.stem, 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def validate_model(record):
    current = inspect_model(record['path'])
    if any(current[key] != record[key] for key in ('path', 'size', 'mtime_ns')):
        raise ValueError('模型檔案已變更，請重新加入後再載入。')
    return current


class ModelRegistry:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'local_ai' / 'registry.json'
        self.settings = dict(DEFAULT_SETTINGS)
        self.models = []
        self.selected_model = ''
        self.error = ''
        self._writable = True
        if self.path.exists():
            try:
                if self.path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError('模型目錄過大。')
                raw = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(raw, dict) or raw.get('version') != 1:
                    raise ValueError('模型目錄版本不支援。')
                settings = normalize_settings(raw.get('settings', {}))
                models = raw.get('models', [])
                if not isinstance(models, list):
                    raise ValueError('模型目錄格式無效。')
                seen = set()
                for model in models:
                    if (not isinstance(model, dict) or not all(isinstance(model.get(k), str)
                            and model[k] for k in ('id', 'name', 'path'))
                            or type(model.get('size')) is not int
                            or type(model.get('mtime_ns')) is not int
                            or model['id'] in seen):
                        raise ValueError('模型目錄項目無效。')
                    UUID(model['id'])
                    seen.add(model['id'])
                selected = raw.get('selected_model', '')
                if not isinstance(selected, str) or (selected and selected not in seen):
                    raise ValueError('選用模型已不在目錄中。')
                self.settings, self.models, self.selected_model = settings, models, selected
            except (OSError, ValueError, TypeError) as exc:
                self.error = f'無法讀取本地 AI 模型目錄，原檔已保留：{exc}'
                self._writable = False

    def update(self, *, settings=None, models=None, selected_model=None):
        if not self._writable:
            raise ValueError(self.error)
        new_settings = normalize_settings(self.settings if settings is None else settings)
        new_models = copy.deepcopy(self.models if models is None else models)
        selected = self.selected_model if selected_model is None else selected_model
        atomic_json(self.path, {'version': 1, 'settings': new_settings,
                               'models': new_models, 'selected_model': selected})
        self.settings, self.models, self.selected_model = new_settings, new_models, selected

    def register(self, path):
        record = inspect_model(path)
        models = copy.deepcopy(self.models)
        existing = next((m for m in models if os.path.normcase(m['path']) ==
                         os.path.normcase(record['path'])), None)
        if existing:
            unchanged = all(record[key] == existing[key] for key in ('size', 'mtime_ns'))
            # A replaced weight file is a different identity even at the same
            # location; existing chat receipts keep their original model ID.
            record['id'] = existing['id'] if unchanged else str(uuid4())
            existing.update(record)
        else:
            record['id'] = str(uuid4())
            models.append(record)
        self.update(models=models, selected_model=record['id'])
        return copy.deepcopy(record)
