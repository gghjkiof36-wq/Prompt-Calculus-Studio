"""Real Qt input and pixels: focus remains useful without a sticky mouse ring."""
import copy
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QHBoxLayout,
                              QPushButton, QRadioButton, QToolButton, QVBoxLayout, QWidget)

from prompt_calculus_studio.button_focus import install_button_focus_styling
from prompt_calculus_studio.core import DEFAULT_SETTINGS
from prompt_calculus_studio.recent_overlay import RecentOverlay
from prompt_calculus_studio.theme import stylesheet, visual_tokens, widget_palette


APP = QApplication.instance() or QApplication([])
install_button_focus_styling()


class ButtonFocusTests(unittest.TestCase):
    def setUp(self):
        self.host = QWidget()
        self.host.state = {'settings': copy.deepcopy(DEFAULT_SETTINGS)}
        self.host.resize(640, 400)
        layout = QVBoxLayout(self.host)
        self.first = QPushButton('First')
        self.recent = QPushButton('Recent')
        self.recent.setObjectName('IconButton')
        self.tool = QToolButton()
        self.tool.setText('Tool')
        self.tool.setObjectName('IconButton')
        self.tool.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.check = QCheckBox('Check')
        self.radio = QRadioButton('Radio')
        for control in (self.first, self.recent, self.tool, self.check, self.radio):
            layout.addWidget(control)
        self.blank = QWidget()
        self.blank.setMinimumHeight(100)
        layout.addWidget(self.blank)
        self.apply('graphite')
        self.host.show()
        self.host.activateWindow()
        QTest.qWait(30)

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        APP.processEvents()

    def apply(self, palette):
        self.host.state['settings']['visual_palette'] = palette
        self.host.setPalette(widget_palette(self.host.state['settings']))
        self.host.setStyleSheet(stylesheet(self.host.state['settings']))

    def border(self, control):
        APP.processEvents()
        image = control.grab().toImage()
        return image.pixelColor(image.width() // 2, 0).name()

    def unhover(self):
        QTest.mouseMove(self.blank, self.blank.rect().center())
        APP.processEvents()

    def test_mouse_retains_focus_without_keyboard_border_in_all_palettes(self):
        for palette in ('graphite', 'paper', 'mist'):
            with self.subTest(palette=palette):
                self.apply(palette)
                self.first.setFocus()
                self.unhover()
                normal = self.border(self.recent)
                QTest.mouseClick(self.recent, Qt.MouseButton.LeftButton)
                self.unhover()
                self.assertIs(APP.focusWidget(), self.recent)
                self.assertFalse(self.recent.property('pcsKeyboardFocus'))
                self.assertEqual(self.border(self.recent), normal)
                QTest.keyClick(self.recent, Qt.Key.Key_Space)
                self.assertTrue(self.recent.property('pcsKeyboardFocus'))
                self.assertEqual(self.border(self.recent), visual_tokens(self.host.state['settings'])['accent'])

    def test_tab_backtab_space_and_default_enter_preserve_keyboard_focus(self):
        self.first.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(self.first, Qt.Key.Key_Tab)
        self.assertIs(APP.focusWidget(), self.recent)
        self.assertTrue(self.recent.property('pcsKeyboardFocus'))
        clicked = QSignalSpy(self.recent.clicked)
        QTest.keyClick(self.recent, Qt.Key.Key_Space)
        self.assertEqual(clicked.count(), 1)
        QTest.keyClick(self.recent, Qt.Key.Key_Tab)
        self.assertIs(APP.focusWidget(), self.tool)
        QTest.keyClick(self.tool, Qt.Key.Key_Backtab)
        self.assertIs(APP.focusWidget(), self.recent)
        self.assertTrue(self.recent.property('pcsKeyboardFocus'))
        dialog = QDialog(self.host)
        layout = QHBoxLayout(dialog)
        default = QPushButton('Confirm', dialog)
        default.setDefault(True)
        layout.addWidget(default)
        dialog.show()
        default.setFocus(Qt.FocusReason.TabFocusReason)
        APP.processEvents()
        accepted = QSignalSpy(default.clicked)
        QTest.keyClick(default, Qt.Key.Key_Return)
        self.assertEqual(accepted.count(), 1)
        self.assertTrue(default.isDefault())
        self.assertTrue(default.autoDefault())
        self.assertTrue(default.property('pcsKeyboardFocus'))
        dialog.close()
        dialog.deleteLater()

    def test_overlay_mouse_outside_and_keyboard_escape_restore_focus_modality(self):
        sheets = []
        self.recent.clicked.connect(lambda: sheets.append(RecentOverlay(self.host, QWidget())))
        for opening, closing, visible in (('mouse', 'outside', False),
                                         ('keyboard', 'escape', True),
                                         ('keyboard', 'outside', False)):
            with self.subTest(opening=opening, closing=closing):
                if opening == 'mouse':
                    QTest.mouseClick(self.recent, Qt.MouseButton.LeftButton)
                else:
                    self.recent.setFocus(Qt.FocusReason.TabFocusReason)
                    QTest.keyClick(self.recent, Qt.Key.Key_Space)
                sheet = sheets[-1]
                finished = QSignalSpy(sheet.finished)
                APP.processEvents()
                self.assertIs(sheet.previous_focus, self.recent)
                if closing == 'outside':
                    QTest.mouseClick(sheet, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
                else:
                    QTest.keyClick(sheet, Qt.Key.Key_Escape)
                APP.processEvents()
                self.assertEqual(finished.count(), 1)
                self.assertIs(APP.focusWidget(), self.recent)
                self.assertEqual(self.recent.property('pcsKeyboardFocus'), visible)
                self.unhover()
                if visible:
                    self.assertEqual(self.border(self.recent), visual_tokens(self.host.state['settings'])['accent'])
                else:
                    focused = self.border(self.recent)
                    self.first.setFocus()
                    self.assertEqual(focused, self.border(self.recent))
                sheet.deleteLater()

    def test_pointer_does_not_change_checked_policy_or_checkbox_radio_focus(self):
        self.recent.setCheckable(True)
        policy = self.recent.focusPolicy()
        QTest.mouseClick(self.recent, Qt.MouseButton.LeftButton)
        self.assertTrue(self.recent.isChecked())
        self.assertEqual(self.recent.focusPolicy(), policy)
        QTest.keyClick(self.recent, Qt.Key.Key_Space)
        self.assertFalse(self.recent.isChecked())
        QTest.mouseClick(self.tool, Qt.MouseButton.LeftButton)
        self.assertIs(APP.focusWidget(), self.tool)
        self.assertFalse(self.tool.property('pcsKeyboardFocus'))
        QTest.keyClick(self.tool, Qt.Key.Key_Space)
        self.assertTrue(self.tool.property('pcsKeyboardFocus'))
        for control in (self.check, self.radio):
            QTest.mouseClick(control, Qt.MouseButton.LeftButton, pos=QPoint(8, control.height() // 2))
            self.assertIs(APP.focusWidget(), control)
            self.assertTrue(control.isChecked())
            self.assertIsNone(control.property('pcsKeyboardFocus'))


if __name__ == '__main__':
    unittest.main()
