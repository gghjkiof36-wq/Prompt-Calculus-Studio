"""Actual Qt events for long parameter choices, without a Window or service."""
import unittest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from prompt_calculus_studio.stage_parameter_choices import ParameterChoices
from prompt_calculus_studio.theme import stylesheet,visual_tokens,widget_palette
from prompt_calculus_studio.core import DEFAULT_SETTINGS


APP = QApplication.instance() or QApplication([])


class StageParameterChoicesTests(unittest.TestCase):
    def setUp(self):
        self.host = QWidget()
        self.host.resize(420, 120)
        body = QVBoxLayout(self.host)
        self.combo = ParameterChoices()
        body.addWidget(self.combo)
        self.host.show()
        QTest.qWait(10)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.combo.hidePopup()
        self.host.close()
        self.host.deleteLater()
        APP.processEvents()

    def fill(self, count=25):
        for index in range(count):
            self.combo.addItem(f'model-{index:02}.safetensors', f'actual/path/{index}')
        self.combo.setCurrentIndex(17)
        self.changes = []
        self.activations = []
        self.combo.currentIndexChanged.connect(self.changes.append)
        self.combo.activated.connect(self.activations.append)

    def open_mouse(self):
        QTest.mouseClick(self.combo, Qt.MouseButton.LeftButton)
        QTest.qWait(15)
        popup = self.combo._choices_popup
        self.assertIsNotNone(popup)
        self.assertIs(APP.activePopupWidget(), popup)
        self.assertTrue(popup.search.hasFocus())
        return popup

    def test_short_list_keeps_existing_combo_popup(self):
        self.fill(19)
        QTest.mouseClick(self.combo, Qt.MouseButton.LeftButton)
        QTest.qWait(15)
        self.assertIsNone(self.combo._choices_popup)
        self.assertIs(APP.activePopupWidget(), self.combo._popup)
        self.assertTrue(self.combo._popup.mask().isEmpty())
        self.assertTrue(self.combo._popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        QTest.keyClick(self.combo.view(), Qt.Key.Key_Escape)
        QTest.qWait(10)
        self.assertEqual(self.combo.currentIndex(), 17)
        self.assertEqual(self.changes, [])

    def test_search_popup_paints_a_tinted_surface_with_application_styles(self):
        # A top-level popup is composited over the editor on Windows. Checking
        # only selected rows misses transparent list gaps and the popup frame.
        self.fill()
        before = self.combo.currentData()
        for name in ('graphite','mist','paper'):
            settings=dict(DEFAULT_SETTINGS,visual_palette=name);tokens=visual_tokens(settings)
            # The desktop applies its stylesheet to Window, not QApplication.
            self.host.setStyleSheet(stylesheet(settings));self.host.setPalette(widget_palette(settings))
            popup = self.open_mouse()
            for query in ('', 'not-a-model'):
                popup.search.setText(query)
                APP.processEvents()
                rendered = popup.grab().toImage()
                for point in (QPoint(5, popup.height() // 2),
                              QPoint(popup.width() // 2, popup.height() - 5),
                              popup.items.mapTo(popup, QPoint(popup.items.width() - 20, 3))):
                    pixel = rendered.pixelColor(point)
                    self.assertEqual(pixel.alpha(), 255, (name,query, point, pixel.getRgb()))
                actual=rendered.pixelColor(5, popup.height() // 2)
                expected=QColor(tokens['raised'])
                # The frosted surface composites its softened in-app snapshot
                # once. Raw content cannot shine through the native window.
                self.assertTrue(all(abs(a-b)<=16 for a,b in zip(actual.getRgb()[:3],expected.getRgb()[:3])))
            QTest.keyClick(popup.search, Qt.Key.Key_Escape);APP.processEvents()
        self.assertEqual(self.combo.currentData(), before)
        self.assertEqual(self.changes, [])

    def test_search_preserves_order_and_mouse_chooses_original_typed_data(self):
        self.fill(20)
        self.combo.setItemData(3, 123)
        self.combo.setItemData(13, False)
        self.combo.setItemText(3, 'shared item')
        self.combo.setItemText(13, 'SHARED item')
        popup = self.open_mouse()
        QTest.keyClicks(popup.search, 'shared')
        self.assertEqual([popup.items.item(i).text() for i in range(popup.items.count())], ['shared item', 'SHARED item'])
        self.assertEqual(self.combo.currentIndex(), 17)
        self.assertEqual(self.changes, [])
        item = popup.items.item(1)
        QTest.mouseClick(popup.items.viewport(), Qt.MouseButton.LeftButton,
                         pos=popup.items.visualItemRect(item).center())
        QTest.qWait(10)
        self.assertIsNone(self.combo._choices_popup)
        self.assertEqual(self.combo.currentIndex(), 13)
        self.assertIs(self.combo.currentData(), False)
        self.assertEqual(self.changes, [13])
        self.assertEqual(self.activations, [13])
        self.assertTrue(self.combo.hasFocus())

    def test_keyboard_search_requires_selection_then_enter(self):
        self.fill()
        self.combo.setFocus()
        QTest.keyClick(self.combo, Qt.Key.Key_Space)
        QTest.qWait(10)
        popup = self.combo._choices_popup
        self.assertIsNotNone(popup)
        QTest.keyClicks(popup.search, 'model-0')
        self.assertEqual(popup.items.count(), 10)
        self.assertIsNone(popup.items.currentItem())
        QTest.keyClick(popup.search, Qt.Key.Key_Return)
        self.assertEqual(self.combo.currentIndex(), 17)
        self.assertEqual(self.changes, [])
        QTest.keyClick(popup.search, Qt.Key.Key_Down)
        QTest.keyClick(popup.search, Qt.Key.Key_Down)
        QTest.keyClick(popup.search, Qt.Key.Key_Return)
        QTest.qWait(10)
        self.assertEqual(self.combo.currentIndex(), 1)
        self.assertEqual(self.combo.currentData(), 'actual/path/1')
        self.assertEqual(self.activations, [1])
        self.assertTrue(self.combo.hasFocus())

    def test_no_results_enter_and_escape_do_not_change_value(self):
        self.fill()
        popup = self.open_mouse()
        QTest.keyClicks(popup.search, 'not-a-model')
        self.assertEqual(popup.items.count(), 0)
        self.assertTrue(popup.empty.isVisible())
        QTest.keyClick(popup.search, Qt.Key.Key_Down)
        QTest.keyClick(popup.search, Qt.Key.Key_Return)
        self.assertTrue(popup.isVisible())
        QTest.keyClick(popup.search, Qt.Key.Key_Escape)
        QTest.qWait(10)
        self.assertIsNone(self.combo._choices_popup)
        self.assertEqual(self.combo.currentData(), 'actual/path/17')
        self.assertEqual(self.changes, [])
        self.assertEqual(self.activations, [])
        self.assertTrue(self.combo.hasFocus())
        popup = self.open_mouse()
        self.assertEqual(popup.search.text(), '')
        self.assertEqual(popup.items.count(), 25)

    def test_invalid_or_missing_selection_never_falls_back_to_first(self):
        self.fill()
        self.combo.addItem('removed-model · 已失效', 'removed-model')
        self.combo.setCurrentIndex(25)
        self.changes.clear()
        popup = self.open_mouse()
        QTest.keyClicks(popup.search, 'model-01')
        QTest.keyClick(popup.search, Qt.Key.Key_Escape)
        QTest.qWait(10)
        self.assertEqual(self.combo.currentData(), 'removed-model')
        self.assertEqual(self.changes, [])
        self.combo.setCurrentIndex(-1)
        self.changes.clear()
        popup = self.open_mouse()
        self.assertIsNone(popup.items.currentItem())
        QTest.keyClick(popup.search, Qt.Key.Key_Return)
        QTest.keyClick(popup.search, Qt.Key.Key_Escape)
        QTest.qWait(10)
        self.assertEqual(self.combo.currentIndex(), -1)
        self.assertEqual(self.changes, [])

    def test_stale_model_item_cannot_select_a_replacement(self):
        self.fill()
        popup = self.open_mouse()
        self.combo.clear()
        self.combo.addItem('replacement', 'new-data')
        self.changes.clear()
        QTest.keyClick(popup.search, Qt.Key.Key_Return)
        QTest.qWait(10)
        self.assertIsNone(self.combo._choices_popup)
        self.assertEqual(self.combo.currentData(), 'new-data')
        self.assertEqual(self.changes, [])
        self.assertEqual(self.activations, [])

    def test_popup_stays_on_screen_and_typing_opens_search(self):
        self.fill()
        available = self.host.screen().availableGeometry()
        self.host.move(available.bottomRight() - QPoint(self.host.width(), self.host.height()))
        QTest.qWait(10)
        self.combo.setFocus()
        QTest.keyClicks(self.combo, 'm')
        QTest.qWait(10)
        popup = self.combo._choices_popup
        self.assertIsNotNone(popup)
        self.assertEqual(popup.search.text(), 'm')
        self.assertTrue(available.contains(popup.geometry()), (available, popup.geometry()))
        self.assertEqual(self.changes, [])
        QTest.keyClick(popup.search, Qt.Key.Key_Escape)


if __name__ == '__main__':
    unittest.main()
