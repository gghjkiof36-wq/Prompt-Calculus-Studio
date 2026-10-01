"""Versioned, explicit Stage parameter intentions. No Qt, network or dispatch.

Native graphs remain live. This module never assembles a replacement prompt.
"""
import copy
import hashlib
import json
import math
import re
from decimal import Decimal, InvalidOperation

VERSION = 1
SAFE_INTEGER = 2**53 - 1
SEED_MODES = ('fixed', 'increment', 'decrement', 'randomize')
TYPES = ('INT', 'FLOAT', 'STRING', 'BOOLEAN', 'ENUM')


def finite(value):
    if type(value) not in (int,float):return False
    try:return math.isfinite(value)
    except OverflowError:return False


def key(field):
    return tuple(field.get('path', [field['node']])), field['field']


def identity(profile):
    return dict(workflow=profile['id'], frontend_id=profile.get('frontend_id', ''),
                origin=copy.deepcopy(profile.get('origin', {})))


def empty(profile):
    return dict(version=VERSION, identity=identity(profile), patches=[])


def validate(config):
    if not isinstance(config, dict) or config.get('version') != VERSION:
        raise ValueError('不支援的 Stage 參數版本。')
    owner = config.get('identity')
    if (not isinstance(owner, dict) or not isinstance(owner.get('workflow'), str) or not owner['workflow']
            or not isinstance(owner.get('frontend_id'), str) or not owner['frontend_id']
            or not isinstance(owner.get('origin', {}), dict)):
        raise ValueError('Stage 參數工作流身分無效。')
    if any(not isinstance(v,str) or len(v)>1000 for v in owner.get('origin',{}).values()):raise ValueError('Stage 參數來源無效。')
    fields = config.get('patches')
    if not isinstance(fields, list) or len(fields) > 1000:
        raise ValueError('Stage 參數清單無效。')
    seen = set()
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError('Stage 參數無效。')
        for name in ('node', 'class_type', 'field', 'schema'):
            if not isinstance(field.get(name), str) or not 1 <= len(field[name]) <= 10000:
                raise ValueError('Stage 參數定位無效。')
        path = field.get('path')
        if (not isinstance(path, list) or not 1 <= len(path) <= 32
                or any(not isinstance(p, str) or not p or len(p)>1000 for p in path) or path[-1]!=field['node']
                or field.get('type') not in TYPES or key(field) in seen):
            raise ValueError('Stage 參數路徑、型別或重複欄位無效。')
        seen.add(key(field))
        for name in ('min','max','step'):
            if name in field and not finite(field[name]):raise ValueError('Stage 參數範圍無效。')
        if field.get('step',1)<=0 or field.get('min',-math.inf)>field.get('max',math.inf):raise ValueError('Stage 參數範圍無效。')
        if 'enforce_step' in field and type(field['enforce_step']) is not bool:raise ValueError('Stage 參數步進無效。')
        if field['type']=='ENUM' and (not isinstance(field.get('choices'),list) or len(field['choices'])>10000):raise ValueError('Stage 參數選項無效。')
        validate_value(field.get('value'), field)
        if 'resolved' in field:validate_value(field['resolved'], field)
        if 'base' not in field:
            raise ValueError('Stage 參數缺少外部基準。')
        if type(field['base']) not in (int,float,str,bool) or type(field['base']) is float and not math.isfinite(field['base']):raise ValueError('Stage 參數外部基準無效。')
        if 'seed_mode' in field:
            if (field['field'] not in ('seed', 'noise_seed') or field['type'] != 'INT'
                    or field['seed_mode'] not in SEED_MODES
                    or field.get('seed_timing') not in ('before', 'after')):
                raise ValueError('Stage 種子政策無效。')
    return config


def validate_value(value, field):
    kind = field['type']
    if kind == 'INT':
        valid = type(value) is int and abs(value) <= SAFE_INTEGER
    elif kind == 'FLOAT':
        valid = finite(value)
    elif kind == 'STRING':
        valid = isinstance(value, str) and len(value) <= 100000
    elif kind == 'BOOLEAN':
        valid = type(value) is bool
    else:
        valid = type(value) in (str, int, float, bool) and any(type(v) is type(value) and v==value for v in field.get('choices', []))
    if not valid:
        raise ValueError(field.get('label', field.get('field', '參數')) + '：值或型別無效。')
    if kind in ('INT', 'FLOAT'):
        if ('min' in field and value < field['min']) or ('max' in field and value > field['max']):
            raise ValueError(field.get('field', '參數') + '：超出此節點範圍。')
        step = field.get('step')
        if step and field.get('enforce_step'):
            origin = field.get('min', 0)
            if (Decimal(str(value)) - Decimal(str(origin))) % Decimal(str(step)):
                raise ValueError(field['field'] + '：不符合此節點步進。')
    return value


def parse(text, field):
    """Intermediate editor text stays in the UI; only Apply calls this parser."""
    kind = field['type']
    if kind == 'INT':
        if not isinstance(text, str) or not re.fullmatch(r'-?[0-9]+', text):
            raise ValueError(field['field'] + '：請輸入完整整數。')
        value = int(text)
    elif kind == 'FLOAT':
        try:
            value = float(Decimal(text))
        except (InvalidOperation, ValueError, OverflowError):
            raise ValueError(field['field'] + '：請輸入完整數值。') from None
    else:
        value = text
    return validate_value(value, field)


def capture(state, keys):
    """Capture only explicit PCS overrides, including explicit empty intentions."""
    result = {}
    for stage in keys:
        config = state['multi_output']['stages'][stage].get('parameters')
        result[stage] = copy.deepcopy(config) if config is not None else None
    return result


def effective(saved, stage):
    # Missing on an old queue row means no overrides. Never backfill from live UI.
    config = saved.get('parameters', {}).get(stage)
    custom = saved.get('parameter_overrides', {}).get(stage)
    if custom is not None:
        config = custom
    return copy.deepcopy(config)


def fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode('utf-8')).hexdigest()


def descriptor_fields(description):
    return {key(dict(f, node=n['id'], path=n['path'])): dict(f, node=n['id'],
            path=n['path'], class_type=n['class_type'], node_title=n['title'])
            for n in description.get('nodes', []) for f in n.get('fields', [])}


def seed_signature(config, field):
    # Display labels and unrelated parameters never restart a seed intention.
    names = ('node', 'path', 'class_type', 'field', 'schema', 'value',
             'seed_mode', 'seed_timing', 'intent_id')
    return fingerprint(dict(identity=config['identity'],
                            field={k:field[k] for k in names if k in field}))


def verify(config, description, *, conflict=True, owned=()):
    """Check each intended target; unrelated names, positions and values are ignored."""
    validate(config)
    if config['identity'] != description.get('identity'):
        raise ValueError('參數工作流身分已變更，請重新對應。')
    actual = descriptor_fields(description)
    errors = []
    for patch in config['patches']:
        field = actual.get(key(patch))
        reason = None
        if field is None or field.get('class_type') != patch['class_type']:
            reason = '節點或欄位已移除／變更'
        elif field.get('schema') != patch['schema'] or field.get('type') != patch['type']:
            reason = '欄位定義或序列化已變更'
        elif not field.get('editable'):
            reason = field.get('reason') or '此欄位不可編輯'
        elif (patch['node'], patch['field']) in owned:
            reason = '此欄位由 PCS 輸入供應'
        elif conflict and field.get('value') != patch['base']:
            reason = 'ComfyUI 同一欄位已被修改'
        if reason:
            errors.append('#' + '/'.join(patch['path']) + ' / ' + patch['field'] + '：' + reason)
        else:
            validate_value(patch['value'], field)
    if errors:
        raise ValueError('\n'.join(errors))
    return config


def payload_evidence(config, graph, receipt):
    """Verify the actual native serialization, without modifying any input."""
    validate(config)
    if not isinstance(receipt, list) or len(receipt) != len(config['patches']):
        raise ValueError('未取得完整的原生參數套用證據。')
    seen = set()
    for patch, proof in zip(config['patches'], receipt):
        node = graph.get(patch['node'], {})
        value = node.get('inputs', {}).get(patch['field'])
        if (not isinstance(proof, dict) or proof.get('node') != patch['node']
                or proof.get('field') != patch['field'] or proof.get('schema') != patch['schema']
                or proof.get('path')!=patch['path'] or proof.get('class_type')!=patch['class_type'] or proof.get('requested')!=patch['value']
                or node.get('class_type') != patch['class_type'] or isinstance(value, list)
                or proof.get('actual') != value or key(patch) in seen):
            raise ValueError('原生參數回執與指定欄位不符。')
        validate_value(value, patch)
        if not patch.get('seed_mode') or patch['seed_mode'] == 'fixed':
            expected = patch.get('resolved', patch['value'])
            if value != expected:
                raise ValueError('#' + patch['node'] + ' / ' + patch['field'] + ' 尚未正確套用。')
        if patch.get('seed_mode') and (proof.get('seed_mode') != patch['seed_mode'] or proof.get('seed_timing') != patch['seed_timing']):
            raise ValueError('原生種子政策與指定政策不符。')
        seen.add(key(patch))
    return copy.deepcopy(receipt)
