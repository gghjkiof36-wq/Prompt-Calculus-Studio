"""Real recovery/attach and close retries, using only a synthetic transport."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from prompt_calculus_studio import clip_flow, drafts, materials
from prompt_calculus_studio.comfy_client import ComfyClient
from prompt_calculus_studio.draft_cache import DraftCache
from prompt_calculus_studio.generation import submission, validate_profile
from prompt_calculus_studio.local_documents import io, workflow
from prompt_calculus_studio.snapshots import make_snapshot, validate_snapshot
from prompt_calculus_studio.workspace_recovery import key, state_digest
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from test_material_editor import state
from test_multi_output import workflow as profile_fixture


FIELDS = ('items', 'uses', 'output_order', 'multi_output', 'draft', 'draft_base')


def selected(value):
    return {name: copy.deepcopy(value[name]) for name in FIELDS}


def events():
    QApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def portable_fixture(root, name):
    value = state()
    canvas = next(iter(value['multi_output']['canvases']))
    materials.add_to_canvas(value, value['items'][0]['id'], canvas)
    profile = profile_fixture('handoff-profile')
    validate_profile(profile)
    value['generation'] = dict(mode='txt2img', profiles=[profile], chosen={'txt2img': profile['id']}, source=None)
    clip = next(iter(value['multi_output']['clip_inputs']), None) or clip_flow.add(value)
    clip_flow.set_binding(value, profile['id'], clip, ('6', 'text'))
    drafts.edit(value, '')
    result = io.create(root, name + '.pcs-workflow', workflow.from_state(value))
    if result.status != 'saved':
        raise AssertionError(result)
    return root / (name + '.pcs-workflow')


class HandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-owner-handoff-')
        self.root = Path(self.area.name) / 'files'
        self.root.mkdir()
        self.data = Path(self.area.name) / 'data'
        self.paths = [portable_fixture(self.root, name) for name in ('One', 'Two')]
        self.originals = {str(path): path.read_bytes() for path in self.paths}
        self.windows = []
        self.errors = []
        self.calls = []
        self.previous_hook = sys.excepthook
        sys.excepthook = lambda kind, value, trace: self.errors.append(str(value))

    def window(self):
        window = WorkspaceShell(self.root, self.data)
        window.autosave.delay_ms = 60000
        window.report_error = self.errors.append
        self.windows.append(window)
        window.show()
        events()
        return window

    def tearDown(self):
        # Failed assertions still release only this fixture's resources. This
        # is cleanup, never evidence of a successful application close.
        for window in reversed(self.windows):
            if not isValid(window):
                continue
            if not window.runtimes.closed:
                window.close()
            if not window.runtimes.closed:
                window.scheduler.stop()
                window.runtimes.close()
            window.deleteLater()
            events()
        sys.excepthook = self.previous_hook
        self.area.cleanup()

    def transport(self, client, route, data=None, done=None, failed=None, raw=None, content_type='application/json'):
        self.calls.append((route, copy.deepcopy(data)))
        if route == 'workflow/native/open':
            done(dict(id=data['id'], state='accepted'))
        elif route == '/prompt':
            done(dict(prompt_id=data['prompt_id']))
        else:
            raise AssertionError('Unexpected synthetic transport request: ' + route)

    def seed_recovery(self, *, pending=True, newer=False, mapped=False):
        window = self.window()
        tab = window.open_path(self.paths[0], True, new_tab=True)
        self.assertIsNotNone(tab, self.errors)
        events()
        if mapped:
            target = window.commands.files.run('save_as', window.commands.context('tab', tab=tab),
                                               name='Mapped', directory=self.root)
            self.assertIsNotNone(target, self.errors)
            self.assertIsInstance(tab.document.identity_maps, dict)
        document, owner = tab.document, tab.document.execution
        owner.persist()
        predecessor = copy.deepcopy(document.state)
        token = owner.runtime_token
        if pending:
            snapshot = make_snapshot(document.state)
            validate_snapshot(snapshot)
            profile = document.state['generation']['profiles'][0]
            payload = submission(profile, snapshot)
            queued = []
            with patch.object(ComfyClient, 'request', lambda client, *args, **kwargs: self.transport(client, *args, **kwargs)):
                owner.client.request('workflow/native/open', dict(id='accepted-handoff-operation'), done=lambda value: None)
                owner.client.generation.submit_payload(payload, queued.append,
                    lambda error: self.fail(str(error)))
            self.assertEqual(len(queued), 1)
            self.assertEqual(queued[0]['state'], 'queued')
        journal = list(owner.store.db.execute('SELECT id,body,created FROM generation_jobs ORDER BY id'))
        operations = list(owner.store.db.execute('SELECT * FROM workspace_operations ORDER BY id'))
        recovered = copy.deepcopy(predecessor)
        next(iter(recovered['uses'].values()))['prompt'] = 'recovered independent copy'
        drafts.edit(recovered, '')
        document.state = recovered
        self.assertIsNotNone(window.close_recovery.preserve(tab))
        cache = copy.deepcopy(window.close_recovery.get(document.path))
        self.assertEqual(cache['runtime_base_sha256'], state_digest(predecessor))
        document.state = copy.deepcopy(predecessor)
        if newer:
            next(iter(document.state['uses'].values()))['prompt'] = 'newer committed runtime'
            owner.persist()
        expected = selected(document.state if newer else recovered)
        target = document.path
        target_bytes = target.read_bytes()
        # Save a legitimate layout without the source tab; it must be recovered
        # as a detached execution on the next WorkspaceShell startup.
        for page in list(window.all_pages()):
            self.assertTrue(window.close_tab(window.index_of(page.key), decision='discard',
                                            make_home=False, pane=window.pane_for(page)))
        self.assertTrue(window.close())
        self.assertTrue(window.runtimes.closed)
        window.deleteLater()
        events()
        self.assertEqual(DraftCache(self.data).read(key(target)), cache)
        self.assertEqual(target.read_bytes(), target_bytes)
        return target, token, expected, cache, journal, operations, target_bytes

    def check_recovery(self, **options):
        target, token, expected, cache, journal, operations, target_bytes = self.seed_recovery(**options)
        calls_before = copy.deepcopy(self.calls)
        window = self.window()
        self.assertFalse(any(document.path == target for document in window.documents))
        pending = options.get('pending', True)
        detached = window.runtimes.executions.get(token)
        if pending:
            self.assertIsNotNone(detached)
            self.assertIsNone(detached.document)
            self.assertEqual(list(detached.store.db.execute('SELECT id,body,created FROM generation_jobs ORDER BY id')), journal)
        else:
            self.assertIsNone(detached)
        tab = window.open_path(target, True, new_tab=True)
        self.assertIsNotNone(tab, self.errors)
        events()
        owner = tab.document.execution
        print(json.dumps(dict(case=self.id(), token=token, runtime_restored=tab.document.runtime_restored,
            pending=pending, expected_text=next(iter(expected['uses'].values()))['prompt'],
            actual_text=next(iter(tab.document.state['uses'].values()))['prompt'],
            same_owner=owner is detached if pending else None, errors=self.errors), ensure_ascii=False), flush=True)
        self.assertEqual(selected(tab.document.state), expected)
        self.assertIs(owner.state, tab.document.state)
        self.assertIs(owner.document, tab.document)
        if pending:
            self.assertIs(owner, detached)
        self.assertEqual(owner.runtime_token, token)
        self.assertEqual(list(owner.store.db.execute('SELECT id,body,created FROM generation_jobs ORDER BY id')), journal)
        self.assertEqual(list(owner.store.db.execute('SELECT * FROM workspace_operations ORDER BY id')), operations)
        self.assertEqual(self.calls, calls_before)
        self.assertEqual(target.read_bytes(), target_bytes)
        self.assertEqual(DraftCache(self.data).read(key(target)), cache)
        self.assertEqual(tab.document.state['draft'], '')
        self.assertTrue(tab.document.state['multi_output']['bindings'])
        self.assertEqual(self.errors, [])
        self.assertTrue(window.close())
        self.assertTrue(owner.closed)

    def test_detached_owner_accepts_recovery_without_rewinding_or_resubmitting(self):
        self.check_recovery()

    def test_newer_runtime_keeps_priority_over_old_cache(self):
        self.check_recovery(newer=True)

    def test_ordinary_recovery_without_detached_owner(self):
        self.check_recovery(pending=False)

    def test_mapped_recovery_preserves_owner_and_bindings(self):
        self.check_recovery(mapped=True)

    def test_utility_close_retry_after_all_state_preservation_failed(self):
        window = self.window()
        first = window.open_path(self.paths[0], True, new_tab=True)
        second = window.open_path(self.paths[1], True, new_tab=True)
        window.open_route('gallery')
        events()
        utility = window.utility
        self.assertIsNotNone(utility)
        first_owner, second_owner = first.document.execution, second.document.execution
        before = selected(first_owner.state)
        with patch.object(first_owner, 'persist', side_effect=sqlite3.OperationalError('synthetic save failure')), patch.object(
                DraftCache, 'write', side_effect=OSError('synthetic full recovery storage')):
            self.assertFalse(window.close())
        self.assertFalse(first_owner.closed)
        self.assertTrue(second_owner.closed)
        self.assertTrue(utility.comfy.stopped)
        with self.assertRaises(sqlite3.ProgrammingError):
            utility.store.db.execute('SELECT 1')
        self.assertEqual(selected(first_owner.state), before)
        self.assertEqual(self.errors, [])
        accepted = window.close()
        print(json.dumps(dict(case=self.id(), retry_accepted=accepted, errors=self.errors,
            first_owner_closed=first_owner.closed, second_owner_closed=second_owner.closed,
            runtime_closed=window.runtimes.closed), ensure_ascii=False), flush=True)
        self.assertEqual(self.errors, [])
        self.assertTrue(accepted)
        self.assertTrue(first_owner.closed)
        self.assertTrue(window.runtimes.closed)
        self.assertEqual(selected(first_owner.state), before)
        self.assertEqual({str(path): path.read_bytes() for path in self.paths}, self.originals)

    def test_utility_retry_when_only_error_report_failed(self):
        window = self.window()
        first = window.open_path(self.paths[0], True, new_tab=True)
        second = window.open_path(self.paths[1], True, new_tab=True)
        window.open_route('settings', 'appearance')
        events()
        owner = first.document.execution
        original_write = DraftCache.write

        def write(cache, name, value):
            if name == 'execution-close-errors':
                raise OSError('synthetic report storage failure')
            return original_write(cache, name, value)

        with patch.object(owner, 'persist', side_effect=OSError('synthetic owner save failure')), patch.object(DraftCache, 'write', write):
            self.assertFalse(window.close())
        self.assertTrue(owner.closed)
        self.assertTrue(second.document.execution.closed)
        self.assertTrue(window.runtimes.closed)
        self.assertIsNotNone(DraftCache(owner.store.directory).read('execution-close-state'))
        self.assertTrue(window.close())
        self.assertIn('synthetic owner save failure', DraftCache(window.data).read('execution-close-errors')['error'])
        self.assertEqual(self.errors, [])

    def test_cancel_close_keeps_views_usable_then_normal_utility_close(self):
        window = self.window()
        tab = window.open_path(self.paths[0], True, new_tab=True)
        canvas = next(iter(tab.document.state['multi_output']['canvases']))
        self.assertTrue(tab.host.canvas.commit(lambda value: value['multi_output']['canvases'][canvas].update(name='kept after cancel')))
        window.open_route('gallery')
        events()
        utility = window.utility
        self.assertFalse(window.close_tab(window.index_of(tab.key), decision='cancel', pane=window.pane_for(tab)))
        self.assertFalse(utility.comfy.stopped)
        self.assertFalse(utility.__dict__.get('_close_completed', False))
        self.assertEqual(utility.store.db.execute('SELECT 1').fetchone(), (1,))
        window.open_route('settings', 'appearance')
        self.assertTrue(window.close_tab(window.index_of(tab.key), decision='save', pane=window.pane_for(tab)))
        reopened = window.open_path(self.paths[0], True, new_tab=True)
        self.assertEqual(reopened.document.state['multi_output']['canvases'][canvas]['name'], 'kept after cancel')
        self.assertTrue(window.close())
        self.assertTrue(utility.__dict__.get('_close_completed', False))
        self.assertTrue(utility.close())
        self.assertEqual(self.errors, [])

    def test_utility_cooperative_background_close_finishes_before_shell_retry(self):
        window = self.window()
        first = window.open_path(self.paths[0], True, new_tab=True)
        window.open_route('gallery')
        events()
        utility = window.utility
        self.assertTrue(utility.jobs.start('Synthetic cancellable work', lambda cancel: cancel.wait(3), lambda value: None))
        self.assertFalse(window.close())
        self.assertFalse(utility.__dict__.get('_close_completed', False))
        self.assertFalse(first.document.execution.closed)
        deadline = time.monotonic() + 5
        while not utility.__dict__.get('_close_completed', False) and time.monotonic() < deadline:
            events()
            time.sleep(.01)
        self.assertTrue(utility.jobs.pool.waitForDone(1000))
        self.assertTrue(utility.__dict__.get('_close_completed', False))
        self.assertTrue(window.close())
        self.assertTrue(first.document.execution.closed)
        self.assertEqual(self.errors, [])
