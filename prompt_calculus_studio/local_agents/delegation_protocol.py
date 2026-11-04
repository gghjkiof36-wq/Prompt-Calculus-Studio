"""Small, Qt-free protocol for same-user local delegation, separate from Chrome."""
import hashlib
import json
import os
from pathlib import Path
import re

MAX_REQUEST = 64 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
MAX_TASK = 16000
IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
TASK_ID = re.compile(r'[0-9a-f]{64}\Z')
PIPE_NAME = re.compile(r'pcs-agent-[0-9a-f]{24}\Z')


class DelegationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def fingerprint(value):
    return hashlib.sha256(encode(value)).hexdigest()


def discovery_directory():
    override = os.environ.get('PCS_AGENT_DISCOVERY')
    if override:
        return Path(override).absolute()
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'.local'/'share')))
    return base/'PromptCalculusStudio'/'AgentDelegation'/'instances'


def validate(request):
    if not isinstance(request, dict):
        raise DelegationError('invalid_request', '委派請求格式無效。')
    command = request.get('command')
    fields = {
        'catalog': set(),
        'submit': {'project_id', 'agent_id', 'task', 'idempotency_key'},
        'status': {'task_id'}, 'stop': {'task_id'}, 'result': {'task_id'},
    }
    common = {'version', 'command', 'request_id', 'instance_id', 'connection_id', 'token'}
    if command not in fields or set(request) != common | fields[command]:
        raise DelegationError('invalid_request', '不支援此命令或額外的設定。')
    if type(request['version']) is not int or request['version'] != 1:
        raise DelegationError('invalid_request', '不支援此委派版本。')
    for field in ('request_id', 'instance_id', 'connection_id', *({'project_id', 'agent_id', 'idempotency_key'} if command == 'submit' else ())):
        if not isinstance(request[field], str) or not IDENTIFIER.fullmatch(request[field]):
            raise DelegationError('invalid_request', '委派識別碼無效。')
    if command == 'submit' and (not isinstance(request['task'], str) or not request['task'].strip() or len(request['task']) > MAX_TASK):
        raise DelegationError('invalid_task', '請提供一項明確的唯讀工作，最多 16,000 字。')
    if 'task_id' in request and (not isinstance(request['task_id'], str) or not TASK_ID.fullmatch(request['task_id'])):
        raise DelegationError('unknown_task', '找不到這個委派任務。')
    if len(encode(request)) > MAX_REQUEST:
        raise DelegationError('request_too_large', '委派請求超過大小限制。')
    return request
