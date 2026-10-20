"""Real document/Stage journals with a shared synthetic ComfyUI executor."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio import clip_flow, multi_output, stage_model
from prompt_calculus_studio.composition import node
from prompt_calculus_studio.comfy_client import ComfyClient, RequestFailure
from prompt_calculus_studio.workspace_files import create_file
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from prompt_calculus_studio.workspace_runtime import unfinished
from test_multi_output import workflow
from test_084_stages import Executor


class WorkspaceExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-workspace-execution-')
        self.base = Path(self.area.name)
        self.root = self.base/'files'
        self.root.mkdir()
        self.data = self.base/'data'
        self.errors = []
        self.notices = []
        self.calls = []
        self.executors = {}
        self.lost_reply = False
        self.lost_apply_reply = False
        self.hold_cancel = False
        self.reject_aux = False
        self.old_hook = sys.excepthook
        sys.excepthook = lambda kind, value, trace: self.errors.append(str(value))
        self.transport = patch.object(ComfyClient, 'request',
            lambda client, *args, **kwargs: self.transport_request(client, *args, **kwargs))
        self.transport.start()
        self.w = self.window()
        self.a = self.make('A', 'alpha')
        self.b = self.make('B', 'beta')
        self.peer = self.executors[self.a.document.runtime_token]
        second = self.executors[self.b.document.runtime_token]
        # Both document clients talk to the same native receipt store, queue,
        # history and frontend identity, as they would on one ComfyUI server.
        for name in ('native', 'service', 'pending', 'history', 'submissions',
                     'live', 'graphs', 'identities', 'results', 'applied', 'cancelled', 'root'):
            setattr(second, name, getattr(self.peer, name))

    def window(self):
        window = WorkspaceShell(self.root, self.data)
        window.autosave.delay_ms = 60000
        window.report_error = self.errors.append
        window.show_notice = self.notices.append
        if self.app.platformName() == 'windows':
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        window.show()
        return window

    def make(self, name, text):
        with patch('prompt_calculus_studio.canvas_starter.initialize', side_effect=lambda state, **kw: state):
            path = create_file(self.root, 'workflow', name)
        tab = self.w.open_path(path, new_tab=True)
        self.assertIsNotNone(tab, self.errors)
        host = tab.host
        host.notice = self.notices.append
        host.error = self.notices.append
        profile = workflow()
        profile['frontend_id'] = 'native-A'
        profile['graph']['9'] = dict(class_type='PreviewImage', inputs=dict(images=['5', 0]))
        host.generation_panel.save_profile(profile)
        canvas = host.canvas
        cid = next(iter(canvas.data()['canvases']))
        clip = next(iter(canvas.data()['clip_inputs']))
        def setup(state):
            state['uses']['root'] = node(name, text)
            multi_output.assign(state, 'root', cid)
            clip_flow.set_binding(state, 'flow', clip, ('6', 'text'))
            stage = stage_model.add(state, workflow='flow')
            state['multi_output']['stages'][stage].update(output='9', text_output=['6', 'text'])
            multi_output.connect(state, clip, stage, 'control')
        self.assertTrue(canvas.commit(setup), self.notices)
        request = host.comfy.request
        executor = Executor(host, self.base/'server')
        host.comfy.request = request  # Keep the workspace's final transport guard.
        host.comfy.timer.stop()
        host.comfy.enabled = False
        self.executors[tab.document.runtime_token] = executor
        self.assertTrue(tab.document.save(), tab.document.error)
        return tab

    def transport_request(self, client, route, data=None, done=None, failed=None, raw=None,
                          content_type='application/json'):
        token = getattr(client.window, 'runtime_token', None)
        self.calls.append((token, route, copy.deepcopy(data)))
        if route == 'config':
            if done: done(dict(token='fixture'))
            return
        if route == 'desktop/status':
            if done: done(dict(ready=True, running=len(self.peer.pending), pending=0,
                running_ids=list(self.peer.pending), queued_ids=[],
                snapshot_versions=[1, 2, 3, 4, 5, 6, 7],
                capabilities=['native_queue_v1', 'native_bindings_v1', 'stage_execution_v1']))
            return
        if route == 'desktop/workflows':
            if done: done(None)
            return
        if route == 'workflow/native/inspect' and self.reject_aux:
            failed(RequestFailure('fixture: explicit rejection', rejected=True, status_code=400))
            return
        executor = self.executors.get(token)
        if executor is None:
            if failed: failed('fixture: unknown owner')
            return
        executor.client = client
        if self.hold_cancel and route == 'workflow/native/cancel':
            return
        lose = ((self.lost_reply and route == 'workflow/native/start')
                or (self.lost_apply_reply and route == 'workflow/native/apply'))
        executor.request(route, data, None if lose else done,
                         failed, raw=raw, content_type=content_type)

    def click(self, tab):
        self.w.focus_tab(tab)
        controls = tab.host.canvas.execution_bar.controls
        controls.refresh()
        self.assertTrue(controls.run_button.isEnabled(), self.notices)
        QTest.mouseClick(controls.run_button, Qt.MouseButton.LeftButton)
        self.tick()

    def tick(self):
        for _ in range(5):
            self.w.scheduler.refresh()
            status = dict(running_ids=list(self.peer.pending), queued_ids=[])
            for client in self.w.scheduler.clients.values():
                if not client.stopped:
                    client.generation.observe(status)
                    client.input_flow.observe(status)
            QTest.qWait(20)

    def finish(self):
        self.peer.finish()
        self.tick()

    def reopen(self, *, restore_tabs=True):
        self.w.close()
        self.w.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if not restore_tabs:
            (self.data/'workspace-layout.json').unlink()
        self.w = self.window()
        for token, client in self.w.scheduler.clients.items():
            if token in self.executors:
                self.executors[token].client = client
            client.connected = client.native_supported = client.flow_supported = True
            client.snapshot_versions = [1, 2, 3, 4, 5, 6, 7]
            client.timer.stop()
        self.tick()

    def tearDown(self):
        self.w.close()
        self.w.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.transport.stop()
        sys.excepthook = self.old_hook
        self.area.cleanup()
        self.assertEqual(self.errors, [])

    def test_two_files_fifo_frozen_input_and_shared_view(self):
        self.click(self.a)
        self.click(self.b)
        self.assertEqual(len(self.peer.submissions), 1, self.notices)
        self.assertIn('排隊第 1 位', self.b.host.comfy.scheduling_message)
        other = self.w.duplicate_view(self.a, True)
        self.assertIs(other.host.comfy, self.a.host.comfy)
        self.b.host.canvas.commit(lambda state: state['uses']['root'].update(prompt='later edit'))
        self.tick()
        self.assertEqual(len(self.peer.submissions), 1)
        self.finish()
        self.assertEqual(len(self.peer.submissions), 2, self.notices)
        self.assertEqual(self.peer.submissions[1]['prompt']['6']['inputs']['text'], 'beta')
        self.finish()
        self.assertIsNone(self.w.scheduler.owner)

    def test_cancel_waiting_file_does_not_touch_running_file(self):
        self.click(self.a)
        self.click(self.b)
        self.b.host.comfy.interrupt(clear_pending=True)
        self.tick()
        self.assertFalse(self.peer.cancelled)
        self.assertFalse(self.w.scheduler.waiting)
        self.finish()
        self.assertEqual(len(self.peer.submissions), 1)

    def test_cancel_running_file_keeps_lane_until_backend_receipt(self):
        self.click(self.a)
        self.click(self.b)
        self.hold_cancel = True
        self.a.host.comfy.interrupt(clear_pending=True)
        self.tick()
        self.assertEqual(len(self.peer.submissions), 1)
        self.assertTrue(unfinished(self.a.document.execution.store.db))
        self.hold_cancel = False
        self.finish()
        self.assertEqual(len(self.peer.submissions), 2, self.notices)

    def test_final_transport_guard_blocks_other_file_mutations(self):
        self.click(self.a)
        rejected = []
        before = len(self.calls)
        for route in ('workflow/native/start', 'workflow/native/apply', 'workflow/native/open',
                      'workflow/native/inspect', 'workflow/queue/capture', 'workflow/queue/submit',
                      '/prompt', 'desktop/command'):
            self.b.host.comfy.request(route, dict(id='not-sent'), failed=rejected.append)
        self.assertEqual(len(self.calls), before)
        self.assertEqual(len(rejected), 8)
        self.assertTrue(all(error.rejected for error in rejected))

    def test_entire_multistage_flow_finishes_before_next_file(self):
        from stage_fixture import pure_profile
        from prompt_calculus_studio.flow_data import add_image_input
        profile = pure_profile('C')
        self.a.host.generation_panel.save_profile(profile)
        def setup(state):
            first = next(iter(state['multi_output']['stages']))
            following = stage_model.add(state, workflow='C')
            state['multi_output']['stages'][following]['output'] = '9'
            multi_output.connect(state, first, following, 'done')
            image = add_image_input(state)
            state['multi_output']['image_inputs'][image].update(workflow='C', node='10')
            multi_output.connect(state, first, image, 'image')
            multi_output.connect(state, image, following, 'control')
        self.assertTrue(self.a.host.canvas.commit(setup), self.notices)
        self.peer.graphs['C'] = copy.deepcopy(profile['graph'])
        self.peer.identities['C'] = dict(workflow='C', path='', frontend_id=profile['frontend_id'])
        self.peer.live['workflows'] = list(self.peer.identities.values())
        self.click(self.a)
        self.click(self.b)
        self.finish()
        self.assertEqual(len(self.peer.submissions), 2)
        self.assertEqual(self.w.scheduler.owner, self.a.document.runtime_token)
        self.assertEqual(self.peer.submissions[-1]['extra_data']['extra_pnginfo']['prompt_studio']['generation']['workflow_id'], 'C')
        self.finish()
        self.assertEqual(len(self.peer.submissions), 3)
        self.assertEqual(self.w.scheduler.owner, self.b.document.runtime_token)

    def test_input_only_lost_reply_recovers_and_releases_lane(self):
        def without_stage(state):
            stages = set(state['multi_output']['stages'])
            state['multi_output']['stages'].clear()
            state['multi_output']['connections'] = [
                edge for edge in state['multi_output']['connections']
                if edge['destination'] not in stages and edge['source'] not in stages]
        self.assertTrue(self.a.host.canvas.commit(without_stage))
        self.assertTrue(self.a.document.save())
        self.lost_apply_reply = True
        self.click(self.a)
        token = self.a.document.runtime_token
        self.assertEqual(len(self.peer.applied), 1)
        self.assertFalse(self.w.comfy_connection.connect_to('http://127.0.0.1:18188'))
        self.assertEqual(self.w.comfy_connection.url, 'http://127.0.0.1:8188')
        self.click(self.b)
        self.assertEqual(len(self.peer.submissions), 0)
        self.hold_cancel = True
        self.reopen(restore_tabs=False)
        self.hold_cancel = False
        self.lost_apply_reply = False
        owner = self.w.runtimes.executions[token]
        self.assertTrue(unfinished(owner.store.db))
        owner.client.poll()
        self.tick()
        self.assertFalse(unfinished(owner.store.db), self.notices)
        self.assertEqual(len(self.peer.applied), 1, 'Do not apply the same input twice after restart')
        self.assertIsNone(self.w.scheduler.owner)

    def test_waiting_work_can_be_cancelled_after_disconnect(self):
        self.click(self.a)
        self.click(self.b)
        self.b.host.comfy.connected = False
        controls = self.b.host.canvas.execution_bar.controls
        controls.refresh()
        self.assertTrue(controls.stop.isEnabled())
        QTest.mouseClick(controls.stop, Qt.MouseButton.LeftButton)
        self.tick()
        self.assertFalse(self.w.scheduler.waiting)
        self.assertEqual(len(self.peer.submissions), 1)

    def test_history_cancels_recovered_input_only_by_original_id(self):
        from prompt_calculus_studio.workspace_history import WorkspaceHistory
        def without_stage(state):
            stages = set(state['multi_output']['stages'])
            state['multi_output']['stages'].clear()
            state['multi_output']['connections'] = [
                edge for edge in state['multi_output']['connections']
                if edge['destination'] not in stages and edge['source'] not in stages]
        self.assertTrue(self.a.host.canvas.commit(without_stage))
        self.lost_apply_reply = True
        self.click(self.a)
        token = self.a.document.runtime_token
        ident = self.a.host.comfy.input_flow.chain.store.rows('input')[0]['id']
        self.hold_cancel = True
        self.reopen(restore_tabs=False)
        self.hold_cancel = False
        dialog = WorkspaceHistory(self.w)
        dialog.picker.setCurrentIndex(next(i for i, v in enumerate(dialog.values) if v['token'] == token))
        self.assertTrue(dialog.stop.isEnabled())
        before = len(self.calls)
        dialog.cancel()
        self.tick()
        self.assertIn((token, 'workflow/native/cancel', dict(id=ident)), self.calls[before:])
        operation = dialog.execution.client.input_flow.chain.store.read(ident)
        self.assertTrue(operation['ended'])
        self.assertEqual(operation['status'], 'applied')
        self.assertFalse(unfinished(dialog.execution.store.db))
        self.assertIsNone(self.w.scheduler.owner)
        self.assertEqual(len(self.peer.applied), 1)
        self.assertFalse(self.peer.submissions)
        dialog.reject()
        dialog.deleteLater()

    def test_registry_write_failure_blocks_new_submission(self):
        self.w.runtimes.errors['fixture'] = 'disk error'
        self.a.host.comfy.run()
        self.tick()
        self.assertFalse(self.peer.submissions)
        self.assertTrue(self.a.host.comfy.input_flow.chain.runs(True))

    def test_rejected_frontend_operation_does_not_leave_unknown_lock(self):
        self.reject_aux = True
        rejected = []
        self.a.host.comfy.request('workflow/native/inspect', dict(id='rejected-inspect'), failed=rejected.append)
        self.assertTrue(rejected[0].rejected)
        self.assertFalse(unfinished(self.a.host.store.db))
        self.tick()
        self.assertIsNone(self.w.scheduler.owner)
        self.click(self.b)
        self.assertEqual(len(self.peer.submissions), 1)

    def test_failed_batch_acceptance_rolls_back_before_register_or_submit(self):
        runner = self.a.host.comfy.input_flow.chain
        calls = []
        original = runner.reserve_supply
        def reserve(*args, **kwargs):
            calls.append(True)
            if len(calls) == 3:
                raise OSError('fixture: cursor save failed')
            return original(*args, **kwargs)
        before = len(self.calls)
        with patch.object(runner, 'reserve_supply', reserve):
            with self.assertRaises(OSError):
                runner.start(2)
        self.assertEqual(runner.runs(), [])
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.peer.submissions, [])

    def test_failed_runtime_save_cannot_bind_new_file_to_old_state(self):
        document = self.a.document
        before = document.path.read_bytes()
        metadata = copy.deepcopy(self.w.runtimes.rows())
        document.host.canvas.commit(lambda state: state['uses']['root'].update(prompt='new text'))
        with patch.object(document.host, 'persist', return_value=False):
            self.assertFalse(document.save())
        self.assertEqual(document.path.read_bytes(), before)
        self.assertEqual(self.w.runtimes.rows(), metadata)
        self.assertIn('文件未覆寫', document.error)
        self.assertTrue(document.save(), document.error)

    def test_apply_rejected_after_prepare_does_not_hold_unknown_lane(self):
        from prompt_calculus_studio.native_submission import prepare
        def without_stage(state):
            stages = set(state['multi_output']['stages'])
            state['multi_output']['stages'].clear()
            state['multi_output']['connections'] = [
                edge for edge in state['multi_output']['connections']
                if edge['destination'] not in stages and edge['source'] not in stages]
        self.assertTrue(self.a.host.canvas.commit(without_stage))
        def delayed(client, state, workflow, valid, submit, failed, **kwargs):
            def finish_prepare(*args):
                self.w.runtimes.errors['fixture'] = 'ticket write failed while preparing'
                submit(*args)
            return prepare(client, state, workflow, valid, finish_prepare, failed, **kwargs)
        with patch('prompt_calculus_studio.native_submission.prepare', delayed):
            self.click(self.a)
        self.assertFalse(any(route == 'workflow/native/apply' for _, route, _ in self.calls))
        operation = self.a.host.comfy.input_flow.chain.store.rows('input')[0]
        self.assertTrue(operation['ended'])
        self.assertTrue(operation['rejected'])
        self.assertEqual(operation['status'], 'failed')
        self.assertFalse(unfinished(self.a.host.store.db))
        self.w.runtimes.errors.clear()
        self.click(self.b)
        self.assertEqual(len(self.peer.submissions), 1)

    def test_restart_recovers_original_prompt_and_pauses_waiting_flow(self):
        self.click(self.a)
        self.click(self.b)
        token = self.a.document.runtime_token
        job = next(iter(self.a.host.comfy.generation.jobs.values()))
        prompt = job['prompt_id']
        self.reopen()
        self.assertEqual(len(self.peer.submissions), 1, self.notices)
        owner = self.w.runtimes.executions[token]
        self.assertEqual(owner.client.generation.record(job['id'])['prompt_id'], prompt)
        self.finish()
        self.assertEqual(len(self.peer.submissions), 1, 'Waiting work must require explicit resume after restart')
        b = next(d for d in self.w.documents if d.path.stem == 'B')
        client = b.execution.client
        run = client.input_flow.chain.runs(True)[0]
        self.assertEqual(run['status'], 'paused')
        client.input_flow.chain.resume(run['id'])
        self.tick()
        self.assertEqual(len(self.peer.submissions), 2, self.notices)

    def test_lost_submit_reply_is_reconciled_without_resubmission_even_without_tab(self):
        self.lost_reply = True
        self.click(self.a)
        token = self.a.document.runtime_token
        job_id = next(iter(self.a.host.comfy.generation.jobs))
        self.assertNotIn('prompt_id', self.a.host.comfy.generation.record(job_id))
        self.reopen(restore_tabs=False)
        self.lost_reply = False
        owner = self.w.runtimes.executions[token]
        self.assertIsNone(owner.document)
        self.assertEqual(len(self.peer.submissions), 1)
        self.assertEqual(owner.client.generation.record(job_id)['prompt_id'], next(iter(self.peer.pending)))
        self.finish()
        self.assertEqual(owner.client.generation.record(job_id)['state'], 'complete')
        self.assertIsNone(self.w.scheduler.owner)

    def test_save_as_keeps_live_original_owner_and_reopens_same_executor(self):
        self.click(self.a)
        original = self.a.document.execution
        token = original.runtime_token
        context = self.w.commands.context('tab', tab=self.a)
        target = self.w.commands.files.run('save_as', context, name='A copy', directory=self.root)
        self.assertIsNotNone(target, self.errors)
        self.assertIsNone(original.document)
        copied = self.a.document
        self.assertNotEqual(copied.runtime_token, token)
        reopened = self.w.open_path(self.root/'A.pcs-workflow', new_tab=True)
        self.assertIsNotNone(reopened, self.errors)
        self.assertIs(reopened.document.execution, original)
        self.assertIs(reopened.host.store, original.store)
        self.assertEqual(len(original.client.generation.jobs), 1)
        self.finish()
        self.assertFalse(copied.execution.received)
        self.assertEqual(original.client.input_flow.chain.runs()[0]['status'], 'complete')
        self.assertTrue(original.client.input_flow.chain.results)
        self.assertFalse(copied.execution.client.input_flow.chain.results)

    def test_dirty_save_as_preserves_original_file_and_detached_tasks_after_restart(self):
        source = self.a.document.path
        before = source.read_bytes()
        self.click(self.a)
        original = self.a.document.execution
        token = original.runtime_token
        self.a.host.canvas.commit(lambda state: state['uses']['root'].update(prompt='copy only draft'))
        context = self.w.commands.context('tab', tab=self.a)
        target = self.w.commands.files.run('save_as', context, name='A copy', directory=self.root)
        self.assertIsNotNone(target, self.errors)
        self.assertTrue(next(r for r in self.w.runtimes.rows() if r['token'] == token)['recovery_only'])
        reopened = self.w.open_path(source, new_tab=True)
        self.assertEqual(reopened.document.state['uses']['root']['prompt'], 'alpha')
        self.assertFalse(reopened.document.dirty)
        self.assertIsNot(reopened.document.execution, original)
        self.assertEqual(source.read_bytes(), before)
        self.reopen()
        old = self.w.runtimes.executions[token]
        self.assertIsNone(old.document)
        source_document = next(d for d in self.w.documents if d.path == source)
        copy_document = next(d for d in self.w.documents if d.path == target)
        self.assertEqual(source_document.state['uses']['root']['prompt'], 'alpha')
        self.assertFalse(source_document.dirty)
        self.assertEqual(copy_document.state['uses']['root']['prompt'], 'copy only draft')
        self.assertEqual(len(self.peer.submissions), 1)
        self.finish()
        self.assertTrue(old.client.input_flow.chain.results)
        self.assertFalse(copy_document.execution.client.input_flow.chain.results)
        self.assertFalse(source_document.execution.client.input_flow.chain.results)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(len(self.peer.submissions), 1)

    def test_workspace_history_reopens_detached_completed_tasks_without_submission(self):
        from prompt_calculus_studio.workspace_history import WorkspaceHistory
        self.click(self.a)
        token = self.a.document.runtime_token
        self.a.host.canvas.commit(lambda state: state['uses']['root'].update(prompt='copy draft'))
        context = self.w.commands.context('tab', tab=self.a)
        self.assertIsNotNone(self.w.commands.files.run('save_as', context, name='History copy', directory=self.root))
        self.finish()
        self.reopen(restore_tabs=False)
        self.assertNotIn(token, self.w.runtimes.executions)
        dialog = WorkspaceHistory(self.w)
        index = next(i for i, value in enumerate(dialog.values) if value['token'] == token)
        dialog.picker.setCurrentIndex(index)
        self.assertIsNone(dialog.execution.document)
        self.assertIn('alpha', dialog.detail.toPlainText())
        self.assertFalse(dialog.stop.isEnabled())
        self.assertEqual(len(self.peer.submissions), 1)
        dialog.reject()
        dialog.deleteLater()

    def test_flow_history_updates_status_and_only_enables_applicable_actions(self):
        from PySide6.QtWidgets import QPushButton, QListWidget, QPlainTextEdit, QCheckBox
        from prompt_calculus_studio.stage_widgets import show_history
        from prompt_calculus_studio.widgets import StudioDialog
        self.click(self.a)
        self.click(self.b)
        def inspect(dialog):
            controls = {b.text(): b for b in dialog.findChildren(QPushButton)}
            listing = dialog.findChild(QListWidget)
            detail = dialog.findChild(QPlainTextEdit)
            self.assertIn('排隊中', listing.item(0).text())
            self.assertFalse(controls['繼續'].isEnabled())
            self.assertFalse(controls['重取結果／重試失敗項'].isEnabled())
            self.assertFalse(controls['查看原任務回覆'].isEnabled())
            self.assertTrue(controls['取消這份流程'].isEnabled())
            self.finish()
            QTest.qWait(600)
            self.assertEqual(listing.count(), 2)
            listing.setCurrentRow(1)
            self.assertTrue(controls['查看原任務回覆'].isEnabled())
            self.assertNotIn('實際提交工作流：', detail.toPlainText())
            dialog.findChild(QCheckBox).setChecked(True)
            self.assertIn('實際提交工作流：', detail.toPlainText())
            self.finish()
            QTest.qWait(600)
            self.assertIn('完成', listing.item(1).text())
            self.assertFalse(controls['取消這份流程'].isEnabled())
            self.assertFalse(controls['繼續'].isEnabled())
            self.assertFalse(controls['重取結果／重試失敗項'].isEnabled())
            dialog.accept()
        with patch.object(StudioDialog, 'exec', inspect):
            show_history(self.w, execution=self.b.document.execution)

    def test_runtime_state_and_save_as_mapping_survive_file_save_and_restart(self):
        context = self.w.commands.context('tab', tab=self.a)
        target = self.w.commands.files.run('save_as', context, name='A copy', directory=self.root)
        document = self.a.document
        token = document.runtime_token
        runtime_workspace = document.state['workspace']
        maps = copy.deepcopy(document.identity_maps)
        document.host.canvas.commit(lambda state: state['uses']['root'].update(prompt='runtime recovery'))
        document.host.persist()
        # Preserve an unsaved runtime write: simulate process shutdown without
        # exporting it to the file, while retaining the SQLite source cursor.
        before = target.read_bytes()
        self.w.prepare_close = lambda *args, **kwargs: True
        self.reopen()
        recovered = next(d for d in self.w.documents if d.path == target)
        self.assertEqual(recovered.runtime_token, token)
        self.assertEqual(recovered.state['workspace'], runtime_workspace)
        self.assertEqual(recovered.identity_maps, maps)
        self.assertEqual(recovered.state['uses']['root']['prompt'], 'runtime recovery')
        self.assertEqual(target.read_bytes(), before)
        self.assertTrue(recovered.save(), recovered.error)
        self.reopen()
        again = next(d for d in self.w.documents if d.path == target)
        self.assertEqual(again.runtime_token, token, 'Atomic file replacement must keep the execution owner')
        self.assertEqual(again.state['uses']['root']['prompt'], 'runtime recovery')

    def test_rename_updates_unopened_owner_and_external_copy_gets_new_runtime(self):
        token = self.a.document.runtime_token
        context = self.w.commands.context('tab', tab=self.a)
        target = self.w.commands.files.run('rename', context, name='Renamed')
        self.assertIsNotNone(target, self.errors)
        self.reopen()
        document = next(d for d in self.w.documents if d.path == target)
        self.assertEqual(document.runtime_token, token)
        copied = self.root/'external-copy.pcs-workflow'
        copied.write_bytes(target.read_bytes())
        self.w.close()
        self.w.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        (self.data/'workspace-layout.json').unlink()
        self.w = self.window()
        tab = self.w.open_path(copied, new_tab=True)
        self.assertNotEqual(tab.document.runtime_token, token)
