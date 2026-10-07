"""Detached or replaced card layout callbacks cannot outlive their owner."""
import sys
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from test_stage_lifecycle_repair import StageLifecycleTests


class CardLifecycleTests(StageLifecycleTests):
    def test_removed_scheduler_stops_timer_and_ignores_late_callback(self):
        _,key=self.queue();card=self.c.flow_cards[key];QTest.qWait(20)
        card.proxy.relayout.start(0)
        self.c.remove_flow_node('schedulers',key)
        self.assertIsNone(card.scene())
        self.assertFalse(card.proxy.relayout.isActive())
        with patch.object(card,'layout_card') as layout:
            card.proxy.fit_content()
            QCoreApplication.sendEvent(card.panel,QEvent(QEvent.Type.LayoutRequest))
            self.assertFalse(card.proxy.relayout.isActive())
            layout.assert_not_called()

    def test_missing_model_is_rejected_before_card_pruning(self):
        _,key=self.queue();card=self.c.flow_cards[key];QTest.qWait(20)
        model=self.c.data()['schedulers'].pop(key)
        try:
            with patch.object(card,'layout_card') as layout:
                card.proxy.fit_content();layout.assert_not_called()
        finally:self.c.data()['schedulers'][key]=model

    def test_same_key_replacement_does_not_refresh_old_card(self):
        from prompt_calculus_studio.flow_widgets import FlowCard
        _,key=self.queue();old=self.c.flow_cards[key];QTest.qWait(20)
        replacement=FlowCard(self.c,key,'schedulers')
        self.c.flow_cards[key]=replacement;self.c.view.scene().addItem(replacement);replacement.attach()
        try:
            with patch.object(old,'layout_card') as layout:
                old.proxy.fit_content();layout.assert_not_called()
        finally:self.c.view.scene().removeItem(old);old.deleteLater()

    def test_live_card_still_reacts_to_layout_requests(self):
        _,key=self.queue();card=self.c.flow_cards[key];QTest.qWait(20)
        with patch.object(card,'layout_card',wraps=card.layout_card) as layout:
            QCoreApplication.sendEvent(card.panel,QEvent(QEvent.Type.LayoutRequest))
            QTest.qWait(30);self.assertTrue(layout.called)

    def test_workspace_switch_retires_pending_layout_without_changing_saved_schedule(self):
        import copy
        from prompt_calculus_studio.workspace_scene import create
        _,key=self.queue();QTest.qWait(20)
        workspace=self.w.state['workspace'];expected=copy.deepcopy(self.c.data()['schedulers'][key])
        other=create(self.w.state,'Other isolated workspace',False);errors=[]
        with patch.object(sys,'excepthook',side_effect=lambda *args:errors.append(args)):
            self.c.flow_cards[key].proxy.relayout.start(0)
            self.w.change_workspace(other);QTest.qWait(20)
            self.assertNotIn(key,self.c.flow_cards)
            self.w.change_workspace(workspace);QTest.qWait(20)
            self.assertEqual(self.c.data()['schedulers'][key],expected)
            self.assertIn(key,self.c.flow_cards)
        self.assertEqual(errors,[])

    def test_running_queue_replacement_has_no_unhandled_callback(self):
        errors=[]
        with patch.object(sys,'excepthook',side_effect=lambda *args:errors.append(args)):
            super().test_replace_queue_keeps_old_waiting_without_owning_new_run()
            QTest.qWait(40)
        self.assertEqual(errors,[])

    def test_remove_undo_redo_keeps_new_card_live_and_no_old_callback(self):
        _,key=self.queue();QTest.qWait(20);errors=[]
        with patch.object(sys,'excepthook',side_effect=lambda *args:errors.append(args)):
            old=self.c.flow_cards[key];old.proxy.relayout.start(0)
            self.c.remove_flow_node('schedulers',key);self.c.undo();QTest.qWait(20)
            self.assertIn(key,self.c.flow_cards);self.assertIsNot(old,self.c.flow_cards[key])
            self.c.redo();QTest.qWait(30);self.assertNotIn(key,self.c.flow_cards)
        self.assertEqual(errors,[])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(CardLifecycleTests(n) for n in CardLifecycleTests.__dict__ if n.startswith('test_'))
