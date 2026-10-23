"""Reachable Qt flows for retained demos, manual review and portable copies."""
import json
import os
from pathlib import Path
import time
from unittest.mock import patch
import unittest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
import test_workspace_panes as fixture
from test_conversation_import import sample
from prompt_calculus_studio.conversation_archive import canonical
from prompt_calculus_studio.markdown_source import SourceEdit
from prompt_calculus_studio.retained_files import COMPLETE


class RetentionInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.WorkspacePaneTests.setUpClass.__func__(cls)
        # Windows offscreen does not enumerate the system font fallback set.
        from PySide6.QtGui import QFontDatabase, QFont
        for name in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
            path=Path('C:/Windows/Fonts')/name
            if path.exists():QFontDatabase.addApplicationFont(str(path))
        cls.app.setFont(QFont('Microsoft JhengHei UI',10))
    setUp = fixture.WorkspacePaneTests.setUp
    open = fixture.WorkspacePaneTests.open
    events = fixture.WorkspacePaneTests.events

    def wait_job(self, sheet):
        for _ in range(1000):
            QTest.qWait(10); time.sleep(.002)
            if not isValid(sheet) or sheet.job is None:
                self.events(); return
        self.fail('background job timeout: ' + sheet.status.text())

    def tearDown(self):
        for sheet in list(getattr(self.w,'_retained_sheets',[])):
            if isValid(sheet):sheet.reject(); self.wait_job(sheet)
        fixture.WorkspacePaneTests.tearDown(self)

    def screenshot(self, sheet, name):
        folder=os.environ.get('PCS_MD12_SCREENSHOTS')
        if folder:
            self.events(); QTest.qWait(200)
            self.assertTrue(sheet.grab().save(str(Path(folder)/name)))

    def demo_sheet(self):
        tab=self.open('Notes.md'); surface=tab.widget.md_surface
        surface.set_mode('write')
        tab.widget.insertPlainText('未保存中文草稿\n')
        before=tab.widget.buffer.snapshot().text
        sheet=surface.insert_demo(); self.events()
        folder=self.root/'演示來源';folder.mkdir()
        original=folder/'入口.html';original.write_text('<p>demo</p><script src="app.js"></script>',encoding='utf-8')
        (folder/'app.js').write_text('window.demo=true;',encoding='utf-8')
        sheet.title.setText('中文流程演示');sheet.original.setText(str(original));sheet.dependencies.setText(str(folder))
        QTest.mouseClick(sheet.prepare_button,Qt.MouseButton.LeftButton);self.wait_job(sheet)
        self.assertIsNotNone(sheet.prepared,sheet.status.text())
        return tab,sheet,before,original

    def test_demo_insert_save_reopen_undo_and_missing_asset_repair(self):
        tab,sheet,before,original=self.demo_sheet()
        self.screenshot(sheet,'demo-preview.png')
        QTest.mouseClick(sheet.save_button,Qt.MouseButton.LeftButton);self.wait_job(sheet)
        after=tab.widget.buffer.snapshot().text
        self.assertIn('pcs-demo:',after);self.assertIn('未保存中文草稿',after)
        self.assertTrue(tab.widget.save())
        tab.widget.undo();self.assertEqual(tab.widget.buffer.snapshot().text,before)
        tab.widget.redo();self.assertEqual(tab.widget.buffer.snapshot().text,after)
        saved=list((self.root/'assets').glob('demo-*/演示.json'))[0]
        self.assertTrue((saved.parent/COMPLETE).is_file());self.assertTrue(original.exists())
        from prompt_calculus_studio.markdown_demo_dialog import open_demo
        old=json.loads(saved.read_text(encoding='utf-8'))
        (saved.parent/old['original']).unlink()
        repair=open_demo(tab.widget.md_surface,saved);self.wait_job(repair)
        self.assertIn('缺失',repair.details.toPlainText())
        repair.original.setText(str(original));repair.dependencies.setText(str(original.parent))
        repair.prepare_button.click();self.wait_job(repair)
        repair.save_button.click();self.wait_job(repair)
        self.assertIn('pcs-demo:',tab.widget.buffer.snapshot().text)
        self.assertNotEqual(tab.widget.buffer.snapshot().text,after)
        tab.widget.undo();self.assertEqual(tab.widget.buffer.snapshot().text,after)
        tab.widget.redo();self.assertTrue(tab.widget.save())
        last=tab.widget.buffer.snapshot().text
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key),decision='discard',make_home=False))
        reopened=self.open('Notes.md');self.assertEqual(reopened.widget.buffer.snapshot().text,last)

    def test_demo_stale_document_and_read_mode_do_not_insert(self):
        tab,sheet,before,_=self.demo_sheet()
        snap=tab.widget.buffer.snapshot();tab.widget.buffer.apply(snap,[SourceEdit(0,0,'later\n')])
        expected=tab.widget.buffer.snapshot().text
        sheet.save_button.click();self.wait_job(sheet)
        self.assertEqual(tab.widget.buffer.snapshot().text,expected)
        self.assertIn('已改變',sheet.status.text())
        sheet.reject();self.events()
        tab.widget.md_surface.set_mode('read')
        self.assertIsNone(tab.widget.md_surface.insert_demo())

    def review_sheet(self):
        tab=self.open('Notes.md')
        (self.root/'原始對話.json').write_bytes(canonical(sample()))
        sheet=tab.widget.md_surface.review_conversations();self.events();self.wait_job(sheet)
        self.assertIsNotNone(sheet.source_data,sheet.status.text())
        sheet.branches.setCurrentIndex(0)
        return tab,sheet

    def test_manual_chatgpt_lookup_pin_paste_edit_save_reload(self):
        tab,sheet=self.review_sheet()
        sheet.query.setText('共同歷史');sheet.find_button.click();self.wait_job(sheet)
        self.assertEqual(sheet.results.count(),1)
        self.assertIn('回答 A',sheet.original.toPlainText());self.assertNotIn('回答 B',sheet.original.toPlainText())
        sheet.pin();self.assertEqual(sheet.pins,{'a'})
        sheet.tabs.setCurrentIndex(1);sheet.packet_button.click();self.wait_job(sheet)
        self.assertIn('request_id',sheet.prompt.toPlainText())
        sheet.copy_packet();self.assertEqual(QApplication.clipboard().text(),sheet.prompt.toPlainText())
        payload=dict(request_id=sheet.request['request_id'],chunk=1,items=[dict(category='AI 建議',text='測試候選',sources=['b'])])
        sheet.reply.setPlainText(json.dumps(payload));sheet.accept_reply()
        self.assertEqual(len(sheet.items),1)
        sheet.grid.item(0,2).setText('人工修正候選')
        sheet.compose();self.assertIn('人工修正候選',sheet.output.toPlainText())
        sheet.output.appendPlainText('額外人工說明')
        complete_text=sheet.output.toPlainText()
        sheet.output.setPlainText('漏掉釘選的手改稿')
        QApplication.clipboard().setText('keep clipboard')
        sheet.copy_output();self.assertEqual(QApplication.clipboard().text(),'keep clipboard')
        self.assertIn('缺少釘選',sheet.status.text())
        with patch('prompt_calculus_studio.conversation_review_dialog.QFileDialog.getSaveFileName') as picker:
            sheet.export_md();picker.assert_not_called()
        sheet.output.setPlainText(complete_text+'x'*sheet.maximum.value())
        sheet.copy_output();self.assertEqual(QApplication.clipboard().text(),'keep clipboard')
        self.assertIn('超過',sheet.status.text())
        sheet.output.setPlainText(complete_text);sheet.copy_output()
        self.assertEqual(QApplication.clipboard().text(),complete_text)
        self.screenshot(sheet,'review-candidate.png')
        sheet.save();self.wait_job(sheet)
        saved=list((self.root/'整理稿').glob('*/整理.json'))[0]
        self.assertIn('額外人工說明',saved.with_name('整理.md').read_text(encoding='utf-8'))
        sheet.reject();self.events()
        other=tab.widget.md_surface.review_conversations();self.wait_job(other)
        with patch('prompt_calculus_studio.conversation_review_dialog.QFileDialog.getOpenFileName',return_value=(str(saved),'')):
            other.choose_candidate();self.wait_job(other)
        self.assertIn('額外人工說明',other.output.toPlainText())
        self.assertEqual(other.items[0]['text'],'人工修正候選')
        other.grid.item(0,2).setText('第二版');other.save()
        self.assertIn('重新組成',other.status.text())
        other.compose();other.save();self.wait_job(other)
        self.assertEqual(len(list((self.root/'整理稿').glob('*/整理.json'))),2)

    def test_portable_ui_preview_export_and_new_root_reopen(self):
        tab=self.open('Notes.md');tab.widget.md_surface.set_mode('write')
        tab.widget.insertPlainText('unsaved portable\n')
        sheet=tab.widget.md_surface.export_portable();self.events()
        sheet.preview_button.click();self.wait_job(sheet)
        self.assertIsNotNone(sheet.package,sheet.status.text())
        destination=self.root.parent/'exported';destination.mkdir()
        sheet.format.setCurrentIndex(1)
        self.screenshot(sheet,'portable-preview.png')
        with patch('prompt_calculus_studio.markdown_portable_dialog.QFileDialog.getExistingDirectory',return_value=str(destination)):
            sheet.export_button.click();self.wait_job(sheet)
        self.assertTrue(sheet.result_path.is_file(),sheet.status.text())
        zipped=sheet.result_path;sheet.reject();self.events()
        with patch('prompt_calculus_studio.markdown_portable_dialog.QFileDialog.getOpenFileName',return_value=(str(zipped),'')),patch('prompt_calculus_studio.markdown_portable_dialog.QFileDialog.getExistingDirectory',return_value=str(destination)):
            reopened=tab.widget.md_surface.import_portable();self.wait_job(reopened)
        active=self.w.tabs_by_key[self.w.active_key]
        self.assertIn('unsaved portable',active.widget.buffer.snapshot().text)
        self.assertNotEqual(active.path,tab.path)
        self.assertTrue(tab.widget.dirty)

    def test_light_narrow_window_keeps_main_actions_reachable(self):
        settings=dict(self.w.settings,visual_palette='paper',material='solid')
        self.w.sync_display_settings(settings)
        tab,sheet,_,_=self.demo_sheet()
        sheet.resize(520,720);self.events()
        self.screenshot(sheet,'demo-light-narrow.png')
        self.assertLessEqual(sheet.width(),550)
        for control in (sheet.save_button,sheet.prepare_button,sheet.cancel_button):
            point=control.mapTo(sheet,control.rect().center())
            self.assertTrue(sheet.rect().contains(point));self.assertTrue(control.isVisible())
        sheet.reject();self.events()
        review=tab.widget.md_surface.review_conversations();self.events()
        review.resize(520,720);self.events()
        self.screenshot(review,'review-light-narrow-empty.png')
        self.assertLessEqual(review.width(),550)

    def test_background_completion_after_switch_never_edits_other_page(self):
        tab,sheet,before,_=self.demo_sheet()
        from prompt_calculus_studio import markdown_demos
        original=markdown_demos.save
        from threading import Event
        ready,release=Event(),Event()
        def delayed(*args,**kwargs):
            ready.set();release.wait(5);return original(*args,**kwargs)
        with patch.object(markdown_demos,'save',side_effect=delayed):
            sheet.save_button.click()
            for _ in range(100):
                if ready.is_set():break
                QTest.qWait(5);time.sleep(.001)
            other=self.open('Empty.md');expected=other.widget.buffer.snapshot().text
            release.set();self.wait_job(sheet)
        self.assertEqual(tab.widget.buffer.snapshot().text,before)
        self.assertEqual(other.widget.buffer.snapshot().text,expected)
        self.assertIn('已改變',sheet.status.text())


if __name__=='__main__':unittest.main()
