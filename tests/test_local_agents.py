"""Real filesystem boundaries and skill snapshots; no inference/downloads."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prompt_calculus_studio.local_agents.store import AgentStore
from prompt_calculus_studio.local_agents.sources import folder_snapshot, metadata
from prompt_calculus_studio.local_agents.execution import ReadSession, run
from prompt_calculus_studio.local_ai.engine import Cancellation, Cancelled


def make_skill(root, name='saving', manual=False):
    root.mkdir(parents=True)
    (root / 'references').mkdir()
    (root / 'SKILL.md').write_text('---\nname: ' + name + '\ndescription: >-\n  Inspect saving flows.\ndisable-model-invocation: ' + str(manual).lower() + '\nextra: preserve-me\n---\nRead references/save.md', encoding='utf8')
    (root / 'references/save.md').write_text('MARKER-57\nValidate, write, publish.', encoding='utf8')
    return root


class AgentCore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'save.py').write_text('def save():\n    validate()\n    publish()\n', encoding='utf8')
        self.skill_path = make_skill(self.root / 'skill')
        self.store = AgentStore(self.root / 'data')
        self.skill = self.store.add_skill(self.skill_path)
        self.project_record = self.store.save_project('Project', self.project, [])
        self.agent = self.store.save_agent('A', self.project_record['id'], 'test-model', [self.skill['id']])

    def request(self, text='inspect saving'):
        return self.store.request(self.agent['id'], text, 'auto')

    def test_complete_copy_preserves_original_and_unknown_metadata(self):
        before = (self.skill_path / 'SKILL.md').read_bytes()
        item = self.store.add_skill(self.skill_path, managed=True)
        self.assertEqual(item['metadata']['extra'], 'preserve-me')
        self.assertEqual((Path(item['root']) / 'references/save.md').read_bytes(), (self.skill_path / 'references/save.md').read_bytes())
        (Path(item['root']) / 'SKILL.md').write_bytes(before + b'\nEdited copy')
        self.assertEqual((self.skill_path / 'SKILL.md').read_bytes(), before)
        self.assertNotEqual(item['revision'], self.store.refresh_skill(item['id'])['revision'])

    def test_multiple_skill_roles_and_persistence(self):
        second = self.store.add_skill(make_skill(self.root / 'second', 'other'))
        a = self.store.save_agent('A', self.project_record['id'], 'test-model', [self.skill['id'], second['id']], self.agent['id'])
        b = self.store.save_agent('B', self.project_record['id'], 'test-model', [second['id']])
        self.assertEqual(len(self.store.request(a['id'], 'task', 'auto')['skills']), 2)
        self.store.save_agent('A', a['project'], a['model'], [self.skill['id']], a['id'])
        self.assertEqual(AgentStore(self.root / 'data').get('agents', b['id'])['skills'], [second['id']])
        self.store.enable_skill(self.skill['id'], False)
        self.assertEqual(self.request()['skills'], [])
        with self.assertRaisesRegex(ValueError, '停用'):
            self.request('@saving task')

    def test_manual_policy_and_duplicate_name_not_overwritten(self):
        second_path = make_skill(self.root / 'manual', 'manual', True)
        second = self.store.add_skill(second_path)
        self.store.save_agent('A', self.agent['project'], 'test-model', [self.skill['id'], second['id']], self.agent['id'])
        self.assertEqual(len(self.request()['skills']), 1)
        self.assertEqual(len(self.request('@manual task')['skills']), 2)
        duplicate = self.store.add_skill(make_skill(self.root / 'dup', 'saving'))
        self.store.save_agent('A', self.agent['project'], 'test-model', [self.skill['id'], duplicate['id']], self.agent['id'])
        with self.assertRaisesRegex(ValueError, '重複'):
            self.request('@saving task')
        self.assertEqual(len(self.store.value['skills']), 3)

    def test_codex_host_manual_policy_is_not_auto_invoked(self):
        (self.skill_path / 'agents').mkdir()
        (self.skill_path / 'agents/openai.yaml').write_text('policy:\n  allow_implicit_invocation: false\n', encoding='utf8')
        self.store.refresh_skill(self.skill['id'])
        self.assertEqual(self.request()['skills'], [])
        self.assertEqual(len(self.request('@saving task')['skills']), 1)

    def test_source_update_before_acceptance_fails_after_acceptance_frozen(self):
        request = self.request()
        session = ReadSession(request, Cancellation())
        (self.skill_path / 'references/save.md').write_text('CHANGED', encoding='utf8')
        self.assertIn('MARKER-57', session.execute('read_skill', {'skill':'saving', 'path':'references/save.md'}))
        with self.assertRaisesRegex(ValueError, '已更新'):
            ReadSession(request, Cancellation())
        self.store.refresh_skill(self.skill['id'])
        self.assertIn('CHANGED', ReadSession(self.request(), Cancellation()).execute('read_skill', {'skill':'saving','path':'references/save.md'}))

    def test_path_escape_hidden_and_unsupported_action(self):
        session = ReadSession(self.request(), Cancellation())
        for path in ('../outside.md', 'C:/outside.md', '.git/config'):
            with self.assertRaises((ValueError, OSError)):
                session.execute('read_file', dict(path=path, start=1, count=10))
        with self.assertRaises(ValueError):
            session.execute('shell', {'command':'write file'})
        with self.assertRaises(ValueError):
            session.execute('read_skill', {'skill':'saving', 'path':'../outside.md'})

    @unittest.skipUnless(os.name == 'nt', 'Windows junction')
    def test_registered_junction_works_and_nested_escape_rejected(self):
        import _winapi
        alias = self.root / 'alias'
        _winapi.CreateJunction(str(self.skill_path), str(alias))
        self.addCleanup(lambda: alias.rmdir() if alias.exists() else None)
        self.assertEqual(folder_snapshot(alias)['root'], str(self.skill_path))
        outside = self.root / 'outside'; outside.mkdir()
        (outside / 'private.md').write_text('private', encoding='utf8')
        link = self.skill_path / 'escape'
        _winapi.CreateJunction(str(outside), str(link))
        self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        with self.assertRaisesRegex(ValueError, '超出'):
            folder_snapshot(self.skill_path)

    def test_read_cache_and_real_search_evidence(self):
        events = []
        session = ReadSession(self.request(), Cancellation(), events.append)
        first = session.execute('read_file', dict(path='save.py', start=1, count=10))
        (self.project / 'save.py').write_text('Changed', encoding='utf8')
        self.assertEqual(first, session.execute('read_file', dict(path='save.py', start=1, count=10)))
        self.assertEqual([e['status'] for e in events], ['read', 'cached'])
        self.assertEqual(events[1]['content'], '')
        self.assertIn('更換 glob', session.execute('search_files', dict(glob='nothing*.py', query='owner')))
        self.assertIn('save.py:1', session.execute('search_files', dict(glob='*.py', query='Changed')))

    def test_recursive_glob_includes_immediate_children(self):
        from prompt_calculus_studio.local_agents.execution import matches_glob
        for path in ('app/local_documents/io.py','app/local_documents/sub/io.py'):
            self.assertTrue(matches_glob(path,'**/local_documents/**/*.py'))
        self.assertTrue(matches_glob('save.py','**/*.py'))
        self.assertFalse(matches_glob('app/local_documents/sub/io.py','*/local_documents/*.py'))
        self.assertFalse(matches_glob('app/other/io.py','**/local_documents/**/*.py'))

    def test_permission_cancel_and_budgets(self):
        request = self.request(); request['approval'] = 'ask'
        session = ReadSession(request, Cancellation(), approve=lambda action: False)
        with self.assertRaisesRegex(ValueError, '核准'):
            session.execute('list_skills', {'query':''})
        request['approval'] = 'off'
        with self.assertRaisesRegex(ValueError, '停用'):
            ReadSession(request, Cancellation()).execute('list_skills', {'query':''})
        cancel = Cancellation(); cancel.cancel()
        with self.assertRaises(Cancelled):
            ReadSession(self.request(), cancel)
        session = ReadSession(self.request(), Cancellation())
        session.calls = 40
        with self.assertRaisesRegex(ValueError, '上限'):
            session.execute('list_skills', {'query':''})
        session = ReadSession(self.request(), Cancellation())
        session.total = 512 * 1024
        with self.assertRaisesRegex(ValueError, '512 KiB'):
            session.execute('read_skill', dict(skill='saving', path='SKILL.md'))

    @unittest.skipUnless(os.name == 'nt', 'Windows junction')
    def test_cyclic_skill_link_fails_before_capture(self):
        import _winapi
        link = self.skill_path / 'loop'
        _winapi.CreateJunction(str(self.skill_path), str(link))
        self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        with self.assertRaisesRegex(ValueError, '循環'):
            folder_snapshot(self.skill_path)

    def test_registry_compare_and_swap_and_missing_source(self):
        other = AgentStore(self.root / 'data')
        self.store.enable_skill(self.skill['id'], False)
        with self.assertRaisesRegex(ValueError, '其他頁面'):
            other.enable_skill(self.skill['id'], True)
        (self.skill_path / 'SKILL.md').unlink()
        with self.assertRaises(ValueError):
            self.store.refresh_skill(self.skill['id'])

    def test_incomplete_registry_is_rejected_without_rewriting_original(self):
        damaged = copy.deepcopy(self.store.value)
        del damaged['skills'][0]['files']
        raw = json.dumps(damaged).encode()
        self.store.path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, '不完整'):
            AgentStore(self.root / 'data')
        self.assertEqual(self.store.path.read_bytes(), raw)

    def test_yaml_duplicates_aliases_rejected_and_unknowns_preserved(self):
        for content in ('name: a\nname: b\ndescription: test', 'name: a\ndescription: &anchor test\nunknown: *anchor'):
            with self.assertRaises(ValueError):
                metadata(('---\n' + content + '\n---\n').encode())

    def test_structured_loop_reads_entry_and_reference_no_text_commands(self):
        class Engine:
            index = 0
            def tool_budget(self, messages, params, cancel, tools, *, final=False):
                cancel.check()
                return dict(input=100, output=512, capacity=8192)

            def tool_turn(self, messages, params, cancel, tools, *, final=False):
                self.index += 1
                if self.index <= 2:
                    path = 'SKILL.md' if self.index == 1 else 'references/save.md'
                    return 'completed', dict(role='assistant', content='', tool_calls=[dict(id=str(self.index), type='function', function=dict(name='read_skill', arguments=json.dumps({'skill':'saving','path':path})))])
                self.result = messages[-1]['content']
                return 'completed', dict(role='assistant', content='Validated MARKER-57')
        engine, deltas, events = Engine(), [], []
        result = run(engine, [{'role':'user', 'content':'@saving inspect'}], {}, Cancellation(), self.request('@saving inspect'), deltas.append, events.append)
        self.assertIn('MARKER-57', engine.result)
        self.assertEqual(result, ('completed', 'Validated MARKER-57'))
        self.assertEqual(len([e for e in events if e['operation'] == 'read_skill']), 2)


if __name__ == '__main__':
    unittest.main()
