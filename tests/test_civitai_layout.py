"""Search drafts and image-first CivitAI details, with no external requests."""
import os
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidgetItem
from prompt_calculus_studio.window import Window

APP = QApplication.instance() or QApplication([])
if APP.platformName() == 'offscreen':
    for name in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + name)


class CivitAIRevisionLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.request = patch('prompt_calculus_studio.comfy_client.ComfyClient.request', side_effect=AssertionError('No service in UI test'))
        self.request.start()
        self.w = Window(self.temp.name)
        self.w.state['settings'].update(online=False, material='solid')
        self.w.apply_theme(); self.w.show(); self.w.settings('civitai')
        self.page = self.w.settings_page.civitai
        self.page.initial_requested = True
        QTest.qWait(40)

    def tearDown(self):
        self.w.close(); APP.processEvents(); self.request.stop(); self.temp.cleanup()

    def test_filter_draft_cancel_does_not_change_query_or_issue_request(self):
        page = self.page
        page.query.setText('soft light')
        with patch.object(page, 'queue_search') as search:
            page.open_filters()
            page.filters_popup.types.button(1).setChecked(True)
            page.filters_popup.base.setText('SDXL 1.0')
            page.filters_popup.hide()
            self.assertEqual(page.kind.currentData(), '')
            self.assertEqual(page.base.text(), '')
            self.assertEqual(page.query.text(), 'soft light')
            search.assert_not_called()
            page.open_filters()
            self.assertEqual(page.filters_popup.types.checkedId(), 0)
            self.assertEqual(page.filters_popup.base.text(), '')

    def test_apply_filters_issues_one_search_and_clear_restores_all(self):
        page = self.page
        with patch.object(page, 'queue_search') as search:
            page.open_filters()
            page.filters_popup.types.button(1).setChecked(True)
            page.filters_popup.base.setText(' SDXL 1.0 ')
            page.filters_popup.apply()
            search.assert_called_once()
            self.assertEqual(page.kind.currentData(), 'LORA')
            self.assertEqual(page.base.text(), 'SDXL 1.0')
            self.assertEqual(page.filter_button.text(), '篩選 · 2')
            self.assertFalse(page.filters_popup.isVisible())
            search.reset_mock()
            page.open_filters(); page.filters_popup.clear()
            self.assertEqual(page.kind.currentData(), 'LORA')
            page.filters_popup.apply(); search.assert_called_once()
            self.assertEqual(page.kind.currentData(), '')
            self.assertEqual(page.base.text(), '')

    def test_image_metadata_and_download_stay_inside_detail_card(self):
        page = self.page
        record = dict(id=1, name='Example model', type='LORA', modelVersions=[
            dict(id=22, name='v2', baseModel='SDXL 1.0', images=[], _details_loaded=True,
                 files=[dict(id=33, name='example.safetensors', primary=True,
                             downloadUrl='https://civitai.com/api/download/models/22')])])
        item = QListWidgetItem('Example'); item.setData(Qt.ItemDataRole.UserRole, record)
        page.list.addItem(item); page.list.setCurrentItem(item)
        second = QListWidgetItem('Another example'); second.setData(Qt.ItemDataRole.UserRole, {**record, 'id': 2})
        page.list.addItem(second)
        image = QImage(600, 900, QImage.Format.Format_RGB32); image.fill(0xff65827a)
        page.preview.set_image(image)
        for width, height in ((2560, 1440), (1280, 800), (800, 640), (640, 540)):
            self.w.resize(width, height); QTest.qWait(60)
            self.assertEqual(self.w.width(), width)
            self.assertGreater(page.preview.width(), page.detail_body.width() * .9)
            self.assertGreaterEqual(page.preview.height(), 180)
            self.assertGreater(page.brief.y(), page.preview.geometry().bottom())
            self.assertGreater(page.version.y(), page.brief.geometry().bottom())
            self.assertTrue(page.detail_card.isAncestorOf(page.download))
            self.assertTrue(page.detail_card.isAncestorOf(page.link))
            self.assertGreater(page.download.y(), page.link.geometry().bottom())
            self.assertTrue(page.rect().contains(page.download.mapTo(page, page.download.rect().center())))
            self.assertLessEqual(page.detail_scroll.widget().width(), page.detail_scroll.viewport().width())
            if width <= 800:
                self.assertGreaterEqual(page.preview.height(), 200 if height < 600 else 240)
                page.detail_scroll.ensureWidgetVisible(page.version)
                APP.processEvents()
                self.assertTrue(page.detail_scroll.viewport().rect().intersects(
                    page.version.rect().translated(page.version.mapTo(page.detail_scroll.viewport(), page.version.rect().topLeft()))))
                page.detail_scroll.verticalScrollBar().setValue(0)
        page.hide_details(); QTest.qWait(20)
        self.assertIs(page.list.currentItem(), item)
        self.assertTrue(page.query.isVisible())
        self.assertTrue(page.filter_button.isVisible())
        self.assertGreaterEqual(page.query.height(),page.query.minimumSizeHint().height())
        self.assertGreaterEqual(page.sort.height(),page.sort.minimumSizeHint().height())
        self.assertLess(page.filter_button.mapTo(page, page.filter_button.rect().topRight()).x(), page.width())
        self.assertEqual(page.list.visualItemRect(item).y(),page.list.visualItemRect(second).y())


if __name__ == '__main__':
    unittest.main()
