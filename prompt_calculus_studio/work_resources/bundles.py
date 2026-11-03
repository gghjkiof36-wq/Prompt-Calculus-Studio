"""Immutable local working sets. Only frozen selections cross the bundle boundary."""
from dataclasses import dataclass, replace
from functools import cached_property
import os
from pathlib import Path
import re
from ..conversation_archive import Limits, canonical, checkpoint, decode_json
from ..local_documents.directory_pin import pinned_target
from ..markdown_source import SourceLedger, split_pieces
from ..retained_files import Bundle, COMPLETE, digest, directory_identity, write_new
from .catalog import connection, now, root_identity, token
from .folder_index import LIMIT
from .source_io import read_source, directory
from .stories import stories

SCHEMA = 'pcs.work-resource-bundle.v1'
CAPACITY = 64 * 1024 * 1024
MAX_SELECTIONS = 512


@dataclass(frozen=True)
class Selection:
    reference: bytes
    content: bytes

    @property
    def ref(self):
        return decode_json(self.reference)

    @cached_property
    def sha256(self):
        return digest(self.content)


def document(store, row, cancel=None):
    sid = row['source']
    source = store.source(sid)
    with connection(store) as db:
        record = db.execute('SELECT * FROM document WHERE source=? AND id=?', (sid, row['item'])).fetchone()
    if record is None or record['state'] != '可用':
        raise ValueError(record['state'] if record is not None else '找不到這份文件。')
    if record['rel'] != row['title']:
        raise ValueError('來源已改名，請重新選取。')
    root = Path(source['path'])
    if source['kind'] == 'copy':
        root = root.parent
    raw, _ = read_source(root, record['rel'], root_identity(source), LIMIT, cancel)
    if digest(raw) != row['sha'] or record['sha'] != row['sha']:
        raise ValueError('來源已變更，請重建索引並重新選取。')
    return raw, root/record['rel']


def select_document(store, row, span=None, cancel=None):
    raw, _ = document(store, row, cancel)
    return document_selection(row, raw, span)


def document_selection(row, raw, span=None):
    """Form a selection from an already verified read (also used for batch selection)."""
    start, end = 0, len(raw)
    if span is not None:
        left, right = span
        ledger = SourceLedger(raw)
        if left < 0 or right <= left or right > len(ledger.text_of(ledger.pieces)):
            raise ValueError('選取範圍無效。')
        before, rest = split_pieces(ledger.pieces, left)
        selected, _ = split_pieces(rest, right-left)
        start = len(ledger.bom) + sum(len(p.raw().encode()) for p in before)
        end = start + sum(len(p.raw().encode()) for p in selected)
    ref = dict(kind='document', source=row['source'], item=row['item'], version=row['sha'],
               title=row['title'], range=[start,end], unit='byte', total_bytes=len(raw),
               scope='全文' if span is None else '選取文字', excluded_bytes=len(raw)-(end-start), notes=[])
    return Selection(canonical(ref), raw[start:end])


def materialize(store, picks, cancel=None):
    """Resolve checked metadata only on save, reading each selected source once."""
    if sum(len(pick.get('spans') or pick.get('leaves') or [None]) for pick in picks) > MAX_SELECTIONS:
        raise ValueError('每個組合最多保存 512 項，請減少勾選項目。')
    selections = {}
    archives = {}
    size = count = 0
    def append(index, selection):
        nonlocal size, count
        size += len(selection.content) + len(selection.reference)
        count += 1
        if count > MAX_SELECTIONS or size > CAPACITY:
            raise ValueError('這個組合太大，請減少勾選項目或縮小文字範圍。')
        selections.setdefault(index, []).append(selection)
    for index, pick in enumerate(picks):
        checkpoint(cancel)
        row = pick['row']
        if row['kind'] == 'document':
            raw, _ = document(store, row, cancel)
            for span in pick.get('spans') or [None]:
                append(index, document_selection(row, raw, span))
        else:
            archives.setdefault((row['source'], row['sha']), []).append((index, pick))
    for (source, version), group in archives.items():
        checkpoint(cancel)
        raw, name = store.original(source, version, cancel)
        parsed = {story.key: story for story in stories(name, raw, cancel)}
        del raw
        for index, pick in group:
            checkpoint(cancel)
            row = pick['row']
            story = parsed.get(row['story'])
            if story is None:
                raise ValueError('此版本找不到選取的對話。')
            story = replace(story, origin=(source, row['item'], version))
            for leaf in pick.get('leaves') or [story.current]:
                append(index, select_branch(row, story, leaf))
        del parsed
    return [selection for index in range(len(picks)) for selection in selections[index]]


def load_story(store, row, cancel=None):
    raw, name = store.original(row['source'], row['sha'], cancel)
    found = next((s for s in stories(name,raw,cancel) if s.key == row['story']), None)
    if found is None:
        raise ValueError('此版本找不到該段對話。')
    return replace(found, origin=(row['source'],row['item'],row['sha']))


def select_branch(row, story, leaf):
    if row['story'] != story.key or story.origin != (row['source'],row['item'],row['sha']):
        raise ValueError('請重新選取此版本的對話。')
    selected = story.selected(leaf)
    notes = list(story.notes)
    for message in selected:
        notes.extend(message['notes'])
        notes.extend(a['reference']+' · '+a['state'] for a in message['attachments'])
    ref = dict(kind='branch', source=row['source'], item=row['item'], version=row['sha'], story=story.key,
               title=story.title, leaf=leaf, nodes=len(selected), scope='選定分支',
               excluded_nodes=len(story.nodes)-len(selected), notes=list(dict.fromkeys(notes)))
    return Selection(canonical(ref), canonical({'messages':selected}))


def prepared(selections, *, key, title, created, capacity=CAPACITY):
    if not selections or len(selections) > MAX_SELECTIONS:
        raise ValueError('每包請選 1 至 512 項，較多項目請分包。')
    if not title.strip() or len(title) > 160:
        raise ValueError('資料包名稱須為 1 至 160 字。')
    refs, files = [], []
    total = 0
    for i, selected in enumerate(selections, 1):
        ref = selected.ref
        name = f'{i:04d}.json' if ref['kind']=='branch' else f'{i:04d}.txt'
        refs.append(dict(**ref, file=name, size=len(selected.content), sha256=selected.sha256))
        files.append((name, selected.content))
        total += len(selected.content)
    record = canonical(dict(schema=SCHEMA, id=key, title=title.strip(), saved=created,
                            model_budget='unchecked', content_bytes=total, selections=refs))
    size = total+len(record)
    if size > capacity:
        raise ValueError(f'內容與清單共 {size:,} bytes，超過 {capacity:,} bytes。請縮小選區或分成多包。')
    return Bundle(tuple([('manifest.json', record), *files]), {'schema':SCHEMA,'id':key}), size


def current_state(store, ref, cancel=None):
    try:
        source = store.source(ref['source'])
        path = Path(source['path'])
        if ref['kind']=='document':
            with connection(store) as db:
                row = db.execute('SELECT rel,state FROM document WHERE id=? AND source=?',
                                 (ref['item'], ref['source'])).fetchone()
            if not row or row['state']=='已移除':
                return '來源已移除'
            root = path if source['kind']=='folder' else path.parent
            rel, limit = row['rel'], LIMIT
        else:
            root, rel, limit = path.parent, path.name, Limits().source
        raw, _ = read_source(root, rel, root_identity(source), limit, cancel)
        if digest(raw) != ref['version']:
            return '來源已變更'
        if ref['kind']=='document' and rel != ref['title']:
            return '來源已改名'
        return '版本一致'
    except (OSError, ValueError):
        return '來源目前不可用'


def save(store, selections, title, cancel=None, *, capacity=CAPACITY):
    key = token()
    selections = tuple(selections)
    bundle, _ = prepared(selections, key=key, title=title, created=now(), capacity=capacity)
    seen = set()
    for selection in selections:
        checkpoint(cancel)
        ref = selection.ref
        identity = (ref['source'],ref['item'],ref['version'])
        if identity in seen:
            continue
        seen.add(identity)
        if ref['kind']=='document':
            if current_state(store, ref, cancel) not in ('版本一致','來源已改名'):
                raise ValueError('選取後來源已變更或無法讀取，請重新選取。')
        else:
            # The user picked a particular retained import, including historical revisions.
            store.source(ref['source'])
            store.original(ref['source'],ref['version'],cancel)
    return write_new(store.bundles, key, bundle, cancel, root_identity=store.bundles_id).name


def open_bundle(store, key, cancel=None, *, contents=True):
    if not re.fullmatch('[0-9a-f]{32}', key):
        raise ValueError('資料包識別無效。')
    def read(name, limit):
        return read_source(store.bundles, key+'/'+name, store.bundles_id, limit, cancel)[0]
    complete = decode_json(read(COMPLETE, 4*1024*1024))
    if (complete.get('version') != 1 or complete.get('state')!='complete' or
        complete.get('metadata') != {'schema':SCHEMA, 'id':key}):
        raise ValueError('資料包未完成或格式不相符。')
    inventory = {f['path']:f for f in complete['files']}
    def verified(name, limit):
        raw = read(name, limit)
        if name not in inventory or digest(raw) != inventory[name]['sha256'] or len(raw) != inventory[name]['size']:
            raise ValueError('資料包內容與保存紀錄不符。')
        return raw
    manifest = verified('manifest.json', CAPACITY)
    record = decode_json(manifest)
    refs = record.get('selections')
    if (record.get('schema')!=SCHEMA or record.get('id')!=key or not isinstance(refs,list) or
        not 1<=len(refs)<=MAX_SELECTIONS or record.get('content_bytes',-1)<0 or
        len(manifest)+record['content_bytes']>CAPACITY):
        raise ValueError('不支援此資料包的清單或容量。')
    payloads = []
    for i, ref in enumerate(refs, 1):
        name = f'{i:04d}.json' if ref['kind']=='branch' else f'{i:04d}.txt'
        if (ref['file']!=name or name not in inventory or ref['sha256']!=inventory[name]['sha256']
                or ref['size']!=inventory[name]['size']):
            raise ValueError('選取項目與保存紀錄不符。')
        if contents:
            payloads.append(verified(name, ref['size']))
    if set(inventory) != {'manifest.json',*(r['file'] for r in refs)} or sum(r['size'] for r in refs) != record['content_bytes']:
        raise ValueError('資料包清單不完整。')
    return record, payloads


def saved(store):
    records = []
    with directory(store.bundles, expected=store.bundles_id):
        candidates = list(store.bundles.iterdir())
    for p in candidates:
        if not re.fullmatch('[0-9a-f]{32}',p.name):
            continue
        try:
            record,_ = open_bundle(store,p.name,contents=False)
            records.append((record['saved'],record['title'],p.name))
        except (ValueError,OSError,KeyError,TypeError):
            continue
    return sorted(records, reverse=True)


def saved_rows(store, cancel=None):
    """Read collection summaries once; browsing never loads all saved contents."""
    result = []
    with directory(store.bundles, expected=store.bundles_id):
        candidates = [p.name for p in store.bundles.iterdir() if re.fullmatch('[0-9a-f]{32}',p.name)]
    for key in candidates:
        checkpoint(cancel)
        try:
            record,_ = open_bundle(store,key,cancel,contents=False)
            result.append(dict(kind='bundle',item=key,title=record['title'],date=record['saved'],
                size=record['content_bytes'],count=len(record['selections']),state='已保存',location='',
                source='',sha='',story='',node=''))
        except (ValueError,OSError,KeyError,TypeError):
            continue
    return result


def export_original(store, row, destination, expected, cancel=None):
    root = Path(destination)
    if root == store.data or store.data in root.parents:
        raise ValueError('請選擇內容資料夾。')
    store.source(row['source'])
    raw, name = store.original(row['source'],row['sha'],cancel)
    package = Bundle(((name,raw),),{'kind':'work-resource-original','source':row['source'],'sha256':row['sha']})
    return write_new(root,'封存原件-'+token(),package,cancel,root_identity=expected)/name


def copy_markdown(store, row, destination, cancel=None, *, expected=None):
    raw, origin = document(store,row,cancel)
    if origin.suffix.casefold() != '.md':
        raise ValueError('管理副本目前支援 Markdown 文件。')
    root = Path(destination)
    if root == store.data or store.data in root.parents:
        raise ValueError('請選擇工作區的內容資料夾。')
    from ..retained_files import _write
    # Publish a durable file with Windows' exclusive rename. A failed write
    # leaves only a hidden recovery file and never a partial named copy.
    checkpoint(cancel)
    with pinned_target(root, '.pcs-copy-'+token()+'.incomplete') as stage:
        if expected is not None and directory_identity(root) != expected:
            raise ValueError('副本目的資料夾已被替換。')
        _write(stage,raw)
        for i in range(1,1001):
            name = origin.stem+' - 副本'+('' if i==1 else ' '+str(i))+'.md'
            target = stage.parent/name
            try:
                os.rename(stage,target)
                break
            except FileExistsError:
                continue
        else:
            raise ValueError('目的位置已有太多同名副本。')
    sid = store.register(target,'copy',copied_from=row['item'])
    store.rebuild(sid)
    return target, sid
