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
from .approval import ApprovalMode, normalize_mode

MAX_REGISTRY_BYTES = 2 * 1024 * 1024
MODEL_PARAMETERS = ('context_size', 'max_tokens', 'temperature', 'top_p', 'seed', 'gpu_layers')
MAX_MODEL_PROMPT = 1_000_000  # Matches the portable chat attempt prompt bound.


def normalize_model_presets(value, models):
    if not isinstance(value, dict):
        raise ValueError('模型個別設定格式無效。')
    identities = {model['id'] for model in models}
    result = {}
    for model_id, preset in value.items():
        if (model_id not in identities or not isinstance(preset, dict)
                or set(preset) != {'parameters', 'system_prompt'}
                or not isinstance(preset['parameters'], dict)
                or set(preset['parameters']) != set(MODEL_PARAMETERS)):
            raise ValueError('模型個別設定不完整，或已不屬於目前模型。')
        prompt = preset['system_prompt']
        if not isinstance(prompt, str) or len(prompt) > MAX_MODEL_PROMPT:
            raise ValueError('模型預設指示格式無效或過長。')
        settings = normalize_settings(preset['parameters'])
        result[model_id] = {'parameters': {key: settings[key] for key in MODEL_PARAMETERS},
                            'system_prompt': prompt}
    return result


def _folder(value):
    if not isinstance(value, str) or '\x00' in value or (value and not Path(value).is_absolute()):
        raise ValueError('模型資料夾必須是完整路徑。')
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('本地 AI 設定檔不能是捷徑。')
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', dir=path.parent,
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
        raise ValueError('模型檔案已變更，請重新加入後再傳送訊息。')
    return current


class ModelRegistry:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'local_ai' / 'registry.json'
        self.settings = dict(DEFAULT_SETTINGS)
        self.models = []
        self.selected_model = ''
        self.model_folder = ''
        self.model_presets = {}
        self.approval_mode = ApprovalMode.ASK.value
        self.error = ''
        self._writable = True
        if self.path.exists():
            try:
                if self.path.stat().st_size > MAX_REGISTRY_BYTES:
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
                folder = _folder(raw.get('model_folder', ''))
                presets = normalize_model_presets(raw.get('model_presets', {}), models)
                approval_mode = normalize_mode(raw.get('approval_mode', ApprovalMode.ASK.value))
                self.settings, self.models, self.selected_model = settings, models, selected
                self.model_folder, self.model_presets = folder, presets
                self.approval_mode = approval_mode
            except (OSError, ValueError, TypeError) as exc:
                self.error = f'無法讀取本地 AI 模型目錄，原檔已保留：{exc}'
                self._writable = False

    def update(self, *, settings=None, models=None, selected_model=None, model_folder=None, model_presets=None, approval_mode=None):
        if not self._writable:
            raise ValueError(self.error)
        new_settings = normalize_settings(self.settings if settings is None else settings)
        new_models = copy.deepcopy(self.models if models is None else models)
        selected = self.selected_model if selected_model is None else selected_model
        folder = self.model_folder if model_folder is None else _folder(model_folder)
        mode = self.approval_mode if approval_mode is None else normalize_mode(approval_mode)
        # Replaced weights receive a new UUID. Old per-model defaults must not
        # silently transfer to those weights; portable attempts retain theirs.
        identities = {model['id'] for model in new_models}
        presets = ({key: value for key, value in self.model_presets.items() if key in identities}
                   if model_presets is None else model_presets)
        presets = normalize_model_presets(presets, new_models)
        value = {'version': 1, 'settings': new_settings, 'models': new_models,
                 'selected_model': selected, 'model_folder': folder, 'model_presets': presets,
                 'approval_mode': mode}
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')) > MAX_REGISTRY_BYTES:
            raise ValueError('模型目錄過大，請選擇較小的資料夾。原登記保持不變。')
        atomic_json(self.path, value)
        self.settings, self.models, self.selected_model = new_settings, new_models, selected
        self.model_folder = folder
        self.model_presets = presets
        self.approval_mode = mode

    def merge_scanned(self, records, *, baseline=None):
        """Merge validated snapshots once, without dropping manually added files.

        This runs on the service owner thread against the current registry, not
        the scanner's old snapshot. New discoveries never change selection.
        """
        models = copy.deepcopy(self.models)
        by_path = {os.path.normcase(m['path']): m for m in models}
        before = ({os.path.normcase(m['path']): m for m in baseline}
                  if baseline is not None else None)
        selected = self.selected_model
        added = changed = 0
        for found in records:
            record = {key: found[key] for key in ('path', 'name', 'size', 'mtime_ns')}
            existing = by_path.get(os.path.normcase(record['path']))
            # A manual registration accepted after discovery started wins over
            # an older scan, including a newly registered previously unseen path.
            if existing is not None and before is not None and before.get(os.path.normcase(record['path'])) != existing:
                continue
            if existing is None:
                record['id'] = str(uuid4())
                models.append(record)
                by_path[os.path.normcase(record['path'])] = record
                added += 1
            elif any(existing[key] != record[key] for key in ('size', 'mtime_ns')):
                previous_id = existing['id']
                record['id'] = str(uuid4())
                existing.update(record)
                if selected == previous_id:
                    selected = record['id']
                changed += 1
        if added or changed:
            self.update(models=models, selected_model=selected)
        return added, changed

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
