"""Missing-source recovery through the real workspace page, without dialogs."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QInputMethodEvent, QFontDatabase, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from prompt_calculus_studio.workspace_layout import MissingDocument
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from prompt_calculus_studio.workspace_recovery import key
from prompt_calculus_studio.workspace_recovery_export import RecoveryPublishedError
import test_workspace_panes as fixture


class MissingRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # Windows offscreen Qt has no system font discovery. This is a QA-only
        # font registration; it never changes the user's application settings.
        if os.environ.get('PCS_RECOVERY_SCREENSHOTS'):
            for path in ('C:/Windows/Fonts/segoeui.ttf', 'C:/Windows/Fonts/msjh.ttc'):
                if Path(path).is_file():QFontDatabase.addApplicationFont(path)
            cls.app.setFont(QFont('Microsoft JhengHei UI', 10))
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def restart(self, *, interrupted=False):
        data = self.w.data
        if interrupted:
            # Simulate a prior exit with a durable record. The normal R11 close
            # guard remains tested separately and is never disabled in product.
            with patch.object(self.w, 'prepare_close', return_value=True):
                self.assertTrue(self.w.close())
        else:
            self.assertTrue(self.w.close())
        self.w.deleteLater(); self.events()
        self.w = WorkspaceShell(self.root, data)
        self.w.report_error = self.errors.append
        self.w.autosave.delay_ms = 60000
        self.w.show(); self.events()

    def missing(self, name='Notes.md', *, cache=True):
        tab = self.open(name, retained=True)
        tab.widget.insertPlainText('尚未儲存的內容\n')
        expected = tab.widget.encoded()
        if cache:self.assertIsNotNone(self.w.close_recovery.preserve(tab))
        path = tab.path
        path.unlink()
        self.restart(interrupted=True)
        tab = next(t for t in self.w.all_pages() if t.path == path)
        self.assertIsInstance(tab.widget, MissingDocument)
        self.assertEqual(self.w.close_recovery.memory, {})
        return tab, expected

    def submit(self, tab, name='恢復筆記', directory=None):
        page = tab.widget
        page.name.setText(name)
        page.directory.setEditText(str(directory or self.root))
        QTest.mouseClick(page.recover_button, Qt.MouseButton.LeftButton)
        self.events()

    def test_restart_inline_recovery_preserves_bytes_and_renumbers(self):
        tab, expected = self.missing()
        original = tab.path
        index, pane = self.w.index_of(tab.key), self.w.pane_for(tab)
        (self.root/'恢復筆記.md').write_bytes(b'keep existing')
        self.assertFalse(tab.widget.isWindow())
        self.assertFalse(any(w.isVisible() for w in QApplication.topLevelWidgets() if isinstance(w, QDialog)))
        self.submit(tab)
        target = self.root/'恢復筆記(1).md'
        self.assertEqual(target.read_bytes(), expected)
        self.assertEqual((self.root/'恢復筆記.md').read_bytes(), b'keep existing')
        self.assertFalse(original.exists())
        self.assertIsNone(self.w.close_recovery.cache.read(key(original)))
        opened = self.w.tabs_by_key[self.w.active_key]
        self.assertEqual(opened.path, target)
        self.assertEqual(self.w.index_of(opened.key), index)
        self.assertIs(self.w.pane_for(opened), pane)
        self.assertEqual(opened.widget.encoded(), expected)
        self.restart()
        opened = next(t for t in self.w.all_pages() if t.path == target)
        self.assertEqual(opened.widget.encoded(), expected)
        self.assertFalse(original.exists())

    def test_same_original_target_is_rejected_inline(self):
        tab, _ = self.missing()
        self.submit(tab, 'Notes')
        self.assertIn('不同名稱', tab.widget.message.text())
        self.assertTrue(tab.widget.recover_button.isEnabled())
        self.assertFalse(tab.path.exists())
        self.assertIsNotNone(self.w.close_recovery.cache.read(key(tab.path)))

    def test_failed_write_keeps_record_and_allows_retry(self):
        tab, expected = self.missing()
        with patch('prompt_calculus_studio.workspace_recovery_export.create_markdown', side_effect=OSError('磁碟暫時無法寫入')):
            self.submit(tab)
        self.assertIn('磁碟暫時無法寫入', tab.widget.message.text())
        self.assertTrue(tab.widget.recover_button.isEnabled())
        self.assertFalse((self.root/'恢復筆記.md').exists())
        self.assertIsNotNone(self.w.close_recovery.cache.read(key(tab.path)))
        self.submit(tab)
        self.assertEqual((self.root/'恢復筆記.md').read_bytes(), expected)

    def test_open_failure_retries_same_file_without_another_export(self):
        tab, expected = self.missing()
        page = tab.widget
        self.w.last_open_error = '測試開啟失敗'
        with patch.object(self.w, 'open_path', return_value=None):self.submit(tab)
        self.assertIn('已另存至', page.message.text())
        self.assertEqual(page.recover_button.text(), '開啟文件')
        self.assertFalse(page.name.isEnabled())
        self.assertFalse(page.directory.isEnabled())
        self.assertIsNotNone(self.w.close_recovery.cache.read(key(tab.path)))
        with patch.object(self.w.close_recovery, 'recover_as', side_effect=AssertionError('must not write again')):
            QTest.mouseClick(page.recover_button, Qt.MouseButton.LeftButton)
            self.events()
        self.assertEqual((self.root/'恢復筆記.md').read_bytes(), expected)
        self.assertFalse((self.root/'恢復筆記(1).md').exists())
        self.assertIsNone(self.w.close_recovery.cache.read(key(tab.path)))

    def test_changed_published_file_never_clears_original_recovery(self):
        tab, expected = self.missing()
        page = tab.widget
        self.w.last_open_error = '測試開啟失敗'
        with patch.object(self.w, 'open_path', return_value=None):self.submit(tab)
        target = page.recovered_path
        target.write_bytes(b'unrelated replacement')
        QTest.mouseClick(page.recover_button, Qt.MouseButton.LeftButton); self.events()
        self.assertIn(tab.key, self.w.tabs_by_key)
        self.assertIsNotNone(self.w.close_recovery.cache.read(key(tab.path)))
        self.assertEqual(target.read_bytes(), b'unrelated replacement')
        self.assertTrue(page.message.text())
        target.write_bytes(expected)
        QTest.mouseClick(page.recover_button, Qt.MouseButton.LeftButton); self.events()
        self.assertIsNone(self.w.close_recovery.cache.read(key(tab.path)))

    def test_changed_target_can_be_recovered_to_another_copy_explicitly(self):
        tab, expected = self.missing()
        page = tab.widget
        self.w.last_open_error = '測試開啟失敗'
        with patch.object(self.w, 'open_path', return_value=None):self.submit(tab)
        target = page.recovered_path
        target.write_bytes(b'keep external replacement')
        QTest.mouseClick(page.recover_button, Qt.MouseButton.LeftButton); self.events()
        self.assertTrue(page.another_button.isVisible())
        QTest.mouseClick(page.another_button, Qt.MouseButton.LeftButton); self.events()
        self.assertTrue(page.name.isEnabled())
        QTest.mouseClick(page.recover_button, Qt.MouseButton.LeftButton); self.events()
        self.assertEqual(target.read_bytes(), b'keep external replacement')
        self.assertEqual((self.root/'恢復筆記(1).md').read_bytes(), expected)
        self.assertIsNone(self.w.close_recovery.cache.read(key(tab.path)))

    def test_publication_warning_requires_verification_before_clearing_cache(self):
        tab, expected = self.missing()
        recover = self.w.close_recovery.recover_as
        def published(*args):
            target = recover(*args)
            raise RecoveryPublishedError('新檔已寫入，待確認', target)
        with patch.object(self.w.close_recovery, 'recover_as', side_effect=published):self.submit(tab)
        self.assertIn(tab.key, self.w.tabs_by_key)
        self.assertIsNotNone(self.w.close_recovery.cache.read(key(tab.path)))
        QTest.mouseClick(tab.widget.recover_button, Qt.MouseButton.LeftButton); self.events()
        self.assertIsNone(self.w.close_recovery.cache.read(key(tab.path)))
        self.assertEqual((self.root/'恢復筆記.md').read_bytes(), expected)
        self.assertFalse((self.root/'恢復筆記(1).md').exists())

    def test_duplicate_missing_views_share_one_recovered_file_and_keep_positions(self):
        left = self.open('Notes.md', retained=True)
        right = self.w.duplicate_view(left, True)
        left.widget.insertPlainText('共同草稿')
        expected = left.widget.encoded()
        original = left.path
        self.w.close_recovery.preserve(left)
        original.unlink()
        self.restart(interrupted=True)
        missing = [t for t in self.w.all_pages() if t.path == original]
        self.assertEqual(len(missing), 2)
        positions = [(self.w.pane_for(t), self.w.index_of(t.key)) for t in missing]
        self.submit(missing[0])
        views = [t for t in self.w.all_pages() if t.path == self.root/'恢復筆記.md']
        self.assertEqual(len(views), 2)
        self.assertEqual([(self.w.pane_for(t), self.w.index_of(t.key)) for t in views], positions)
        self.assertIs(views[0].widget.document(), views[1].widget.document())
        self.assertEqual(views[0].widget.encoded(), expected)
        self.assertIsNone(self.w.close_recovery.cache.read(key(original)))
        self.assertFalse((self.root/'恢復筆記(1).md').exists())

    def test_second_view_open_failure_keeps_cache_and_retries_same_target(self):
        left = self.open('Notes.md', retained=True)
        self.w.duplicate_view(left, True)
        left.widget.insertPlainText('共同草稿')
        original = left.path
        self.w.close_recovery.preserve(left)
        original.unlink(); self.restart(interrupted=True)
        missing = [t for t in self.w.all_pages() if t.path == original]
        opener = self.w.open_path
        count = 0
        def second_fails(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                self.w.last_open_error = '第二個視圖開啟失敗'
                return None
            return opener(*args, **kwargs)
        with patch.object(self.w, 'open_path', side_effect=second_fails):self.submit(missing[0])
        remaining = next(t for t in self.w.all_pages() if isinstance(t.widget, MissingDocument))
        self.assertIsNotNone(self.w.close_recovery.cache.read(key(original)))
        self.assertEqual(remaining.widget.recover_button.text(), '開啟文件')
        QTest.mouseClick(remaining.widget.recover_button, Qt.MouseButton.LeftButton); self.events()
        self.assertIsNone(self.w.close_recovery.cache.read(key(original)))
        views = [t for t in self.w.all_pages() if t.path == self.root/'恢復筆記.md']
        self.assertEqual(len(views), 2)
        self.assertIs(views[0].widget.document(), views[1].widget.document())

    def test_hidden_history_view_opens_recovered_content_after_cache_is_cleared(self):
        left = self.open('Notes.md', retained=True)
        self.w.duplicate_view(left, True)
        left.widget.insertPlainText('歷史頁面的共同草稿')
        expected, original = left.widget.encoded(), left.path
        self.w.close_recovery.preserve(left)
        original.unlink(); self.restart(interrupted=True)
        left, right = [t for t in self.w.all_pages() if t.path == original]
        self.w.focus_tab(left)
        self.w.open_path(self.root/'Empty.md', pane=self.w.pane_for(left))
        self.assertIn(left.key, self.w.saved_pages)
        old_slot = left.slot
        self.w.focus_tab(right)
        self.submit(right)
        self.assertIsNone(self.w.close_recovery.cache.read(key(original)))
        hidden = next(t for t in self.w.saved_pages.values() if t.path == self.root/'恢復筆記.md')
        self.assertEqual(hidden.slot, old_slot)
        self.assertNotIsInstance(hidden.widget, MissingDocument)
        self.assertEqual(hidden.widget.encoded(), expected)
        self.assertFalse(any(e[0] == 'document' and e[1] == left.key for e in self.w.history))
        for _ in range(len(self.w.history)):
            if self.w.active_key == hidden.key:break
            self.w.go(-1)
        self.assertEqual(self.w.active_key, hidden.key)
        self.assertEqual(self.w.tabs_by_key[hidden.key].widget.encoded(), expected)
        self.assertFalse(original.exists())

    def test_corrupt_record_has_inline_error_and_is_not_removed(self):
        tab, _ = self.missing()
        record = self.w.close_recovery.cache.read(key(tab.path))
        record['raw'] = '!not-base64!'
        self.w.close_recovery.cache.write(key(tab.path), record)
        self.submit(tab)
        self.assertIn('無法讀取', tab.widget.message.text())
        self.assertIsNotNone(self.w.close_recovery.cache.read(key(tab.path)))
        self.assertFalse((self.root/'恢復筆記.md').exists())

    def test_newer_recovery_written_during_open_is_never_cleared(self):
        tab, _ = self.missing()
        opener = self.w.open_path
        def changed_cache(*args, **kwargs):
            record = self.w.close_recovery.cache.read(key(tab.path))
            record['raw'] = 'bmV3ZXIgZHJhZnQ='
            self.w.close_recovery.cache.write(key(tab.path), record)
            return opener(*args, **kwargs)
        with patch.object(self.w, 'open_path', side_effect=changed_cache):self.submit(tab)
        self.assertIn(tab.key, self.w.tabs_by_key)
        self.assertEqual(self.w.close_recovery.cache.read(key(tab.path))['raw'], 'bmV3ZXIgZHJhZnQ=')
        self.assertTrue(tab.widget.message.text())
        self.assertFalse(tab.path.exists())

    def test_native_category_and_workflow_recover_as_independent_documents(self):
        from prompt_calculus_studio.local_documents import io
        for filename in ('Scene.pcs-category', 'Street.pcs-workflow'):
            with self.subTest(filename=filename):
                tab = self.open(filename, retained=True)
                original, old_id = tab.path, tab.document.key
                if tab.document.kind == 'category':
                    tab.document.state['items'][0]['prompt'] = '恢復的分類內容'
                    tab.document.update_dirty()
                else:
                    canvas = tab.host.canvas
                    ident = next(iter(canvas.data()['canvases']))
                    self.assertTrue(canvas.commit(lambda s: s['multi_output']['canvases'][ident].update(name='恢復的畫布')))
                self.assertIsNotNone(self.w.close_recovery.preserve(tab))
                original.unlink(); self.restart(interrupted=True)
                missing = next(t for t in self.w.all_pages() if t.path == original)
                self.submit(missing, original.stem + ' 恢復')
                target = original.with_stem(original.stem + ' 恢復')
                opened = next(t for t in self.w.all_pages() if t.path == target)
                self.assertNotEqual(opened.document.key, old_id)
                loaded = io.load(self.root, target.name).document
                if loaded['kind'] == 'category':
                    self.assertEqual(loaded['payload']['items'][0]['prompt'], '恢復的分類內容')
                else:
                    self.assertIn('恢復的畫布', [v['name'] for v in loaded['payload']['state']['multi_output']['canvases'].values()])
                self.assertIsNone(self.w.close_recovery.cache.read(key(original)))
                self.assertFalse(original.exists())
                self.assertTrue(self.w.save_tab(opened))
                self.restart()
                self.assertEqual(next(t for t in self.w.all_pages() if t.path == target).document.key, opened.document.key)

    def test_no_cache_keeps_retry_only(self):
        tab, _ = self.missing(cache=False)
        self.assertIsNone(tab.widget.recover)
        self.assertFalse(hasattr(tab.widget, 'recover_button'))
        tab.path.write_bytes(b'# returned original\n')
        QTest.mouseClick(tab.widget.retry_button, Qt.MouseButton.LeftButton)
        self.events()
        opened = self.w.tabs_by_key[self.w.active_key]
        self.assertEqual(opened.path, tab.path)
        self.assertEqual(opened.widget.encoded(), b'# returned original\n')

    def test_ime_enter_does_not_publish_partial_name(self):
        tab, _ = self.missing()
        page = tab.widget
        page.name.setFocus()
        self.app.sendEvent(page.name, QInputMethodEvent('中文', []))
        with patch.object(self.w.close_recovery, 'recover_as') as recover:
            QTest.keyClick(page.name, Qt.Key.Key_Return)
            QTest.mouseClick(page.recover_button, Qt.MouseButton.LeftButton)
            self.events()
            recover.assert_not_called()
        self.app.sendEvent(page.name, QInputMethodEvent())

    def test_inline_page_fits_narrow_and_wide_workspace(self):
        tab, _ = self.missing()
        page = tab.widget
        destination = os.environ.get('PCS_RECOVERY_SCREENSHOTS')
        for size in ((1200, 780), (760, 560)):
            self.w.resize(*size); self.events()
            self.assertFalse(page.isWindow())
            self.assertTrue(page.rect().contains(page.recover_button.mapTo(page, page.recover_button.rect().center())))
            self.assertLessEqual(page.name.width(), page.width())
            if destination:
                folder = Path(destination); folder.mkdir(parents=True, exist_ok=True)
                self.w.grab().save(str(folder/f'recovery-{size[0]}.png'))

    def test_recovery_survives_two_fresh_processes_including_closed_tab(self):
        area = Path(self.area.name)/'fresh-process'
        area.mkdir()
        program = textwrap.dedent('''
            import sys
            from pathlib import Path
            from unittest.mock import patch
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import Qt
            from PySide6.QtTest import QTest
            from prompt_calculus_studio.workspace_shell import WorkspaceShell
            from prompt_calculus_studio.workspace_layout import MissingDocument
            from prompt_calculus_studio.workspace_home import WorkspaceHome
            from prompt_calculus_studio.workspace_recovery import key
            mode, base = sys.argv[1], Path(sys.argv[2])
            root, data = base/'files', base/'data'
            root.mkdir(exist_ok=True)
            app = QApplication([])
            if mode == 'seed':
                for name in ('Open', 'Closed'):
                    (root/(name+'.md')).write_bytes(b'\\xef\\xbb\\xbf# original\\r\\n')
            w = WorkspaceShell(root, data)
            w.autosave.delay_ms = 60000
            w.show(); app.processEvents(); QTest.qWait(30)
            if mode == 'seed':
                for name in ('Open', 'Closed'):
                    tab = w.open_path(root/(name+'.md'), True, new_tab=True)
                    tab.widget.insertPlainText('recover this draft\\n')
                    (base/(name+'.expected')).write_bytes(tab.widget.encoded())
                    assert w.close_recovery.preserve(tab)
                    tab.path.unlink()
                    if name == 'Closed':
                        assert w.close_tab(w.index_of(tab.key), decision='discard', make_home=False)
                # Reproduce an older exit with durable records, not a product
                # request to relax the current missing-source close guard.
                with patch.object(w, 'prepare_close', return_value=True):assert w.close()
            else:
                assert not w.close_recovery.memory
                for name in ('Open', 'Closed'):
                    original = root/(name+'.md')
                    if name == 'Closed':
                        home = w.new_tab().widget
                        assert isinstance(home, WorkspaceHome)
                        # The page lists the durable orphan even though the
                        # old session no longer has a tab or a recent entry.
                        assert w.close_recovery.has_record(original)
                    tab = w.layout_store.open_recovery(original)
                    assert isinstance(tab.widget, MissingDocument)
                    tab.widget.name.setText(name+' recovered')
                    tab.widget.directory.setEditText(str(root))
                    tab.widget.recover_button.click(); app.processEvents()
                    target = root/(name+' recovered.md')
                    assert target.read_bytes() == (base/(name+'.expected')).read_bytes()
                    assert not original.exists()
                    assert w.close_recovery.cache.read(key(original)) is None
                assert w.close()
            w.deleteLater(); app.processEvents()
        ''')
        for mode in ('seed', 'recover'):
            environment = dict(os.environ, PROMPT_STUDIO_DATA=str(area/'process-data'))
            result = subprocess.run([sys.executable, '-c', program, mode, str(area)],
                                    capture_output=True, text=True, encoding='utf-8',
                                    env=environment, timeout=60)
            self.assertEqual(result.returncode, 0, mode+'\n'+result.stdout+'\n'+result.stderr)


if __name__ == '__main__':unittest.main()
