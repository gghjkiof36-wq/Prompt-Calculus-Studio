"""Selected-note packages with explicit branch extraction and no local database."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote, urlsplit
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

from .conversation_archive import Archive, Limits, canonical, checkpoint, conversation, decode_json, zip_members
from .conversation_markdown import attachment_plan, branch_files, index_markdown
from .conversation_review import Source, subset, freshness
from .local_documents import paths
from .local_documents.directory_pin import pinned_target
from .markdown_links import Notes
from .capture_images import source_links
from .retained_files import (Bundle, COMPLETE, START, MAX_FILE, MAX_TOTAL, digest, inventory,
                             load_record, read_local, write_new)
from .retained_files import directory_identity, verify_directory

MANIFEST = '匯出清單.json'
DENIED = {'.git', '.obsidian', '.pcs', 'data', 'cache', 'credentials', 'logs', 'qa', 'qa-private', 'node_modules'}
DENIED_FILES = {'.env', 'credentials.json', 'auth.json', 'settings.json', 'studio.sqlite3', 'error.log', 'fault.log'}


@dataclass(frozen=True)
class Package:
    bundle: Bundle
    entry: str
    warnings: tuple


def prepare(root, document, text, source=None, branches=(), include_summary=True,
            cancel=None, progress=lambda _: None):
    root = paths.root_path(root)
    root_identity = directory_identity(root)
    document = paths.checked(root, Path(document).absolute().relative_to(root).as_posix())
    if document.suffix.lower() != '.md':
        raise ValueError('請選 Markdown 文件。')
    entry = document.relative_to(root).as_posix()
    files, warnings, visited = {}, [], set()
    notes = Notes(root)
    pending = [(document, text)]
    special = set()
    snapshots = {}

    def capture(path, limit=MAX_FILE):
        data = read_local(path,limit,cancel)
        value = digest(data)
        if path in snapshots and snapshots[path] != value:
            raise ValueError('來源在預覽期間已變更：'+path.name)
        snapshots[path] = value
        return data

    def put(name, data):
        parts = paths.relative_parts(name)
        if any(part.casefold() in DENIED for part in parts) or parts[-1].casefold() in DENIED_FILES:
            raise ValueError('可攜包不包含設定、憑證或診斷資料：' + name)
        if name in files and files[name] != data:
            raise ValueError('同一路徑有不同固定內容：' + name)
        files[name] = data
        inventory(tuple(files.items()), hash_contents=False)

    if source:
        source.verify()
        if not branches:
            raise ValueError('請選匯出的對話分支。')
        raw = subset(source, branches)
        item = conversation(raw, Limits())
        allowed = set(item.raw['mapping'])
        # Anchors travel with a renamed/moved note; folder names aren't identity.
        mentioned = set()
        for match in re.finditer(r'<!--\s*(\{[^\n]*\})\s*-->',text):
            try:trace = decode_json(match[1].encode('utf-8'))
            except ValueError:continue
            if isinstance(trace,dict) and trace.get('node') in source.item.raw['mapping']:
                mentioned.add(trace['node'])
        if document.parent == source.path.parent:
            branch_file = re.match(r'分支-(\d+)-\d+\.md$',document.name)
            if branch_file and not mentioned:
                number = int(branch_file[1])
                if 1<=number<=len(source.item.leaves):mentioned.update(source.scope(source.item.leaves[number-1]))
        if mentioned - allowed:
            raise ValueError('目前正文包含未選分支的訊息。請開啟要匯出的分支文件，或改選全部分支。')
        relative = source.path.parent.relative_to(root)
        def source_name(name):return (relative / name).as_posix()
        put(source_name('原始對話.json'), canonical(raw))
        special.add(source.path)
        from .conversation_resources import selected_resources
        mapping, resources, copied, missing = selected_resources(source, item, cancel)
        warnings.extend(missing)
        for target, data in resources.items():
            snapshots[paths.checked(source.path.parent,target)] = digest(data)
            put(source_name(target), data)
        raw['pcs_extraction']['attachments'] = copied
        raw['pcs_extraction']['asset_hashes'] = {name:digest(data) for name,data in resources.items()}
        files[source_name('原始對話.json')] = canonical(raw)
        from .capture_images import prepare_capture_images
        rendered_item, capture_images = prepare_capture_images(item, cancel)
        for target, data in capture_images.items():
            put(source_name(target), data)
        volumes = []
        generated = list(branch_files(rendered_item, mapping, Limits(), cancel))
        for number, part, name, data in generated:
            volumes.append((number, part, name))
            path = source.path.parent / name
            if path != document:
                put(source_name(name), data)
            special.add(path)
        index = index_markdown(item, volumes, warnings, {})
        index = index.replace('原始完整紀錄'.encode(), '所選範圍原始紀錄（非完整封存）'.encode())
        if source.path.parent / '對話.md' != document:
            put(source_name('對話.md'), index)
        special.add(source.path.parent / '對話.md')

    while pending:
        checkpoint(cancel)
        path, content = pending.pop()
        if path in visited:continue
        visited.add(path)
        progress('核對引用：' + path.name)
        relative = path.relative_to(root).as_posix()
        put(relative, content.encode('utf-8'))
        for link in source_links(content):
            address = urlsplit(link.target)
            if address.scheme or address.netloc:
                warnings.append(path.name + '：只保留外部連結 ' + link.target[:200]); continue
            targets, _ = notes.resolve(link.target, path, link.wiki)
            if len(targets) != 1:
                warnings.append(path.name + '：缺失或不唯一的引用 ' + link.target); continue
            target = targets[0]
            if target == path or target in special:continue
            name = target.relative_to(root).as_posix()
            if target.name == '原始對話.json':
                warnings.append('原始對話必須明確選取分支才會包含：' + name); continue
            if target.suffix.lower() == '.md':
                # Crossing into another conversation is always explicit.
                other = next((p / '原始對話.json' for p in target.parents
                              if p.is_relative_to(root) and (p/'原始對話.json').is_file()), None)
                if other:
                    warnings.append('未選取的對話文件，未帶入：' + name); continue
                if link.embed:
                    pending.append((target, capture(target, 8*1024*1024).decode('utf-8-sig')))
                else:
                    warnings.append('未選取的關聯筆記，未帶入：' + name)
                continue
            if target.name == '演示.json':
                from .markdown_demos import inspect
                record, statuses = inspect(target)
                put(name, capture(target, 8*1024*1024))
                for row, state in statuses:
                    if state != '已保存':
                        warnings.append(name + '：' + row['path'] + ' · ' + state); continue
                    asset = paths.checked(target.parent, row['path'])
                    data = capture(asset)
                    if digest(data)!=row['sha256']:raise ValueError('演示附件內容已變更，請重新核對。')
                    put(asset.relative_to(root).as_posix(), data)
                warnings.extend(record.get('warnings', []))
            else:
                put(name, capture(target))

    summary = document.parent / '整理.json'
    if include_summary and summary.is_file():
        record = decode_json(capture(summary,8*1024*1024))
        if record.get('kind')!='conversation-summary' or record.get('version')!=1:
            raise ValueError('整理稿格式不支援，原件已保留。')
        request = record['request']
        if not source or request['conversation_id'] != source.item.key or request['branch'] not in branches:
            raise ValueError('整理稿引用未選分支。請補選對應分支，或取消包含整理追溯資料。')
        state = freshness(record, source, request['branch'])
        if state.startswith(('不同', '來源已更新')):
            raise ValueError('整理稿與選定來源版本不符，請選擇其保存的原始紀錄，或取消包含整理追溯資料。')
        record['extracted_revision'] = digest(files[source.path.relative_to(root).as_posix()])
        put(summary.relative_to(root).as_posix(), canonical(record))

    readme = ('# 可攜文件\n\n入口：[' + document.name + '](' + quote(entry, safe='/') + ')\n\n'
              '以 PCS 接入此資料夾後開啟入口，或使用普通 Markdown 閱讀器。\n'
              '本包是所選內容的獨立副本，沒有本機資料庫、帳號或憑證。\n'
              '對話來源若有 pcs_extraction，代表範圍擷取，並非完整原封存。\n'
              'HTML／SVG 原檔只在明確開啟時交給外部程式。\n\n## 缺失與連線需求\n\n'
              + '\n'.join('- '+w for w in dict.fromkeys(warnings)) + '\n')
    if '閱讀說明.md' in files or MANIFEST in files:
        raise ValueError('來源名稱與匯出清單保留名稱相同，請先另存文件。')
    put('閱讀說明.md', readme.encode('utf-8'))
    manifest = dict(version=1, kind='portable-markdown', entry=entry, files=inventory(tuple(files.items())),
                    warnings=list(dict.fromkeys(warnings)), scope=dict(source_id=source.item.key if source else None,
                    source_revision=source.item.digest if source else None, branches=list(branches)),
                    document_snapshot=digest(text.encode('utf-8')))
    put(MANIFEST, canonical(manifest))
    for path, expected in snapshots.items():
        checkpoint(cancel)
        if digest(read_local(path,cancel=cancel)) != expected:
            raise ValueError('來源在預覽期間已變更，請重新預覽：'+path.name)
    if source:source.verify()
    verify_directory(root,root_identity)
    return Package(Bundle(tuple(files.items()), dict(kind='portable-markdown', entry=entry)), entry, tuple(dict.fromkeys(warnings)))


def export_folder(destination, package, cancel=None, progress=lambda _: None, *, root_identity=None):
    folder = write_new(destination, 'PCS-匯出-' + uuid.uuid4().hex[:12], package.bundle, cancel, progress, root_identity=root_identity)
    return folder


def export_zip(destination, package, cancel=None, progress=lambda _: None, *, root_identity=None):
    package.bundle.validate()
    expected = root_identity if root_identity is not None else directory_identity(destination)
    name = 'PCS-匯出-' + uuid.uuid4().hex[:12]
    temporary = name + '.incomplete.zip'
    with pinned_target(destination, temporary) as path:
        verify_directory(destination,expected)
        operation = uuid.uuid4().hex
        with path.open('xb') as stream:
            with ZipFile(stream, 'w', compression=ZIP_DEFLATED) as archive:
                archive.writestr(START, canonical(dict(version=1, state='incomplete', operation=operation)))
                for index, (filename, data) in enumerate(package.bundle.files, 1):
                    checkpoint(cancel); progress(f'打包 {index}/{len(package.bundle.files)}：{filename}')
                    archive.writestr(filename, data)
                checkpoint(cancel)
                archive.writestr(COMPLETE, canonical(dict(version=1, state='complete', operation=operation,
                                  files=package.bundle.validate(), metadata=package.bundle.metadata)))
            stream.flush(); os.fsync(stream.fileno())
        load_zip(path, cancel)  # verify before publishing the final name
        checkpoint(cancel)
        with pinned_target(destination, name + '.zip') as final:
            os.link(path, final)
        path.unlink()  # own exclusive temporary, with parent pinned
    return final


def validate_package(files):
    inventory(tuple((n, d) for n, d in files.items() if n not in (START, COMPLETE)))
    if COMPLETE not in files or MANIFEST not in files:
        raise ValueError('此包缺少完成收據或匯出清單，不能當成完成匯入。')
    receipt, manifest = decode_json(files[COMPLETE]), decode_json(files[MANIFEST])
    if (receipt.get('version') != 1 or receipt.get('state') != 'complete'
        or manifest.get('version') != 1 or manifest.get('kind') != 'portable-markdown'):
        raise ValueError('可攜包格式不支援。')
    for record, expected in ((receipt, set(files)-{START, COMPLETE}), (manifest, set(files)-{START, COMPLETE, MANIFEST})):
        rows = record.get('files')
        if not isinstance(rows, list) or len(rows) != len(expected) or {row['path'] for row in rows} != expected:
            raise ValueError('清單與實際檔案範圍不一致。')
        for row in rows:
            data = files[row['path']]
            if len(data) != row['size'] or digest(data) != row['sha256']:
                raise ValueError('可攜檔案內容已改變：' + row['path'])
    entry = manifest['entry']
    paths.relative_parts(entry)
    if entry not in files or not entry.lower().endswith('.md'):
        raise ValueError('可攜包入口不存在。')
    return Package(Bundle(tuple((n, d) for n, d in files.items() if n not in (START, COMPLETE)),
                         dict(kind='portable-markdown', entry=entry)), entry, tuple(manifest.get('warnings', [])))


def load_zip(path, cancel=None):
    from io import BytesIO
    data = read_local(path, MAX_TOTAL, cancel)
    with ZipFile(BytesIO(data)) as archive:
        members = zip_members(archive, Limits(source=MAX_TOTAL, members=2002))
        files = {}
        used = 0
        for name in members:
            checkpoint(cancel)
            with archive.open(name) as stream:
                raw = stream.read(MAX_FILE+1)
            used += len(raw)
            if len(raw) > MAX_FILE or used > MAX_TOTAL:
                raise ValueError('可攜包解壓容量超過限制。')
            files[name] = raw
    return validate_package(files)


def import_zip(path, destination, cancel=None, progress=lambda _: None, *, root_identity=None):
    expected = root_identity if root_identity is not None else directory_identity(destination)
    package = load_zip(path, cancel)
    folder = write_new(destination, 'PCS-副本-' + uuid.uuid4().hex[:12], package.bundle, cancel, progress, root_identity=expected)
    return folder, folder / package.entry
