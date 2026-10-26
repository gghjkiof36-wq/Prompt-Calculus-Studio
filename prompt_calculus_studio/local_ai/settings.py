"""Validated settings shared by the UI, request snapshots and runtime adapter."""
from __future__ import annotations

import math
import os
from pathlib import Path
import sys


DEFAULT_SETTINGS = {
    'runtime_path': '',
    'context_size': 4096,
    'max_tokens': 1024,
    'temperature': 0.7,
    'top_p': 0.95,
    'seed': -1,
    'gpu_layers': 0,
    'idle_minutes': 10,
}
LOAD_KEYS = ('runtime_path', 'context_size', 'gpu_layers')
SAMPLING_KEYS = ('max_tokens', 'temperature', 'top_p', 'seed')


def normalize_settings(values, *, base=None):
    if not isinstance(values, dict):
        raise ValueError('本地 AI 設定格式無效。')
    if set(values) - set(DEFAULT_SETTINGS):
        raise ValueError('本地 AI 設定包含不支援的項目。')
    result = dict(DEFAULT_SETTINGS if base is None else base)
    result.update(values)
    path = result['runtime_path']
    if not isinstance(path, str) or '\x00' in path:
        raise ValueError('引擎路徑無效。')
    result['runtime_path'] = path.strip()
    ranges = {
        'context_size': (256, 1048576), 'max_tokens': (1, 262144),
        'seed': (-1, 4294967295), 'gpu_layers': (-1, 999),
        'idle_minutes': (0, 1440),
    }
    for key, (low, high) in ranges.items():
        value = result[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{key} 必須介於 {low} 到 {high}。')
    for key, low, high in (('temperature', 0., 2.), ('top_p', 0.01, 1.)):
        value = result[key]
        if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
            raise ValueError(f'{key} 必須介於 {low} 到 {high}。')
        result[key] = float(value)
    if result['max_tokens'] >= result['context_size']:
        raise ValueError('回覆長度必須小於上下文長度，並為輸入保留空間。')
    return result


def resolve_runtime(configured=''):
    """Resolve only explicitly supplied or bundled executables; never PATH/services."""
    name = 'llama-server.exe' if os.name == 'nt' else 'llama-server'
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ValueError('找不到指定的 llama-server，請重新選擇引擎檔案。')
        if path.name.lower() != name:
            raise ValueError(f'請選擇 {name} 引擎檔案。')
        return path.resolve()
    roots = [Path(sys.executable).parent, Path(__file__).resolve().parents[2]]
    bundled = getattr(sys, '_MEIPASS', None)
    if bundled:
        roots.insert(0, Path(bundled))
    for root in roots:
        path = root / 'runtimes' / 'llama.cpp' / name
        if path.is_file():
            return path.resolve()
    raise ValueError('尚未安裝本地推論引擎。請在設定中選擇 llama-server 檔案。')


def validate_messages(value):
    if not isinstance(value, list) or not value or len(value) > 10000:
        raise ValueError('對話訊息為空或格式無效。')
    messages = []
    total = 0
    for item in value:
        if not isinstance(item, dict) or set(item) != {'role', 'content'}:
            raise ValueError('此版本只接受純文字對話。')
        role, content = item['role'], item['content']
        if role not in ('system', 'user', 'assistant') or not isinstance(content, str):
            raise ValueError('對話角色或內容無效。')
        total += len(content)
        if total > 4 * 1024 * 1024:
            raise ValueError('這次對話過大，請減少輸入內容。')
        messages.append({'role': role, 'content': content})
    if messages[-1]['role'] != 'user' or not messages[-1]['content'].strip():
        raise ValueError('請先輸入要傳送的訊息。')
    return messages
