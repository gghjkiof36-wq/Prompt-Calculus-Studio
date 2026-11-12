"""Journal layout 1: owned JSON records over canonical, reference-counted content.

Only the documented paths are extracted. No application/Qt objects, global
connection cache, or borrowed mutable objects survive this synchronous boundary.
"""
import hashlib
import json
import sqlite3

ENCODING = 'json-utf8-v1'
SAVED = ('scene', 'values', 'inputs', 'batches', 'readers', 'parameters',
         'parameter_overrides', 'parameter_profiles')


class JournalCorrupt(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def strict_loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError('non-standard JSON number: ' + value)

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def identical(left, right):
    """JSON equality including bool/int/float distinctions (and signed zero)."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(identical(v, right[k]) for k, v in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(identical(a, b) for a, b in zip(left, right))
    if isinstance(left, float):
        return canonical(left) == canonical(right)
    return left == right


def pointer(parts):
    return ''.join('/' + str(p).replace('~', '~0').replace('/', '~1') for p in parts)


def _parts(path):
    if not path.startswith('/'):
        raise JournalCorrupt('Invalid journal pointer: ' + path)
    parts = path[1:].split('/')
    decoded = [p.replace('~1', '/').replace('~0', '~') for p in parts]
    if pointer(decoded) != path:
        raise JournalCorrupt('Invalid journal pointer: ' + path)
    return decoded


def _at(value, parts):
    for part in parts:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def _put(value, parts, replacement):
    parent = _at(value, parts[:-1])
    key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
    parent[key] = replacement


def paths(body):
    """Explicit non-overlapping cuts; progress and unknown fields stay in body."""
    found = []

    def fields(obj, prefix, names):
        if isinstance(obj, dict):
            found.extend(prefix + (name,) for name in names if name in obj)

    def saved(obj, prefix):
        fields(obj, prefix, SAVED)

    def context(obj, prefix):
        if isinstance(obj, dict):
            fields(obj, prefix, ('scene', 'results', 'sources'))
            saved(obj.get('saved'), prefix + ('saved',))

    kind = body.get('kind')
    if kind == 'entry':
        saved(body.get('saved'), ('saved',))
    elif kind == 'attempt':
        fields(body, (), ('snapshot_state', 'images', 'result', 'operation_evidence'))
    elif kind == 'batch_snapshot':
        fields(body, (), ('scene', 'base', 'supply', 'segments', 'results', 'parameters'))
    elif kind == 'run':
        fields(body, (), ('frozen_scene', 'free_scene', 'results', 'produced'))
        context(body.get('ctx'), ('ctx',))
        if isinstance(body.get('round_saved'), list):
            for i, value in enumerate(body['round_saved']):
                saved(value, ('round_saved', i))
        fields(body.get('feed'), ('feed',), ('scene', 'plan'))
        if isinstance(body.get('stack'), list):
            for i, frame in enumerate(body['stack']):
                if not isinstance(frame, dict):
                    continue
                prefix = ('stack', i)
                fields(frame, prefix, ('produced', 'results', 'sources', 'variants'))
                context(frame.get('ctx'), prefix + ('ctx',))
                saved(frame.get('saved'), prefix + ('saved',))
                fields(frame.get('feed'), prefix + ('feed',), ('scene', 'plan', 'supply'))
                fields(frame.get('retry'), prefix + ('retry',), ('snapshot_state', 'images', 'result', 'replay', 'operation_evidence'))
    # Empty containers/nulls carry no large payload. Keeping them in the header
    # avoids a blob lookup for every empty interpreter frame. A parameters map
    # whose values are all null likewise contains only presence information.
    return [p for p in found if _at(body, p) and not
            (p[-1] == 'parameters' and isinstance(_at(body, p), dict)
             and all(v is None for v in _at(body, p).values()))]


def projection(db, body):
    # Old full-record reads accepted JSON even when an optional display field
    # was malformed. Do not turn eager projection into a new migration gate:
    # retain that body and defer the same error to the original summary API.
    try:
        return _projection(db, body)
    except (ValueError, TypeError, KeyError, sqlite3.DatabaseError) as exc:
        result = {'error': _projection_error(exc)}
        saved = body.get('saved')
        if body.get('kind') == 'entry' and isinstance(saved, dict):
            result['saved_order'] = {k: list(saved[k]) for k in ('values', 'inputs', 'batches') if isinstance(saved.get(k), dict)}
        return result


def _projection_error(exc):
    args = [exc.msg, exc.doc, exc.pos] if isinstance(exc, json.JSONDecodeError) else list(exc.args)
    return {'type': type(exc).__name__, 'args': args}


def raise_projection_error(raw):
    if raw is not None:
        error = json.loads(raw)
        classes = {'ValueError': ValueError, 'TypeError': TypeError, 'KeyError': KeyError,
                   'JSONDecodeError': json.JSONDecodeError, 'OperationalError': sqlite3.OperationalError,
                   'DatabaseError': sqlite3.DatabaseError}
        raise classes.get(error['type'], ValueError)(*error['args'])


def _projection(db, body):
    """Derive the old SQL semantics before canonical encoding reorders keys."""
    result = {}
    kind = body.get('kind')
    if kind == 'attempt' and 'snapshot_state' in body:
        raw = json.dumps(body['snapshot_state'], ensure_ascii=False, allow_nan=False)
        result.update(parameter_stages=[], parameter_errors=[])
        for key, value in db.execute("SELECT key,value FROM json_each(?, '$.multi_output.stages')", (raw,)):
            try:
                parameter_type = db.execute("SELECT json_type(?,'$.parameters')", (value,)).fetchone()[0]
            except sqlite3.DatabaseError as exc:
                result['parameter_errors'].append(dict(stage=key, error=_projection_error(exc)))
            else:
                if parameter_type is not None and parameter_type != 'null':
                    result['parameter_stages'].append(key)
    if kind == 'batch_snapshot' and 'segments' in body:
        segments = body['segments']
        result['segments'] = [{k: s[k] for k in ('owner', 'start', 'total', 'deferred') if k in s}
                              if isinstance(s, dict) else s for s in segments] if isinstance(segments, list) else segments
    if kind == 'entry' and ('saved' in body or 'source' in body):
        saved = body.get('saved')
        saved = saved if isinstance(saved, dict) else {}
        raw = json.dumps(dict(source=body.get('source'), saved={k: saved[k] for k in
                              ('values', 'inputs', 'batches') if k in saved}), ensure_ascii=False, allow_nan=False)
        row = db.execute("""SELECT json_extract(body,'$.source'),
            (SELECT value FROM json_each(body,'$.saved.values') WHERE json_extract(value,'$.type')='image' LIMIT 1),
            (SELECT value FROM json_each(body,'$.saved.inputs') WHERE json_extract(value,'$.type')='image' LIMIT 1),
            (SELECT json_extract(value,'$[0]') FROM json_each(body,'$.saved.batches') WHERE json_array_length(value)>0 LIMIT 1),
            (SELECT MAX(json_array_length(value)) FROM json_each(body,'$.saved.batches')) FROM (SELECT ? AS body)""", (raw,)).fetchone()
        source = json.loads(row[0]) if row[0] is not None else None
        first = row[1] if row[1] is not None else row[2]
        value = json.loads(first)['value'] if first is not None else None
        batch = json.loads(row[3]) if row[3] is not None else None
        image = source or value or batch
        result.update(image=image, image_count=1 if source else row[4] if row[4] is not None else 1 if image else None)
        # These mappings' insertion order defines the old first-image rule.
        result['saved_order'] = {k: list(saved[k]) for k in ('values', 'inputs', 'batches') if isinstance(saved.get(k), dict)}
    return result


def split(db, body):
    cuts = paths(body)
    # Copy only the skeleton: never deep-copy a scene immediately before encoding.
    header = dict(body)
    copied = {()}
    refs = {}
    content = {}
    for parts in cuts:
        raw = canonical(_at(body, parts))
        digest = hashlib.sha256(raw).hexdigest()
        if digest in content and content[digest] != raw:
            raise JournalCorrupt('Journal content hash collision')
        content[digest] = raw
        refs[pointer(parts)] = digest
        for end in range(1, len(parts)):
            prefix = parts[:end]
            if prefix not in copied:
                old = _at(header, prefix)
                _put(header, prefix, dict(old) if isinstance(old, dict) else list(old))
                copied.add(prefix)
        _put(header, parts, None)  # Arrays keep their indices, including nulls.
    return header, projection(db, body), refs, content


def _collect(db, digests):
    for digest in digests:
        db.execute('DELETE FROM stage_content WHERE sha256=? AND NOT EXISTS '
                   '(SELECT 1 FROM stage_content_ref WHERE sha256=?)', (digest, digest))


def write(db, body, position, *, columns=None):
    header, projected, refs, content = split(db, body)
    return write_parts(db, header, projected, refs, content, position, columns=columns)


def write_parts(db, header, projected, refs, content, position, *, columns=None):
    ident = header['id']
    old = dict(db.execute('SELECT pointer,sha256 FROM stage_content_ref WHERE record_id=?', (ident,)))
    previous = db.execute('SELECT content_paths FROM stage_header WHERE id=?', (ident,)).fetchone()
    if previous is not None and previous[0] != canonical(old).decode('utf-8'):
        raise JournalCorrupt('Missing or unexpected journal reference: ' + ident)
    for digest, raw in content.items():
        existing = db.execute('SELECT encoding,bytes,body FROM stage_content WHERE sha256=?', (digest,)).fetchone()
        if existing is None:
            db.execute('INSERT INTO stage_content VALUES (?,?,?,?)', (digest, ENCODING, len(raw), raw))
        elif existing != (ENCODING, len(raw), raw):
            raise JournalCorrupt('Journal content mismatch: ' + ident + ' / ' + digest)
    columns = columns or (ident, header.get('kind'), header.get('workspace'), header.get('owner'))
    values = (*columns, position, canonical(header).decode('utf-8'), canonical(projected).decode('utf-8'), canonical(refs).decode('utf-8'))
    if previous is not None:
        db.execute('UPDATE stage_header SET kind=?,workspace=?,owner=?,position=?,body=?,projection=?,content_paths=? WHERE id=?',
                   (*values[1:], ident))
    else:
        db.execute('INSERT INTO stage_header VALUES (?,?,?,?,?,?,?,?)', values)
    for path, digest in old.items():
        if refs.get(path) != digest:
            db.execute('DELETE FROM stage_content_ref WHERE record_id=? AND pointer=?', (ident, path))
    for path, digest in refs.items():
        if old.get(path) != digest:
            db.execute('INSERT INTO stage_content_ref VALUES (?,?,?)', (ident, path, digest))
    _collect(db, set(old.values()) - set(refs.values()))


def delete(db, ident):
    old = [r[0] for r in db.execute('SELECT sha256 FROM stage_content_ref WHERE record_id=?', (ident,))]
    db.execute('DELETE FROM stage_content_ref WHERE record_id=?', (ident,))
    db.execute('DELETE FROM stage_header WHERE id=?', (ident,))
    _collect(db, set(old))


def record_columns(alias='stage_header'):
    """Check even empty manifests, without aggregating JSON for empty records."""
    prefix = '' if alias == 'stage_header' else alias + '.'
    return (','.join(prefix + name for name in ('id', 'body', 'content_paths', 'projection')) +
            ',EXISTS(SELECT 1 FROM stage_content_ref WHERE record_id=' + alias + '.id)')


RECORD_COLUMNS = record_columns()


def records(db, rows):
    """Accept four columns, or record_columns() with reference presence."""
    rows = list(rows)
    if all(len(r) == 5 and r[2] == '{}' and not r[4] and '"saved_order"' not in r[3] for r in rows):
        return [json.loads(r[1]) for r in rows]
    blobs = {}
    actual = {}
    manifests = {r[0]: {} if r[2] == '{}' else json.loads(r[2]) for r in rows}
    pending = [r[0] for r in rows if len(r) == 4 or r[4]]
    for start in range(0, len(pending), 400):
        ids = pending[start:start + 400]
        slots = ','.join('?' for _ in ids)
        for ident, path, digest in db.execute('SELECT record_id,pointer,sha256 FROM stage_content_ref '
                                             'WHERE record_id IN (' + slots + ')', ids):
            actual.setdefault(ident, {})[path] = digest
    digests = list({digest for refs in actual.values() for digest in refs.values()})
    for start in range(0, len(digests), 400):
        chunk = digests[start:start + 400]
        for digest, encoding, size, raw in db.execute('SELECT sha256,encoding,bytes,body FROM stage_content '
                                                     'WHERE sha256 IN (' + ','.join('?' for _ in chunk) + ')', chunk):
            if encoding != ENCODING or len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
                raise JournalCorrupt('Invalid journal content: ' + digest)
            blobs[digest] = raw
    result = []
    for row in rows:
        ident, raw, manifest, projected = row[:4]
        refs = manifests[ident]
        if refs != actual.get(ident, {}):
            raise JournalCorrupt('Missing or unexpected journal reference: ' + ident)
        body = json.loads(raw)
        try:
            parts = sorted(refs)
            for i, path in enumerate(parts):
                if i and path.startswith(parts[i - 1] + '/'):
                    raise JournalCorrupt('Overlapping journal references: ' + ident)
                where = _parts(path)
                if _at(body, where) is not None:
                    raise JournalCorrupt('Invalid journal placeholder: ' + ident)
                _put(body, where, json.loads(blobs[refs[path]]))
            orders = json.loads(projected).get('saved_order', {}) if '"saved_order"' in projected else {}
            for field, order in orders.items():
                value = body['saved'][field]
                if set(order) != set(value) or len(order) != len(value):
                    raise JournalCorrupt('Invalid journal order projection: ' + ident)
                body['saved'][field] = {key: value[key] for key in order}
        except (KeyError, IndexError, TypeError) as exc:
            raise JournalCorrupt('Missing journal content: ' + ident) from exc
        result.append(body)
    return result
