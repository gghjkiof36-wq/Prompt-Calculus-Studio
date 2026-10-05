"""Real item actions over isolated Qt/SQLite with a synthetic native executor."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt,QPoint
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from test_084_stages import StageTests
from prompt_calculus_studio import multi_output as model
from prompt_calculus_studio.flow_data import add_scheduler


class ScheduleItemActionsTests(StageTests):
    def queue(self,count=3):
        self.stages();keys=[]
        def setup(state):
            key=add_scheduler(state);keys.append(key)
            model.connect(state,self.out,key+'::clip1','clip');model.connect(state,key+'::clip1',self.clip,'clip')
        self.assertTrue(self.c.commit(setup));self.controls.count.setValue(count);self.click()
        return keys[0],self.runner.store.rows('entry',active=True)

    def test_failed_item_right_click_only_removes_that_item_and_preserves_next_inputs(self):
        key,entries=self.queue();self.executor.finish(error=True)
        panel=self.c.flow_cards[key].panel;panel.refresh()
        self.assertEqual(self.runner.entry_status(entries[0]),'failed')
        saved=copy.deepcopy(entries[1:]);connections=copy.deepcopy(self.c.data()['connections']);menus=[]
        point=panel.list.visualItemRect(panel.list.item(0)).center()
        with patch('prompt_calculus_studio.widgets.RoundMenu.open_at',lambda menu,*_:menus.append(menu)):
            event=QContextMenuEvent(QContextMenuEvent.Reason.Mouse,point,panel.list.viewport().mapToGlobal(point))
            QApplication.sendEvent(panel.list.viewport(),event)
        self.assertEqual(len(menus),1)
        action=next(a for a in menus[0].actions() if a.text()=='移除此項');self.assertTrue(action.isEnabled());action.trigger()
        self.assertIn(key,self.c.data()['schedulers']);self.assertEqual(self.c.data()['connections'],connections)
        self.assertEqual([self.runner.store.read(e['id']) for e in saved],saved)
        self.assertEqual(self.runner.store.read(entries[0]['id'])['status'],'removed')
        self.runner.resume(entries[0]['run']);QTest.qWait(40)
        self.executor.finish();self.executor.finish()
        self.assertEqual(len(self.executor.submissions),3)

    def test_delete_waiting_item_and_empty_context_never_remove_module(self):
        key,entries=self.queue();panel=self.c.flow_cards[key].panel;panel.refresh()
        panel.list.setCurrentRow(1);QTest.keyClick(panel.list,Qt.Key.Key_Delete)
        self.assertEqual(self.runner.store.read(entries[1]['id'])['status'],'removed')
        self.assertEqual(self.runner.store.read(entries[2]['id']),entries[2])
        self.assertIn(key,self.c.data()['schedulers'])
        event=QContextMenuEvent(QContextMenuEvent.Reason.Mouse,QPoint(-1,-1),QPoint())
        panel.list.contextMenuEvent(event);self.assertTrue(event.isAccepted())

    def test_bottom_cancel_preserves_waiting_rows_until_explicit_resume(self):
        key,entries=self.queue();attempt=self.runner.store.rows('attempt')[0]
        QTest.mouseClick(self.controls.stop,Qt.MouseButton.LeftButton);QTest.qWait(40)
        self.assertEqual(self.executor.cancelled,[attempt['id']])
        self.assertEqual(self.runner.store.read(entries[0]['id'])['status'],'cancelled',self.notices)
        self.assertEqual([self.runner.store.read(e['id']) for e in entries[1:]],entries[1:])
        self.assertEqual(len(self.executor.submissions),1)
        self.runner.resume(entries[0]['run']);QTest.qWait(40)
        self.executor.finish();self.executor.finish();self.assertEqual(len(self.executor.submissions),3)

    def test_cancel_acknowledgement_is_not_completion_and_unknown_cannot_be_removed(self):
        key,entries=self.queue();attempt=self.runner.store.rows('attempt')[0]
        original=self.w.comfy.request
        def delayed(route,data=None,done=None,failed=None,**kw):
            if route=='workflow/native/cancel':
                self.executor.cancelled.append(data['id']);done(dict(state='cancelling'));return
            return original(route,data,done,failed,**kw)
        self.w.comfy.request=delayed;self.runner.cancel_current(entries[0]['id'])
        self.assertEqual(self.runner.store.read(entries[0]['id'])['status'],'running')
        self.assertEqual(self.runner.entry_status(entries[0]),'cancelling')
        self.assertFalse(self.runner.can_remove_entry(entries[0]['id']))
        with self.assertRaises(ValueError):self.runner.remove_entry(entries[0]['id'])
        with self.assertRaises(ValueError):self.runner.resume(entries[0]['run'])
        with self.assertRaises(ValueError):self.runner.retry(attempt['id'])
        from prompt_calculus_studio.stage_store import StageStore
        self.assertTrue(StageStore(self.w.store.db).read(attempt['id'])['cancel_requested'])
        self.executor.finish(error=True);QTest.qWait(30)
        self.assertEqual(self.runner.store.read(entries[0]['id'])['status'],'cancelled')
        self.assertEqual([self.runner.store.read(e['id']) for e in entries[1:]],entries[1:])

    def test_explicit_cancel_all_remains_separate_from_single_item(self):
        key,entries=self.queue();self.w.comfy.interrupt(True)
        self.assertEqual(self.runner.store.read(entries[0]['run'])['status'],'cancelled')
        self.assertTrue(all(self.runner.store.read(e['id'])['status']=='retained' for e in entries[1:]))

    def test_more_menu_cancel_uses_current_item_in_this_scheduler(self):
        key,entries=self.queue();panel=self.c.flow_cards[key].panel;panel.refresh();menus=[]
        with patch('prompt_calculus_studio.widgets.RoundMenu.open_for',lambda menu,*_:menus.append(menu)):panel.more_actions()
        next(a for a in menus[0].actions() if a.text()=='取消目前項目').trigger();QTest.qWait(30)
        self.assertEqual(self.runner.store.read(entries[0]['id'])['status'],'cancelled')
        self.assertEqual([self.runner.store.read(e['id']) for e in entries[1:]],entries[1:])

    def test_unknown_receipt_cannot_be_removed_or_resumed_and_survives_reopen(self):
        key,entries=self.queue();attempt=self.runner.store.rows('attempt')[0]
        self.runner.store.update(attempt['id'],status='unconfirmed',error='lost reply')
        self.runner.pause(attempt['owner']);before=copy.deepcopy(self.runner.store.rows('entry'))
        self.assertFalse(self.runner.can_remove_entry(entries[0]['id']))
        with self.assertRaises(ValueError):self.runner.remove_entry(entries[0]['id'])
        from prompt_calculus_studio.stage_store import StageStore
        reopened=StageStore(self.w.store.db)
        self.assertEqual(reopened.rows('entry'),before)
        self.assertEqual(reopened.read(attempt['id'])['status'],'unconfirmed')

    def test_nested_failed_child_removal_preserves_its_parent_and_other_outer_items(self):
        stage=self.stages()[0];keys=[]
        def setup(s):
            inner=add_scheduler(s);outer=add_scheduler(s);keys.extend((inner,outer))
            model.connect(s,self.out,inner+'::clip1','clip');model.connect(s,inner+'::clip1',self.clip,'clip')
            model.connect(s,stage,outer+'::flow','flow')
        self.assertTrue(self.c.commit(setup));self.controls.count.setValue(2);self.click()
        entries=self.runner.store.rows('entry');child=next(e for e in entries if e.get('parent'))
        parent=self.runner.store.read(child['parent']);siblings=[e for e in entries if e['scheduler']==keys[1] and e['id']!=parent['id']]
        self.executor.finish(error=True);self.runner.remove_entry(child['id'])
        self.assertEqual([self.runner.store.read(e['id']) for e in siblings],siblings)
        self.assertEqual(self.runner.store.read(parent['id'])['status'],'running')
        self.assertEqual(self.runner.store.read(child['id'])['status'],'removed')
        self.runner.resume(parent['run']);QTest.qWait(40)
        self.assertEqual(len(self.executor.submissions),2,self.notices)
        self.executor.finish();self.assertIsNone(self.runner.current())

    def test_cancel_before_native_receipt_blocks_late_submit_callback(self):
        held=[];original=self.w.comfy.request
        def delay(route,data=None,done=None,failed=None,**kw):
            if route=='workflow/native/start':
                self.executor.native.poll(self.executor.live);self.executor.native.start(data)
                held.append((data,done));return
            if route=='workflow/native/cancel':
                self.executor.cancelled.append(data['id']);done(self.executor.native.cancel(data['id'],None,None));return
            return original(route,data,done,failed,**kw)
        self.w.comfy.request=delay;key,entries=self.queue()
        self.assertEqual(len(held),1);self.assertEqual(self.runner.entry_status(entries[0]),'preparing')
        self.runner.cancel_current();data,done=held[0];done(self.executor.native.status(data['id']));QTest.qWait(30)
        self.assertEqual(self.executor.native.poll(self.executor.live)['commands'],[])
        self.assertFalse(self.executor.submissions)
        self.assertEqual(self.runner.store.read(entries[0]['id'])['status'],'cancelled')
        self.assertEqual([self.runner.store.read(e['id']) for e in entries[1:]],entries[1:])

    def test_cancel_confirmation_after_workspace_switch_cannot_touch_new_workspace(self):
        key,entries=self.queue();workspace=self.w.state['workspace'];original=self.w.comfy.request
        def delay(route,data=None,done=None,failed=None,**kw):
            if route=='workflow/native/cancel':done(dict(state='cancelling'));return
            return original(route,data,done,failed,**kw)
        self.w.comfy.request=delay;self.runner.cancel_current()
        self.runner.workspace_changed(workspace);self.w.state['workspace']='other-workspace'
        try:
            self.executor.finish(error=True)
            self.assertEqual(self.runner.store.read(entries[0]['id'])['status'],'cancelled')
            self.assertEqual([self.runner.store.read(e['id']) for e in entries[1:]],entries[1:])
            self.assertFalse(self.runner.runs());self.assertEqual(len(self.executor.submissions),1)
        finally:self.w.state['workspace']=workspace


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(ScheduleItemActionsTests(name) for name in ScheduleItemActionsTests.__dict__ if name.startswith('test_'))
