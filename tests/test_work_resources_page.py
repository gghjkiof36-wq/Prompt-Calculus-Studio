"""Exercise the single-list workflow with actual Qt controls and isolated local files."""
import gc
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QEvent, QTimer, QPointF
from PySide6.QtGui import QTextCursor, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit, QDialogButtonBox
from prompt_calculus_studio.conversation_archive import canonical
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from prompt_calculus_studio.work_resources import bundles
from prompt_calculus_studio.widgets import StudioDialog
from test_work_resources import example


@unittest.skipUnless(os.name == 'nt', 'Windows sources')
class ResourceWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, condition):
        end = time.monotonic() + 25
        while time.monotonic() < end:
            self.app.processEvents()
            if self.errors:
                self.fail(str(self.errors))
            if condition():
                return
            time.sleep(.012)
        self.fail(f'操作逾時 · {self.page.status.text()}')

    def idle(self):
        p = self.page
        self.wait(lambda: not p.jobs.active and not p.search_timer.isActive() and not p.search_pending)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.docs = self.base/'研究手記'; self.docs.mkdir()
        self.root = self.base/'工作區'; self.root.mkdir()
        (self.docs/'河岸.md').write_bytes('原文🙂\r\n選這一段\r\n其餘。'.encode())
        (self.docs/'時間.md').write_text('明天九點集合。', encoding='utf8')
        self.archive = self.base/'討論封存.json'
        self.archive.write_bytes(canonical([example()]))
        self.errors = []
        original_init = StudioDialog.__init__
        def quiet_dialog(dialog, *args, **kwargs):
            original_init(dialog, *args, **kwargs)
            dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            dialog._prompt_instant_show = True
        self.dialog_patch = patch.object(StudioDialog, '__init__', quiet_dialog)
        self.dialog_patch.start()
        self.old_hook = sys.excepthook
        import traceback
        sys.excepthook = lambda t,v,b: self.errors.append(''.join(traceback.format_exception(t,v,b)))
        self.open_shell()

    def open_shell(self):
        self.w = WorkspaceShell(self.root, self.base/'data')
        self.w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.w.resize(1240, 790)
        self.w.show()
        self.w.report_error = self.errors.append
        self.w.open_route('explore', 'work_resources')
        self.page = self.w.utility.settings_page.resources_content.load()
        self.page.host.error = self.errors.append
        self.idle()

    def close_shell(self):
        self.page.close_reader()
        self.idle()
        self.w.close(); self.w.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents(); gc.collect()

    def tearDown(self):
        try:
            self.close_shell()
        finally:
            sys.excepthook = self.old_hook
            self.dialog_patch.stop()
            self.tmp.cleanup()

    def click(self, widget):
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def action(self, control, name):
        self.click(control)
        popup = QApplication.activePopupWidget()
        self.assertIsNotNone(popup)
        action = next(a for a in popup.actions() if a.text() == name)
        QTest.mouseClick(popup, Qt.MouseButton.LeftButton, pos=popup.actionGeometry(action).center())
        self.app.processEvents()

    def hit(self, title):
        self.idle()
        tree = self.page.results
        item = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                    if title in tree.topLevelItem(i).text(1))
        tree.setCurrentItem(item)
        QTest.keyClick(tree, Qt.Key.Key_Return)
        self.idle()

    def picture(self, name, widget=None):
        value = os.environ.get('PCS_RESOURCE_QA_SCREENSHOTS')
        if value:
            QTest.qWait(160)
            path = Path(value); path.mkdir(parents=True, exist_ok=True)
            self.assertTrue((widget or self.w).grab().save(str(path/(name+'.png'))))

    def rendered_text(self, surface):
        self.wait(lambda: surface.reader.rich is not None and surface.reader.rich.render_state == 'ready')
        result=[]
        surface.reader.rich.page().runJavaScript('document.querySelector("main").innerText',result.append)
        self.wait(lambda: bool(result))
        return result[0]

    def check(self, title):
        tree = self.page.results
        item = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                    if title in tree.topLevelItem(i).text(1))
        tree.scrollToItem(item)
        self.app.processEvents()
        index = tree.indexFromItem(item)
        rect = tree.check_rect(index)
        self.assertFalse(rect.isEmpty())
        # Native QTest.mouseMove uses the global cursor. Send only to this test viewport instead.
        self.app.sendEvent(tree.viewport(), QMouseEvent(QEvent.Type.MouseMove, QPointF(rect.center()),
            QPointF(tree.viewport().mapToGlobal(rect.center())), Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(tree.hover_row, index.row())
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        self.app.processEvents()

    def test_home_native_archive_to_reading_tab_and_restart(self):
        from prompt_calculus_studio.workspace_file_search import FileSearchDialog
        from prompt_calculus_studio.workspace_files import MarkdownPage
        from test_resource_markdown_export import write_rollout
        source = write_rollout(self.base/'Codex.jsonl')
        original = source.read_bytes()
        picker = FileSearchDialog(self.w,self.w.commands.selected_context())
        picker.show()
        with patch.object(QFileDialog,'getOpenFileName',return_value=(str(source),'')):
            picker.choose_other()
        dialog = self.w._conversation_import
        self.wait(lambda: dialog.job is None)
        self.assertIsNotNone(dialog.archive,dialog.status.text())
        dialog.list.setCurrentRow(0)
        QTest.keyClick(dialog.list,Qt.Key.Key_Space)
        self.assertEqual(dialog.selected(),(0,))
        self.click(dialog.import_button)
        self.wait(lambda: dialog.job is None)
        self.assertIn('完成 1',dialog.completion_summary)
        tabs=[t for t in self.w.all_pages() if t.path and t.path.suffix=='.md']
        self.assertEqual(len(tabs),1)
        tab=tabs[0]; note=tab.path
        self.assertIsInstance(tab.widget,MarkdownPage)
        self.assertEqual(tab.widget.md_surface.mode,'read')
        self.assertIn('print("中文")',self.rendered_text(tab.widget.md_surface))
        self.assertIn('重複的真實問題',note.read_text(encoding='utf8'))
        self.assertTrue(self.w.select_path(note))
        self.assertEqual(source.read_bytes(),original)
        dialog.close()
        self.picture('02-Codex轉MD-首頁閱讀')
        self.close_shell(); self.open_shell()
        self.assertTrue(self.w.select_path(note))
        self.page.open_home(note)
        tab=self.w.tab_for_path(note)
        self.assertIsInstance(tab.widget,MarkdownPage)
        self.assertIn('來源：Codex',tab.widget.baseline.decode('utf8'))

    def test_centered_folders_managed_move_and_existing_markdown(self):
        p=self.page
        (self.root/'筆記.md').write_text('# 標題\n\n| 名稱 | 值 |\n|---|---|\n|甲|1|\n\n```python\nprint(1)\n```',encoding='utf8')
        target=self.root/'整理'; target.mkdir()
        sid=p.store.register(self.root/'筆記.md','copy'); p.store.rebuild(sid)
        p.enter_folder(self.root); self.idle()
        self.w.resize(1600,960); self.app.processEvents()
        self.assertLessEqual(p.content.width(),920)
        self.assertLess(abs(p.content.geometry().center().x()-p.rect().center().x()),3)
        self.assertEqual(p.results.headerItem().text(2),'來源')
        self.assertFalse(hasattr(p,'kind_buttons'))
        self.assertFalse(hasattr(p,'save'))
        self.picture('01-工作資源-置中')
        self.hit('整理'); self.assertEqual(p.location,target)
        self.click(p.back_button); self.idle()
        self.assertEqual(p.location,self.root)
        row=p.store.browse(source=sid)[0][0]
        p.open_row(row); self.idle()
        tab=self.w.tab_for_path(self.root/'筆記.md')
        self.assertIsNotNone(tab)
        self.assertEqual(tab.widget.md_surface.mode,'read')
        self.assertIn('print(1)',self.rendered_text(tab.widget.md_surface))
        self.w.open_route('explore','work_resources')
        moved=p.file_operation(row,'move',directory=target); self.idle()
        self.assertEqual(moved,target/'筆記.md')
        self.assertEqual(Path(p.store.source(sid)['path']),moved)
        self.assertIsNotNone(self.w.tab_for_path(moved))
        self.assertTrue(self.w.select_path(moved))
        p.enter_folder(target); self.idle()
        self.assertEqual([r['title'] for r in p.shown_rows],['筆記.md'])
        renamed=p.file_operation(p.shown_rows[0],'rename',name='新名稱'); self.idle()
        self.assertEqual(renamed,target/'新名稱.md')
        self.assertEqual(Path(p.store.source(sid)['path']),renamed)
        def fill():
            dialog=QApplication.activeModalWidget()
            self.assertIsNotNone(dialog)
            dialog.name.setText('新資料夾')
            self.click(dialog.submit_button)
        QTimer.singleShot(40,fill)
        self.click(p.new_folder_button); self.idle()
        self.assertTrue((target/'新資料夾').is_dir())
        self.assertTrue(self.w.select_path(target/'新資料夾'))
        self.w.resize(900,750); self.app.processEvents()
        self.assertLessEqual(p.content.width(),p.width())
        self.picture('03-工作資源-窄窗')

    def test_dialogue_selection_persists_across_search_pages_folders_and_batch(self):
        from copy import deepcopy
        records=[]
        for i in range(61):
            item=deepcopy(example()); item['id']=f'c-{i}'; item['title']=f'討論-{i:02}'
            records.append(item)
        self.archive.write_bytes(canonical(records))
        p=self.page
        p.add_source(self.archive,'archive'); self.idle()
        self.assertEqual((p.total,p.results.topLevelItemCount()),(61,50))
        self.check('討論-00')
        self.click(p.next); self.idle()
        self.check('討論-60')
        p.filter.setText('討論-00'); self.idle()
        self.assertEqual(len(p.picks),2)
        self.assertEqual(p.results.topLevelItem(0).checkState(0),Qt.CheckState.Checked)
        p.enter_folder(self.root); self.idle()
        self.assertEqual(len(p.picks),2)
        before=len([t for t in self.w.all_pages() if t.path])
        with patch.object(QFileDialog,'getExistingDirectory',return_value=str(self.root)):
            self.click(p.export)
        self.idle()
        self.assertEqual(len(p.last_export.completed),2,p.last_export.failed)
        self.assertEqual(len(p.picks),0)
        self.assertEqual(len([t for t in self.w.all_pages() if t.path]),before)
        self.picture('04-逐份輸出結果',p.export_result)
        p.export_result.close()
        self.assertTrue(all(self.w.select_path(path) for path in p.last_export.completed))
        # Legacy bundles remain untouched, with no product navigation back to them.
        marker=p.store.bundles/'historical-record'; marker.mkdir()
        (marker/'original').write_bytes(b'preserved')
        p.refresh_list(); self.idle()
        self.assertEqual((marker/'original').read_bytes(),b'preserved')
        self.assertEqual([p.kind.itemData(i) for i in range(p.kind.count())],['all','document','archive','definitions'])

    def test_existing_renderer_external_original_and_definition_copy(self):
        from prompt_calculus_studio.workspace_files import MarkdownPage
        from prompt_calculus_studio.work_resources.markdown_preview import MarkdownPreview
        p=self.page
        (self.docs/'SKILL.md').write_text('# Skill 範例\n\n這是待檢視的定義。\n\n```text\n不要執行\n```',encoding='utf8')
        p.add_source(self.docs,'folder'); self.idle()
        self.hit('時間.md')
        self.assertIsInstance(p.reader,MarkdownPreview)
        self.assertIsInstance(p.reader.editor,MarkdownPage)
        self.assertEqual(p.reader.surface.mode,'read')
        p.close_reader()
        row=next(r for r in p.shown_rows if r['title']=='時間.md')
        p.copy(row); self.idle()
        tab=self.w.tabs_by_key[self.w.active_key]
        self.assertEqual(tab.path.parent,self.root)
        self.assertEqual((self.docs/'時間.md').read_text(encoding='utf8'),'明天九點集合。')
        self.w.open_route('explore','work_resources')
        p.set_kind('definitions'); self.idle()
        self.assertEqual(p.total,1)
        row=p.shown_rows[0]
        p.open_row(row); self.idle()
        self.assertIn('Skill 定義',p.reader.info.text())
        self.assertIn('不要執行',self.rendered_text(p.reader.surface))
        self.picture('05-Skill定義-既有閱讀器',p.reader)
        p.close_reader()
        before=len([t for t in self.w.all_pages() if t.path])
        p.copy(row); self.idle()
        self.assertTrue(p.reader.editable)
        self.assertTrue(p.reader.editor.path.is_relative_to(self.base/'data'))
        self.assertEqual(len([t for t in self.w.all_pages() if t.path]),before)
        self.assertFalse((self.root/'SKILL.md').exists())
        p.reader.surface.apply_mode('source')
        p.reader.editor.moveCursor(QTextCursor.MoveOperation.End)
        p.reader.editor.insertPlainText('\n管理副本修改')
        self.assertTrue(p.reader.save(),p.reader.feedback.text())
        self.assertNotIn('管理副本修改',(self.docs/'SKILL.md').read_text(encoding='utf8'))
        self.picture('06-管理副本',p.reader)
        p.close_reader()
