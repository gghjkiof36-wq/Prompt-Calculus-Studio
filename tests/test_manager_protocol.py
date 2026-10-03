import json
import tempfile
import unittest
from pathlib import Path

from prompt_studio.manager_protocol import Journal, installed_packages, make_plan, queue_busy


class ManagerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'manager.json'
        self.packages = installed_packages({
            'paint': {'ver': '1.0', 'enabled': True, 'cnr_id': 'paint', 'aux_id': None},
            'edge': {'ver': 'nightly', 'enabled': True, 'cnr_id': None, 'aux_id': 'author/edge'},
            'sleep': {'ver': '1.0', 'enabled': False, 'cnr_id': 'sleep'},
            'mystery': {'ver': 'unknown', 'enabled': True},
            'comfyui-manager': {'ver': '4.3', 'enabled': True, 'cnr_id': 'comfyui-manager'},
        })

    def test_plan_excludes_disabled_pinned_unknown_and_manager(self):
        plan = make_plan(self.packages, [p['id'] for p in self.packages], pins={'paint'}, include_core=True)
        self.assertEqual([p['package_id'] for p in plan], ['edge', '__comfyui__'])
        self.assertEqual(plan[0]['params'], {'node_name': 'edge', 'node_ver': 'unknown'})
        self.assertEqual(plan[1]['params'], {'is_stable': True})

    def test_git_commit_is_not_used_as_registry_version_selector(self):
        packages = installed_packages({
            'renamed-directory': {'ver': 'abc123def456', 'enabled': True, 'cnr_id': 'my-node', 'aux_id': 'author/my-node'},
            'another-git-pack': {'ver': 'bca123fed456', 'enabled': True, 'cnr_id': '', 'aux_id': 'author/unknown'},
            'registry-node': {'ver': '1.2.3', 'enabled': True, 'cnr_id': 'registry-node', 'aux_id': None},
            'renamed-manager': {'ver': '2a6cb164', 'enabled': True, 'cnr_id': 'comfyui-manager', 'aux_id': 'Comfy-Org/ComfyUI-Manager'},
            'old-manager': {'ver': '2a6cb164', 'enabled': True, 'cnr_id': '', 'aux_id': 'ltdrdata/ComfyUI-Manager'},
        })
        plan = make_plan(packages, [pack['id'] for pack in packages])
        values = {task['package_id']: task['params'] for task in plan}
        self.assertEqual(values, {
            'renamed-directory': {'node_name': 'my-node', 'node_ver': 'nightly'},
            'another-git-pack': {'node_name': 'another-git-pack', 'node_ver': 'unknown'},
            'registry-node': {'node_name': 'registry-node', 'node_ver': '1.2.3'},
        })

    def test_journal_blocks_unknown_after_reload_and_accepts_only_own_receipt(self):
        journal = Journal(self.path)
        operation = journal.begin('http://127.0.0.1:8188', make_plan(self.packages, ['paint']))
        task = operation['tasks'][0]
        task['state'] = 'unknown'
        journal.save()
        reloaded = Journal(self.path)
        with self.assertRaisesRegex(ValueError, '待確認'):
            reloaded.begin('http://127.0.0.1:8188', make_plan(self.packages, ['paint']))
        receipt = dict(ui_id=task['ui_id'], client_id='other', kind='update',
                       status={'completed': True, 'status_str': 'success'})
        self.assertFalse(reloaded.reconcile('http://127.0.0.1:8188', {task['ui_id']: receipt}))
        receipt['client_id'] = journal.data['client_id']
        receipt['kind'] = 'install'
        self.assertFalse(reloaded.reconcile('http://127.0.0.1:8188', {task['ui_id']: receipt}))
        receipt['kind'] = 'update'
        self.assertTrue(reloaded.reconcile('http://127.0.0.1:8188', {task['ui_id']: receipt}))
        self.assertEqual(reloaded.operations('http://127.0.0.1:8188')[0]['tasks'][0]['state'], 'pending_restart')

    def test_pin_is_per_server_and_persisted(self):
        journal = Journal(self.path)
        journal.pin('http://127.0.0.1:8188', 'paint', True)
        self.assertEqual(Journal(self.path).pins('http://127.0.0.1:8188'), {'paint'})
        self.assertFalse(journal.pins('http://127.0.0.1:8288'))

    def test_remote_failure_diagnostics_are_not_persisted(self):
        journal = Journal(self.path)
        operation = journal.begin('server', make_plan(self.packages, ['paint']))
        task = operation['tasks'][0]
        task['state'] = 'queued'
        receipt = dict(ui_id=task['ui_id'], client_id=journal.data['client_id'], kind='update',
                       result='https://user:SECRET@example.test?token=SECRET C:/private/account/model',
                       status={'completed': True, 'status_str': 'failed'})
        self.assertTrue(journal.reconcile('server', {task['ui_id']: receipt}))
        text = self.path.read_text(encoding='utf-8')
        self.assertNotIn('SECRET', text)
        self.assertNotIn('private', text)
        self.assertEqual(task['state'], 'failed')

    def test_invalid_journal_preserved_and_mutations_blocked(self):
        self.path.write_text('{not json', encoding='utf-8')
        journal = Journal(self.path)
        self.assertTrue(journal.error)
        with self.assertRaises(ValueError):
            journal.begin('server', [])
        self.assertEqual(self.path.read_text(), '{not json')

    def test_incomplete_task_schema_is_rejected_without_touching_file(self):
        value = dict(version=1, client_id='client', pins={}, operations=[
            dict(id='operation', server='server', created=1, tasks=[dict(state='unknown', ui_id='x')])])
        original = json.dumps(value)
        self.path.write_text(original, encoding='utf-8')
        journal = Journal(self.path)
        self.assertTrue(journal.error)
        self.assertFalse(journal.operations('server'))
        self.assertEqual(self.path.read_text(encoding='utf-8'), original)

    def test_shape_checks_and_legacy_completed_queue(self):
        with self.assertRaises(ValueError):
            installed_packages({'bad': {'ver': '1.0', 'enabled': 'false'}})
        with self.assertRaises(ValueError):
            queue_busy({'is_processing': 'false'})
        self.assertFalse(queue_busy({'total_count': 3, 'done_count': 3, 'is_processing': False}))
        self.assertTrue(queue_busy({'total_count': 2, 'done_count': 30, 'pending_count': 2, 'is_processing': False}))


if __name__ == '__main__':
    unittest.main()
