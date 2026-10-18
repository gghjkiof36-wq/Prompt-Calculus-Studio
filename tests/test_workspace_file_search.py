"""Search opening preserves the clicked pane, drafts and file boundaries."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtTest import QTest

import test_workspace_shell as fixture


class WorkspaceFileSearchTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    events = fixture.WorkspaceShellTests.events

    def setUp(self):
        fixture.WorkspaceShellTests.setUp(self)
        self.w.sidebar_preference = False
        self.w.sync_sidebar()
        self.left = self.w.open_path(self.root/'Notes.md', True)
        self.left.widget.insertPlainText('unsaved ')
        self.right = self.w.ensure_split()
        self.home = self.w.tabs_by_key[self.right.strip.bar.tabData(0)]
        self.events()

    def tearDown(self):
        dialog = getattr(self.w, 'file_search_dialog', None)
        if dialog:dialog.reject()
        QThreadPool.globalInstance().waitForDone(3000)
        for pane in list(self.w.panes):
            while pane in self.w.panes and pane.strip.bar.count():
                self.w.close_tab(0, decision='discard', make_home=False, pane=pane)
        self.w.close()
        self.w.deleteLater()
        self.events()
        sys.excepthook = self.old_hook
        self.area.cleanup()
        self.assertEqual(self.errors, [])

    def popup(self):
        with patch('prompt_calculus_studio.workspace_file_search.QFileDialog.getOpenFileName') as native:
            QTest.mouseClick(self.home.widget.open_button, Qt.MouseButton.LeftButton)
            self.events()
            native.assert_not_called()
        dialog = self.w.file_search_dialog
        self.assertIsNotNone(dialog)
        for _ in range(100):
            if not dialog.loading:break
            QTest.qWait(10)
        self.assertFalse(dialog.loading)
        self.assertIs(self.app.focusWidget(), dialog.search)
        return dialog

    def test_home_search_opens_result_in_right_slot_and_preserves_left_draft(self):
        dialog = self.popup()
        self.assertEqual(self.home.widget.open_button.text(), '開啟檔案')
        QTest.keyClicks(dialog.search, 'street')
        self.assertEqual(dialog.listing.count(), 1)
        QTest.keyClick(dialog.search, Qt.Key.Key_Return)
        self.events()
        tab = self.w.tabs_by_key[self.right.strip.bar.tabData(0)]
        self.assertEqual(tab.path, self.root/'Street.pcs-workflow')
        self.assertEqual(tab.slot, self.home.slot)
        self.assertEqual(self.right.strip.bar.count(), 1)
        self.assertIs(self.w.panes[0].pages.currentWidget(), self.left.widget)
        self.assertTrue(self.left.widget.toPlainText().startswith('unsaved '))
        self.assertTrue(self.left.widget.document().isUndoAvailable())
        self.assertFalse(self.w.sidebar.isVisible())

    def test_escape_keeps_home_history_and_files(self):
        before = (list(self.w.history), set(self.root.iterdir()))
        dialog = self.popup()
        dialog.search.setText('not present')
        self.assertEqual(dialog.listing.count(), 0)
        self.assertTrue(dialog.status.isVisible())
        QTest.keyClick(dialog.search, Qt.Key.Key_Escape)
        self.events()
        self.assertIs(self.right.pages.currentWidget(), self.home.widget)
        self.assertEqual((self.w.history, set(self.root.iterdir())), before)

    def test_arrows_ime_and_click_open(self):
        dialog = self.popup()
        QTest.keyClick(dialog.search, Qt.Key.Key_Down)
        self.assertEqual(dialog.listing.currentRow(), 1)
        QTest.keyClick(dialog.search, Qt.Key.Key_Up)
        self.assertEqual(dialog.listing.currentRow(), 0)
        dialog.search.composing = True
        QTest.keyClick(dialog.search, Qt.Key.Key_Return)
        self.assertTrue(dialog.isVisible())
        self.assertIs(self.right.pages.currentWidget(), self.home.widget)
        dialog.search.composing = False
        dialog.search.setText('scene')
        item = dialog.listing.item(0)
        QTest.mouseClick(dialog.listing.viewport(), Qt.MouseButton.LeftButton,
                        pos=dialog.listing.visualItemRect(item).center())
        self.events()
        tab = self.w.tabs_by_key[self.right.strip.bar.tabData(0)]
        self.assertEqual(tab.path, self.root/'Scene.pcs-category')

    def test_scan_finds_new_nested_files_and_excludes_internal_assets(self):
        nested = self.root/'Folder'/'新筆記.md'
        nested.write_text('nested', encoding='utf-8')
        hidden = self.root/'assets'
        hidden.mkdir(exist_ok=True)
        (hidden/'private.md').write_text('excluded', encoding='utf-8')
        (self.root/'新筆記.md').write_text('root', encoding='utf-8')
        dialog = self.popup()
        dialog.search.setText('新筆記')
        self.assertEqual(dialog.listing.count(), 2)
        self.assertEqual({dialog.listing.item(i).text() for i in range(2)}, {'新筆記', 'Folder/新筆記'})
        dialog.search.setText('folder 新筆記')
        self.assertEqual(dialog.listing.count(), 1)
        dialog.search.setText('private')
        self.assertEqual(dialog.listing.count(), 0)

    def test_removed_result_shows_error_without_closing_or_replacing_home(self):
        dialog = self.popup()
        dialog.search.setText('empty')
        (self.root/'Empty.md').unlink()
        QTest.keyClick(dialog.search, Qt.Key.Key_Return)
        self.assertTrue(dialog.isVisible())
        self.assertTrue(dialog.status.isVisible())
        self.assertIs(self.right.pages.currentWidget(), self.home.widget)

    def test_native_fallback_is_explicit_and_cancel_keeps_search(self):
        dialog = self.popup()
        with patch('prompt_calculus_studio.workspace_file_search.QFileDialog.getOpenFileName', return_value=('', '')) as native:
            QTest.mouseClick(dialog.browse, Qt.MouseButton.LeftButton)
            native.assert_called_once()
        self.assertTrue(dialog.isVisible())
        path = Path(self.area.name)/'outside.md'
        path.write_text('external', encoding='utf-8')
        with patch('prompt_calculus_studio.workspace_file_search.QFileDialog.getOpenFileName', return_value=(str(path), '')):
            QTest.mouseClick(dialog.browse, Qt.MouseButton.LeftButton)
        self.events()
        self.assertEqual(self.w.tabs_by_key[self.right.strip.bar.tabData(0)].path, path)

    def test_many_results_remain_searchable_and_reopen_uses_one_dialog(self):
        dialog = self.popup()
        dialog.set_entries([(self.root/f'File{i:04d}.md', 'markdown') for i in range(3000)], [])
        self.assertEqual(dialog.listing.count(), 100)
        self.assertIs(self.w.choose_file(), dialog)
        dialog.search.setText('file2999')
        self.assertEqual(dialog.listing.count(), 1)
        dialog.reject()
        self.events()
        self.w.activateWindow()
        self.home.widget.setFocus()
        self.events()
        QTest.keyClick(self.home.widget, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)
        self.events()
        self.assertIsNotNone(self.w.file_search_dialog)

    def test_slow_inventory_keeps_cached_search_responsive_and_can_be_cancelled(self):
        release = threading.Event()
        def slow(root):
            release.wait(3)
            yield self.root/'Empty.md', 'markdown'
        try:
            with patch.object(self.w.locations, 'inventory', side_effect=slow):
                dialog = self.w.choose_file()
                dialog.search.setText('street')
                self.assertEqual(dialog.listing.count(), 1)
                ticks = []
                QTimer.singleShot(0, lambda: ticks.append(True))
                self.events()
                self.assertEqual(ticks, [True])
                self.assertTrue(dialog.loading)
                dialog.reject()
                self.events()
                self.assertIsNone(self.w.file_search_dialog)
        finally:
            release.set()
            QThreadPool.globalInstance().waitForDone(3000)
        self.assertIs(self.right.pages.currentWidget(), self.home.widget)


if __name__ == '__main__':
    unittest.main()
