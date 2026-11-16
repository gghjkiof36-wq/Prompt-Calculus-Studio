"""Real manager/Markdown/chat editing while a workflow waits and recovers."""
from _qt_wait import wait_ms
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from prompt_calculus_studio import stage_batches, schedule_batch_actions as actions
from prompt_calculus_studio.local_documents import category, io
from prompt_calculus_studio.workspace_files import create_file
from test_material_editor import state
from test_stage_sched_readiness import SchedulingReadinessTests as Fixture
from test_workspace_shell import WorkspaceShellTests as EditorFixture


class CrossTypeSchedulingTests(unittest.TestCase):
    setUpClass = classmethod(Fixture.setUpClass.__func__)
    setUp = Fixture.setUp
    tearDown = Fixture.tearDown
    window = Fixture.window
    make = Fixture.make
    transport_request = Fixture.transport_request
    tick = Fixture.tick
    finish = Fixture.finish
    scheduler = Fixture.scheduler
    drain = Fixture.drain
    editor = EditorFixture.editor

    def events(self):
        self.app.processEvents(); wait_ms(20)

    def test_manager_md_chat_edit_save_reopen_during_wait_and_recovery(self):
        runner, key = self.scheduler()
        runner.start(3); runner.timer.stop()
        group = runner.store.rows('batch_control')[0]['group']
        actions.bypass(runner, key, group); self.tick()
        self.assertEqual(self.a.host.comfy.scheduling_message, '已繞過')
        value = state()
        doc = category.from_state(value, value['items'][0]['module'])
        self.assertEqual(io.create(self.root, 'Manager.pcs-category', doc).status, 'saved')
        paths = [self.root/'Manager.pcs-category', create_file(self.root, 'markdown', 'Note'),
                 create_file(self.root, 'chat', 'Chat')]
        tabs = [self.w.open_path(path, new_tab=True) for path in paths]
        self.tick()
        for phase in ('waiting', 'recovering'):
            if phase == 'recovering':
                actions.bypass(runner, key, group); self.tick()
                self.assertTrue(self.peer.pending)
            manager, md, chat = tabs
            self.w.focus_tab(manager); self.events()
            ident, dialog = self.editor(manager)
            QTest.keyClicks(dialog.prompt, ' '+phase)
            QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
            self.events()
            self.assertTrue(self.w.save_tab(manager))
            self.assertIn(phase, io.load(self.root, paths[0].name).document['payload']['items'][0]['prompt'])
            self.w.focus_tab(md); self.events()
            md.widget.md_surface.set_mode('write')
            md.widget.insertPlainText(phase+'\n')
            self.assertTrue(self.w.save_tab(md))
            self.assertIn(phase, paths[1].read_text(encoding='utf-8-sig'))
            self.w.focus_tab(chat); self.events()
            QTest.keyClicks(chat.widget.composer, ' '+phase)
            self.assertTrue(self.w.save_tab(chat))
            self.assertIn(phase, io.load(self.root, paths[2].name).document['payload']['draft'])
            self.assertFalse(manager.host.comfy.can_run)
            self.assertFalse(manager.host.comfy.input_flow.chain.runs())
            self.assertFalse(getattr(chat.document, 'execution', None))
            self.assertEqual(len(self.peer.submissions), int(phase == 'recovering'))
        self.drain(3)
        for tab, path in zip(tabs, paths):
            self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), pane=self.w.pane_for(tab), make_home=False))
            reopened = self.w.open_path(path, new_tab=True)
            self.assertIsNotNone(reopened)
        self.assertEqual(len(self.peer.submissions), 3)
        self.assertEqual(self.errors, [])


del Fixture, EditorFixture
