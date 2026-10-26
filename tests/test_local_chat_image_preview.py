"""Actual image attachment gestures, without disk/model access."""
import base64
import copy
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QBuffer, QIODevice, QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from prompt_calculus_studio.local_ai.attachment_strip import AttachmentStrip, ImageThumbnail
from prompt_calculus_studio.local_ai.attachment_preview import snapshot_image
from prompt_calculus_studio.theme import visual_tokens


def image_item(name='私人圖片名稱.png'):
    image = QImage(360, 120, QImage.Format.Format_RGB32)
    image.fill(QColor('#5b89aa'))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    raw = bytes(buffer.data())
    return dict(id='image-id', name=name, kind='image', size=len(raw),
                data_uri='data:image/png;base64,' + base64.b64encode(raw).decode('ascii'))


class ImagePreviewContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.host = QWidget()
        self.host.resize(900, 640)
        layout = QVBoxLayout(self.host)
        self.behind = QPushButton('原頁面')
        layout.addWidget(self.behind)
        self.strip = AttachmentStrip()
        layout.addWidget(self.strip)
        layout.addStretch()
        self.item = image_item()
        self.strip.sync([self.item])
        self.strip.apply_appearance(visual_tokens())
        self.host.show()
        self.app.processEvents()

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()

    def open_preview(self):
        thumb = self.strip.findChild(ImageThumbnail)
        QTest.mouseClick(thumb, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        overlay = self.strip._preview
        self.assertIsNotNone(overlay)
        self.assertTrue(overlay.isVisible())
        self.assertEqual(overlay.geometry(), self.host.rect())
        return overlay

    def test_square_image_without_visible_filename_and_document_keeps_name(self):
        text = dict(id='text-id', name='研究筆記.md', kind='text', text='原文', size=6)
        original = copy.deepcopy([self.item, text])
        self.strip.sync(original)
        self.app.processEvents()
        thumb = self.strip.findChild(ImageThumbnail)
        self.assertEqual(thumb.width(), 64)
        self.assertEqual(thumb.height(), 64)
        self.assertEqual(thumb.text(), '')
        self.assertEqual(thumb.toolTip(), self.item['name'])
        labels = [item.text() for item in self.strip.findChildren(QPushButton)]
        self.assertNotIn(self.item['name'], labels)
        self.assertIn('研究筆記.md', labels)
        self.assertEqual(original, [self.item, text])

    def test_remove_is_separate_from_preview_and_busy_disables_it(self):
        removed = []
        self.strip.removeRequested.connect(removed.append)
        remove = self.strip._remove_buttons[0]
        QTest.mouseClick(remove, Qt.MouseButton.LeftButton)
        self.assertEqual(removed, ['image-id'])
        self.assertIsNone(self.strip._preview)
        self.strip.sync([self.item], busy=True)
        QTest.mouseClick(remove, Qt.MouseButton.LeftButton)
        self.assertEqual(removed, ['image-id'])

    def test_sent_images_have_same_thumbnail_without_remove(self):
        sent = AttachmentStrip(self.host, read_only=True)
        self.host.layout().insertWidget(1, sent)
        sent.sync([self.item])
        self.app.processEvents()
        self.assertEqual(len(sent.findChildren(ImageThumbnail)), 1)
        self.assertFalse(sent._remove_buttons)
        frame = sent.findChild(ImageThumbnail).parentWidget()
        self.assertEqual(frame.geometry().right(), sent.container.width() - 1)
        sent.sync([dict(self.item, id='updated-image')])
        self.assertEqual(sent.items_layout.count(), 2)
        sent.findChild(ImageThumbnail).click()
        self.assertTrue(sent._preview.isVisible())
        sent._preview.dismiss()

    def test_image_click_keeps_open_outside_closes_without_click_through(self):
        activated = []
        self.behind.clicked.connect(lambda: activated.append(True))
        overlay = self.open_preview()
        self.assertEqual(overlay.image.width() / overlay.image.height(), 3)
        QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=overlay.image_rect.center())
        self.assertTrue(overlay.isVisible())
        QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))
        self.assertIsNone(self.strip._preview)
        self.assertFalse(activated)

    def test_escape_and_close_control_each_dismiss(self):
        overlay = self.open_preview()
        QTest.keyClick(overlay, Qt.Key.Key_Escape)
        self.assertIsNone(self.strip._preview)
        overlay = self.open_preview()
        QTest.mouseClick(overlay.close_button, Qt.MouseButton.LeftButton)
        self.assertIsNone(self.strip._preview)

    def test_resize_and_hidden_source_cannot_leave_orphan_overlay(self):
        overlay = self.open_preview()
        self.host.resize(480, 320)
        self.app.processEvents()
        self.assertEqual(overlay.geometry(), self.host.rect())
        self.assertTrue(overlay.rect().contains(overlay.image_rect))
        self.strip.hide()
        self.assertIsNone(self.strip._preview)

    def test_keyboard_thumbnail_opens_and_escape_restores_focus(self):
        thumb = self.strip.findChild(ImageThumbnail)
        thumb.setFocus()
        QTest.keyClick(thumb, Qt.Key.Key_Space)
        overlay = self.strip._preview
        self.assertIsNotNone(overlay)
        QTest.keyClick(overlay, Qt.Key.Key_Tab)
        self.assertTrue(overlay.close_button.hasFocus())
        QTest.keyClick(overlay.close_button, Qt.Key.Key_Escape)
        self.assertIsNone(self.strip._preview)
        self.assertTrue(thumb.hasFocus())

    def test_invalid_snapshot_has_bounded_placeholder_and_can_close(self):
        invalid = dict(self.item, data_uri='file:///private.png')
        self.assertTrue(snapshot_image(invalid).isNull())
        self.strip.preview(invalid)
        overlay = self.strip._preview
        self.assertTrue(overlay.image.isNull())
        QTest.keyClick(overlay, Qt.Key.Key_Escape)
        self.assertIsNone(self.strip._preview)


if __name__ == '__main__':
    unittest.main()
