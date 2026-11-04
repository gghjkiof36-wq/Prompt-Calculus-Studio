"""Bounded task receipts; ordinary .pcs-chat remains the conversation owner."""
import copy
import json
from pathlib import Path
from datetime import datetime, timezone

from ..local_ai.registry import atomic_json
from ..local_ai.thinking import split_thinking
from .sources import read_bytes
from .delegation_protocol import TASK_ID, DelegationError, encode, fingerprint

RECORD_LIMIT = 4 * 1024 * 1024
SOURCE_OPERATIONS = {'rule', 'read_skill', 'read_source', 'read_file', 'search_files', 'list_skills', 'tool_error'}
TERMINAL = {'complete', 'stopped', 'failed', 'limited', 'interrupted', 'save_failed'}


def now():
    return datetime.now(timezone.utc).isoformat()


def attempt_in(payload, turn_id, attempt_id):
    histories = [payload.get('turns', [])] + [r['turns'] for r in payload.get('revisions', [])]
    for turns in histories:
        for turn in turns:
            if turn['id'] == turn_id:
                for attempt in turn['attempts']:
                    if attempt['id'] == attempt_id:
                        return attempt
    return None


def result_of(attempt, *, saved, state, receipt=None):
    """Export only the answer and actual read events, never wire/thinking logs."""
    attempt = attempt or {}
    answer = split_thinking(attempt.get('text', ''), receiving=True).answer
    raw = answer.encode('utf-8')
    shortened = len(raw) > 256 * 1024
    answer = raw[:256*1024].decode('utf-8', errors='ignore')
    agent = attempt.get('agent', {})
    request = agent.get('request', {})
    evidence, used = [], 0
    evidence_shortened = False
    for event in agent.get('events', []):
        if event['operation'] not in SOURCE_OPERATIONS:
            continue
        item = copy.deepcopy(event)
        size = len(encode(item))
        if used + size > 768 * 1024:
            evidence_shortened = True
            break
        used += size
        evidence.append(item)
    project, role = request.get('project', {}), request.get('agent', {})
    configuration = dict(
        project={k: project.get(k) for k in ('id', 'name', 'cwd')},
        agent={k: role.get(k) for k in ('id', 'name', 'model')},
        approval=request.get('approval'), model=attempt.get('model'),
        parameters=copy.deepcopy(attempt.get('parameters', {})),
        reasoning=copy.deepcopy(attempt.get('reasoning')),
        registered_sources=copy.deepcopy(project.get('sources', [])),
        skills=[dict(id=s['id'], name=s['name'], root=s['root'], version=fingerprint(s)) for s in request.get('skills', [])],
    )
    return dict(state=state, saved=saved, review_required=True, draft=answer,
                reason=attempt.get('reason', ''), configuration=configuration,
                evidence=evidence, saved_receipt=receipt,
                limits=dict(draft_truncated=shortened, evidence_truncated=evidence_shortened,
                            semantic_verified=False, raw_reasoning_included=False,
                            registered_snapshots_precede_tool_approval=True))


class TaskStore:
    def __init__(self, data):
        self.data = Path(data).resolve()
        self.directory = self.data/'local_delegation'
        self.settings_path = self.directory/'settings.json'

    def checked(self, path):
        # Do not follow a redirected receipt/settings directory out of data.
        path = Path(path)
        if not path.resolve().is_relative_to(self.data):
            raise DelegationError('storage_unavailable', '委派資料位置已變更。')
        return path

    def settings(self):
        path = self.checked(self.settings_path)
        if not path.exists():
            return set()
        value = json.loads(read_bytes(path, 64*1024))
        if (not isinstance(value, dict) or set(value) != {'version', 'projects'} or value['version'] != 1
                or not isinstance(value['projects'], list) or len(value['projects']) > 1000
                or any(not isinstance(p, str) for p in value['projects'])):
            raise DelegationError('storage_unavailable', '本機委派設定無效。')
        return set(value['projects'])

    def save_settings(self, projects):
        atomic_json(self.checked(self.settings_path), dict(version=1, projects=sorted(projects)))

    def path(self, task_id):
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
            raise DelegationError('unknown_task', '找不到這個委派任務。')
        return self.checked(self.directory/'tasks'/(task_id+'.json'))

    def read(self, task_id):
        path = self.path(task_id)
        if not path.exists():
            return None
        value = json.loads(read_bytes(path, RECORD_LIMIT))
        if (not isinstance(value, dict) or value.get('version') != 1 or value.get('task_id') != task_id
                or not {'state', 'project_id', 'agent_id', 'input_hash', 'document_id', 'path'}.issubset(value)):
            raise DelegationError('storage_unavailable', '委派回執無效，原對話仍保留。')
        return value

    def write(self, record):
        if len(encode(record)) > RECORD_LIMIT:
            raise DelegationError('storage_unavailable', '委派回執超過大小限制。')
        atomic_json(self.path(record['task_id']), record)
