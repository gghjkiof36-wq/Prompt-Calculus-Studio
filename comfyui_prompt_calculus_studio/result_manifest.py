"""Durable result contract, independent of the lossy native event projection.

Native executed images are display candidates. Only successful, exact-prompt
history can replace them with a final list; candidate occurrences are not final
execution semantics. Explicit provenance adapters remain an internal interface.
No image IO, serialization or SQLite runs in observe(). No document is opened.
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import threading
import time
from collections import deque
from pathlib import Path

from . import event_protocol as p
from .shared.native_graph import graph_matches

CAPABILITY = 'result_manifest_v1'
SCHEMA = 'pcs.result-manifest.v1'
MAX_CAPTURE = 512 * 1024
MAX_QUEUE_BYTES = 8 * 1024 * 1024
MAX_IMAGE = 25 * 1024 * 1024
PAGE_ITEMS = 64
LEASE_SECONDS = 300
RESULT_EVENTS = frozenset(('executed', 'execution_cached', 'execution_success',
                          'execution_error', 'execution_interrupted'))


def digest(value):
    return hashlib.sha256(p.encode(value)).hexdigest()


def text(value, name, limit=200):
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        raise ValueError('Invalid '+name)
    return value


def number(value, name):
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValueError('Invalid '+name)
    return value


def owner(value):
    if not isinstance(value, dict) or set(value) != {'library_id', 'workspace_id', 'document_id'}:
        raise ValueError('Complete result owner required')
    return {key: text(value[key], key) for key in sorted(value)}


def identity(value):
    if not isinstance(value, dict) or set(value) != {'owner', 'server_instance', 'operation', 'prompt_id', 'graph_digest'}:
        raise ValueError('Complete manifest identity required')
    operation = value['operation']
    if not isinstance(operation, dict) or set(operation) != {'kind', 'id'} or operation['kind'] not in ('native', 'frozen'):
        raise ValueError('Invalid result operation')
    graph = text(value['graph_digest'], 'graph digest', 64)
    if len(graph) != 64 or any(c not in '0123456789abcdef' for c in graph):
        raise ValueError('Invalid graph digest')
    return dict(owner=owner(value['owner']), server_instance=text(value['server_instance'], 'server'),
                operation=dict(kind=operation['kind'], id=text(operation['id'], 'operation id')),
                prompt_id=text(value['prompt_id'], 'prompt'), graph_digest=graph)


def node_path(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError('Unmapped node path')
    return [text(part, 'node path', 200) for part in value]


def clone_capture(value):
    """Bound allocation before JSON serialization, including many tiny items."""
    used = [0]
    def clone(item, depth=0):
        used[0] += len(item)*4 if isinstance(item, str) else 32
        if used[0] > MAX_CAPTURE or depth > 20:
            raise ValueError('Result capture exceeds budget')
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError('Non-string result key')
            return {clone(key, depth+1): clone(val, depth+1) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [clone(val, depth+1) for val in item]
        if item is None or type(item) in (str, bool, int, float):
            return item
        raise ValueError('Non-JSON result capture')
    result = clone(value)
    return result, used[0]


class ResultManifests:
    def __init__(self, service, roots, *, clock=time.time):
        self.service, self.clock = service, clock
        self.roots = {key: Path(value).absolute() for key, value in roots.items()}
        self.secret = secrets.token_bytes(32)
        self.write_transactions = 0
        self.transaction_times = deque(maxlen=4096)  # Diagnostics only; no idle writes.
        with service.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS result_manifests (
                    id TEXT PRIMARY KEY, identity TEXT NOT NULL, owner TEXT NOT NULL,
                    prompt TEXT NOT NULL, operation TEXT NOT NULL UNIQUE,
                    paths TEXT NOT NULL, revision INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS result_manifest_owner ON result_manifests(owner,id);
                CREATE INDEX IF NOT EXISTS result_manifest_prompt ON result_manifests(prompt);
                CREATE TABLE IF NOT EXISTS result_versions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, manifest TEXT NOT NULL,
                    revision INTEGER NOT NULL, checksum TEXT NOT NULL, body TEXT NOT NULL,
                    UNIQUE(manifest,revision));
                CREATE TABLE IF NOT EXISTS result_items (
                    manifest TEXT NOT NULL, semantic TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    revision INTEGER NOT NULL, body TEXT NOT NULL, file_stamp TEXT NOT NULL,
                    PRIMARY KEY(manifest,semantic), UNIQUE(manifest,ordinal));
                CREATE INDEX IF NOT EXISTS result_items_page ON result_items(manifest,ordinal,revision);
                CREATE TABLE IF NOT EXISTS result_sources (
                    manifest TEXT NOT NULL, source TEXT NOT NULL, revision INTEGER NOT NULL,
                    digest TEXT NOT NULL, PRIMARY KEY(manifest,source,revision));
                CREATE TABLE IF NOT EXISTS result_observations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, prompt TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, epoch TEXT NOT NULL, body TEXT NOT NULL,
                    UNIQUE(prompt,fingerprint));
                CREATE INDEX IF NOT EXISTS result_observation_prompt ON result_observations(prompt,sequence);
                CREATE TABLE IF NOT EXISTS result_gaps (
                    id TEXT PRIMARY KEY, prompt TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS result_candidates (
                    manifest TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    revision INTEGER NOT NULL, body TEXT NOT NULL, file_stamp TEXT NOT NULL,
                    PRIMARY KEY(manifest,ordinal));
                CREATE TABLE IF NOT EXISTS result_native_state (
                    manifest TEXT PRIMARY KEY, sequence INTEGER NOT NULL, terminal TEXT);
                CREATE TABLE IF NOT EXISTS result_occurrences (
                    manifest TEXT NOT NULL, node TEXT NOT NULL, next INTEGER NOT NULL,
                    PRIMARY KEY(manifest,node));
            ''')

    def _written(self):
        self.write_transactions += 1
        self.transaction_times.append(self.clock())

    def _check_server(self, value):
        if value['server_instance'] != self.service.events.server_instance:
            raise ValueError('Foreign result server')

    def _current(self, db):
        if db.execute('SELECT epoch FROM event_meta WHERE id=1').fetchone()[0] != self.service.events.epoch:
            raise ValueError('Stale result publisher')

    def bind(self, value):
        """Authenticated PCS supplies document mapping; verify the saved operation.

        This does not grant any occurrence, nested-path or zero-output proof.
        It is safe to bind after capture: unresolved evidence is keyed by prompt.
        """
        value = identity(value)
        self._check_server(value)
        with self.service.connect() as db:
            graph = self._submitted_graph(db, value)
        header = self.register(value, [[key] for key in graph])
        publisher = getattr(self.service.events, 'publisher', None)
        if publisher is not None:
            publisher.results.request(value['prompt_id'])
        return header

    def _submitted_graph(self, db, value):
        """Resolve the immutable original; never reconstruct it from history.

        The original digest/owner checks are shared by bind and the seal worker.
        Numeric equivalence applies only after this identity has been verified.
        """
        table = {'native': 'native_operations', 'frozen': 'queue_attempts'}[value['operation']['kind']]
        raw = db.execute('SELECT body FROM '+table+' WHERE id=?', (value['operation']['id'],)).fetchone()
        if not raw:
            raise ValueError('Original operation unavailable')
        operation = json.loads(raw[0])
        snapshot = operation.get('snapshot', {})
        if table == 'queue_attempts':
            raw = db.execute('SELECT body FROM queue_captures WHERE id=?', (operation.get('capture'),)).fetchone()
            snapshot = json.loads(raw[0]).get('snapshot', {}) if raw else {}
        graph = operation.get('payload', {}).get('prompt')
        if (operation.get('prompt_id') != value['prompt_id'] or not isinstance(graph, dict)
                or digest(graph) != value['graph_digest']
                or snapshot.get('library_id') != value['owner']['library_id']
                or snapshot.get('state', {}).get('workspace') != value['owner']['workspace_id']):
            raise ValueError('Original owner/prompt/graph mismatch')
        return graph

    def register(self, value, paths):
        """Server-side adapter boundary; paths must come from frozen graph evidence."""
        value = identity(value)
        self._check_server(value)
        paths = sorted([node_path(path) for path in paths])
        if len(paths) > 8192 or len({p.encode(path) for path in paths}) != len(paths):
            raise ValueError('Invalid path map')
        ident = digest(value)
        operation = digest(dict(server=value['server_instance'], operation=value['operation']))
        with self.service.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._current(db)
            old = db.execute('SELECT id,identity,paths FROM result_manifests WHERE operation=?', (operation,)).fetchone()
            if old:
                if old != (ident, p.encode(value).decode(), p.encode(paths).decode()):
                    raise ValueError('Result identity or path map changed')
                return self._header(db, ident)
            db.execute('INSERT INTO result_manifests VALUES (?,?,?,?,?,?,0)',
                       (ident, p.encode(value).decode(), p.encode(value['owner']).decode(),
                        value['prompt_id'], operation, p.encode(paths).decode()))
            header = dict(schema=SCHEMA, manifest_id=ident, identity=value, revision=0,
                          item_count=0, items_digest=digest([]), sealed=False,
                          terminal_state=None, expected_count=None, final_digest=None,
                          result_state='results_pending', source_epoch=self.service.events.epoch)
            self._version(db, header)
        self._written()
        return header

    def _header(self, db, ident, revision=None, checksum=None):
        if revision is None:
            row = db.execute('SELECT body,checksum FROM result_versions WHERE manifest=? ORDER BY revision DESC LIMIT 1', (ident,)).fetchone()
        else:
            number(revision, 'manifest revision')
            row = db.execute('SELECT body,checksum FROM result_versions WHERE manifest=? AND revision=?', (ident, revision)).fetchone()
        if not row or (checksum is not None and not hmac.compare_digest(row[1], checksum)):
            raise ValueError('Unknown manifest revision/checksum')
        return dict(json.loads(row[0]), checksum=row[1])

    def _version(self, db, header):
        header.pop('checksum', None)
        header['revision'] += 1
        checksum = digest(header)
        db.execute('INSERT INTO result_versions(manifest,revision,checksum,body) VALUES (?,?,?,?)',
                   (header['manifest_id'], header['revision'], checksum, p.encode(header).decode()))
        db.execute('UPDATE result_manifests SET revision=? WHERE id=?', (header['revision'], header['manifest_id']))
        # No prompt_id/fetch_required on the envelope: pre-D consumers ignore it.
        # D consumers route by the exact manifest identity, never by a native job.
        self.service.events.append(db, 'result.manifest',
            dict(manifest_id=header['manifest_id'], revision=header['revision'], checksum=checksum),
            entity='result_manifest:'+header['manifest_id'],
            scope=p.scope(dict(domain=p.DOMAIN, user_scope='default',
                               **{k: header['identity']['owner'][k] for k in ('library_id', 'workspace_id')})))
        header['checksum'] = checksum

    @staticmethod
    def _not_redirected(path):
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Redirected result path')

    def _path(self, image):
        if not isinstance(image, dict) or set(image) != {'type', 'subfolder', 'filename'}:
            raise ValueError('Invalid result descriptor')
        root = self.roots.get(image['type'])
        filename = text(image['filename'], 'PNG filename', 255)
        subfolder = image['subfolder']
        if (root is None or not filename.lower().endswith('.png')
                or any(c in filename for c in '/\\:%\x00') or filename in ('.', '..')
                or filename.rstrip(' .') != filename
                or not isinstance(subfolder, str) or len(subfolder) > 2048
                or any(c in subfolder for c in '\\:%\x00')):
            raise ValueError('Only registered output/temp PNG paths are allowed')
        parts = subfolder.split('/') if subfolder else []
        if any(part in ('', '.', '..') or part.rstrip(' .') != part for part in parts):
            raise ValueError('Invalid result subfolder')
        path = root.joinpath(*parts, filename)
        self._not_redirected(path)
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Result escaped root')
        return path

    @staticmethod
    def _stamp(info):
        return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]

    def _read_file(self, image, prompt, *, history_authority=False):
        path = self._path(image)
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or not 8 <= before.st_size <= MAX_IMAGE:
            raise ValueError('Invalid result size/type')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            # Windows/Python versions disagree about ctime between stat(path)
            # (birth time) and fstat(handle) (change time). Compare ctime only
            # within the same API, keeping identity/size/mtime across both.
            if self._stamp(opened)[:4] != self._stamp(before)[:4]:
                raise ValueError('Result changed while opening')
            content = stream.read(MAX_IMAGE+1)
            after = os.fstat(stream.fileno())
        self._not_redirected(path)
        if (len(content) != before.st_size or self._stamp(opened) != self._stamp(after)
                or self._stamp(before) != self._stamp(path.stat())
                or not content.startswith(b'\x89PNG\r\n\x1a\n')):
            raise ValueError('Result changed or is not PNG')
        # Preserve the existing submission-metadata check, using verified bytes.
        from .shared.pnginfo import png_metadata_stream
        import io
        metadata = png_metadata_stream(io.BytesIO(content))
        embedded = metadata.get('raw', {}).get('prompt_studio')
        if (not history_authority and isinstance(embedded, dict)
                and embedded.get('submission_id') not in (None, '', prompt)):
            raise ValueError('PNG belongs to another prompt')
        return content, self._stamp(before)

    def ingest(self, packet):
        """Worker-only, trusted adapter input. Never interprets native path strings."""
        value = identity(packet['identity'])
        self._check_server(value)
        ident = digest(value)
        source = text(packet['source_id'], 'source id')
        source_revision = number(packet['source_revision'], 'source revision')
        evidence = text(packet['evidence_ref'], 'source evidence', 512)
        # Epoch is provenance, not part of either replay or semantic identity.
        packet_digest = digest({k: v for k, v in packet.items() if k != 'source_epoch'})
        with self.service.connect() as db:
            old = db.execute('SELECT digest FROM result_sources WHERE manifest=? AND source=? AND revision=?',
                             (ident, source, source_revision)).fetchone()
            if old:
                if old[0] != packet_digest:
                    raise ValueError('Conflicting source revision digest')
                return self._header(db, ident)
            row = db.execute('SELECT paths FROM result_manifests WHERE id=?', (ident,)).fetchone()
            if not row:
                raise ValueError('Manifest not registered for this identity')
            paths = json.loads(row[0])
        items = packet.get('items', [])
        if not isinstance(items, list) or len(items) > 2048:
            raise ValueError('Invalid result item batch')
        verified = []
        for item in items:
            path = node_path(item['node_path'])
            if path not in paths:
                raise ValueError('Unmapped node path')
            semantic = dict(occurrence=text(item['occurrence'], 'occurrence'), node_path=path,
                            output_slot=text(item['output_slot'], 'output slot'),
                            item_index=number(item['item_index'], 'item index'))
            content, stamp = self._read_file(item['image'], value['prompt_id'])
            asset_hash = hashlib.sha256(content).hexdigest()
            if item.get('asset_sha256', asset_hash) != asset_hash:
                raise ValueError('Source asset checksum mismatch')
            body = dict(**semantic, ordinal=number(item['ordinal'], 'ordinal'), image=item['image'],
                        asset_sha256=asset_hash, size=len(content), format='png')
            verified.append((digest(semantic), body, stamp))
        with self.service.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._current(db)
            header = self._header(db, ident)
            # Recheck after file IO; one worker normally owns this, but public
            # server adapters and concurrent retries must also be safe.
            old = db.execute('SELECT digest FROM result_sources WHERE manifest=? AND source=? AND revision=?',
                             (ident, source, source_revision)).fetchone()
            if old:
                if old[0] != packet_digest:
                    raise ValueError('Conflicting source revision digest')
                return header
            changed = False
            for key, body, stamp in verified:
                raw = p.encode(body).decode()
                old = db.execute('SELECT body FROM result_items WHERE manifest=? AND semantic=?', (ident, key)).fetchone()
                if old:
                    if old[0] != raw:
                        raise ValueError('Conflicting semantic result item')
                    continue
                if header['sealed']:
                    raise ValueError('Cannot append to sealed manifest')
                db.execute('INSERT INTO result_items VALUES (?,?,?,?,?,?)',
                           (ident, key, body['ordinal'], header['revision']+1, raw, p.encode(stamp).decode()))
                changed = True
            rows = [json.loads(r[0]) for r in db.execute('SELECT body FROM result_items WHERE manifest=? ORDER BY ordinal', (ident,))]
            header.update(item_count=len(rows), items_digest=digest(rows))
            seal = packet.get('seal')
            if seal is not None:
                if (not isinstance(seal, dict) or seal.get('complete') is not True
                        or seal.get('terminal_state') not in ('success', 'error', 'interrupted')
                        or number(seal.get('expected_count'), 'expected count') != len(rows)
                        or seal.get('final_digest') != digest(rows)
                        or [row['ordinal'] for row in rows] != list(range(len(rows)))
                        or (not rows and seal.get('zero_output') is not True)):
                    raise ValueError('Incomplete final seal')
                if header['sealed'] and (header['terminal_state'] != seal['terminal_state'] or header['final_digest'] != seal['final_digest']):
                    raise ValueError('Conflicting final seal')
                if not header['sealed']:
                    changed = True
                    header.update(sealed=True, terminal_state=seal['terminal_state'],
                                  expected_count=len(rows), final_digest=seal['final_digest'],
                                  zero_output=not rows, seal_evidence=evidence,
                                  result_state='ready' if seal['terminal_state'] == 'success' else 'partial')
            if changed:
                header['source_epoch'] = text(packet.get('source_epoch', self.service.events.epoch), 'source epoch')
                header['source_evidence'] = dict(id=source, revision=source_revision,
                                                 ref=evidence, digest=packet_digest)
                self._version(db, header)
            # Different envelopes containing only identical items are also no-op.
            if changed:
                db.execute('INSERT INTO result_sources VALUES (?,?,?,?)', (ident, source, source_revision, packet_digest))
        if changed:
            self._written()
        return header

    def observe_native(self, event, data, epoch, capture_id=None):
        prompt = text(data.get('prompt_id'), 'native prompt')
        body = dict(event=event, data=data, reason='display evidence; final requires successful history')
        # Identical executed callbacks can be different loop iterations. The
        # queue's capture ID survives persistence retries; sequence is assigned
        # durably, not from filenames, bytes or a guessed native occurrence.
        fingerprint = capture_id or digest(body)
        with self.service.connect() as db:
            self._current(db)
            if db.execute('SELECT 1 FROM result_observations WHERE prompt=? AND fingerprint=?', (prompt, fingerprint)).fetchone():
                return
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO result_observations(prompt,fingerprint,epoch,body) VALUES (?,?,?,?)',
                       (prompt, fingerprint, epoch, p.encode(body).decode()))
            # An observation is not an item, count or final seal. Existing
            # executed events remain untouched and keep their original contract.
        self._written()

    def pending_native(self):
        with self.service.connect() as db:
            return [row[0] for row in db.execute('''SELECT DISTINCT m.prompt
                FROM result_manifests m JOIN result_versions v
                ON v.manifest=m.id AND v.revision=m.revision
                WHERE json_extract(v.body,'$.sealed')=0''')]

    def _native_item(self, prompt, node, image, index, ordinal, occurrence, path, *, history=False):
        image = {key: image.get(key, '' if key == 'subfolder' else None)
                 for key in ('filename', 'subfolder', 'type')}
        # Cached PNG metadata can name the original generating prompt. Only
        # authenticated exact history (never event fields) grants this authority.
        content, stamp = self._read_file(image, prompt, history_authority=history)
        asset_hash = hashlib.sha256(content).hexdigest()
        return dict(node_id=node, node_path=path, occurrence=occurrence,
                    output_slot='images', item_index=index, ordinal=ordinal,
                    image=image, asset_sha256=asset_hash, size=len(content), format='png',
                    asset_key=digest([node, image, asset_hash])), stamp

    def _apply_native(self, ident, observation):
        sequence, raw, epoch = observation
        capture = json.loads(raw)
        event, data = capture['event'], capture['data']
        with self.service.connect() as db:
            state = db.execute('SELECT sequence,terminal FROM result_native_state WHERE manifest=?', (ident,)).fetchone()
            if state and sequence <= state[0]:
                return
            header = self._header(db, ident)
            if header['sealed']:
                return
            node = data.get('node')
            occurrence = db.execute('SELECT next FROM result_occurrences WHERE manifest=? AND node=?',
                                    (ident, node)).fetchone() if isinstance(node, str) else None
            occurrence = occurrence[0] if occurrence else 0
            count = db.execute('SELECT COUNT(*) FROM result_candidates WHERE manifest=?', (ident,)).fetchone()[0]
        verified = []
        if event == 'executed' and isinstance(node, str) and node:
            path = data.get('node_path')
            try:
                path = node_path(path) if path is not None else [text(node, 'native node')]
                images = data.get('output', {}).get('images', [])
                if not isinstance(images, list):
                    raise ValueError('Invalid executed images')
                for index, image in enumerate(images):
                    verified.append(self._native_item(data['prompt_id'], node, image,
                        index, count+len(verified), str(occurrence), path))
            except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
                # Display is best effort. Never let an expired/malformed early
                # image block independent final history reconciliation.
                self.gap(data['prompt_id'], 'candidate unavailable: '+str(exc))
        with self.service.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._current(db)
            state = db.execute('SELECT sequence,terminal FROM result_native_state WHERE manifest=?', (ident,)).fetchone()
            if state and sequence <= state[0]:
                return
            header = self._header(db, ident)
            if header['sealed']:
                return
            terminal = state[1] if state else None
            # Failure evidence cannot later be upgraded by a stray success.
            if event in ('execution_error', 'execution_interrupted'):
                terminal = event.removeprefix('execution_')
            elif event == 'execution_success' and terminal not in ('error', 'interrupted'):
                terminal = 'success'
            if event == 'executed' and isinstance(node, str) and node:
                db.execute('INSERT OR REPLACE INTO result_occurrences VALUES (?,?,?)', (ident, node, occurrence+1))
            for item, stamp in verified:
                db.execute('INSERT INTO result_candidates VALUES (?,?,?,?,?)',
                    (ident, item['ordinal'], header['revision']+1, p.encode(item).decode(), p.encode(stamp).decode()))
            if verified or header.get('terminal_state') != terminal:
                rows = [json.loads(r[0]) for r in db.execute(
                    'SELECT body FROM result_candidates WHERE manifest=? ORDER BY ordinal', (ident,))]
                header.update(item_source='candidates', item_count=len(rows), items_digest=digest(rows),
                              terminal_state=terminal, source_epoch=epoch, result_state='results_pending')
                self._version(db, header)
            db.execute('INSERT OR REPLACE INTO result_native_state VALUES (?,?,?)', (ident, sequence, terminal))
        self._written()

    def reconcile_native(self, prompt, queue):
        """Worker only. Return True while exact history still needs a retry."""
        with self.service.connect() as db:
            manifests = [r[0] for r in db.execute('SELECT id FROM result_manifests WHERE prompt=?', (prompt,))]
        retry = False
        for ident in manifests:
            with self.service.connect() as db:
                header = self._header(db, ident)
                state = db.execute('SELECT sequence FROM result_native_state WHERE manifest=?', (ident,)).fetchone()
                observations = db.execute('''SELECT sequence,body,epoch FROM result_observations
                    WHERE prompt=? AND sequence>? ORDER BY sequence''', (prompt, state[0] if state else 0)).fetchall()
            if header['sealed']:
                continue
            for observation in observations:
                self._apply_native(ident, observation)
            if queue is not None:
                retry = self.seal_history(ident, queue.get_history(prompt_id=prompt)) or retry
        return retry

    def seal_history(self, ident, history):
        """Final rows come ONLY from a complete successful history snapshot.

        ComfyUI publishes outputs and successful status atomically in task_done.
        Empty/absent expected image outputs cannot prove zero. No candidate is
        promoted, even with an identical descriptor and content hash.
        """
        with self.service.connect() as db:
            header = self._header(db, ident)
            if header['sealed']:
                return False
            state = db.execute('SELECT terminal FROM result_native_state WHERE manifest=?', (ident,)).fetchone()
            if state and state[0] in ('error', 'interrupted'):
                return False
            candidates = [json.loads(r[0]) for r in db.execute(
                'SELECT body FROM result_candidates WHERE manifest=? ORDER BY ordinal', (ident,))]
        prompt = header['identity']['prompt_id']
        entry = history.get(prompt) if isinstance(history, dict) else None
        if not isinstance(entry, dict):
            return bool(state and state[0] == 'success')
        status = entry.get('status', {})
        if not isinstance(status, dict) or status.get('completed') is not True or status.get('status_str') != 'success':
            return bool(state and state[0] == 'success')
        saved = entry.get('prompt')
        if (not isinstance(saved, (list, tuple)) or len(saved) < 5 or saved[1] != prompt
                or not isinstance(saved[2], dict)
                or not isinstance(saved[4], (list, tuple))):
            return True
        with self.service.connect() as db:
            try:
                graph = self._submitted_graph(db, header['identity'])
            except ValueError:
                return True
        if not graph_matches(graph, saved[2]):
            return True
        outputs, expected = entry.get('outputs'), saved[4]
        if not isinstance(outputs, dict) or any(not isinstance(n, str) for n in expected):
            return True
        images = []
        for node, output in outputs.items():
            if not isinstance(node, str) or not isinstance(output, dict):
                return True
            listed = output.get('images', [])
            if not isinstance(listed, list):
                return True
            for index, image in enumerate(listed):
                if not isinstance(image, dict):
                    return True
                # Same PNG selection as Service.results; no 64/120 item cap.
                if str(image.get('filename', '')).lower().endswith('.png'):
                    images.append((node, index, image))
        image_nodes = {node for node, definition in saved[2].items()
                       if isinstance(definition, dict) and definition.get('class_type') in ('SaveImage', 'PreviewImage')}
        # Known top-level image outputs must be represented. Unknown/custom
        # and flattened subgraph keys are taken directly from history; never
        # force them into the candidate path map or invent native semantics.
        if image_nodes.intersection(expected)-{node for node, _, _ in images}:
            return True
        if not images and (candidates or image_nodes.intersection(expected)):
            return True
        # An executed image slot is also evidence that zero cannot be guessed,
        # even if its file disappeared before candidate verification.
        if not images:
            if (any(node not in outputs or not outputs[node] for node in expected)
                    or any('images' in output for output in outputs.values())):
                return True
            with self.service.connect() as db:
                if db.execute('''SELECT 1 FROM result_observations WHERE prompt=?
                    AND json_array_length(body,'$.data.output.images')>0 LIMIT 1''', (prompt,)).fetchone():
                    return True
        verified = [self._native_item(prompt, node, image, index, ordinal, 'history', [node], history=True)
                    for ordinal, (node, index, image) in enumerate(images)]
        rows = [item for item, _ in verified]
        with self.service.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._current(db)
            current = self._header(db, ident)
            if current['sealed']:
                return False
            if current['revision'] != header['revision']:
                return True
            if db.execute('SELECT 1 FROM result_items WHERE manifest=?', (ident,)).fetchone():
                raise ValueError('Cannot mix adapter and native history sources')
            for item, stamp in verified:
                key = digest([item['node_id'], item['item_index']])
                db.execute('INSERT INTO result_items VALUES (?,?,?,?,?,?)',
                    (ident, key, item['ordinal'], header['revision']+1, p.encode(item).decode(), p.encode(stamp).decode()))
            header.update(item_source='history', item_count=len(rows), items_digest=digest(rows),
                          sealed=True, terminal_state='success', expected_count=len(rows),
                          final_digest=digest(rows), zero_output=not rows, result_state='ready',
                          source_epoch=self.service.events.epoch, seal_evidence='history:'+prompt,
                          history_digest=digest(entry))
            self._version(db, header)
        self._written()
        return False

    def gap(self, prompt, reason):
        prompt = prompt if isinstance(prompt, str) and len(prompt) <= 200 else ''
        body = dict(prompt_id=prompt, reason=str(reason)[:512], source_epoch=self.service.events.epoch)
        key = digest(body)
        with self.service.connect() as db:
            self._current(db)
            if db.execute('SELECT 1 FROM result_gaps WHERE id=?', (key,)).fetchone():
                return
            db.execute('INSERT INTO result_gaps VALUES (?,?,?)', (key, prompt, p.encode(body).decode()))
            self.service.events.append(db, p.LOG_GAP, dict(body, result_gap=key), entity='log:gap')
        self._written()

    def headers(self, expected_owner, *, after='', as_of=None, limit=PAGE_ITEMS):
        expected_owner = owner(expected_owner)
        if not isinstance(after, str) or (after and (len(after) != 64 or any(c not in '0123456789abcdef' for c in after))):
            raise ValueError('Invalid header cursor')
        limit = min(PAGE_ITEMS, max(1, int(limit)))
        with self.service.connect() as db:
            ceiling = db.execute('SELECT COALESCE(MAX(sequence),0) FROM result_versions').fetchone()[0]
            as_of = ceiling if as_of is None else number(as_of, 'header cursor')
            if as_of > ceiling:
                raise ValueError('Future header cursor')
            rows = db.execute('''SELECT v.body,v.checksum FROM result_manifests m
                JOIN result_versions v ON v.manifest=m.id
                WHERE m.owner=? AND m.id>? AND v.sequence=(SELECT MAX(sequence)
                FROM result_versions WHERE manifest=m.id AND sequence<=?)
                ORDER BY m.id LIMIT ?''', (p.encode(expected_owner).decode(), after, as_of, limit+1)).fetchall()
            headers = []
            size = 1024
            for raw, checksum in rows[:limit]:
                entry = dict(json.loads(raw), checksum=checksum)
                size += len(p.encode(entry))
                if size > p.MAX_PAGE_BYTES:
                    break
                headers.append(entry)
        return dict(schema=SCHEMA, headers=headers, as_of=as_of, done=len(headers) == len(rows),
                    next_after=headers[-1]['manifest_id'] if headers else after)

    def _owned(self, db, ident, revision, checksum, expected_owner):
        header = self._header(db, ident, revision, checksum)
        if header['identity']['owner'] != owner(expected_owner):
            raise ValueError('Foreign manifest owner')
        self._check_server(header['identity'])
        return header

    def _lease(self, header, expires):
        return hmac.new(self.secret, p.encode([header['identity'], header['revision'], header['checksum'], expires]), hashlib.sha256).hexdigest()

    def page(self, ident, revision, checksum, expected_owner, *, after=-1, limit=PAGE_ITEMS):
        if type(after) is not int or not -1 <= after < 2**63:
            raise ValueError('Invalid item cursor')
        limit = min(PAGE_ITEMS, max(1, int(limit)))
        with self.service.connect() as db:
            header = self._owned(db, ident, revision, checksum, expected_owner)
            table = 'result_candidates' if header.get('item_source') == 'candidates' else 'result_items'
            rows = db.execute('SELECT body FROM '+table+' WHERE manifest=? AND revision<=? AND ordinal>? ORDER BY ordinal LIMIT ?',
                              (ident, revision, after, limit+1)).fetchall()
        items = []
        size = len(p.encode(header))+1024
        for raw, in rows[:limit]:
            size += len(raw.encode('utf-8'))
            if size > p.MAX_PAGE_BYTES:
                break
            items.append(json.loads(raw))
        expires = int(self.clock())+LEASE_SECONDS
        return dict(header=header, items=items, done=len(items) == len(rows),
                    next_after=items[-1]['ordinal'] if items else after,
                    expires=expires, asset_token=self._lease(header, expires))

    def evidence(self, ident, revision, checksum, expected_owner, *, after=0, limit=1):
        """Unresolved native captures are explicit evidence, never semantic items."""
        number(after, 'evidence cursor')
        # Each capture is bounded separately; one capture may exceed event/page
        # bytes. This endpoint is explicitly bounded at MAX_CAPTURE*2 JSON bytes.
        with self.service.connect() as db:
            header = self._owned(db, ident, revision, checksum, expected_owner)
            prompt = header['identity']['prompt_id']
            row = db.execute('SELECT sequence,epoch,body FROM result_observations WHERE prompt=? AND sequence>? ORDER BY sequence LIMIT 1', (prompt, after)).fetchone()
            gaps = [json.loads(r[0]) for r in db.execute('SELECT body FROM result_gaps WHERE prompt IN (?,\'\') ORDER BY id LIMIT 16', (prompt,))]
        return dict(manifest_id=ident, observation=dict(sequence=row[0], source_epoch=row[1], **json.loads(row[2])) if row else None,
                    next_after=row[0] if row else after, done=row is None, gaps=gaps, result_state='results_pending')

    def asset(self, ident, revision, checksum, expected_owner, *, ordinal, asset_sha256, expires, token):
        number(ordinal, 'asset ordinal')
        number(expires, 'lease expiry')
        with self.service.connect() as db:
            header = self._owned(db, ident, revision, checksum, expected_owner)
            if (not self.clock() < expires <= self.clock()+LEASE_SECONDS+1
                    or not hmac.compare_digest(self._lease(header, expires), token)):
                raise ValueError('Expired or foreign result lease')
            table = 'result_candidates' if header.get('item_source') == 'candidates' else 'result_items'
            row = db.execute('SELECT body,file_stamp FROM '+table+' WHERE manifest=? AND revision<=? AND ordinal=?', (ident, revision, ordinal)).fetchone()
        if not row:
            raise ValueError('Asset not in requested manifest revision')
        item = json.loads(row[0])
        if asset_sha256 != item['asset_sha256']:
            raise ValueError('Foreign asset checksum')
        content, stamp = self._read_file(item['image'], header['identity']['prompt_id'],
                                         history_authority=header.get('item_source') == 'history')
        if stamp != json.loads(row[1]) or hashlib.sha256(content).hexdigest() != asset_sha256:
            raise ValueError('Registered asset expired or changed')
        return content


class ResultManifestBuffer:
    """Bounded capture queue + one worker; optional trusted source adapter.

    adapter(event, copied_data) runs on the worker, never on the hook. It returns
    an explicit contract packet or None. By default executed captures are
    display-only and exact queue history supplies the final seal.
    """
    def __init__(self, service, *, capacity=256, adapter=None, queue=None):
        self.service, self.store, self.capacity = service, service.result_manifests, capacity
        self.adapter, self.queue = adapter, queue
        self.items = deque()
        self.bytes = 0
        self.lock = threading.Lock()
        self.pending_gaps = {}
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.retry_at = 0
        self.capture_prefix = secrets.token_hex(16)
        self.capture_sequence = 0
        self.requests = set()
        self.retries = {}
        self.wait_returns = 0  # Diagnostics: excludes initial startup and stop.
        self.recovery_pending = True

    def available(self):
        return (self.adapter is None and callable(getattr(self.queue, 'get_history', None))
                and self.thread is not None and self.thread.is_alive() and not self.stop_event.is_set())

    def request(self, prompt):
        with self.lock:
            self.requests.add(prompt)
        self.wake.set()

    def _loss(self, prompt, reason):
        prompt = prompt if isinstance(prompt, str) and len(prompt) <= 200 else ''
        with self.lock:
            key = prompt if prompt in self.pending_gaps or len(self.pending_gaps) < 16 else ''
            self.pending_gaps[key] = reason
        self.service.events.record_loss(reason, prompt_id=prompt, persist=False)
        self.wake.set()

    def observe(self, event, data):
        if event not in RESULT_EVENTS or not isinstance(data, dict):
            return
        try:
            copied, size = clone_capture(data)
            with self.lock:
                full = len(self.items) >= self.capacity or self.bytes+size > MAX_QUEUE_BYTES
                if not full:
                    self.capture_sequence += 1
                    capture_id = (self.capture_prefix+':'+str(self.capture_sequence)
                                  if event == 'executed' and isinstance(copied.get('node'), str) else None)
                    self.items.append((event, copied, self.service.events.epoch, size, capture_id))
                    self.bytes += size
            if full:
                self._loss(data.get('prompt_id'), 'result capture overflow; exact prompt reconciliation required')
            self.wake.set()
        except Exception:
            self._loss(data.get('prompt_id'), 'result capture incomplete; exact prompt reconciliation required')

    def drain(self):
        if time.monotonic() < self.retry_at:
            return
        with self.lock:
            gaps = dict(self.pending_gaps)
        try:
            if self.recovery_pending:
                for prompt in self.store.pending_native():
                    self.request(prompt)
                self.recovery_pending = False
            for prompt, reason in gaps.items():
                self.store.gap(prompt, reason)
                with self.lock:
                    if self.pending_gaps.get(prompt) == reason:
                        self.pending_gaps.pop(prompt, None)
            for _ in range(8):
                with self.lock:
                    item = self.items[0] if self.items else None
                if item is None:
                    break
                event, data, epoch, size, capture_id = item
                try:
                    packet = self.adapter(event, data) if self.adapter else None
                    if packet is None:
                        self.store.observe_native(event, data, epoch, capture_id)
                        self.request(data['prompt_id'])
                    else:
                        self.store.ingest(dict(packet, source_epoch=epoch))
                except (ValueError, KeyError, TypeError, OSError, sqlite3.IntegrityError) as exc:
                    # Invalid evidence remains an explicit, durable gap. A DB
                    # failure in gap() leaves the original capture queued.
                    prompt = data.get('prompt_id')
                    if isinstance(prompt, str) and 1 <= len(prompt) <= 200:
                        self.store.observe_native(event, data, epoch, capture_id)
                        self.request(prompt)
                    self.store.gap(data.get('prompt_id'), str(exc))
                with self.lock:
                    self.items.popleft()
                    self.bytes -= size
            # Drain queued captures before reading history, so an already
            # captured error/interruption always wins over success evidence.
            with self.lock:
                if self.items:
                    return
                prompts = self.requests
                self.requests = set()
            now = time.monotonic()
            prompts.update(prompt for prompt, (at, _) in self.retries.items() if at <= now)
            for prompt in prompts:
                try:
                    retry = self.store.reconcile_native(prompt, self.queue)
                except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
                    self.store.gap(prompt, 'history pending: '+str(exc))
                    retry = True
                if retry:
                    attempt = self.retries.get(prompt, (0, 0))[1]
                    delay = (.1, .5, 1, 5, 30)[min(attempt, 4)]
                    self.retries[prompt] = (time.monotonic()+delay, attempt+1)
                else:
                    self.retries.pop(prompt, None)
        except Exception:
            # Recover durable observations as well as queued captures after a
            # failed transaction, without dropping the in-flight prompt set.
            with self.lock:
                self.requests.update(locals().get('prompts', ()))
            self.retry_at = time.monotonic()+1
            self.service.events.record_loss('result worker persistence failed', persist=False)

    def start(self):
        if self.thread:
            return
        def run():
            while not self.stop_event.is_set():
                self.wake.clear()
                self.drain()
                with self.lock:
                    pending = bool(self.items or self.pending_gaps or self.requests or self.recovery_pending)
                now = time.monotonic()
                if pending and self.retry_at <= now:
                    continue
                deadlines = [at for at, _ in self.retries.values()]
                if pending:
                    deadlines.append(self.retry_at)
                timeout = (self.retry_at-now if self.retry_at > now else
                           max(0, min(deadlines)-now) if deadlines else None)
                self.wake.wait(timeout)
                if not self.stop_event.is_set():
                    self.wait_returns += 1
        self.thread = threading.Thread(target=run, name='pcs-result-writer', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=12)
            if self.thread.is_alive():
                self.service.events.record_loss('result worker did not stop; epoch reconciliation required', persist=False)
                return
        # A normal unload drains accepted captures. A hard process crash instead
        # forces epoch reconciliation; unsealed results never become zero.
        while (self.items or self.pending_gaps or self.requests) and time.monotonic() >= self.retry_at:
            self.drain()
