"""Local file operations: validate first, publish without replacing another file."""
import os
from pathlib import Path
import re
import uuid
from urllib.parse import unquote, urlsplit

from .local_documents import assets, io, paths, schema
from .workspace_locations import KINDS, INTERNAL_NAMES


def named_path(parent, name, suffix=''):
    if not name or '/' in name or '\\' in name:
        raise ValueError('請輸入名稱，不含路徑。')
    paths.relative_parts(name)
    if name.casefold() in INTERNAL_NAMES:
        raise ValueError('這個名稱保留給程式資料，請使用其他名稱。')
    if suffix and not name.casefold().endswith(suffix.casefold()):
        name += suffix
    if suffix and not name[:-len(suffix)].strip():
        raise ValueError('請輸入副檔名之前的名稱。')
    paths.relative_parts(name)
    return paths.root_path(parent) / name


def stamp(path):
    """No links, and enough identity to detect a changed source during a dialog."""
    path = paths.checked(path.parent, path.name)
    if path.is_dir():
        result = {}
        for directory, folders, files in os.walk(path, followlinks=False):
            for name in [*folders, *files]:
                child = Path(directory)/name
                if paths.is_link(child):
                    raise ValueError('資料夾包含連結或重解析點，未搬動：' + str(child))
                info = child.stat()
                result[child.relative_to(path).as_posix()] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        info = path.stat()
        return (info.st_dev, info.st_ino, result)
    raw, info = paths.read_bytes(path, schema.MAX_BYTES)
    return (info.st_dev, info.st_ino, schema.digest(raw))


def relocate(path, source, destination):
    if path == source:
        return destination
    return destination/path.relative_to(source) if source in path.parents else path


def markdown_references(text):
    # Fenced/inline code is literal text. Unresolved wiki syntax is deliberately
    # rejected on relocation; this batch does not rewrite an Obsidian vault.
    text = re.sub(r'(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1\s*$', '', text)
    text = re.sub(r'`+[^`]*`+', '', text)
    if re.search(r'\[\[[^\]]+\]\]', text):
        raise ValueError('含有尚未支援的位置引用 [[…]]；請保留目前資料夾，或先調整引用。')
    values = re.findall(r'!?\[[^\]]*\]\(\s*(<[^>]*>|[^\s)]+)', text)
    values += re.findall(r'(?m)^\s*\[[^\]]+\]:\s*(<[^>]*>|\S+)', text)
    values += re.findall(r'(?:src|href)\s*=\s*[\"\']([^\"\']+)', text, re.I)
    for value in values:
        value = value.strip('<>')
        address = urlsplit(value)
        if address.scheme or address.netloc or value.startswith(('/', '#', '\\')):
            continue
        relative = unquote(address.path)
        if relative:
            yield relative


def validate_markdown_location(text, old_file, new_file, moving=None):
    if old_file.parent == new_file.parent and moving is None:
        return
    for reference in markdown_references(text):
        old = (old_file.parent/reference).resolve()
        new = (new_file.parent/reference).resolve()
        expected = relocate(old, *moving) if moving else old
        if new == expected and old.exists():
            continue
        disappears = moving is not None and relocate(new, *moving) != new
        if not disappears and old.is_file() and new.is_file() and not paths.is_link(new):
            if paths.read_bytes(old, assets.MAX_ASSET_BYTES)[0] == paths.read_bytes(new, assets.MAX_ASSET_BYTES)[0]:
                continue
        raise ValueError('位置變更後無法確認這個 Markdown 引用：' + reference + '\n請選擇可解析引用的位置；原檔尚未移動。')


class PublishedMarkdownError(OSError):
    """The destination is complete, but cleanup failed after publication."""


def create_markdown(target, raw):
    paths.checked(target.parent, target.name, new=True)
    temporary = target.with_name('.pcs-new-' + uuid.uuid4().hex + '.tmp')
    published = False
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        paths.checked(target.parent, target.name, new=True)
        os.link(temporary, target)
        published = True
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as error:
            if published:
                raise PublishedMarkdownError('新檔已寫入，但暫存檔尚未清理：' + str(temporary)) from error
            raise


def prepare_move(source, target, loaded_pages):
    """Complete dependency checks/copies before the source is renamed."""
    folder = source.is_dir()
    files = [p for p in source.rglob('*') if p.is_file()] if folder else [source]
    for path in files:
        if path.suffix.casefold() not in KINDS:
            continue
        destination = relocate(path, source, target)
        if path.suffix.casefold() == '.md':
            page = next((t.widget for t in loaded_pages if t.path == path), None)
            text = page.toPlainText() if page else paths.read_bytes(path, 8*1024*1024)[0].decode('utf-8-sig')
            validate_markdown_location(text, path, destination, (source, target))
        else:
            loaded = io.load(path.parent, path.name)
            if loaded.issues:
                raise ValueError('文件附件尚未完整，請先處理再移動：' + str(path))
            if not folder and path.parent != destination.parent:
                copied = assets.prepare(loaded.document, path.parent, destination.parent)
                if copied != loaded.document:
                    raise ValueError('附件需要格式轉換；原檔尚未移動。')


def rename_exclusive(source, target):
    # Windows rename does not replace an existing destination. Case-only names
    # use that same atomic operation, with no temporary user-visible filename.
    if os.name == 'nt':
        os.rename(source, target)
        return
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, 'renameat2', None)
    if renameat2 is None:
        raise OSError('此平台尚不支援不覆寫的原子移動。')
    if renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))
