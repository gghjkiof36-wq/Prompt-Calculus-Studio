"""Export each selected conversation as an ordinary, independently readable note."""
from dataclasses import dataclass, field, replace
from io import BytesIO
import hashlib
from pathlib import Path

from ..conversation_archive import Archive, Limits, canonical, conversation, identity, parse_archive, Cancelled
from ..conversation_import import ImportResult, import_selected
from ..retained_files import Bundle, write_new
from .catalog import token, root_identity
from .stories import stories


@dataclass
class BatchResult(ImportResult):
    exported: list = field(default_factory=list)


def source_for(store, row, cancel, cache=None):
    enrolled = store.source(row['source'])
    cache_key = row['source'], row['sha'], row['item']
    if cache is not None and cache_key in cache:
        archive, keys = cache[cache_key]
        if row['story'] not in keys:
            raise ValueError('找不到已選對話版本。')
        return archive, keys.index(row['story'])
    raw, name = store.original(row['source'], row['sha'], cancel)
    carrier = store.originals / row['item'] / 'source.bin'
    if Path(name).suffix.lower() == '.pcs-chat':
        items = []
        for story in stories(name, raw, cancel):
            mapping = {}
            current = None
            # PCS attempts carry explicit context paths, not parent pointers.
            # Share only identical prefixes; never merge different reply paths.
            for leaf in dict.fromkeys((*story.branches, story.current)):
                parent = None
                for node in story.selected(leaf):
                    key = hashlib.sha256(canonical([parent, node['node_id']])).hexdigest()
                    if key not in mapping:
                        mapping[key] = dict(id=key, parent=parent, children=[], message=dict(
                            id=node['message_id'], author={'role': node['role']}, create_time=node['timestamp'],
                            content={'content_type': 'text', 'parts': [node['text']]},
                            metadata={'attachments': [{'id': a['reference']} for a in node['attachments']]}))
                        if parent:
                            mapping[parent]['children'].append(key)
                    parent = key
                if leaf == story.current:
                    current = parent
            item = conversation(dict(id=story.key, title=story.title, mapping=mapping,
                                     current_node=current, pcs_provider='PCS'), Limits())
            items.append((story.key, item))
        selected = next((item for key, item in items if key == row['story']), None)
        if selected is None:
            raise ValueError('找不到已選對話版本。')
        archive = Archive(carrier, identity(carrier.stat()), row['sha'], [item for _, item in items], (), Limits(), format='pcs')
        keys = [key for key, _ in items]
        if cache is not None:
            cache[cache_key] = archive, keys
        return archive, keys.index(row['story'])
    archive = parse_archive(BytesIO(raw), Path(name), (), row['sha'], cancel)
    index = next((i for i, item in enumerate(archive.conversations) if item.digest == row['story']), None)
    if index is None:
        raise ValueError('找不到已選對話版本。')
    same_root = str(enrolled['path']) == row['location']
    archive = replace(archive, path=carrier, stamp=identity(carrier.stat()),
                      attachment_root=Path(enrolled['path']).parent if same_root else None,
                      attachment_identity=root_identity(enrolled) if same_root else None)
    if cache is not None:
        cache[cache_key] = archive, [item.digest for item in archive.conversations]
    return archive, index


def export_selected(store, rows, destination, cancel=None):
    result = BatchResult()
    cache = {}
    for position, row in enumerate(rows):
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            result.unprocessed.extend(r['title'] for r in rows[position:])
            break
        try:
            source, index = source_for(store, row, cancel, cache)
            current = import_selected(source, [index], destination, cancel, readable=True)
            result.completed.extend(current.completed)
            result.failed.extend(current.failed)
            result.titles.update(current.titles)
            if current.completed:
                result.exported.append(position)
            for path in current.completed:
                try:
                    sid = store.register(path, 'copy', copied_from=row['source'] + ':' + row['sha'] + ':' + row['story'])
                    store.rebuild(sid)
                except (ValueError, OSError) as error:
                    result.failed.append((row['title'], str(path), '文件已保存，來源登記未完成：' + str(error)))
            result.unprocessed.extend(current.unprocessed)
            if current.cancelled:
                result.cancelled = True
                result.unprocessed.extend(r['title'] for r in rows[position+1:])
                break
        except Cancelled:
            result.cancelled = True
            result.unprocessed.extend(r['title'] for r in rows[position:])
            break
        except (ValueError, OSError) as error:
            result.failed.append((row['title'], '', str(error)))
    return result


def copy_definition(store, row, cancel=None):
    from .bundles import document
    from ..local_documents.directory_pin import pinned_target
    raw, origin = document(store, row, cancel)
    with pinned_target(store.base, 'definitions/record', create_parents=True):
        pass
    folder = write_new(store.base, 'definitions/' + token(),
                       Bundle(((origin.name, raw),), dict(kind='definition-copy', original=str(origin),
                            source=row['source'], sha256=row['sha'], activation='none')),
                       cancel, root_identity=store.base_id)
    path = folder / origin.name
    sid = store.register(path, 'definition', copied_from=row['item'])
    store.rebuild(sid)
    return path, sid


def relocate_copies(store, source, target):
    """Retarget registered managed files after the shared file transaction commits."""
    changed = []
    for item in store.sources():
        path = Path(item['path'])
        if item['kind'] != 'copy' or not (path == source or source in path.parents):
            continue
        moved = target if path == source else target / path.relative_to(source)
        store.relink(item['id'], moved)
        store.rebuild(item['id'])
        changed.append(item['id'])
    return changed
