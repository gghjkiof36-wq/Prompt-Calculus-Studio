"""Manager wire records and durable operation receipts, independent of Qt.

The v2 contract was checked against ComfyUI-Manager 4.3 (f764cc0e).
Legacy queue counters are deliberately not treated as operation receipts.
"""
from __future__ import annotations

import json
import math
import os
import time
import uuid
from pathlib import Path

ACTIVE = frozenset({'submitting', 'queued', 'running', 'unknown'})
TERMINAL = frozenset({'pending_restart', 'skipped', 'failed', 'not_sent'})
MAX_BODY = 8 * 1024 * 1024


def validate_journal(data):
    def text(value, required=True, limit=2000):
        return isinstance(value, str) and (not required or bool(value)) and len(value) <= limit

    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError('journal version')
    if not text(data.get('client_id')) or not isinstance(data.get('operations'), list) or not isinstance(data.get('pins'), dict):
        raise ValueError('journal root')
    for server, pins in data['pins'].items():
        if not text(server) or not isinstance(pins, list) or not all(text(ident) for ident in pins):
            raise ValueError('journal pins')
    identities = set()
    for operation in data['operations']:
        if not isinstance(operation, dict) or not text(operation.get('id')) or not text(operation.get('server')) or not isinstance(operation.get('tasks'), list):
            raise ValueError('journal operation')
        created = operation.get('created')
        if type(created) not in (float, int) or not 0 <= created <= 1e12 or not math.isfinite(created):
            raise ValueError('journal timestamp')
        for task in operation['tasks']:
            if not isinstance(task, dict) or task.get('state') not in ACTIVE | TERMINAL:
                raise ValueError('journal task')
            if not all(text(task.get(key)) for key in ('ui_id', 'name', 'package_id')) or not all(text(task.get(key), required=False) for key in ('before', 'message')):
                raise ValueError('journal task fields')
            if task['ui_id'] in identities:
                raise ValueError('journal duplicate identity')
            identities.add(task['ui_id'])
            params = task.get('params')
            if not isinstance(params, dict):
                raise ValueError('journal params')
            if task.get('kind') == 'update':
                if not text(params.get('node_name')) or not text(params.get('node_ver')):
                    raise ValueError('journal update params')
            elif task.get('kind') == 'update-comfyui':
                if type(params.get('is_stable')) is not bool or task['package_id'] != '__comfyui__':
                    raise ValueError('journal core params')
            else:
                raise ValueError('journal operation kind')


def plain(value, limit=500):
    return str(value or '').replace('\x00', '')[:limit]


def installed_packages(data):
    if not isinstance(data, dict) or len(data) > 20000:
        raise ValueError('Manager 套件清單格式不符')
    result = []
    for ident, entry in data.items():
        if not isinstance(ident, str) or not ident or not isinstance(entry, dict):
            raise ValueError('Manager 套件清單格式不符')
        if not isinstance(entry.get('ver'), str) or type(entry.get('enabled')) is not bool:
            raise ValueError('Manager 套件版本格式不符')
        result.append(dict(id=ident, name=ident, version=entry['ver'], enabled=entry['enabled'],
                           cnr_id=plain(entry.get('cnr_id')), aux_id=plain(entry.get('aux_id')),
                           author='', description='', repository='', update_available=None))
    return sorted(result, key=lambda item: item['name'].casefold())


def enrich_legacy(packages, catalog):
    rows = catalog.get('node_packs', {}) if isinstance(catalog, dict) else {}
    if not isinstance(rows, dict):
        return packages
    for pack in packages:
        entry = rows.get(pack['cnr_id']) or rows.get(pack['aux_id']) or rows.get(pack['id'])
        if not isinstance(entry, dict):
            continue
        pack.update(name=plain(entry.get('title') or pack['name']), author=plain(entry.get('author')),
                    description=plain(entry.get('description'), 3000), repository=plain(entry.get('repository')),
                    update_available=entry.get('update-state') in ('true', True))
    return packages


def queue_busy(data):
    if not isinstance(data, dict) or type(data.get('is_processing')) is not bool:
        raise ValueError('Manager 工作狀態格式不符')
    counts = [data.get(key, 0) for key in ('total_count', 'done_count', 'in_progress_count', 'pending_count')]
    if any(type(value) is not int or value < 0 for value in counts):
        raise ValueError('Manager 工作數量格式不符')
    # Legacy total includes completed work. v2 total does not consistently do so.
    return data['is_processing'] or counts[2] > 0 or counts[3] > 0 or counts[0] > counts[1]


def is_manager_package(pack):
    # Installed folder names are user-changeable. Check Registry/Git identity too.
    identities = (pack.get('id'), pack.get('cnr_id'), pack.get('aux_id'))
    return any(str(value or '').rstrip('/').rsplit('/', 1)[-1].lower().removesuffix('.git').replace('_', '-') == 'comfyui-manager'
               for value in identities)


def make_plan(packages, selected, pins=(), include_core=False):
    by_id = {pack['id']: pack for pack in packages}
    plan = []
    for ident in dict.fromkeys(selected):
        if ident not in by_id:
            raise ValueError('套件清單已變更，請重新整理')
        pack = by_id[ident]
        if not pack['enabled'] or ident in pins:
            continue
        if is_manager_package(pack):
            continue  # Manager may be bundled with Desktop; it owns its own update route.
        if not pack['cnr_id'] and not pack['aux_id']:
            continue
        node_name = pack['cnr_id'] or ident
        # installed.ver is a commit hash for Git installs, not a version selector.
        # Passing that hash to unified_update would switch a Registry-linked Git
        # pack onto the Registry release channel. Preserve the installed source.
        node_ver = ('nightly' if pack['aux_id'] else pack['version']) if pack['cnr_id'] else 'unknown'
        plan.append(dict(name=pack['name'], package_id=ident, before=pack['version'], kind='update',
                         params=dict(node_name=node_name, node_ver=node_ver)))
    if include_core:
        plan.append(dict(name='ComfyUI', package_id='__comfyui__', before='', kind='update-comfyui',
                         params=dict(is_stable=True)))
    if not plan:
        raise ValueError('沒有可更新的已啟用套件')
    return plan


class Journal:
    """Write before submit; unresolved requests remain blocked across app restarts."""
    def __init__(self, path):
        self.path = Path(path)
        self.data = dict(version=1, client_id='pcs-manager-' + uuid.uuid4().hex, operations=[], pins={})
        self.error = ''
        if self.path.exists():
            try:
                if self.path.stat().st_size > MAX_BODY:
                    raise ValueError()
                loaded = json.loads(self.path.read_text(encoding='utf-8'))
                validate_journal(loaded)
                for operation in loaded['operations']:
                    for task in operation['tasks']:
                        task['message'] = ('Manager 回報更新失敗，請查看 ComfyUI 紀錄' if task['state'] == 'failed' else
                                           '操作結果待確認' if task['state'] == 'unknown' else '')
                self.data = loaded
            except (OSError, ValueError, TypeError, AttributeError):
                self.error = '操作紀錄無法讀取，請先保留原檔'

    def save(self):
        if self.error:
            raise ValueError(self.error)
        validate_journal(self.data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(self.data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def operations(self, server):
        return [op for op in self.data['operations'] if op['server'] == server]

    def unresolved(self, server):
        return [task for op in self.operations(server) for task in op['tasks'] if task['state'] in ACTIVE]

    def begin(self, server, plan):
        if self.unresolved(server):
            raise ValueError('上次操作結果待確認')
        operation = dict(id=uuid.uuid4().hex, server=server, created=time.time(), tasks=[])
        for item in plan:
            operation['tasks'].append(dict(item, ui_id='pcs-' + uuid.uuid4().hex, state='not_sent', message=''))
        self.data['operations'].append(operation)
        self.save()
        return operation

    def pins(self, server):
        values = self.data['pins'].get(server, [])
        return set(values) if isinstance(values, list) else set()

    def pin(self, server, ident, enabled):
        values = self.pins(server)
        (values.add if enabled else values.discard)(ident)
        self.data['pins'][server] = sorted(values)
        self.save()

    def reconcile(self, server, history):
        if not isinstance(history, dict):
            raise ValueError('Manager 操作紀錄格式不符')
        changed = False
        for task in self.unresolved(server):
            entry = history.get(task['ui_id'])
            if not isinstance(entry, dict) or entry.get('client_id') != self.data['client_id'] or entry.get('ui_id') != task['ui_id'] or entry.get('kind') != task['kind']:
                continue
            status = entry.get('status')
            if not isinstance(status, dict) or status.get('completed') is not True:
                continue
            result = status.get('status_str')
            state = {'success': 'pending_restart', 'skip': 'skipped', 'skipped': 'skipped', 'failed': 'failed', 'error': 'failed'}.get(result)
            if state:
                task['state'] = state
                # Receipts may contain command output, private paths or credential URLs.
                # Persist only a local status sentence; remote diagnostics stay in ComfyUI.
                task['message'] = 'Manager 回報更新失敗，請查看 ComfyUI 紀錄' if state == 'failed' else ''
                changed = True
        if changed:
            self.save()
        return changed
