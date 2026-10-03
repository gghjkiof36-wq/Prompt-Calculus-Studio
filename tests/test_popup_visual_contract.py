"""Popup colors keep the active keyboard choice visible across palettes."""
import unittest

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFontDatabase, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCompleter, QLineEdit, QVBoxLayout, QWidget

from prompt_studio.core import DEFAULT_SETTINGS
from prompt_studio.stage_parameter_choices import ParameterChoices
from prompt_studio.theme import stylesheet, visual_tokens, widget_palette
from prompt_studio.widgets import ComboBox, RoundMenu, style_completion


APP = QApplication.instance() or QApplication([])
for filename in ('msjh.ttc', 'segoeui.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + filename)


class PopupVisualContractTests(unittest.TestCase):
    def setUp(self):
        self.original_style = APP.styleSheet()
        self.original_palette = QPalette(APP.palette())
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.close()
        APP.processEvents()
        APP.setStyleSheet(self.original_style)
        APP.setPalette(self.original_palette)

    def host(self, palette):
        settings = dict(DEFAULT_SETTINGS, visual_palette=palette)
        window = QWidget()
        window.setStyleSheet(stylesheet(settings))
        window.setPalette(widget_palette(settings))
        window.setGeometry(20, 20, 380, 160)
        window.box = QVBoxLayout(window)
        self.windows.append(window)
        return window, visual_tokens(settings)

    def assert_active_pixels(self, widget, rect, tokens):
        APP.processEvents()
        image = widget.grab().toImage()
        point = QPoint(rect.right() - 20, rect.center().y())
        self.assertEqual(image.pixelColor(point).name(), tokens['popup_active'])
        # Measure rendered glyph pixels as well as the row background. This
        # catches warm-white text inheriting a dark color on a dark popup.
        crop = image.copy(rect.adjusted(8, 3, -max(8, rect.width() - 125), -3))
        lightness = [crop.pixelColor(x, y).lightness()
                     for x in range(crop.width()) for y in range(crop.height())]
        if tokens['scheme'] == 'light':
            self.assertLess(min(lightness), 100)
        else:
            self.assertGreater(max(lightness), 170)

    def test_combo_keyboard_hover_and_cancel_share_visible_active_color(self):
        for palette in ('graphite', 'mist', 'paper'):
            with self.subTest(palette=palette):
                window, tokens = self.host(palette)
                combo = ComboBox()
                combo.addItems(['Solid', 'Mica', 'Acrylic'])
                combo.setCurrentIndex(1)
                window.box.addWidget(combo)
                window.show()
                APP.processEvents()
                combo.showPopup()
                view = combo.view()
                QTest.mouseMove(window, QPoint(1, 1))
                QTest.keyClick(view, Qt.Key.Key_Down)
                self.assertEqual(view.currentIndex().row(), 2)
                self.assertEqual(combo.currentIndex(), 1)
                self.assert_active_pixels(view.viewport(), view.visualRect(view.currentIndex()), tokens)
                target = view.model().index(0, 0)
                QTest.mouseMove(view.viewport(), view.visualRect(target).center())
                self.assertEqual(view.currentIndex().row(), 0)
                self.assert_active_pixels(view.viewport(), view.visualRect(target), tokens)
                QTest.keyClick(view, Qt.Key.Key_Escape)
                self.assertEqual(combo.currentIndex(), 1)
                self.assertFalse(view.isVisible())
                window.close()

    def test_long_parameter_popup_tracks_palette_and_keeps_commit_explicit(self):
        for palette in ('graphite', 'mist', 'paper'):
            with self.subTest(palette=palette):
                window, tokens = self.host(palette)
                combo = ParameterChoices()
                combo.addItems([f'Choice {i:02}' for i in range(24)])
                combo.setCurrentIndex(1)
                window.box.addWidget(combo)
                window.show()
                APP.processEvents()
                combo.showPopup()
                popup = combo._choices_popup
                QTest.keyClick(popup.search, Qt.Key.Key_Down)
                self.assertEqual(popup.items.currentRow(), 2)
                self.assertEqual(combo.currentIndex(), 1)
                self.assert_active_pixels(popup.items.viewport(), popup.items.visualItemRect(popup.items.currentItem()), tokens)
                QTest.keyClick(popup.search, Qt.Key.Key_Return)
                self.assertEqual(combo.currentIndex(), 2)
                self.assertIsNone(combo._choices_popup)
                window.close()

    def test_menu_and_completion_keyboard_match_combo_active_color(self):
        for palette in ('graphite', 'mist', 'paper'):
            with self.subTest(palette=palette):
                window, tokens = self.host(palette)
                editor = QLineEdit()
                window.box.addWidget(editor)
                completer = QCompleter(['First choice', 'Second choice'], editor)
                style_completion(completer, editor)
                editor.setCompleter(completer)
                window.show()
                APP.processEvents()
                chosen = []
                menu = RoundMenu(window)
                first = menu.addAction('Open original image', lambda: chosen.append(0))
                second = menu.addAction('View image details', lambda: chosen.append(1))
                menu.open_at(window.mapToGlobal(QPoint(10, 45)))
                menu.setActiveAction(first)
                QTest.keyClick(menu, Qt.Key.Key_Down)
                self.assertIs(menu.activeAction(), second)
                self.assert_active_pixels(menu, menu.actionGeometry(second), tokens)
                QTest.keyClick(menu, Qt.Key.Key_Return)
                self.assertEqual(chosen, [1])
                editor.setFocus()
                completer.setCompletionPrefix('')
                completer.complete(QRect(0, editor.height(), 300, editor.height()))
                view = completer.popup()
                view.setCurrentIndex(completer.completionModel().index(0, 0))
                QTest.keyClick(view, Qt.Key.Key_Down)
                self.assertEqual(view.currentIndex().row(), 1)
                self.assert_active_pixels(view.viewport(), view.visualRect(view.currentIndex()), tokens)
                QTest.keyClick(view, Qt.Key.Key_Return)
                self.assertEqual(editor.text(), 'Second choice')
                window.close()

    def test_completion_reopens_with_new_owner_theme_without_changing_other_window(self):
        main, _ = self.host('graphite')
        other, other_tokens = self.host('paper')
        completers = []
        for window in (main, other):
            editor = QLineEdit()
            window.box.addWidget(editor)
            completer = QCompleter(['First choice', 'Second choice'], editor)
            editor.setCompleter(completer)
            style_completion(completer, editor)
            window.show()
            completers.append((editor, completer))
        other_style, other_palette = other.styleSheet(), QPalette(other.palette())
        app_style, app_palette = APP.styleSheet(), QPalette(APP.palette())

        def inspect(editor, completer, tokens):
            editor.window().activateWindow();editor.setFocus()
            completer.setCompletionPrefix('')
            completer.complete(QRect(0, editor.height(), 300, editor.height()))
            view = completer.popup()
            view.setCurrentIndex(completer.completionModel().index(1, 0))
            self.assert_active_pixels(view.viewport(), view.visualRect(view.currentIndex()), tokens)
            self.assertEqual(view.palette().color(QPalette.ColorRole.Text).name(), tokens['text'])
            view.hide();APP.processEvents()

        for palette in ('graphite', 'paper', 'mist'):
            settings = dict(DEFAULT_SETTINGS, visual_palette=palette)
            main.setStyleSheet(stylesheet(settings));main.setPalette(widget_palette(settings))
            inspect(*completers[0], visual_tokens(settings))
            inspect(*completers[1], other_tokens)
            self.assertEqual(other.styleSheet(), other_style)
            self.assertEqual(other.palette(), other_palette)
            self.assertEqual(APP.styleSheet(), app_style)
            self.assertEqual(APP.palette(), app_palette)
            self.assertEqual(completers[0][0].text(), '')
            self.assertEqual(completers[1][0].text(), '')

    def test_long_choice_reopen_reads_updated_window_theme_without_changing_value(self):
        window, _ = self.host('graphite')
        combo = ParameterChoices()
        combo.addItems([f'Choice {i:02}' for i in range(24)])
        combo.setCurrentIndex(1);window.box.addWidget(combo);window.show()
        for palette in ('graphite', 'paper', 'mist'):
            settings = dict(DEFAULT_SETTINGS, visual_palette=palette)
            window.setStyleSheet(stylesheet(settings));window.setPalette(widget_palette(settings))
            combo.showPopup();popup = combo._choices_popup
            self.assert_active_pixels(popup.items.viewport(), popup.items.visualItemRect(popup.items.currentItem()),
                                      visual_tokens(settings))
            QTest.keyClick(popup.search, Qt.Key.Key_Escape);APP.processEvents()
            self.assertEqual(combo.currentIndex(), 1)


if __name__ == '__main__':
    unittest.main()
