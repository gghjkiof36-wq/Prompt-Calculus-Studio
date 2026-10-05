import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal, Qt, QPoint, QRect
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QCheckBox, QPushButton
from prompt_calculus_studio.manager_page import ManagerPage, PackageDetailText
from prompt_calculus_studio.manager_protocol import installed_packages
from prompt_calculus_studio.manager_gallery import DATA_ROLE, package_status
from prompt_calculus_studio.theme import stylesheet, widget_palette
from prompt_calculus_studio.core import DEFAULT_SETTINGS


class Comfy(QObject):
    stateChanged = Signal()
    url = 'http://127.0.0.1:8188'
    enabled = False
    running = pending = 0
    run_id = ''


class Window(QWidget):
    def __init__(self, directory):
        super().__init__()
        self.store = SimpleNamespace(directory=directory)
        self.comfy = Comfy(self)
        self.state = {'settings': dict(DEFAULT_SETTINGS)}
        self.last_notice = ''
        self.last_settings = ''

    def notice(self, message):
        self.last_notice = message

    def settings(self, key):
        self.last_settings = key


class ManagerPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = Window(Path(self.temp.name))
        self.page = ManagerPage(self.window)
        self.page.setStyleSheet(stylesheet(DEFAULT_SETTINGS))
        self.page.setPalette(widget_palette(DEFAULT_SETTINGS))

    def tearDown(self):
        self.page.shutdown()
        self.page.close()
        self.window.close()
        self.page.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def populated(self):
        self.page.client.packages = installed_packages({
            'Example Image Tools': {'ver': '1.2.3', 'enabled': True, 'cnr_id': 'image-tools'},
            'Example Control Tools': {'ver': '2.0.0', 'enabled': True, 'cnr_id': 'control-tools'},
        })
        self.page.client.available = self.page.client.can_update = True
        self.page.render()

    def test_empty_state_and_true_connection_entry(self):
        self.page.resize(1024, 768)
        self.page.show()
        self.app.processEvents()
        self.assertIs(self.page.list_stack.currentWidget(), self.page.empty)
        self.assertFalse(self.page.gallery.isVisible())
        self.assertEqual(self.page.summary_text.text(), '尚未連線')
        self.page.open_manager.click()
        self.assertEqual(self.window.last_settings, 'workflows')

    def test_responsive_detail_retains_card_size_and_batch_selection(self):
        self.populated()
        self.page.resize(1400, 900)
        self.page.show()
        self.app.processEvents()
        height = self.page.gallery.gridSize().height()
        self.assertEqual(self.page.gallery.cover_height, 56)
        self.page.select('Example Image Tools', True)
        self.app.processEvents()
        self.page.open_detail('Example Image Tools')
        self.app.processEvents()
        self.assertTrue(self.page.list_stack.isVisible())
        self.assertEqual(self.page.detail_back.text(), '關閉詳情')
        self.assertEqual(self.page.gallery.gridSize().height(), height)
        self.page.resize(720, 900)
        self.app.processEvents()
        self.assertFalse(self.page.list_stack.isVisible())
        self.assertTrue(self.page.detail_scroll.isVisible())
        self.assertEqual(self.page.detail_back.text(), '返回套件')
        self.assertEqual(self.page.gallery.gridSize().height(), height)
        self.assertGreaterEqual(self.page.gallery.cover_height, 48)
        self.assertLessEqual(self.page.gallery.cover_height, 64)
        self.page.close_detail()
        self.app.processEvents()
        self.assertTrue(self.page.list_stack.isVisible())
        self.assertIs(self.page.list_stack.currentWidget(), self.page.gallery)
        self.assertEqual(self.page.selected, {'Example Image Tools'})

    def test_selected_update_becomes_primary_and_all_recovers_after_clearing(self):
        self.populated()
        self.assertEqual(self.page.update_all.objectName(), 'Primary')
        enabled = self.page.update_all.isEnabled()
        self.page.select('Example Image Tools', True)
        self.assertEqual(self.page.update_all.objectName(), 'Quiet')
        self.assertEqual(self.page.selected_update.objectName(), 'Primary')
        self.assertEqual(self.page.selected, {'Example Image Tools'})
        self.assertEqual(self.page.update_all.isEnabled(), enabled)
        self.page.search.setText('Control')
        self.page.set_package_view(False)
        self.assertEqual(self.page.update_all.objectName(), 'Quiet')
        self.assertEqual(self.page.selected, {'Example Image Tools'})
        self.page.clear_selection()
        self.assertEqual(self.page.update_all.objectName(), 'Primary')
        self.assertEqual(self.page.update_all.isEnabled(), enabled)
        self.assertFalse(self.page.selected)
        self.assertTrue(self.page.selection_bar.isHidden())
        self.page.search.clear()
        self.page.select('Example Control Tools', True)
        self.page.select('Example Control Tools', False)
        self.assertEqual(self.page.update_all.objectName(), 'Primary')

    def test_detail_card_keeps_natural_height_and_is_independent_of_gallery_scroll(self):
        self.page.client.packages = installed_packages({
            f'Example Package {index:02}': {'ver': '1.0', 'enabled': True, 'cnr_id': f'example-{index}'}
            for index in range(40)
        })
        self.page.client.available = self.page.client.can_update = True
        self.page.resize(1400, 900)
        self.page.render(); self.page.show()
        self.page.select('Example Package 00', True)
        self.page.open_detail('Example Package 00')
        QTest.qWait(40)
        card = self.page.detail
        self.assertLess(card.height(), self.page.detail_scroll.viewport().height() - 80)
        card_top = card.mapTo(self.page, QPoint()).y()
        first_top = self.page.gallery.viewport().mapTo(self.page, self.page.gallery.visualItemRect(self.page.gallery.item(0)).topLeft()).y() + 4
        self.assertLessEqual(abs(card_top - first_top), 1)
        geometry = QRect(card.mapTo(self.page, QPoint()), card.size())
        detail_text = [control.text() for control in card.findChildren(QCheckBox)]
        for grid in (True, False):
            self.page.set_package_view(grid); QTest.qWait(30)
            scroll = self.page.gallery.verticalScrollBar()
            self.assertGreater(scroll.maximum(), 0)
            for position in (scroll.maximum(), 0):
                scroll.setValue(position); QTest.qWait(20)
                self.assertEqual(QRect(card.mapTo(self.page, QPoint()), card.size()), geometry)
                self.assertEqual(self.page.detail_id, 'Example Package 00')
                self.assertEqual(self.page.selected, {'Example Package 00'})
                self.assertEqual([control.text() for control in card.findChildren(QCheckBox)], detail_text)

    def test_narrow_detail_card_can_scroll_to_all_controls_at_large_font(self):
        self.populated()
        self.page.resize(520, 480)
        settings = dict(DEFAULT_SETTINGS, ui_size=18)
        self.page.setStyleSheet(stylesheet(settings))
        self.page.setFont(QFont(self.page.font().family(), 18))
        self.page.open_detail('Example Image Tools'); self.page.show(); QTest.qWait(40)
        scroll = self.page.detail_scroll
        self.assertFalse(self.page.list_stack.isVisible())
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        for action in (*self.page.detail.findChildren(QPushButton), *self.page.detail.findChildren(QCheckBox)):
            scroll.ensureWidgetVisible(action); QTest.qWait(10)
            self.assertTrue(scroll.viewport().rect().contains(QRect(action.mapTo(scroll.viewport(), QPoint()), action.size())), action.text())

    def test_enablement_remains_visible_with_update_and_pin_markers(self):
        self.populated()
        ident = 'Example Image Tools'
        package = next(pack for pack in self.page.client.packages if pack['id'] == ident)
        package['update_available'] = True
        self.page.client.server = 'http://127.0.0.1:8188'
        self.page.client.journal.pin(self.page.client.server, ident, True)
        self.page.render()
        item = next(self.page.gallery.item(i) for i in range(self.page.gallery.count())
                    if self.page.gallery.item(i).data(DATA_ROLE)['pack']['id'] == ident)
        self.assertEqual(package_status(package, True), ('已啟用', ['有更新', '已固定']))
        self.assertIn('已啟用 · 有更新 · 已固定', item.data(Qt.ItemDataRole.AccessibleDescriptionRole))
        package['enabled'] = False
        self.assertEqual(package_status(package, True), ('已停用', ['有更新', '已固定']))
        package['update_available'] = None
        self.assertEqual(package_status(package, False), ('已停用', []))
        self.assertEqual(self.page.batch_button.text(), '多選')

    def test_checkbox_does_not_open_detail_and_keyboard_keeps_selection(self):
        self.populated()
        self.page.resize(720, 640)
        self.page.show()
        self.app.processEvents()
        gallery = self.page.gallery
        item = gallery.item(0)
        ident = item.data(DATA_ROLE)['pack']['id']
        position = gallery.check_rect(gallery.visualItemRect(item)).center()
        QTest.mouseClick(gallery.viewport(), Qt.MouseButton.LeftButton, pos=position)
        self.assertEqual(self.page.selected, {ident})
        self.assertIsNone(self.page.detail_id)
        QTest.keyClick(gallery, Qt.Key.Key_Space)
        self.assertFalse(self.page.selected)
        QTest.keyClick(gallery, Qt.Key.Key_Space)
        self.page.set_package_view(False)
        self.app.processEvents()
        self.assertEqual(self.page.selected, {ident})
        self.assertEqual(gallery.currentItem(), item)
        QTest.keyClick(gallery, Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertEqual(self.page.detail_id, ident)
        self.assertFalse(self.page.list_stack.isVisible())
        self.page.close_detail()
        self.app.processEvents()
        self.assertTrue(gallery.isVisible())
        self.assertEqual(gallery.horizontalScrollBar().maximum(), 0)

    def test_busy_or_pinned_packages_cannot_be_batch_selected(self):
        self.populated()
        ident = 'Example Image Tools'
        self.page.client.busy = True
        self.page.select(ident, True)
        self.assertFalse(self.page.selected)
        self.page.client.busy = False
        self.page.client.server = 'http://127.0.0.1:8188'
        self.page.client.journal.pin(self.page.client.server, ident, True)
        self.page.render()
        self.page.select(ident, True)
        self.assertFalse(self.page.selected)
        matching = next(self.page.gallery.item(i) for i in range(self.page.gallery.count())
                        if self.page.gallery.item(i).data(DATA_ROLE)['pack']['id'] == ident)
        self.assertFalse(matching.data(DATA_ROLE)['selectable'])

    def test_grid_detail_and_list_have_no_horizontal_overflow(self):
        self.populated()
        for width, height in ((1400, 900), (1040, 640), (720, 640), (520, 540)):
            self.page.resize(width, height)
            self.page.show()
            self.page.close_detail()
            self.app.processEvents()
            self.assertLessEqual(self.page.width(), width)
            self.assertEqual(self.page.gallery.horizontalScrollBar().maximum(), 0)
            self.page.open_detail('Example Image Tools')
            self.app.processEvents()
            self.assertEqual(self.page.detail_scroll.horizontalScrollBar().maximum(), 0)
            self.assertFalse(self.page.location.isVisible())
            self.assertFalse(self.page.summary.isVisible())

    def test_grid_uses_available_columns_after_view_mode_change(self):
        self.page.client.packages = installed_packages({
            f'Example Package {index}': {'ver': '1.0', 'enabled': True, 'cnr_id': f'example-{index}'}
            for index in range(8)
        })
        self.page.client.available = self.page.client.can_update = True
        self.page.resize(1200, 900)
        self.page.render()
        self.page.show()
        QTest.qWait(30)
        self.page.set_package_view(False)
        self.page.set_package_view(True)
        QTest.qWait(30)
        gallery = self.page.gallery
        self.assertEqual(gallery.visualItemRect(gallery.item(0)).top(),
                         gallery.visualItemRect(gallery.item(4)).top())
        self.assertGreater(gallery.visualItemRect(gallery.item(5)).top(),
                           gallery.visualItemRect(gallery.item(0)).top())

    def test_filters_preserve_ids_and_refresh_discards_removed_selection_and_detail(self):
        self.populated()
        image_id, control_id = 'Example Image Tools', 'Example Control Tools'
        self.page.select(image_id, True)
        self.page.select(control_id, True)
        self.page.search.setText('Control')
        self.assertEqual(self.page.gallery.count(), 1)
        self.assertEqual(self.page.selected, {image_id, control_id})
        self.assertEqual(self.page.selection_text.text(), '已選 2 項')
        self.page.filter.setCurrentIndex(self.page.filter.findData('disabled'))
        self.assertEqual(self.page.gallery.count(), 0)
        self.assertEqual(self.page.selected, {image_id, control_id})
        self.page.open_detail(image_id)
        self.page.client.packages = [pack for pack in self.page.client.packages if pack['id'] == control_id]
        self.page.render()
        self.assertEqual(self.page.selected, {control_id})
        self.assertIsNone(self.page.detail_id)
        self.assertFalse(self.page.detail_scroll.isVisible())
        self.assertEqual(self.page.selection_text.text(), '已選 1 項')

    def test_update_during_generation_has_no_submit(self):
        self.populated()
        self.window.comfy.running = 1
        self.page.confirm_all()
        self.assertIn('生成工作', self.window.last_notice)
        self.assertFalse(self.page.client.journal.data['operations'])

    def test_generation_started_inside_confirmation_is_rechecked(self):
        self.populated()
        def confirmed():
            self.window.comfy.running = 1
            return 1
        with patch('prompt_calculus_studio.manager_page.StudioDialog.exec', side_effect=confirmed):
            self.page.confirm_all()
        self.assertIn('生成工作', self.window.last_notice)
        self.assertFalse(self.page.client.journal.data['operations'])

    def long_package(self):
        self.populated()
        self.page.client.server = 'http://127.0.0.1:8188'
        pack = self.page.client.packages[0]
        pack.update(name='ComfyUI-Artist-Selector@' + 'ExampleAuthor' * 6,
                    version='a1c70bd0905127135ff68cc2b0014b2420b940213',
                    author='@' + 'ExampleAuthor' * 8,
                    aux_id='ExampleOwner/' + 'LongSourceToken' * 10,
                    description='Description\n' + 'UnbrokenPackageDescription' * 15,
                    update_available=True)
        self.page.render()
        return pack

    def test_unbroken_detail_text_fits_viewport_at_narrow_wide_and_maximum_font(self):
        for filename in ('msjh.ttc', 'msjhbd.ttc'):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + filename)
        pack = self.long_package()
        for width in (1440, 720, 520):
            for size in (11, 18, 22):
                with self.subTest(width=width, size=size):
                    settings = dict(DEFAULT_SETTINGS, ui_size=size)
                    self.page.setStyleSheet(stylesheet(settings))
                    QTest.qWait(20)
                    self.page.resize(width, 900 if width > 1000 else 640)
                    self.page.open_detail(pack['id']); self.page.show(); QTest.qWait(60)
                    scroll = self.page.detail_scroll
                    self.assertEqual(self.page.width(), width)
                    self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                    self.assertLessEqual(self.page.detail.width(), scroll.viewport().width())
                    self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
                    controls = self.page.detail.findChildren(PackageDetailText)
                    for control in controls:
                        self.assertEqual(control.text(), control.toolTip())
                        self.assertEqual(control.text(), control.accessibleName())
                        self.assertGreaterEqual(control.height(), control.heightForWidth(control.width()))
                        layout, height = control.text_layout(control.contentsRect().width())
                        final = layout.lineAt(layout.lineCount() - 1)
                        self.assertEqual(final.textStart() + final.textLength(), len(control.text()))
                        for index in range(layout.lineCount()):
                            line = layout.lineAt(index)
                            self.assertLessEqual(line.naturalTextWidth(), control.contentsRect().width() + 1)
                    pin = self.page.detail.findChild(QCheckBox)
                    scroll.ensureWidgetVisible(pin); QTest.qWait(10)
                    self.assertTrue(scroll.viewport().rect().contains(QRect(pin.mapTo(scroll.viewport(), QPoint()), pin.size())))

    def test_long_package_metadata_keeps_grid_list_tooltips_and_selection(self):
        pack = self.long_package()
        for width, size in ((1440, 11), (640, 18), (640, 22)):
            settings = dict(DEFAULT_SETTINGS, ui_size=size)
            self.page.setStyleSheet(stylesheet(settings)); QTest.qWait(20); self.page.resize(width, 900)
            self.page.show(); self.page.close_detail(); self.page.select(pack['id'], True)
            for grid in (True, False):
                with self.subTest(width=width, size=size, grid=grid):
                    self.page.set_package_view(grid); QTest.qWait(25)
                    gallery = self.page.gallery
                    self.assertEqual(gallery.horizontalScrollBar().maximum(), 0)
                    self.assertEqual(self.page.width(), width)
                    self.assertGreaterEqual(gallery.line_height(), gallery.fontMetrics().height())
                    item = next(gallery.item(i) for i in range(gallery.count()) if gallery.item(i).data(DATA_ROLE)['pack']['id'] == pack['id'])
                    self.assertEqual(item.checkState(), Qt.CheckState.Checked)
                    for text in (pack['name'], pack['author'], pack['version'], pack['aux_id'], '有更新'):
                        self.assertIn(text, item.toolTip())
                    self.assertEqual(self.page.selected, {pack['id']})
                    gallery.setCurrentItem(item); QTest.keyClick(gallery, Qt.Key.Key_Return); QTest.qWait(10)
                    self.assertEqual(self.page.detail_id, pack['id'])
                    self.page.close_detail()

    def test_long_detail_update_and_pin_keep_original_package_identity(self):
        pack = self.long_package()
        self.page.resize(520, 640); self.page.show(); self.page.open_detail(pack['id']); QTest.qWait(20)
        action = next(control for control in self.page.detail.findChildren(QPushButton) if control.text() == '更新此套件')
        self.assertTrue(action.isEnabled())
        with patch.object(self.page, 'confirm_ids') as confirm:
            action.click(); confirm.assert_called_once_with([pack['id']])
        pin = self.page.detail.findChild(QCheckBox); pin.click(); QTest.qWait(20)
        self.assertFalse(self.window.last_notice)
        self.assertIn(pack['id'], self.page.client.journal.pins(self.page.client.server))
        self.assertEqual(self.page.detail_id, pack['id'])
        action = next(control for control in self.page.detail.findChildren(QPushButton) if control.text() == '更新此套件')
        self.assertFalse(action.isEnabled())
        self.page.detail.findChild(QCheckBox).click(); QTest.qWait(20)
        self.assertNotIn(pack['id'], self.page.client.journal.pins(self.page.client.server))
        self.assertFalse(self.page.client.journal.data['operations'])


if __name__ == '__main__':
    unittest.main()
