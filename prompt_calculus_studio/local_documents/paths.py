"""Explicit root boundary and Windows-compatible names on every platform."""
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat

from .schema import fail, MAX_BYTES

RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)', re.I)


def relative_parts(value):
    if not isinstance(value, str) or not value or '\\' in value or PureWindowsPath(value).drive or PurePosixPath(value).is_absolute():
        fail('path', '請使用根內的相對路徑。')
    parts = value.split('/')
    for part in parts:
        if (not part or part in ('.', '..') or part[-1] in '. ' or
                any(ord(c) < 32 or c in '<>:"|?*' for c in part) or RESERVED.match(part) or
                part.casefold() == '.obsidian' or len(part.encode('utf-16-le')) > 510):
            fail('path', '不支援的檔案名稱或路徑。', component=part)
    return parts


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def root_path(root):
    path = Path(root).absolute()
    if any(part.casefold() == '.obsidian' for part in path.parts):
        fail('path', '不將 .obsidian 當成文件根。')
    if not path.is_dir():
        fail('path', '接入根必須是已存在的資料夾。')
    for component in (path, *path.parents):
        if is_link(component):
            fail('path', '接入根不可經過連結或重解析點。')
    return path.resolve(strict=True)


def checked(root, relative, *, new=False):
    base = root_path(root)
    path = base
    parts = relative_parts(relative)
    for index, part in enumerate(parts):
        if path.exists():
            if not path.is_dir():
                fail('path', '父路徑不是資料夾。')
            matches = [p for p in path.iterdir() if p.name.casefold() == part.casefold()]
            if len(matches) > 1:
                fail('collision', '路徑有大小寫衝突。')
            if matches:
                if new and index == len(parts) - 1:
                    fail('exists', '同名檔案已存在。')
                path = matches[0]
            else:
                path = path / part
        else:
            path = path / part
        if os.path.lexists(path) and is_link(path):
            fail('path', '不跟隨連結或重解析點。')
    if not path.resolve().is_relative_to(base):
        fail('path', '路徑超出接入根。')
    return path


def read_bytes(path, limit=MAX_BYTES):
    with open(path, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            fail('limit', '不是一般檔案或檔案過大。')
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(data) > limit:
            fail('limit', '檔案過大。')
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            fail('conflict', '讀取時檔案已改動。')
        return data, after
