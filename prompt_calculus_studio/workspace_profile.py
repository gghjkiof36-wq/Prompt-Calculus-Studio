"""Portable references to an existing workspace; no file migration or merging."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

PROFILE = 'PCS-WORKSPACE.json'


def _directory(value):
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('找不到原本的資料夾，請選擇仍保有 files 和 data 的 PCS 資料夾。')
    if any(part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction())
           for part in (path, *path.parents)):
        raise ValueError('請選擇實際資料夾。')
    return path.resolve()


def validate(root, data):
    root, data = _directory(root), _directory(data)
    if root == data or root in data.parents or data in root.parents:
        raise ValueError('文件與設定需要使用不同的資料夾。')
    return root, data


def read(base):
    path = Path(base)/PROFILE
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 8192:
        raise ValueError('資料位置設定無法讀取，請重新選擇既有資料。')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('version') != 1:
            raise ValueError('資料位置設定版本不支援，請重新選擇既有資料。')
        return validate(value['root'], value['data'])
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError('資料位置設定無法讀取，請重新選擇既有資料。') from error


def from_installation(base):
    base = _directory(Path(base).absolute())
    return read(base) or validate(base/'files', base/'data')


def locations(base, *, root=None, data=None, env_data=None):
    base = Path(base)
    if root is None and data is None and not env_data:
        previous = read(base)
        if previous:
            return previous
    return Path(root or base/'files').absolute(), Path(data or env_data or base/'data').absolute()


def remember(base, root, data):
    root, data = validate(root, data)
    target = Path(base)/PROFILE
    payload = json.dumps(dict(version=1, root=str(root), data=str(data)), ensure_ascii=False, indent=2)+'\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=base,
                                         prefix='.pcs-workspace-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def choose(base):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    while True:
        selected = QFileDialog.getExistingDirectory(None, '選擇原本的 PCS 資料夾', str(base))
        if not selected:
            return None
        try:
            return from_installation(selected)
        except (OSError, ValueError) as error:
            QMessageBox.warning(None, '無法沿用資料', str(error))
