"""Real startup phases and stable first frames, without online side effects."""
import unittest
from collections import defaultdict
from unittest.mock import patch

from PySide6.QtCore import QEvent, QObject
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from prompt_calculus_studio.deferred_page import page_ready
from prompt_calculus_studio.pages import GalleryPage, ModelPage
from prompt_calculus_studio.workspace_startup import StartupProgress, prepare_tool_steps
import test_workspace_shell as fixture


class TextFrames(QObject):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.frames = defaultdict(set)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Paint and isinstance(obj, QLabel) and obj.isVisibleTo(self.window):
            self.frames[obj].add((obj.font().toString(), obj.mapTo(self.window, obj.rect().topLeft()).toTuple()))
        return False


class WorkspaceStartupTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    setUp = fixture.WorkspaceShellTests.setUp
    tearDown = fixture.WorkspaceShellTests.tearDown
    events = fixture.WorkspaceShellTests.events

    def test_basic_preparation_keeps_navigation_drafts_and_expensive_pages_lazy(self):
        tab = self.w.open_path(self.root/'Notes.md', True)
        tab.widget.insertPlainText('pending ')
        previous = self.w.active_key, list(self.w.history), tab.widget.toPlainText()
        with patch.object(GalleryPage, 'refresh_albums') as albums, patch.object(ModelPage, 'scan') as scan:
            for _, action in prepare_tool_steps(self.w):
                action()
            albums.assert_not_called()
            scan.assert_not_called()
        host = self.w.utility
        self.assertTrue(all(page_ready(p) for p in (host.gallery, host.settings_page, host.clean_export)))
        self.assertIsNotNone(host.settings_page.preferences)
        self.assertFalse(any(page_ready(p) for p in (host.models, host.settings_page.comfy_content, host.settings_page.civitai)))
        self.assertEqual((self.w.active_key, self.w.history, tab.widget.toPlainText()), previous)
        self.assertTrue(tab.widget.document().isUndoAvailable())
        with patch.object(GalleryPage, 'refresh_albums') as albums:
            self.w.open_route('gallery')
            albums.assert_called_once()

    def test_startup_presents_real_progress_and_requests_focus_only_at_launch(self):
        self.w.hide()
        failures, ready, progress = [], [], []
        startup = StartupProgress(self.w, lambda _: 'restored',
                                  lambda window, initial: ready.append((window, initial)), failures.append)
        startup.progress.valueChanged.connect(progress.append)
        with patch('prompt_calculus_studio.workspace_startup.foreground',
                   side_effect=lambda w: w.show()) as focus:
            startup.start()
            for _ in range(200):
                QTest.qWait(10)
                if startup.finished_loading:
                    break
            self.assertEqual(failures, [])
            self.assertTrue(startup.finished_loading)
            self.assertEqual(progress, list(range(1, 8)))
            self.assertEqual(ready, [(self.w, 'restored')])
            self.assertFalse(startup.isVisible())
            self.assertTrue(self.w.isVisible())
            self.assertIs(startup.window(), self.w)
            self.assertFalse(startup.isWindow())
            self.assertEqual([call.args[0] for call in focus.call_args_list], [self.w, self.w])
            for route in ('settings', 'explore', 'export', 'gallery', 'home'):
                self.w.open_route(route)
            self.assertEqual(focus.call_count, 2)
        startup.deleteLater()

    def test_startup_failure_stops_remaining_steps(self):
        failures, ready = [], []
        def fail():
            raise OSError('startup fixture failure')
        with patch.object(self.w, 'initialize_documents', side_effect=fail):
            startup = StartupProgress(self.w, lambda _: self.fail('must not restore'),
                                      lambda *_: ready.append(True), failures.append)
            with patch('prompt_calculus_studio.workspace_startup.foreground'), \
                    patch.object(self.w, 'close'), patch.object(self.app, 'exit') as exit_:
                startup.start()
                QTest.qWait(50)
                self.assertTrue(startup.cancelled)
                self.assertEqual(str(failures[0]), 'startup fixture failure')
                self.assertEqual(ready, [])
                exit_.assert_called_once_with(1)
        self.w.centralWidget().setEnabled(True)
        startup.deleteLater()

    def test_closing_before_restore_keeps_saved_layout_and_one_window(self):
        from prompt_calculus_studio.workspace_shell import WorkspaceShell
        data = self.root.parent/'deferred-startup-data'
        data.mkdir()
        saved = data/'workspace-layout.json'
        original = b'{"synthetic": "preserve until restore"}'
        saved.write_bytes(original)
        window = WorkspaceShell(self.root, data, defer_restore=True)
        startup = StartupProgress(window, lambda _: None, lambda *_: None, self.fail)
        try:
            startup.show()
            self.assertIs(startup.window(), window)
            self.assertFalse(window.workspace_ready)
            self.assertTrue(window.close())
            self.assertTrue(startup.cancelled)
            self.assertEqual(saved.read_bytes(), original)
            self.assertFalse(startup.timer.isActive())
        finally:
            window.deleteLater()
            self.events()

    def test_tool_text_stays_in_place_from_first_paint_after_preparation(self):
        for _, action in prepare_tool_steps(self.w):
            action()
        self.events()
        frames = TextFrames(self.w)
        self.app.installEventFilter(frames)
        try:
            for route in ('gallery', 'explore', 'export', 'settings'):
                frames.frames.clear()
                self.w.open_route(route)
                QTest.qWait(160)
                self.assertTrue(frames.frames, route)
                moving = [(obj.text(), positions) for obj, positions in frames.frames.items() if len(positions) > 1]
                self.assertEqual(moving, [], route)
                self.w.open_route('home')
                self.events()
        finally:
            self.app.removeEventFilter(frames)
