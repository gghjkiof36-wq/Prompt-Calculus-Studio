"""Snapshot ownership across actual document tabs and shell/store reopening."""
import copy
import unittest

from prompt_calculus_studio import multi_output,stage_batches
from prompt_calculus_studio.flow_data import add_scheduler
from test_workspace_execution import WorkspaceExecutionTests as Fixture


class BatchWorkspaceTests(unittest.TestCase):
    setUpClass=classmethod(Fixture.setUpClass.__func__)
    setUp=Fixture.setUp
    _fixture_teardown=Fixture.tearDown
    window=Fixture.window
    make=Fixture.make
    transport_request=Fixture.transport_request
    tick=Fixture.tick
    finish=Fixture.finish
    reopen=Fixture.reopen

    def tearDown(self):
        from PySide6.QtTest import QTest
        # A restored hidden tab still owns its initial, zero-timer view restore.
        # Let real Show/restore finish before the same QApplication deletes it;
        # a real process exit does not reopen another shell in that event loop.
        for tab in list(self.w.all_pages()):
            if tab.host:tab.host.comfy.input_flow.chain.timer.stop()
            self.w.focus_tab(tab)
            QTest.qWait(10)
        self._fixture_teardown()

    def queue(self,count):
        host=self.a.host
        def setup(state):
            key=add_scheduler(state)
            stage=next(iter(state['multi_output']['stages']))
            multi_output.connect(state,stage,key+'::flow','flow')
        self.assertTrue(host.canvas.commit(setup))
        self.w.focus_tab(self.a)
        runner=host.comfy.input_flow.chain
        runner.start(count);runner.timer.stop()
        return runner

    def test_edit_switch_tabs_and_undo_keep_both_accepted_groups(self):
        runner=self.queue(3)
        self.a.host.canvas.commit(lambda state:state['uses']['root'].update(prompt='second accepted text'))
        runner.start(3);runner.timer.stop()
        saved=copy.deepcopy(runner.store.rows('batch_snapshot'))
        self.a.host.canvas.commit(lambda state:state['uses']['root'].update(prompt='later edit'))
        self.w.focus_tab(self.b)
        self.a.host.canvas.undo()
        runner.timer.stop()
        self.assertEqual(runner.store.rows('batch_snapshot'),saved)
        for _ in range(6):stage_batches.expand(runner,20)
        self.assertEqual([v['value'] for e in runner.store.rows('entry') for v in e['saved']['values'].values()],['alpha']*3+['second accepted text']*3)
        self.assertEqual(self.b.host.state['uses']['root']['prompt'],'beta')

    def test_shell_and_storage_reopen_after_partial_expansion(self):
        runner=self.queue(7)
        stage_batches.expand(runner,20);runner.timer.stop()
        self.assertEqual(runner.store.rows('batch_snapshot')[0]['expanded'],1)
        token=self.a.document.runtime_token
        ident=runner.runs()[0]['id']
        self.assertTrue(self.a.document.save())
        self.reopen()
        runner=self.w.runtimes.executions[token].client.input_flow.chain
        self.assertEqual(runner.store.read(ident)['status'],'paused')
        self.assertEqual(runner.store.rows('batch_snapshot')[0]['expanded'],1)
        runner.resume(ident);self.tick()
        self.assertEqual(len(self.peer.submissions),1)
        for _ in range(7):self.finish()
        self.assertEqual(len(self.peer.submissions),7)
        self.assertEqual(len({p['prompt_id'] for p in self.peer.submissions}),7)
        self.assertEqual(runner.store.read(ident)['status'],'complete')
        self.assertEqual(len(runner.store.rows('entry')),7)
        self.assertEqual(runner.store.rows('batch_snapshot'),[])

    def test_shell_and_storage_reopen_during_native_execution(self):
        import sys,traceback
        previous=sys.excepthook
        def diagnose(kind,error,tb):
            traceback.print_exception(kind,error,tb)
            previous(kind,error,tb)
        sys.excepthook=diagnose
        runner=self.queue(7)
        token=self.a.document.runtime_token
        ident=runner.runs()[0]['id']
        runner.later();self.tick()
        self.assertEqual(len(self.peer.submissions),1)
        self.assertTrue(self.a.document.save())
        prompt_id=self.peer.submissions[0]['prompt_id']
        self.a=self.b=None
        runner=None
        self.reopen()
        runner=self.w.runtimes.executions[token].client.input_flow.chain
        self.assertEqual(len(self.peer.submissions),1)
        self.assertEqual(self.errors,[])
        self.assertIn(prompt_id,self.peer.pending)
        runner.resume(ident);self.tick()
        for _ in range(7):self.finish()
        self.assertEqual(len(self.peer.submissions),7)
        self.assertEqual(len({p['prompt_id'] for p in self.peer.submissions}),7)
        self.assertEqual(runner.store.read(ident)['status'],'complete')
        self.assertEqual(len(runner.store.rows('entry')),7)
        self.assertEqual(runner.store.rows('batch_snapshot'),[])


# unittest discovery also collects imported TestCase classes.
del Fixture
