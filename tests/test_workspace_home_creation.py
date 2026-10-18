"""Every new document starts in its clicked home slot without a naming detour."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from prompt_calculus_studio.local_documents import io
from prompt_calculus_studio.workspace_files import create_file
from prompt_calculus_studio.workspace_file_dialogs import RenameDialog
from prompt_calculus_studio.workspace_shell import WorkspaceShell


class HomeWorkflowCreationTests(unittest.TestCase):
    kind = 'workflow'
    suffix = '.pcs-workflow'
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-home-create-')
        self.base = Path(self.area.name)
        self.root = self.base/'content'
        self.folder = self.root/'Folder'
        self.folder.mkdir(parents=True)
        self.note = self.root/'Keep.md'
        self.note.write_text('keep', encoding='utf-8')
        self.errors = []
        self.notices = []
        self.old_hook = sys.excepthook
        sys.excepthook = lambda kind, error, trace: self.errors.append(str(error))
        self.w = WorkspaceShell(self.root, self.base/'data')
        self.w.autosave.delay_ms = 60000
        self.w.report_error = self.errors.append
        self.w.show_notice = self.notices.append
        self.w.resize(1280, 840)
        self.w.show()
        self.events()

    def events(self):
        self.app.processEvents()
        QTest.qWait(20)
        if self.w.sidebar_resize.motion_target is not None:
            self.w.sidebar_resize.motion.setCurrentTime(self.w.sidebar_resize.motion.duration())
        self.app.processEvents()

    def tearDown(self):
        if self.w.commands.files.inline:
            self.w.commands.files.inline.cancel()
        for pane in list(self.w.panes):
            while pane in self.w.panes and pane.strip.bar.count():
                self.w.close_tab(0, decision='discard', make_home=False, pane=pane)
        self.w.close()
        self.w.deleteLater()
        self.app.processEvents()
        sys.excepthook = self.old_hook
        self.area.cleanup()
        self.assertEqual(self.errors, [])

    def click_workflow(self, home):
        QTest.mouseClick(home.widget.create_buttons[self.kind], Qt.MouseButton.LeftButton)
        self.events()
        return self.w.tabs_by_key[self.w.active_key]

    def test_click_replaces_home_without_opening_sidebar_or_changing_other_drafts(self):
        old = self.w.open_path(self.note)
        old.widget.insertPlainText('unsaved ')
        for position in ('top', 'side'):
            with self.subTest(position=position):
                self.w.set_navigation_position(position)
                home = self.w.new_tab()
                self.w.select_path(self.folder)
                self.w.sidebar_preference = False
                self.w.sync_sidebar()
                self.events()
                count, slot = self.w.tab_strip.bar.count(), home.slot
                created = self.click_workflow(home)
                self.assertIsNone(self.w.commands.files.inline)
                self.assertFalse(self.w.sidebar.isVisible())
                self.assertFalse(self.w.sidebar_preference)
                self.assertIsNone(self.app.activeModalWidget())
                self.assertEqual(self.w.tab_strip.bar.count(), count)
                self.assertEqual(created.slot, slot)
                self.assertEqual(created.path.parent, self.root)
                self.assertTrue(created.path.is_file())
                if self.kind == 'workflow':
                    self.assertTrue(created.host.canvas.isVisible())
                elif self.kind == 'category':
                    self.assertTrue(created.host.prompt_editor.isVisible())
                else:
                    self.assertTrue(created.widget.isVisible())
                self.assertTrue(self.w.top.isVisible())
                self.assertTrue(self.w.tab_strip.isVisible())
                self.assertFalse(self.w.isFullScreen())
                self.assertEqual(created.widget.width(), self.w.active_pane.pages.width())
                self.assertIs(self.w.tabs_by_key[old.key].widget, old.widget)
                self.assertTrue(old.widget.document().isUndoAvailable())
                self.assertEqual(old.widget.toPlainText(), 'unsaved keep')
                self.assertEqual(self.note.read_text('utf-8'), 'keep')

    def test_unique_names_and_late_click_do_not_overwrite_or_create_extra_files(self):
        existing = create_file(self.root, self.kind, '未命名')
        original = existing.read_bytes()
        self.w.sidebar_preference = False
        self.w.sync_sidebar()
        paths = []
        for _ in range(2):
            home = self.w.new_tab()
            context = self.w.commands.context('home')
            paths.append(self.click_workflow(home).path)
            before = set(self.root.iterdir())
            self.w.commands.new(self.kind, context)
            self.assertEqual(set(self.root.iterdir()), before)
        self.assertEqual(len(set(paths)), 2)
        self.assertNotIn(existing, paths)
        self.assertEqual(existing.read_bytes(), original)

    def test_creation_failure_and_cancelled_location_keep_home_and_sidebar(self):
        self.w.sidebar_preference = False
        self.w.sync_sidebar()
        home = self.w.tabs_by_key[self.w.active_key]
        before = set(self.root.iterdir())
        with patch.object(self.w.commands, 'create_at', side_effect=OSError('synthetic write failure')):
            self.assertIs(self.click_workflow(home), home)
        self.assertIn('synthetic write failure', self.notices)
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertFalse(self.w.sidebar.isVisible())
        self.w.remove_root(self.root)
        with patch.object(self.w, 'choose_root') as choose:
            self.assertIs(self.click_workflow(home), home)
            choose.assert_called_once()
        self.assertFalse(self.w.sidebar.isVisible())
        self.assertIsNone(self.w.commands.files.inline)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_manual_split_creation_uses_clicked_home_and_keeps_other_pane(self):
        left = self.w.open_path(self.note)
        right = self.w.ensure_split()
        home = self.w.tabs_by_key[right.strip.bar.tabData(0)]
        self.w.sidebar_preference = False
        self.w.sync_sidebar()
        self.events()
        created = self.click_workflow(home)
        self.assertEqual(created.pane, right.key)
        self.assertEqual(created.slot, home.slot)
        self.assertEqual(right.strip.bar.count(), 1)
        self.assertEqual(len(self.w.panes), 2)
        self.assertIs(self.w.panes[0].pages.currentWidget(), left.widget)
        self.assertFalse(self.w.sidebar.isVisible())

    def test_created_document_can_be_renamed_saved_closed_and_reopened(self):
        self.w.sidebar_preference = False
        self.w.sync_sidebar()
        home = self.w.tabs_by_key[self.w.active_key]
        tab = self.click_workflow(home)
        old_path = tab.path
        identity = io.load(old_path.parent, old_path.name).document['document_id'] if self.kind != 'markdown' else None
        # Rename the clicked (inactive) tab through its actual menu while the
        # sidebar stays closed, then exercise the real name dialog.
        other = self.w.open_path(self.note, new_tab=True)
        bar = self.w.tab_strip.bar
        bar.customContextMenuRequested.emit(bar.tabRect(self.w.index_of(tab.key)).center())
        self.events()
        menu = self.app.activePopupWidget()
        self.assertIsNotNone(menu)
        action = next(a for a in menu.actions() if a.text() == '重新命名')
        self.assertTrue(action.isEnabled())
        observed, failures = [], []
        timer = QTimer()
        def rename():
            dialog = self.app.activeModalWidget() if self.kind == 'workflow' else self.w.commands.files.inline
            if dialog is None:
                return
            timer.stop()
            observed.append(dialog)
            try:
                if self.kind == 'workflow':self.assertIsInstance(dialog, RenameDialog)
                else:self.assertIsNone(self.app.activeModalWidget())
                self.assertFalse(self.w.sidebar.isVisible())
                dialog.name.setText('自己的工作流')
                QTest.keyClick(dialog.name, Qt.Key.Key_Return)
            except BaseException as error:
                failures.append(error)
                dialog.reject()
        timer.timeout.connect(rename)
        timer.start(15)
        try:
            QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
        finally:
            if self.kind != 'workflow':rename()
            timer.stop()
            if isValid(menu):
                menu.close()
        self.assertTrue(observed)
        if failures:
            raise failures[0]
        self.assertEqual(other.path, self.note)
        self.events()
        target = self.root/('自己的工作流'+self.suffix)
        self.assertEqual(tab.path, target)
        self.assertFalse(old_path.exists())
        self.assertTrue(self.w.save_tab(tab))
        self.w.close_tab(self.w.index_of(tab.key))
        reopened = self.w.open_path(target)
        if self.kind != 'markdown':
            self.assertIsNotNone(reopened.host)
            self.assertEqual(io.load(target.parent, target.name).document['document_id'], identity)
        else:
            self.assertEqual(reopened.widget.toPlainText(), '')
        self.assertFalse(self.w.sidebar.isVisible())

    def test_button_keeps_its_home_when_other_pane_has_focus(self):
        left = self.w.open_path(self.note)
        right = self.w.ensure_split()
        home = self.w.tabs_by_key[right.strip.bar.tabData(0)]
        self.w.focus_tab(left)
        self.w.sidebar_preference = False
        self.w.sync_sidebar()
        home.widget.create_buttons[self.kind].click()
        self.events()
        created = self.w.tabs_by_key[right.strip.bar.tabData(0)]
        self.assertEqual(created.path.suffix, self.suffix)
        self.assertEqual(created.slot, home.slot)
        self.assertIs(self.w.panes[0].pages.currentWidget(), left.widget)
        before = set(self.root.iterdir())
        home.widget.create_buttons[self.kind].click()
        self.events()
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertFalse(self.w.sidebar.isVisible())


class HomeCategoryCreationTests(HomeWorkflowCreationTests):
    kind = 'category'
    suffix = '.pcs-category'


class HomeMarkdownCreationTests(HomeWorkflowCreationTests):
    kind = 'markdown'
    suffix = '.md'


if __name__ == '__main__':
    unittest.main()
