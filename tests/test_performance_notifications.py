"""No-document notices and count-only changes through real embedded views."""
import unittest
from unittest.mock import patch

import test_canvas_incremental_refresh as fixture


class PerformanceNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.IncrementalCanvasTests.setUpClass()

    def setUp(self):
        self.f=fixture.IncrementalCanvasTests();self.f.setUp()
        self.w=self.f.w;self.c=self.f.c

    def tearDown(self):self.f.tearDown()

    def test_empty_execution_scope_and_notice_do_not_project_either_host(self):
        second=self.f.f.w.duplicate_view(self.f.tab,True);self.f.f.events()
        other=second.host.canvas
        notices=[];self.w.documentChanged.connect(lambda *args:notices.append(args))
        from prompt_calculus_studio.stage_context import execution_scope
        from prompt_calculus_studio.stage_store import StageStore
        store=StageStore(self.w.store.db)
        with patch.object(self.c,'history_state',wraps=self.c.history_state) as first, \
             patch.object(other,'history_state',wraps=other.history_state) as sibling:
            with execution_scope(self.w,store):pass
            self.assertEqual(notices,[])
            self.w.document_changed(frozenset(),reason='execution',kind='content')
            self.f.f.events()
            self.assertEqual(first.call_count,0);self.assertEqual(sibling.call_count,0)

    def test_nonempty_execution_write_reaches_both_hosts(self):
        second=self.f.f.w.duplicate_view(self.f.tab,True);self.f.f.events()
        other=second.host.canvas
        from prompt_calculus_studio.stage_context import execution_scope
        from prompt_calculus_studio.stage_store import StageStore
        store=StageStore(self.w.store.db)
        with patch.object(self.c,'refresh',wraps=self.c.refresh) as first, \
             patch.object(other,'refresh',wraps=other.refresh) as sibling:
            with execution_scope(self.w,store) as tx:
                tx.register('uses','root1');self.w.state['uses']['root1']['prompt']='progress/result'
            self.f.f.events();self.f.f.events()
            self.assertEqual(first.call_count,1);self.assertEqual(sibling.call_count,1)
        self.assertEqual(other.window.state['uses']['root1']['prompt'],'progress/result')

    def test_count_then_first_execution_notification_preserves_cards(self):
        old=dict(self.c.cards)
        with self.f.builds() as calls:
            self.w.run_controls.count.setValue(100)
            self.f.f.events()
            self.w.document_changed(frozenset({'uses'}),reason='execution',kind='content')
            self.f.f.events()
        self.assertEqual(calls,[])
        for key,card in old.items():self.assertIs(self.c.cards[key],card)

    def test_font_theme_size_and_unknown_settings_still_invalidate_all(self):
        for key,value in (('font_family','Arial'),('visual_palette','paper'),('ui_size',17),('future_visual_setting',True)):
            with self.subTest(key=key):
                old=dict(self.c.cards)
                self.w.state['settings'][key]=value
                with self.f.builds() as calls:
                    self.w.document_changed(frozenset({'settings'}),reason='execution',kind='content')
                    self.f.f.events()
                self.assertEqual(len(calls),len(old))
                self.assertTrue(all(self.c.cards[k] is not card for k,card in old.items()))
