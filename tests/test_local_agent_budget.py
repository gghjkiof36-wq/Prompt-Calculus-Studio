"""Focused navigation, raw-vs-model evidence and bounded final-answer controls."""
import copy
import json
import time
import unittest
from unittest.mock import patch

from prompt_calculus_studio.local_agents.execution import ReadSession, run
from prompt_calculus_studio.local_agents.transcript import ToolTranscript
from prompt_calculus_studio.local_ai.engine import Cancellation, Cancelled
from prompt_calculus_studio.local_documents.chat_agent import validate_evidence
import test_local_agents as core


class Navigation(unittest.TestCase):
    setUp = core.AgentCore.setUp
    request = core.AgentCore.request

    def test_real_paths_symbols_and_small_function_read(self):
        session = ReadSession(self.request(), Cancellation())
        found = session.execute('search_files', dict(glob='*.py', query='save', mode='symbols'))
        self.assertEqual(found, 'save.py:1-3: save')
        result = session.execute('read_file', dict(path='save.py', symbol='save', start=2, count=1))
        self.assertEqual(result, '2:     validate()')
        with self.assertRaisesRegex(ValueError, 'save.py'):
            session.execute('read_file', dict(path='made/up/save.py'))
        with self.assertRaisesRegex(ValueError, '唯一'):
            session.execute('read_file', dict(path='save.py', symbol='invented'))
        with self.assertRaisesRegex(ValueError, '模式'):
            session.execute('search_files', dict(glob='*.py', query='save.*', mode='regex'))
        self.assertIn('正規表示式', session.execute('search_files', dict(glob='*.py', query='save.*')))
        self.assertIn('in save lines 1-3', session.execute('search_files', dict(glob='*.py', query='validate')))
        fallback = session.execute('search_files', dict(glob='wrong/dir/save.py', query='', mode='symbols'))
        self.assertIn('唯一同名', fallback)
        self.assertIn('save.py:1-3: save', fallback)

    def test_nested_and_async_definitions_are_not_invented_call_edges(self):
        (self.project/'nested.py').write_text('class Flow:\n    @property\n    def owner(self):\n        return self._owner\n\nasync def run():\n    def callback():\n        pass\n', encoding='utf8')
        session = ReadSession(self.request(), Cancellation())
        result = session.execute('search_files', dict(glob='nested.py', query='', mode='symbols'))
        self.assertIn('nested.py:2-4: Flow.owner', result)
        self.assertIn('nested.py:7-8: run.callback', result)
        self.assertNotIn('_owner:', result)
        self.assertEqual(session.execute('read_file', dict(path='nested.py', symbol='Flow.owner')), '2:     @property\n3:     def owner(self):\n4:         return self._owner')

    def test_first_large_python_read_includes_verifiable_outline_without_tail_body(self):
        (self.project/'large.py').write_text('# header\n'*220+'def target():\n    return "TAIL"\n', encoding='utf8')
        session = ReadSession(self.request(), Cancellation())
        first = session.execute('read_file', dict(path='large.py', count=2))
        self.assertIn('target [221-222]', first)
        self.assertIn('1: # header\n2: # header', first)
        self.assertNotIn('return "TAIL"', first)
        self.assertEqual(session.execute('read_file', dict(path='large.py', symbol='target')), '221: def target():\n222:     return "TAIL"')

    def test_overlap_and_cache_project_once_but_keep_raw_reads(self):
        session = ReadSession(self.request(), Cancellation())
        transcript = ToolTranscript()
        first = session.execute('read_file', dict(path='save.py', start=1, count=2))
        one = transcript.project('a', session.events[-1], first, 'E1')
        second = session.execute('read_file', dict(path='save.py', start=2, count=2))
        two = transcript.project('b', session.events[-1], second, 'E2')
        repeat = session.execute('read_file', dict(path='save.py', start=1, count=2))
        three = transcript.project('c', session.events[-1], repeat, 'E3')
        self.assertIn('validate()', one['content'])
        self.assertNotIn('validate()', two['content'])
        self.assertIn('publish()', two['content'])
        self.assertIn('already shown in E1', three['content'])
        self.assertEqual(session.events[0]['content'], first)
        self.assertEqual(session.events[1]['content'], second)
        self.assertEqual(session.events[2]['status'], 'cached')

    def test_clipped_projection_does_not_mark_unseen_lines_as_visible(self):
        session = ReadSession(self.request(), Cancellation())
        (self.project/'large.txt').write_text('\n'.join('x'*500 for _ in range(40)), encoding='utf8')
        transcript = ToolTranscript()
        raw = session.execute('read_file', dict(path='large.txt', count=40))
        view = transcript.project('a', session.events[-1], raw, 'E1')
        self.assertIn('omitted', view['content'])
        self.assertEqual(session.events[-1]['content'], raw)
        tail = session.execute('read_file', dict(path='large.txt', start=39, count=2))
        self.assertIn('39: ', transcript.project('b', session.events[-1], tail, 'E2')['content'])


class BudgetEngine:
    def __init__(self, cancel_in_budget=False):
        self.calls, self.cancel_in_budget = 0, cancel_in_budget
        self.final_messages = None

    def tool_budget(self, messages, params, cancel, tools, *, final=False):
        if self.cancel_in_budget:
            cancel.cancel()
        cancel.check()
        return dict(input=100 if self.calls == 0 else 6500, output=512, capacity=8192)

    def tool_turn(self, messages, params, cancel, tools, *, final=False):
        self.calls += 1
        if final:
            self.final_messages = copy.deepcopy(messages)
            return 'completed', dict(role='assistant', content='Only the inspected skill is verified; code flow is still unknown.')
        return 'completed', dict(role='assistant', content='', tool_calls=[dict(id='read', type='function',
            function=dict(name='read_skill', arguments=json.dumps(dict(skill='saving', path='SKILL.md'))))])


class Budgets(unittest.TestCase):
    setUp = core.AgentCore.setUp
    request = core.AgentCore.request

    def test_actual_budget_forces_text_only_close_preserving_incomplete_status_and_receipts(self):
        engine, events, deltas = BudgetEngine(), [], []
        result = run(engine, [dict(role='user', content='inspect full flow')], {}, Cancellation(), self.request(), deltas.append, events.append)
        self.assertEqual(result[0], 'limited')
        self.assertEqual(engine.calls, 2)
        self.assertIn('context reserve', engine.final_messages[0]['content'])
        self.assertEqual([e['operation'] for e in events], ['accepted', 'read_skill', 'projection', 'projection', 'budget'])
        self.assertIn('Read references/save.md', events[1]['content'])
        self.assertTrue(all('tool_calls' not in m and m['role'] != 'tool' for m in engine.final_messages))
        self.assertEqual(engine.final_messages[1]['content'], 'inspect full flow')
        self.assertIn('Read references/save.md', engine.final_messages[-1]['content'])
        validate_evidence(dict(request=self.request(), events=events))
        self.assertEqual(json.loads(events[-1]['content'])[-1]['input'], 6500)

    def test_cancel_during_token_preflight_never_runs_inference(self):
        engine, events = BudgetEngine(True), []
        with self.assertRaises(Cancelled):
            run(engine, [dict(role='user', content='task')], {}, Cancellation(), self.request(), lambda _: None, events.append)
        self.assertEqual(engine.calls, 0)
        self.assertEqual(events[-1]['operation'], 'budget')

    def test_round_limit_reserves_a_final_turn_instead_of_another_read(self):
        engine = BudgetEngine()
        engine.tool_budget = lambda *a, **kw: dict(input=100, output=512, capacity=8192)
        with patch('prompt_calculus_studio.local_agents.execution.MAX_ROUNDS', 2):
            result = run(engine, [dict(role='user', content='task')], {}, Cancellation(), self.request(), lambda _: None, lambda _: None)
        self.assertEqual(result[0], 'limited')
        self.assertIn('tool limit', engine.final_messages[0]['content'])

    def test_loading_time_is_part_of_the_same_final_answer_deadline(self):
        engine = BudgetEngine()
        engine.tool_budget = lambda *a, **kw: dict(input=100, output=512, capacity=8192)
        result = run(engine, [dict(role='user', content='task')], {}, Cancellation(), self.request(), lambda _: None,
                     lambda _: None, deadline=time.monotonic()+60)
        self.assertEqual(engine.calls, 1)
        self.assertEqual(result[0], 'limited')
        self.assertIn('time reserve', engine.final_messages[0]['content'])

    def test_oversized_tool_reply_is_compacted_before_final_send_raw_is_unchanged(self):
        class Engine(BudgetEngine):
            def tool_budget(self, messages, params, cancel, tools, *, final=False):
                cancel.check()
                return dict(input=sum(len(m['content']) for m in messages), output=512, capacity=6500)

            def tool_turn(self, messages, params, cancel, tools, *, final=False):
                if final:
                    budget = self.tool_budget(messages, params, cancel, tools, final=True)
                    assert budget['input'] + budget['output'] <= budget['capacity']
                    return super().tool_turn(messages, params, cancel, tools, final=final)
                self.calls += 1
                return 'completed', dict(role='assistant', content='', tool_calls=[dict(id='a',type='function',
                    function=dict(name='read_file',arguments=json.dumps(dict(path='large.txt',count=80))))])
        (self.project/'large.txt').write_text('\n'.join('marker-'+str(i)+'x'*100 for i in range(80)), encoding='utf8')
        engine, events = Engine(), []
        result = run(engine, [dict(role='user',content='inspect')], {}, Cancellation(), self.request(), lambda _: None, events.append)
        self.assertEqual(result[0], 'limited')
        raw = next(e['content'] for e in events if e['operation'] == 'read_file')
        self.assertIn('marker-79', raw)
        final = next(e['content'] for e in events if e['target'] == 'final-context')
        self.assertNotIn('marker-79', final)
        self.assertIn('Final-budget excerpt', final)
        validate_evidence(dict(request=self.request(), events=events))


if __name__ == '__main__':
    unittest.main()
