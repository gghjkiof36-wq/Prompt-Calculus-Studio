"""Versioned, single-workflow handoff. No file fallback, HTTP, WS or generation.

The caller validates server origin and default-user scope before invoking this
core. Browser capability assertions must come from its verified native adapter.
All accepted state, receipts and operation snapshots use Service's own database.
"""
import copy
import hashlib
import json
import math
import random
import uuid
from contextlib import contextmanager


CAPABILITY = dict(version='standard8-v1', frontend='1.43.18', backend='0.21.1', hooks_verified=True)
KEY_FIELDS = ('library_id', 'workspace_id', 'workflow_id', 'frontend_id', 'path')
WIDGETS = {
    'KSampler': ('seed', None, 'steps', 'cfg', 'sampler_name', 'scheduler', 'denoise'),
    'EmptyLatentImage': ('width', 'height', 'batch_size'),
    'VAEDecode': (), 'CLIPTextEncode': ('text',), 'PreviewImage': (),
    'CheckpointLoaderSimple': ('ckpt_name',),
    'LoraLoader': ('lora_name', 'strength_model', 'strength_clip'),
}
LINK_INPUTS = {
    'KSampler': {'model': 'MODEL', 'positive': 'CONDITIONING', 'negative': 'CONDITIONING', 'latent_image': 'LATENT'},
    'EmptyLatentImage': {}, 'VAEDecode': {'samples': 'LATENT', 'vae': 'VAE'},
    'CLIPTextEncode': {'clip': 'CLIP'}, 'PreviewImage': {'images': 'IMAGE'},
    'CheckpointLoaderSimple': {}, 'LoraLoader': {'model': 'MODEL', 'clip': 'CLIP'},
}
OUTPUTS = {
    'KSampler': ('LATENT',), 'EmptyLatentImage': ('LATENT',), 'VAEDecode': ('IMAGE',),
    'CLIPTextEncode': ('CONDITIONING',), 'PreviewImage': (),
    'CheckpointLoaderSimple': ('MODEL', 'CLIP', 'VAE'), 'LoraLoader': ('MODEL', 'CLIP'),
}


def _fail(code):
    raise ValueError('background_' + code)


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        _fail('invalid_json')


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _text(value, limit=256):
    if not isinstance(value, str) or not value or len(value) > limit or '\x00' in value:
        _fail('invalid_identifier')
    return value


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        _fail('invalid_integer')
    return value


def _sha256(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        _fail('source_revision')
    return value


def _key(value):
    if not isinstance(value, dict) or set(value) != set(KEY_FIELDS):
        _fail('invalid_key')
    key = {field: _text(value[field], 1000) for field in KEY_FIELDS}
    return key, _digest(key)


def _seed_spec(seed):
    fields = {'node_id', 'timing', 'hasExecuted', 'policy', 'seed', 'min', 'max', 'step2'}
    if not isinstance(seed, dict) or set(seed) != fields:
        _fail('seed_spec')
    _text(seed['node_id'])
    if seed['timing'] not in ('before', 'after') or seed['policy'] not in ('fixed', 'increment', 'decrement', 'randomize'):
        _fail('seed_policy')
    if type(seed['hasExecuted']) is not bool:
        _fail('seed_lifecycle_unknown')
    _integer(seed['seed'])
    if seed['seed'] > 2**53 - 1:
        _fail('seed_precision')
    for field in ('min', 'max', 'step2'):
        number = seed[field]
        if type(number) not in (int, float) or not math.isfinite(number):
            _fail('seed_bounds')
    low, high, step = max(0, seed['min']), min(2**50, seed['max']), seed['step2']
    if low != seed['min'] or low > high or step <= 0 or any(int(n) != n for n in (low, high, step)):
        _fail('seed_bounds')
    return int(low), int(high), int(step)


def prepare_seed(seed, draw=None):
    """Match 1.43.18 numeric control, including its before-first-run exception.

    No widget is called or mutated. One random draw, if required, is reserved
    in the operation transaction; retries reuse the already stored transition.
    """
    low, high, step = _seed_spec(seed)
    current = seed['seed']

    def apply(value):
        policy = seed['policy']
        if policy == 'increment':
            value += step
        elif policy == 'decrement':
            value -= step
        elif policy == 'randomize':
            sample = (draw or random.random)()
            if type(sample) not in (int, float) or not math.isfinite(sample) or not 0 <= sample < 1:
                _fail('seed_random_source')
            value = math.floor(sample * ((high - low) / step)) * step + low
        return min(high, max(low, value))

    execution = apply(current) if seed['timing'] == 'before' and seed['hasExecuted'] else current
    after = apply(execution) if seed['timing'] == 'after' else execution
    return dict(execution_seed=execution, after_seed=after, next_has_executed=True)


def _validate_capture(key, visual, output, capability, seed, source_revision):
    _sha256(source_revision)
    if capability != CAPABILITY or capability.get('hooks_verified') is not True:
        _fail('unsupported_capability')
    if not isinstance(visual, dict) or visual.get('id') != key['frontend_id']:
        _fail('visual_identity')
    if visual.get('definitions') or visual.get('subgraphs'):
        _fail('unsupported_subgraph')
    nodes, links = visual.get('nodes'), visual.get('links')
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 128 or not isinstance(links, list) or len(links) > 512:
        _fail('graph_shape')
    if not isinstance(output, dict) or len(output) != len(nodes):
        _fail('output_shape')
    by_id, link_map = {}, {}
    for node in nodes:
        if not isinstance(node, dict) or type(node.get('id')) not in (str, int):
            _fail('node_shape')
        node_id = _text(str(node['id']))
        if node_id in by_id or not isinstance(node.get('type'), str) or node['type'] not in WIDGETS or type(node.get('mode', 0)) is not int or node.get('mode', 0) != 0:
            _fail('unsupported_node')
        by_id[node_id] = node
    if set(by_id) != set(output):
        _fail('output_identity')
    samplers = [n for n, node in by_id.items() if node['type'] == 'KSampler']
    if len(samplers) != 1 or seed.get('node_id') != samplers[0]:
        _fail('sampler_scope')
    _seed_spec(seed)
    if not any(node['type'] == 'PreviewImage' for node in nodes):
        _fail('output_node_missing')
    for link in links:
        if not isinstance(link, list) or len(link) != 6 or type(link[0]) not in (int, str) or str(link[0]) in link_map:
            _fail('link_shape')
        if str(link[1]) not in by_id or str(link[3]) not in by_id:
            _fail('link_node_missing')
        _integer(link[2]); _integer(link[4])
        origin = by_id[str(link[1])]['type']
        if link[2] >= len(OUTPUTS[origin]) or OUTPUTS[origin][link[2]] != link[5]:
            _fail('link_type')
        link_map[str(link[0])] = link
    used_links = set()
    for node_id, node in by_id.items():
        kind = node['type']; values = node.get('widgets_values', [])
        if not isinstance(values, list) or len(values) != len(WIDGETS[kind]):
            _fail('widget_shape')
        expected = {name: values[i] for i, name in enumerate(WIDGETS[kind]) if name is not None}
        for name, widget_value in expected.items():
            if name in ('text', 'ckpt_name', 'lora_name', 'sampler_name', 'scheduler'):
                if not isinstance(widget_value, str):
                    _fail('widget_type')
            elif name in ('seed', 'steps', 'width', 'height', 'batch_size'):
                _integer(widget_value, 0 if name == 'seed' else 1)
            elif type(widget_value) not in (int, float) or not math.isfinite(widget_value):
                _fail('widget_type')
        if kind == 'KSampler' and (values[0] != seed['seed'] or values[1] != seed['policy']):
            _fail('seed_visual_mismatch')
        if kind == 'CLIPTextEncode':
            text = expected['text']
            if not isinstance(text, str) or any(mark in text for mark in ('{', '}', '//', '/*')):
                _fail('dynamic_text_unsupported')
        inputs = node.get('inputs', [])
        if not isinstance(inputs, list):
            _fail('input_shape')
        connected = set()
        for index, item in enumerate(inputs):
            if not isinstance(item, dict):
                _fail('input_shape')
            if item.get('link') is None:
                continue
            name = item.get('name'); link = link_map.get(str(item['link']))
            if not isinstance(name, str) or name not in LINK_INPUTS[kind] or name in connected or link is None:
                _fail('input_link')
            if str(link[3]) != node_id or link[4] != index or link[5] != LINK_INPUTS[kind][name] or str(link[0]) in used_links:
                _fail('link_target')
            expected[name] = [str(link[1]), link[2]]
            connected.add(name); used_links.add(str(link[0]))
        if connected != set(LINK_INPUTS[kind]):
            _fail('required_link')
        api_node = output[node_id]
        if not isinstance(api_node, dict) or api_node.get('class_type') != kind or _json(api_node.get('inputs')) != _json(expected):
            _fail('visual_api_mismatch')
    if used_links != set(link_map):
        _fail('unused_link')


def _content(visual, output, seed, capability, source_revision):
    return dict(visual=visual, output=output, seed=seed, capability=capability, source_revision=source_revision)


def _execution_content(content):
    # Only for validated content: execution graph and the full seed lifecycle
    # must match. Layout/serialization metadata may change on native reopening.
    return dict(visual_id=content['visual']['id'], **{k: content[k] for k in
                ('output', 'seed', 'capability', 'source_revision')})


def _with_seed(content, value, executed):
    result = copy.deepcopy(content)
    seed = result['seed']; seed.update(seed=value, hasExecuted=executed)
    result['output'][seed['node_id']]['inputs']['seed'] = value
    next(node for node in result['visual']['nodes'] if str(node['id']) == seed['node_id'])['widgets_values'][0] = value
    return result


class BackgroundState:
    def __init__(self, service, *, multi_user=False, draw=None):
        self.service = service
        self.multi_user = multi_user
        self.draw = draw
        self.server_epoch = str(uuid.uuid4())
        with self._transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS background_states (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS background_operations (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS background_receipts (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            # No restart silently releases a missing writer or resubmits a job.
            for ident, raw in db.execute('SELECT key,body FROM background_states').fetchall():
                state = json.loads(raw); state['phase'] = 'unknown'
                self._save_state(db, ident, state)

    @contextmanager
    def _transaction(self):
        if self.multi_user:
            _fail('multi_user_unsupported')
        with self.service.lock, self.service.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    def _epoch(self, value):
        if value.get('server_epoch') != self.server_epoch:
            _fail('server_epoch')

    @staticmethod
    def _read_state(db, ident):
        row = db.execute('SELECT body FROM background_states WHERE key=?', (ident,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _save_state(db, ident, state):
        db.execute('INSERT OR REPLACE INTO background_states VALUES (?,?)', (ident, _json(state)))

    @staticmethod
    def _save_operation(db, operation):
        db.execute('INSERT OR REPLACE INTO background_operations VALUES (?,?)', (operation['op_id'], _json(operation)))

    def _state(self, db, value):
        key, ident = _key(value.get('key')); state = self._read_state(db, ident)
        if state is None:
            _fail('state_missing')
        return key, ident, state

    def _checked_lease(self, state, value):
        if state['phase'] != 'editing' or any(state.get(k) != value.get(k) for k in ('lease_id', 'lease_epoch')):
            _fail('lease_conflict')

    @staticmethod
    def _pending(db, ident):
        return any(json.loads(raw)['key_hash'] == ident and json.loads(raw)['state'] in ('prepared', 'uncertain')
                   for raw, in db.execute('SELECT body FROM background_operations'))

    def _receipt(self, db, method, value):
        op_id = _text(value.get('op_id'))
        row = db.execute('SELECT body FROM background_receipts WHERE id=?', (op_id,)).fetchone()
        request_hash = _digest([method, value])
        if row:
            receipt = json.loads(row[0])
            if receipt['request_hash'] != request_hash:
                _fail('operation_conflict')
            return op_id, request_hash, receipt['result']
        return op_id, request_hash, None

    @staticmethod
    def _store_receipt(db, op_id, request_hash, result):
        db.execute('INSERT INTO background_receipts VALUES (?,?)',
                   (op_id, _json(dict(request_hash=request_hash, result=result))))

    def _summary(self, state):
        return {k: copy.deepcopy(state.get(k)) for k in ('key', 'revision', 'digest', 'phase', 'edit_seq',
            'capture_op_id','release_op_id','advance_op_id','advance_from_revision')}

    def lease(self, value):
        key, ident = _key(value.get('key')); session = _text(value.get('session'))
        base = _integer(value.get('base_revision'))
        with self._transaction() as db:
            state = self._read_state(db, ident)
            if self._pending(db, ident):
                _fail('submission_unresolved')
            if state is not None and state['phase'] == 'unknown':
                _fail('recovery_required')
            if state is None:
                state = dict(key=key, revision=0, digest='', phase='new', lease_epoch=0, edit_seq=-1, content=None)
            if state['revision'] != base:
                _fail('revision_conflict')
            if state['phase'] == 'editing':
                if state.get('session') != session:
                    _fail('writer_conflict')
            else:
                state.update(phase='editing', session=session, lease_id=str(uuid.uuid4()),
                             lease_epoch=state['lease_epoch'] + 1, edit_seq=-1, server_epoch=self.server_epoch)
                self._save_state(db, ident, state)
            return dict(self._summary(state), lease_id=state['lease_id'], lease_epoch=state['lease_epoch'], server_epoch=self.server_epoch)

    def capture_receipt(self, value):
        """Read the exact operation acknowledgement, including a lost reply."""
        self._epoch(value)
        with self._transaction() as db:
            return copy.deepcopy(self._receipt(db, 'capture', value)[2])

    def capture(self, value):
        self._epoch(value)
        with self._transaction() as db:
            op_id, request_hash, cached = self._receipt(db, 'capture', value)
            if cached is not None:
                return cached
            key, ident, state = self._state(db, value); self._checked_lease(state, value)
            if _integer(value.get('base_revision')) != state['revision']:
                _fail('revision_conflict')
            seq = _integer(value.get('edit_seq'))
            if seq <= state['edit_seq']:
                _fail('edit_sequence')
            content = _content(*(copy.deepcopy(value.get(k)) for k in ('visual', 'output', 'seed', 'capability', 'source_revision')))
            if len(_json(content).encode('utf-8')) > 4 * 1024 * 1024:
                _fail('capture_too_large')
            if not isinstance(content['seed'], dict) or not isinstance(content['capability'], dict):
                _fail('capture_shape')
            _validate_capture(key, **content)
            self._retain_equivalent_jobs(db, ident, state, content, op_id)
            state.update(content=content, revision=state['revision'] + 1, digest=_digest(content), edit_seq=seq,
                capture_op_id=op_id,release_op_id='',advance_op_id='',advance_from_revision=None,
                source_receipt=copy.deepcopy(value.get('source_receipt')))
            self._save_state(db, ident, state)
            result = dict(committed=True, revision=state['revision'], digest=state['digest'], edit_seq=seq,
                          op_id=op_id, server_epoch=self.server_epoch, source_revision=content['source_revision'])
            self._store_receipt(db, op_id, request_hash, result)
            return result

    def _retain_equivalent_jobs(self, db, ident, state, content, capture_op_id):
        previous = state['content']
        if previous is None or _json(_execution_content(previous)) != _json(_execution_content(content)):
            return
        for raw, in db.execute('SELECT body FROM background_operations').fetchall():
            operation = json.loads(raw)
            if (operation['state'] != 'queued' or operation['key_hash'] != ident
                    or operation['key'] != state['key'] or operation['server_epoch'] != self.server_epoch
                    or operation.get('next_revision') != state['revision']
                    or operation.get('next_digest') != state['digest']):
                continue
            evidence = dict(capture_op_id=capture_op_id, from_revision=state['revision'],
                            from_digest=state['digest'], to_revision=state['revision'] + 1,
                            to_digest=_digest(content), execution_digest=_digest(_execution_content(content)))
            operation.setdefault('equivalent_revisions', []).append(evidence)
            operation.update(next_revision=evidence['to_revision'], next_digest=evidence['to_digest'])
            self._save_operation(db, operation)

    def release(self, value):
        self._epoch(value)
        _integer(value.get('revision')); _integer(value.get('edit_seq'))
        with self._transaction() as db:
            op_id, request_hash, cached = self._receipt(db, 'release', value)
            if cached is not None:
                return cached
            _, ident, state = self._state(db, value); self._checked_lease(state, value)
            if state['content'] is None or any(state[k] != value.get(k) for k in ('revision', 'digest', 'edit_seq')):
                _fail('release_uncommitted')
            state.update(phase='released',release_op_id=op_id); self._save_state(db, ident, state)
            result = dict(released=True, revision=state['revision'], digest=state['digest'], op_id=op_id, server_epoch=self.server_epoch)
            self._store_receipt(db, op_id, request_hash, result)
            return result

    def start(self, value):
        self._epoch(value)
        _integer(value.get('revision'))
        _sha256(value.get('source_revision'))
        with self._transaction() as db:
            op_id, request_hash, cached = self._receipt(db, 'start', value)
            if cached is not None:
                row = db.execute('SELECT body FROM background_operations WHERE id=?', (op_id,)).fetchone()
                return dict(json.loads(row[0]), submit_allowed=False)
            key, ident, state = self._state(db, value)
            if state['phase'] != 'released' or state['server_epoch'] != self.server_epoch:
                _fail('not_released')
            if any(state[k] != value.get(k) for k in ('revision', 'digest')):
                _fail('revision_conflict')
            if state['content']['source_revision'] != value['source_revision']:
                _fail('source_changed')
            if self._pending(db, ident):
                _fail('submission_unresolved')
            transition = prepare_seed(state['content']['seed'], self.draw)
            execution = _with_seed(state['content'], transition['execution_seed'], True)
            after = _with_seed(state['content'], transition['after_seed'], True)
            operation = dict(op_id=op_id, key=key, key_hash=ident, request_hash=request_hash, state='prepared',
                             server_epoch=self.server_epoch, revision=state['revision'], digest=state['digest'],
                             source_revision=state['content']['source_revision'], seed_transition=transition, after=after, prompt_id=None,
                             payload=dict(prompt=execution['output'], extra_data=dict(extra_pnginfo=dict(
                                 workflow=execution['visual'], pcs_background=dict(version=1, operation_id=op_id,
                                     revision=state['revision'], digest=state['digest'],
                                     source_revision=state['content']['source_revision'])))))
            self._save_operation(db, operation)
            self._store_receipt(db, op_id, request_hash, {})
            return dict(copy.deepcopy(operation), submit_allowed=True)

    def _operation(self, db, value):
        self._epoch(value); key, ident = _key(value.get('key')); op_id = _text(value.get('op_id'))
        row = db.execute('SELECT body FROM background_operations WHERE id=?', (op_id,)).fetchone()
        if not row:
            _fail('operation_missing')
        operation = json.loads(row[0])
        if operation['key_hash'] != ident or operation['server_epoch'] != self.server_epoch:
            _fail('operation_identity')
        return ident, operation

    def bind_prompt(self, value):
        prompt_id = _text(value.get('prompt_id'))
        with self._transaction() as db:
            ident, operation = self._operation(db, value)
            if operation['state'] == 'queued':
                if operation['prompt_id'] != prompt_id:
                    _fail('prompt_conflict')
                return operation
            if operation['state'] not in ('prepared', 'uncertain'):
                _fail('submission_state')
            if any(json.loads(raw).get('prompt_id') == prompt_id for raw, in db.execute('SELECT body FROM background_operations')):
                _fail('prompt_conflict')
            state = self._read_state(db, ident)
            if state['revision'] != operation['revision'] or state['digest'] != operation['digest'] or state['phase'] not in ('released', 'uncertain'):
                _fail('revision_conflict')
            state.update(content=operation['after'], revision=state['revision'] + 1,
                         digest=_digest(operation['after']), phase='released',advance_op_id=operation['op_id'],
                         advance_from_revision=operation['revision'])
            operation.update(state='queued', prompt_id=prompt_id, next_revision=state['revision'], next_digest=state['digest'])
            self._save_state(db, ident, state); self._save_operation(db, operation)
            return copy.deepcopy(operation)

    def submission_failed(self, value):
        if type(value.get('uncertain')) is not bool:
            _fail('submission_state')
        with self._transaction() as db:
            ident, operation = self._operation(db, value)
            target = 'uncertain' if value['uncertain'] else 'failed'
            if operation['state'] not in ('prepared', target):
                _fail('submission_state')
            operation['state'] = target
            if target == 'uncertain':
                state = self._read_state(db, ident); state['phase'] = 'uncertain'
                self._save_state(db, ident, state)
            self._save_operation(db, operation)
            return copy.deepcopy(operation)

    def snapshot(self, value):
        with self._transaction() as db:
            _, ident, state = self._state(db, value)
            return dict(self._summary(state), content=copy.deepcopy(state['content']), server_epoch=self.server_epoch,
                        unresolved=self._pending(db, ident), source_receipt=copy.deepcopy(state.get('source_receipt')))

    def attach_snapshot(self, value):
        self._epoch(value); _text(value.get('new_client_id')); prompt_id = _text(value.get('prompt_id'))
        _integer(value.get('revision'))
        with self._transaction() as db:
            _, ident, state = self._state(db, value)
            candidates = [json.loads(raw) for raw, in db.execute('SELECT body FROM background_operations')]
            operation = next((op for op in candidates if op['key_hash'] == ident and op['prompt_id'] == prompt_id), None)
            if not operation or operation['state'] != 'queued' or operation['server_epoch'] != self.server_epoch:
                _fail('attach_job')
            if any(operation[k] != value.get(k) for k in ('revision', 'digest')):
                _fail('attach_revision')
            if state['phase'] not in ('released', 'editing') or state['revision'] != operation['next_revision'] or state['digest'] != operation['next_digest']:
                _fail('attach_current_changed')
            return dict(prompt_id=prompt_id, revision=operation['revision'], digest=operation['digest'],
                        payload=copy.deepcopy(operation['payload']), current=dict(self._summary(state), content=copy.deepcopy(state['content'])),
                        server_epoch=self.server_epoch)
