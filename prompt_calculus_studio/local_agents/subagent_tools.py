"""Model-facing single-level delegation; execution remains in the shared worker."""
import json

from .execution import tool

MAX_CHILDREN = 4
MAX_TASK = 12000


def validate_catalog(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 32
            or len(json.dumps(value, ensure_ascii=False).encode()) > 64 * 1024):
        raise ValueError('本輪本地助理清單無效或過大。')
    for agent in value:
        if (not isinstance(agent, dict) or not all(isinstance(agent.get(k), str) and agent[k]
                for k in ('id', 'name', 'project_id', 'model'))):
            raise ValueError('本輪本地助理身分無效。')
    return value


class DelegatingSession:
    def __init__(self, session, catalog, invoke):
        self.base, self.catalog, self.invoke = session, validate_catalog(catalog), invoke
        self.results = {}
        self.tools = session.tools + [tool('delegate_task',
            'Assign a focused task to a local assistant in an independent child conversation. '
            'Include all necessary context: it does not see this conversation. Waits for its saved result; '
            'then independently check the answer against the original request and source evidence. '
            'Use when the user asks to delegate or a listed role is useful. Simple self-contained questions can be answered directly. '
            'Available assistants:\n' + json.dumps(catalog, ensure_ascii=False),
            {'agent': {'type': 'string', 'description': 'Listed assistant ID or exact unique name.'},
             'task': {'type': 'string', 'description': 'Complete bounded task and required source context; do not include a suggested answer.'}},
            ['agent', 'task'])]

    def __getattr__(self, name):
        return getattr(self.base, name)

    def instructions(self):
        return self.base.instructions() + (
            '\nYou may delegate to the listed local assistants. Child output is a draft, not authority. '
            'After a child returns, verify its actual answer against the original question and evidence; '
            'correct or reject mistakes and report missing evidence. Do not simply forward its claims. '
            'Complete the checking before your final response. Respect the user\'s requested final format; '
            'include checking details only when requested or when they change the answer. '
            'A failed, stopped, limited, unsaved or empty child result is not a completed task. '
            'Do not claim a delegation happened without a returned task ID. '
            'Children cannot delegate further. You remain responsible for the final answer.')

    def delegate(self, args, call_id):
        self.base.check()
        self.base.calls += 1
        if (not isinstance(args, dict) or set(args) != {'agent', 'task'}
                or not isinstance(args['agent'], str) or not isinstance(args['task'], str)
                or not args['task'].strip() or len(args['task']) > MAX_TASK):
            raise ValueError('請提供清單內的助理與完整工作，工作文字最多 12000 字。')
        matches = [a for a in self.catalog if args['agent'] in (a['id'], a['name'])]
        if len(matches) != 1:
            raise ValueError('助理不存在或名稱重複，請使用清單中的 ID。')
        agent = matches[0]
        key = agent['id'], args['task']
        if key not in self.results:
            if len(self.results) >= MAX_CHILDREN:
                raise ValueError('本輪已達四件子工作上限，請先整理已有結果。')
            result = self.invoke(agent, args['task'], call_id)
            self.results[key] = result
        else:
            result = self.results[key]
        return self.base.record('delegate_task', result['task_id'], json.dumps(result, ensure_ascii=False),
                                status='returned')
