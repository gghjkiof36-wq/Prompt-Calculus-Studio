"""Main execution button resumes the matching persisted Stage run."""
import copy
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from _qt_cleanup import close_window
from stage_fixture import StageFixture, ChainExecutor
import test_084_stages as stages_fixture
from prompt_calculus_studio import multi_output as model, stage_model
from prompt_calculus_studio.flow_data import add_scheduler
from prompt_calculus_studio.window import Window


class MainResumeTests(StageFixture):
    setUp=stages_fixture.StageTests.setUp
    stages=stages_fixture.StageTests.stages

    def queue(self):
        self.stages();keys=[]
        def setup(state):
            keys.append(add_scheduler(state))
            model.connect(state,self.out,keys[0]+'::clip1','clip')
            model.connect(state,keys[0]+'::clip1',self.clip,'clip')
        self.assertTrue(self.c.commit(setup),self.notices)
        self.controls.count.setValue(2)
        return keys[0]

    def assert_resume(self,ident):
        entries=[e['id'] for e in self.runner.store.rows('entry')]
        runs=[r['id'] for r in self.runner.runs()]
        self.controls.refresh()
        self.assertEqual(self.controls.run_button.text(),'繼續')
        self.assertTrue(self.controls.run_button.isEnabled())
        with patch.object(self.runner,'resume',wraps=self.runner.resume) as resume, \
             patch.object(self.runner,'start',wraps=self.runner.start) as start:
            QTest.mouseClick(self.controls.run_button,Qt.MouseButton.LeftButton)
            resume.assert_called_once_with(ident);start.assert_not_called()
            self.assertEqual(self.runner.store.read(ident)['status'],'running')
        self.assertEqual([e['id'] for e in self.runner.store.rows('entry')],entries)
        self.assertEqual([r['id'] for r in self.runner.runs()],runs)
        self.assertEqual(self.controls.run_button.text(),'執行')
        QTest.qWait(40)

    def test_main_button_resumes_without_appending_entries(self):
        self.queue();self.click();ident=self.runner.current()['id']
        self.runner.pause(ident)
        self.assert_resume(ident)
        self.executor.finish();self.executor.finish()
        self.assertEqual(self.runner.store.read(ident)['status'],'complete')
        self.assertEqual(len(self.executor.submissions),2)

    def test_two_paused_plans_resume_only_the_current_signature(self):
        self.queue();self.click();ident=self.runner.current()['id'];self.runner.pause(ident)
        original=copy.deepcopy(self.c.data())
        def different_plan(state):
            first=next(iter(state['multi_output']['stages']))
            second=stage_model.add(state,workflow='B')
            model.connect(state,first,second,'done')
        self.assertTrue(self.c.commit(different_plan),self.notices)
        other=self.runner.create_run(stage_model.compile_plan(self.w.state),1,status='paused')['id']
        self.assertTrue(self.c.commit(lambda state:state.update(multi_output=copy.deepcopy(original))))
        self.assertEqual([r['status'] for r in self.runner.runs()],['paused','paused'])
        other_before=copy.deepcopy(self.runner.store.read(other))
        self.assert_resume(ident)
        self.assertEqual(self.runner.store.read(other),other_before)

    def test_reopened_window_resumes_restart_run_without_new_entries(self):
        queue=self.queue()
        self.runner.store.set_scheduler_paused(self.w.state['workspace'],queue,True)
        self.click();ident=self.runner.current()['id']
        self.assertEqual(self.runner.current()['status'],'paused')
        self.assertFalse(self.executor.submissions)
        entries=[e['id'] for e in self.runner.store.rows('entry')]
        self.w.persist();close_window(self.w)
        for executor in self.w.__dict__.get('_test_receipt_executors',[]):executor.close()
        self.w=Window(self.tmp.name);self.w.comfy.timer.stop();self.w.comfy.enabled=False
        self.w.notice=self.notices.append;self.w.error=self.notices.append
        self.w.show();self.w.set_interface_mode('canvas')
        self.c=self.w.canvas;self.controls=self.c.execution_bar.controls
        self.executor=ChainExecutor(self.w,Path(self.tmp.name)/'reopened-executor')
        self.runner=self.w.comfy.input_flow.chain
        run=self.runner.store.read(ident)
        self.assertEqual((run['status'],run['pause_reason']),('paused','restart'))
        self.assertEqual([e['id'] for e in self.runner.store.rows('entry')],entries)
        self.assert_resume(ident)
        self.executor.finish();self.executor.finish()
        self.assertEqual(self.runner.store.read(ident)['status'],'complete')
        self.assertEqual(len(self.executor.submissions),2)

    def test_other_workspace_or_server_pause_does_not_offer_resume(self):
        self.queue();plan=stage_model.compile_plan(self.w.state)
        for workspace,server in ((self.w.state['workspace']+'-other',self.runner.client.url),
                                 (self.w.state['workspace'],self.runner.client.url+'-other')):
            self.runner.store.add('run',workspace,status='paused',server=server,plan=plan,stack=[])
        self.controls.refresh()
        self.assertNotEqual(self.controls.run_button.text(),'繼續')
        self.assertIsNone(self.controls.paused_run())

    def test_scheduler_card_menu_cancels_current_then_all_without_new_submission(self):
        from PySide6.QtCore import QPoint
        from prompt_calculus_studio.widgets import RoundMenu
        key=self.queue();self.controls.count.setValue(3);self.click()
        entries=copy.deepcopy(self.runner.store.rows('entry'))
        ident=self.runner.current()['id'];attempt=self.runner.store.rows('attempt')[0]
        def action(text):
            menus=[]
            with patch.object(RoundMenu,'open_at',lambda menu,*_:menus.append(menu)):
                self.c.flow_cards[key].open_menu(QPoint())
            selected=next(a for a in menus[0].actions() if a.text()==text)
            self.assertTrue(selected.isEnabled());selected.trigger();menus[0].deleteLater()
            QTest.qWait(40)
        action('取消目前項目')
        self.assertEqual(self.executor.cancelled,[attempt['id']])
        self.assertEqual(self.runner.store.read(entries[0]['id'])['status'],'cancelled')
        self.assertEqual([self.runner.store.read(e['id']) for e in entries[1:]],entries[1:])
        action('取消整個預排程的所有流程')
        self.assertEqual(self.runner.store.read(ident)['status'],'cancelled')
        self.assertTrue(all(self.runner.store.read(e['id'])['status']=='retained' for e in entries[1:]))
        self.assertEqual(len(self.executor.submissions),1)

    def test_resume_error_keeps_paused_run_and_never_starts_another_batch(self):
        self.queue();self.click();ident=self.runner.current()['id']
        self.executor.finish(error=True)
        self.assertEqual(self.runner.store.read(ident)['status'],'paused')
        entries=copy.deepcopy(self.runner.store.rows('entry'))
        self.controls.refresh();self.assertEqual(self.controls.run_button.text(),'繼續')
        with patch.object(self.runner,'start',wraps=self.runner.start) as start:
            self.controls.run_button.click();start.assert_not_called()
        self.assertEqual(self.runner.store.rows('entry'),entries)
        self.assertEqual(self.runner.store.read(ident)['status'],'paused')
        self.assertTrue(self.controls.run_button.isEnabled())
        self.assertTrue(any('請先處理該項' in message for message in self.notices))

    def test_scheduler_card_menu_converts_legacy_wiring_with_undo(self):
        from PySide6.QtCore import QPoint
        from prompt_calculus_studio.widgets import RoundMenu
        from prompt_calculus_studio.scheduler_wiring import legacy_edges
        key=self.queue();before=copy.deepcopy(self.c.data())
        self.assertTrue(legacy_edges(self.w.state,key))
        menus=[]
        with patch.object(RoundMenu,'open_at',lambda menu,*_:menus.append(menu)):
            self.c.flow_cards[key].open_menu(QPoint())
        next(a for a in menus[0].actions() if a.text()=='整理舊接線').trigger()
        self.assertFalse(legacy_edges(self.w.state,key),self.notices)
        self.c.undo();self.assertEqual(self.c.data(),before)
        self.c.redo();self.assertFalse(legacy_edges(self.w.state,key))
        self.assertFalse(self.executor.submissions);menus[0].deleteLater()
