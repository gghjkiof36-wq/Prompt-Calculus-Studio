"""Static covers, originals and explicit dependencies; no execution or download."""
from copy import deepcopy
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
from urllib.parse import quote, unquote, urlsplit
import uuid

from .conversation_archive import canonical, checkpoint
from .local_documents import paths
from .retained_files import Bundle, digest, inventory, load_record, read_local, write_new, directory_identity, verify_directory

RECORD = '演示.json'
PREVIEWS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
OPENABLE = PREVIEWS | {'.html', '.htm', '.svg', '.pdf', '.txt', '.csv', '.json',
                        '.wav', '.mp3', '.ogg', '.flac', '.m4a', '.mp4', '.webm', '.ogv'}
REFERENCE = re.compile(r'''(?:\b(?:src|href)\s*=\s*["']([^"']+)["']|url\(\s*["']?([^\s)"']+))''', re.I)


def source_url(value):
    value = value.strip()
    if value:
        parsed = urlsplit(value)
        if (any(ord(c)<32 for c in value) or parsed.scheme not in ('http', 'https')
            or not parsed.hostname or parsed.username or parsed.password):
            raise ValueError('來源請填不含帳密的 http／https 網址。')
    return value


def prepare(title, original=None, preview=None, dependencies=None, source='', previous=None,
            cancel=None, progress=lambda _: None):
    title, source = title.strip(), source_url(source)
    if not title or len(title) > 300:
        raise ValueError('請填寫 1～300 字的標題。')
    if not any((original, preview, source)):
        raise ValueError('至少提供原檔、靜態預覽或來源網址。')
    original = Path(original).absolute() if original else None
    preview = Path(preview).absolute() if preview else None
    base = Path(dependencies).absolute() if dependencies else original.parent if original else None
    if dependencies and original and not original.is_relative_to(base):
        raise ValueError('依賴資料夾必須包含入口原檔，才能保留相對位置。')
    selected = []
    if dependencies:
        paths.root_path(base)
        # An explicit folder selection is still bounded and never follows links.
        for parent, dirs, files in os.walk(base, followlinks=False):
            checkpoint(cancel)
            for name in dirs + files:
                candidate = Path(parent) / name
                if paths.is_link(candidate) or name.casefold() in ('.git', '.obsidian', '.pcs'):
                    raise ValueError('依賴包含設定目錄或連結，請另選專用演示資料夾：' + name)
            selected.extend(Path(parent) / name for name in sorted(files))
            if len(selected) > 1990:
                raise ValueError('依賴檔案過多，請縮小資料夾。')
    elif original:
        selected.append(original)
    if original and original not in selected:
        raise ValueError('指定的入口原檔不在選定檔案中，請重新選擇。')
    files, rows, snapshots = [], [], {}
    for path in selected:
        checkpoint(cancel)
        progress('讀取：' + path.name)
        name = 'original/' + path.relative_to(base).as_posix()
        data = read_local(path, cancel=cancel)
        snapshots[path] = digest(data)
        files.append((name, data))
        rows.append(dict(path=name, sha256=digest(data), size=len(data),
                         role='original' if path == original else 'dependency'))
        inventory(files, hash_contents=False)
    preview_name = None
    if preview:
        if preview.suffix.lower() not in PREVIEWS:
            raise ValueError('靜態預覽請選 PNG、JPEG、GIF、WebP 或 BMP。')
        preview_name = 'preview/' + preview.name
        data = read_local(preview, cancel=cancel)
        if preview in snapshots and snapshots[preview]!=digest(data):
            raise ValueError('來源在預覽期間已變更，請重新預覽：'+preview.name)
        snapshots[preview] = digest(data)
        files.append((preview_name, data))
        rows.append(dict(path=preview_name, sha256=digest(data), size=len(data), role='preview'))
    names = {name.casefold() for name, _ in files}
    warnings = []
    for name, data in files:
        if PurePosixPath(name).suffix.lower() not in ('.html', '.htm', '.svg', '.css'):
            continue
        if len(data) > 8 * 1024 * 1024:
            warnings.append(name + '：內容過大，未核對靜態引用。')
            continue
        for match in REFERENCE.finditer(data.decode('utf-8', errors='replace')):
            target = match[1] or match[2]
            address = urlsplit(target)
            if address.scheme or address.netloc:
                if address.scheme != 'data':
                    warnings.append(name + '：外部引用（僅保留文字）' + target[:200])
            elif address.path:
                relative = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(address.path)))
                if relative.casefold() not in names:
                    warnings.append(name + '：未保存引用 ' + target[:200])
    record = deepcopy(previous) if previous else {}
    record.update(version=1, kind='demo', id=record.get('id', uuid.uuid4().hex),
                  revision=uuid.uuid4().hex, title=title, source=source,
                  acquisition='使用者選取本機檔案／提供網址', files=rows,
                  original='original/' + original.relative_to(base).as_posix() if original else None,
                  preview=preview_name, warnings=list(dict.fromkeys(warnings)),
                  dependency_check='僅核對靜態 src／href／url；未執行或辨識動態依賴。')
    if previous:
        record['previous_revision'] = previous['revision']
    for path, expected in snapshots.items():
        checkpoint(cancel)
        if digest(read_local(path,cancel=cancel)) != expected:
            raise ValueError('來源在預覽期間已變更，請重新預覽：'+path.name)
    files.append((RECORD, canonical(record)))
    bundle = Bundle(tuple(files), dict(kind='demo', id=record['id'], revision=record['revision']))
    bundle.validate()
    return bundle, record


def save(root, bundle, cancel=None, progress=lambda _: None, *, root_identity=None):
    # Parent is created through the same pinned creation path.
    from .local_documents.directory_pin import pinned_target
    expected = root_identity if root_identity is not None else directory_identity(root)
    with pinned_target(root, '.anchor'):
        verify_directory(root,expected)
        with pinned_target(root, 'assets/.anchor', create_parents=True):
            return write_new(root, 'assets/demo-' + uuid.uuid4().hex, bundle, cancel, progress, root_identity=expected)


def markdown(record, folder, document):
    def link(name):
        return quote(os.path.relpath(folder / name, document.parent).replace('\\', '/'), safe='/.-_')
    title = re.sub(r'([\\\[\]*_`<>])', r'\\\1', record['title']).replace('\n', ' ')
    parts = [f'<!-- pcs-demo:{record["id"]}:{record["revision"]} -->', '**' + title + '**']
    if record.get('preview'):
        parts.append('![' + title + '](' + link(record['preview']) + ')')
    parts.append(' · '.join(filter(None, [
        '[開啟原檔](' + link(record['original']) + ')' if record.get('original') else '尚無原檔',
        '[來源](' + quote(record['source'], safe=':/?#@!$&*+,;=%') + ')' if record.get('source') else '',
        '[附件與修訂](' + link(RECORD) + ')'])))
    if record['warnings']:
        parts.append('有未保存或外部引用，請查看附件。')
    parts.extend(['<!-- /pcs-demo -->', ''])
    return '\n\n'.join(parts)


def inspect(path):
    record = load_record(path, 'demo')
    rows = record.get('files')
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError('附件清單無法讀取。')
    statuses = []
    for row in rows:
        try:
            target = paths.checked(path.parent, row['path'])
            raw = read_local(target)
            state = '已保存' if len(raw) == row['size'] and digest(raw) == row['sha256'] else '內容已變更，需核對'
        except (ValueError, OSError, KeyError, TypeError):
            state = '缺失或無法讀取'
        statuses.append((row, state))
    return record, statuses


def block_range(text, record):
    marker = f'<!-- pcs-demo:{record["id"]}:{record["revision"]} -->'
    if text.count(marker) != 1:
        raise ValueError('正文中的演示位置已改變或不唯一，請重新開啟附件。')
    start = text.index(marker)
    end = text.find('<!-- /pcs-demo -->', start)
    if end < 0:
        raise ValueError('演示結尾標記已改變，原文未修改。')
    return start, end + len('<!-- /pcs-demo -->')
