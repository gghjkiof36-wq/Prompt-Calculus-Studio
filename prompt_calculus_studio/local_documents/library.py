"""Explicit Canvas library copies, independent of active Prompt selections."""
import copy
from pathlib import Path

from . import assets, identity, io, paths, schema
from .. import composition

FIELD = schema.LIBRARY_FIELD
FEATURE = schema.LIBRARY_FEATURE


def prepare(path):
    path = Path(path)
    loaded = io.load(path.parent, path.name)
    source = loaded.document
    if source['kind'] != 'category':
        raise ValueError('請選擇 Prompt 管理頁。')
    payload = identity.duplicate(source).document['payload']
    payload['category']['name'] = path.stem
    pending = {}
    references = {}
    # Resolve and verify every attachment before writing or changing the Canvas.
    for reference, _, _ in assets.references(source):
        if reference in references:
            continue
        actual = assets.resolve(source, path.parent, reference)
        raw, _ = paths.read_bytes(actual, assets.MAX_ASSET_BYTES)
        suffix = Path(reference).suffix.lower()
        if not suffix[1:].isalnum() or len(suffix) > 11:
            suffix = '.bin'
        target = 'imports/' + schema.digest(raw) + suffix
        references[reference] = target
        pending[target] = raw
        if sum(map(len, pending.values())) > assets.MAX_TOTAL_BYTES:
            raise ValueError('管理頁的附件超過 256 MiB，未匯入。')
    for item in payload['items']:
        owners = [item, *composition.walk(item['composition'])] if item.get('composition') else [item]
        for owner in owners:
            if owner.get('preview'):
                owner['preview'] = references[owner['preview']]
    return payload, pending


def add(state, payload):
    category = copy.deepcopy(payload['category'])
    existing = {item['name'].casefold() for item in state['modules']}
    name = category['name']
    number = 2
    while category['name'].casefold() in existing:
        category['name'] = f'{name} ({number})'
        number += 1
    state['modules'].append(category)
    state['items'].extend(copy.deepcopy(payload['items']))
    state.setdefault(FIELD, []).append(category['id'])
    return category['id']


def change_only(before, after):
    """A library import/undo never compiles text or changes generation routes."""
    previous, current = set(before.get(FIELD, [])), set(after.get(FIELD, []))
    changed = previous ^ current
    if not changed:
        return False
    def remaining(state):
        value = {k: v for k, v in state.items() if k not in (FIELD, 'modules', 'items')}
        value['modules'] = [m for m in state['modules'] if m['id'] not in changed]
        value['items'] = [i for i in state['items'] if i['module'] not in changed]
        return value
    return remaining(before) == remaining(after)
