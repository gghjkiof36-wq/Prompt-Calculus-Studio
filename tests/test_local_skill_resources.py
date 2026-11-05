"""Skill resources through the real read loop, with deterministic model turns."""
import copy
import json
import unittest
from unittest.mock import patch

import test_local_agents as core
from prompt_calculus_studio.local_agents.execution import ReadSession, run
from prompt_calculus_studio.local_ai.engine import Cancellation
from prompt_calculus_studio.local_documents.chat_agent import validate_evidence


def read(path, name='read_skill', **args):
    if name == 'read_skill':
        args.setdefault('skill', 'saving')
    return dict(role='assistant', content='', tool_calls=[dict(id=path, type='function',
                function=dict(name=name, arguments=json.dumps(dict(path=path, **args))))])


def answer(text='Supported draft'):
    return dict(role='assistant', content=text)


class Engine:
    def __init__(self, *turns):
        self.turns, self.histories = iter(turns), []

    def tool_budget(self, *args, **kwargs):
        return dict(input=100, output=512, capacity=8192)

    def tool_turn(self, history, *args, **kwargs):
        if any(m['role'] == 'system' for m in history[1:]):
            raise RuntimeError('System message must be at the beginning.')
        self.histories.append(copy.deepcopy(history))
        return 'completed', next(self.turns)


class SkillResources(unittest.TestCase):
    setUp = core.AgentCore.setUp
    request = core.AgentCore.request

    def execute(self, engine):
        self.events, self.deltas = [], []
        request = self.request('@saving inspect')
        try:
            return run(engine, [dict(role='user', content='@saving inspect')], {}, Cancellation(),
                       request, self.deltas.append, self.events.append)
        finally:
            validate_evidence(dict(request=request, events=self.events))

    def test_relative_dot_paths_keep_snapshot_and_scope(self):
        session = ReadSession(self.request(), Cancellation())
        expected = (self.skill_path/'references/save.md').read_bytes().decode('utf8')
        for path in ('./references/save.md', 'references\\.\\save.md'):
            self.assertEqual(session.execute('read_skill', dict(skill='saving', path=path)), expected)
            self.assertEqual(session.events[-1]['target'], 'saving/references/save.md')
        for path in ('../outside.md', '/references/save.md', 'C:/references/save.md', 'references/../SKILL.md'):
            with self.assertRaisesRegex(ValueError, '相對路徑'):
                session.execute('read_skill', dict(skill='saving', path=path))

    def test_catalog_is_model_guidance_not_attachment_content_or_source_receipt(self):
        engine = Engine(read('SKILL.md'), read('references/save.md'), answer())
        self.execute(engine)
        entry_view = engine.histories[1][-1]['content']
        self.assertIn('references/save.md [not read]', entry_view)
        self.assertNotIn('MARKER-57', entry_view)
        entry = next(e for e in self.events if e['operation'] == 'read_skill')
        self.assertEqual(entry['content'], (self.skill_path/'SKILL.md').read_bytes().decode('utf8'))
        self.assertNotIn('Host Skill', entry['content'])
        self.assertIn('MARKER-57', engine.histories[2][-1]['content'])

    def test_unread_reference_is_never_fabricated_as_read(self):
        engine = Engine(read('SKILL.md'), answer('Model draft requiring external review'))
        self.execute(engine)
        self.assertEqual(len(engine.histories), 2)
        reads = [e['target'] for e in self.events if e['operation'] == 'read_skill']
        self.assertEqual(reads, ['saving/SKILL.md'])
        self.assertNotIn('MARKER-57', json.dumps(engine.histories))

    def test_project_evidence_has_copyable_citations_without_rewriting_raw_receipts(self):
        engine = Engine(read('SKILL.md'), read('references/save.md'), read('save.py', name='read_file'),
                        answer('final draft'))
        self.assertEqual(self.execute(engine), ('completed', 'final draft'))
        self.assertIn('save.py:2:     validate()', engine.histories[3][-1]['content'])
        raw = next(e for e in self.events if e['operation'] == 'read_file')['content']
        self.assertIn('2:     validate()', raw)
        self.assertNotIn('save.py:', raw)
        self.assertEqual(self.deltas, ['final draft'])
        self.assertEqual(len(engine.histories), 4)

    def test_optional_reference_is_not_automatically_read_or_required(self):
        with (self.skill_path/'SKILL.md').open('a', encoding='utf8') as stream:
            stream.write('\nFor translation only, use references/translate.md.')
        (self.skill_path/'references/translate.md').write_text('UNRELATED-TRANSLATION', encoding='utf8')
        self.store.refresh_skill(self.skill['id'])
        engine = Engine(read('SKILL.md'), read('references/save.md'), answer('No translation requested'))
        self.assertEqual(self.execute(engine)[0], 'completed')
        reads = [e['target'] for e in self.events if e['operation'] == 'read_skill']
        self.assertEqual(reads, ['saving/SKILL.md', 'saving/references/save.md'])
        self.assertNotIn('UNRELATED-TRANSLATION', json.dumps(engine.histories))

    def test_missing_declared_attachment_preserves_draft_but_cannot_complete(self):
        (self.skill_path/'references/save.md').unlink()
        self.store.refresh_skill(self.skill['id'])
        engine = Engine(read('SKILL.md'), read('references/save.md'), answer('The required rule is missing.'))
        with self.assertRaisesRegex(ValueError, '參考讀取未完成.*references/save.md'):
            self.execute(engine)
        self.assertEqual(self.deltas, ['The required rule is missing.'])
        error = next(e for e in self.events if e['operation'] == 'tool_error')
        self.assertIn('references/save.md', error['target'])
        self.assertIn('references/save.md', error['content'])

    def test_wrong_project_scope_is_recoverable_only_by_actual_skill_read(self):
        engine = Engine(read('SKILL.md'), read('references/save.md', name='read_file'),
                        read('references/save.md'), answer())
        self.assertEqual(self.execute(engine)[0], 'completed')
        self.assertEqual(sum(e['operation'] == 'tool_error' for e in self.events), 1)
        (self.skill_path/'references/save.md').unlink()
        self.store.refresh_skill(self.skill['id'])
        with self.assertRaisesRegex(ValueError, '參考讀取未完成'):
            self.execute(Engine(read('SKILL.md'), read('references/save.md', name='read_file'), answer('missing')))

    def test_typo_does_not_poison_successful_corrected_read(self):
        engine = Engine(read('SKILL.md'), read('saving/save.md'), read('references/save.md'), answer())
        self.assertEqual(self.execute(engine)[0], 'completed')

    def test_unsupported_attachment_is_failed_not_read(self):
        (self.skill_path/'references/save.md').write_bytes(b'\x00binary')
        self.store.refresh_skill(self.skill['id'])
        with self.assertRaisesRegex(ValueError, '參考讀取未完成'):
            self.execute(Engine(read('SKILL.md'), read('references/save.md'), answer('unsupported')))
        self.assertEqual([e['target'] for e in self.events if e['operation'] == 'read_skill'], ['saving/SKILL.md'])

    def test_read_budget_rejection_never_marks_entry_as_delivered(self):
        session = ReadSession(self.request(), Cancellation())
        with patch('prompt_calculus_studio.local_agents.execution.MAX_READ', 1):
            with self.assertRaisesRegex(ValueError, '512 KiB'):
                session.execute('read_skill', dict(skill='saving', path='SKILL.md'))
        self.assertEqual(session.used, set())
        self.assertEqual(session.skill_reads.read, {})

    def test_disabled_and_other_role_skill_cannot_be_read(self):
        other = self.store.add_skill(core.make_skill(self.root/'other', 'other'))
        session = ReadSession(self.request(), Cancellation())
        with self.assertRaisesRegex(ValueError, '不存在'):
            session.execute('read_skill', dict(skill=other['id'], path='SKILL.md'))
        self.store.enable_skill(self.skill['id'], False)
        with self.assertRaises(ValueError):
            self.request('@saving inspect')

    def test_permission_is_still_required_for_cached_skill_text(self):
        request = self.request()
        request['approval'] = 'ask'
        decisions = iter([True, False])
        session = ReadSession(request, Cancellation(), approve=lambda _: next(decisions))
        session.execute('read_skill', dict(skill='saving', path='SKILL.md'))
        with self.assertRaisesRegex(ValueError, '核准'):
            session.execute('read_skill', dict(skill='saving', path='SKILL.md'))


if __name__ == '__main__':
    unittest.main()
