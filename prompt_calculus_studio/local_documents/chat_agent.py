"""Portable, inert agent binding and per-attempt evidence; no tool execution."""
import json
from .schema import fail, identifier

FEATURE = 'pcs.chat-agent.v1'


def validate_binding(value):
    if not isinstance(value, str) or value and not identifier(value):
        fail('invalid', '對話的 Agent 身分無效。')


def validate_evidence(value):
    if not isinstance(value, dict) or set(value) != {'request', 'events'}:
        fail('invalid', 'Agent 使用紀錄格式無效。')
    if not isinstance(value['request'], dict) or not isinstance(value['events'], list) or len(value['events']) > 100:
        fail('invalid', 'Agent 使用紀錄過多或格式無效。')
    # Stored data is inert. A reopened/imported attempt is never an execution request.
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 2 * 1024 * 1024:
        fail('limit', 'Agent 使用紀錄超過 2 MiB。')
    for event in value['events']:
        if (not isinstance(event, dict) or set(event) != {'operation', 'target', 'content', 'sha256', 'status', 'skill'}
                or any(not isinstance(v, str) for v in event.values())):
            fail('invalid', 'Agent 讀取紀錄格式無效。')
