"""Real Qt action paint states; no application Window, service or generation."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QPushButton, QStyle, QStyleOptionButton, QWidget

from prompt_studio.color_roles import contrast
from prompt_studio.core import DEFAULT_SETTINGS
from prompt_studio.theme import stylesheet, visual_tokens, widget_palette
from prompt_studio.ui_icons import icon


APP = QApplication.instance() or QApplication([])
for filename in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + filename)

CASES = {
    'graphite': ('graphite', None),
    'paper': ('paper', None),
    'mist': ('mist', None),
    'custom': ('graphite', {'run_seed': '#be46e8', 'stop_seed': '#d36418'}),
}
STATES = ('normal', 'hover', 'pressed', 'disabled')
ROLES = {
    'Run': ('執行', 'play', 'on-run', 'run'),
    'StopRun': ('取消', 'close', 'on-stop', 'stop'),
    'Primary': ('複製 Prompt', 'copy', 'on-accent', 'accent'),
}


def expected_colors(tokens, role, state):
    if state == 'disabled':
        if role in ('Run','StopRun'):
            prefix=ROLES[role][3]
            return tokens[prefix+'_disabled_background'],tokens['on_'+prefix+'_disabled']
        return tokens['disabled_bg'], tokens['disabled_text']
    prefix = ROLES[role][3]
    key = prefix if prefix == 'accent' and state == 'normal' else (
        prefix + ('_background' if state == 'normal' else '_' + state))
    return tokens[key], tokens['on_' + prefix]


class StateButton(QPushButton):
    """Let QA display multiple Qt states without moving the one real pointer."""
    paint_state = None

    def paintEvent(self, event):
        if self.paint_state is None:
            return super().paintEvent(event)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        flags = QStyle.StateFlag
        option.state &= ~(flags.State_MouseOver | flags.State_Sunken | flags.State_HasFocus)
        if self.paint_state in ('hover', 'pressed'):
            option.state |= flags.State_MouseOver
        if self.paint_state == 'pressed':
            option.state |= flags.State_Sunken
            option.state &= ~flags.State_Raised
        painter = QPainter(self)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)


class ActionPanel(QWidget):
    """Static QPushButtons using production QSS roles and vector icon engine."""
    def __init__(self, case):
        super().__init__()
        palette, foundation = CASES[case]
        self.settings = dict(DEFAULT_SETTINGS, visual_palette=palette, ui_size=9)
        self.tokens = visual_tokens(self.settings, foundation=foundation)
        # Custom colors have a validated token API, but intentionally no saved
        # editor yet. Inject only that API result into the production QSS and
        # QPalette builders; do not duplicate any action style declarations.
        with patch('prompt_studio.theme.visual_tokens', return_value=self.tokens):
            self.setStyleSheet(stylesheet(self.settings))
            palette = widget_palette(self.settings)
        self.setPalette(palette)
        APP.setPalette(palette)
        for suffix in ('Accent', 'Run', 'Stop'):
            APP.setProperty('pcsIconOn' + suffix, self.tokens['on_' + suffix.lower()])
        APP.setProperty('pcsIconOnStopDisabled',self.tokens['on_stop_disabled'])
        APP.setProperty('pcsIconOnRunDisabled',self.tokens['on_run_disabled'])
        self.setObjectName('WorkspaceSurface')
        self.setFixedSize(980, 360)
        grid = QGridLayout(self)
        grid.setContentsMargins(26, 24, 26, 24)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(18)
        title = QLabel('執行與取消 · ' + case)
        title.setObjectName('DialogTitle')
        grid.addWidget(title, 0, 0, 1, 5)
        for column, state in enumerate(STATES, 1):
            grid.addWidget(QLabel({'normal': '一般', 'hover': '滑過',
                                  'pressed': '按下', 'disabled': '停用'}[state]), 1, column)
        self.buttons = {}
        for row, (role, (text, glyph, color, _)) in enumerate(ROLES.items(), 2):
            grid.addWidget(QLabel({'Run': '主要執行', 'StopRun': '取消執行',
                                  'Primary': '一般主按鈕'}[role]), row, 0)
            for column, state in enumerate(STATES, 1):
                control = StateButton(text)
                control.setObjectName(role)
                control.setProperty('textButton', True)
                control.setIcon(icon(glyph, color))
                control.setIconSize(QSize(20, 20))
                control.setFixedSize(180, 44)
                grid.addWidget(control, row, column)
                self.buttons[(role, state)] = control
        hint = QLabel('12 px 文字 · 共用語意色派生 · 靜態按鈕，沒有連線或生成操作')
        hint.setObjectName('Subtle')
        grid.addWidget(hint, 5, 0, 1, 5)

    def freeze_states(self):
        """Render Qt style-option states side by side; no per-state color CSS."""
        QTest.mouseMove(self, QPoint(2, 2))
        for (role, state), control in self.buttons.items():
            control.setEnabled(state != 'disabled')
            control.setDown(state == 'pressed')
            control.paint_state = state
            control.update()
        APP.processEvents()


def sampled_background(control):
    image = control.grab().toImage()
    return image.pixelColor(image.width()-18, image.height()//2).name()


class ActionVisualTests(unittest.TestCase):
    def setUp(self):
        self.palette = QPalette(APP.palette())
        self.properties = {key: APP.property(key) for key in
                           ('pcsIconOnAccent', 'pcsIconOnRun', 'pcsIconOnStop','pcsIconOnStopDisabled','pcsIconOnRunDisabled')}
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.close()
        APP.processEvents()
        APP.setPalette(self.palette)
        for key, value in self.properties.items():
            APP.setProperty(key, value)

    def panel(self, case):
        panel = ActionPanel(case)
        self.windows.append(panel)
        panel.show()
        APP.processEvents()
        return panel

    def test_actual_mouse_states_render_action_tokens_and_pressed_wins(self):
        for case in CASES:
            panel = self.panel(case)
            for role in ('Run', 'StopRun'):
                control = panel.buttons[(role, 'normal')]
                with self.subTest(case=case, role=role):
                    QTest.mouseMove(panel, QPoint(2, 2))
                    self.assertEqual(sampled_background(control), expected_colors(panel.tokens, role, 'normal')[0])
                    QTest.mouseMove(control, control.rect().center())
                    self.assertTrue(control.underMouse())
                    self.assertEqual(sampled_background(control), expected_colors(panel.tokens, role, 'hover')[0])
                    QTest.mousePress(control, Qt.MouseButton.LeftButton, pos=control.rect().center())
                    self.assertTrue(control.isDown())
                    self.assertEqual(sampled_background(control), expected_colors(panel.tokens, role, 'pressed')[0])
                    self.assertNotEqual(sampled_background(control), expected_colors(panel.tokens, role, 'hover')[0])
                    QTest.mouseRelease(control, Qt.MouseButton.LeftButton, pos=control.rect().center())
                    control.setEnabled(False)
                    self.assertEqual(sampled_background(control), expected_colors(panel.tokens,role,'disabled')[0])
            panel.close()

    def test_all_states_keep_rendered_text_and_vector_icons_on_their_foreground(self):
        for case in CASES:
            panel = self.panel(case)
            panel.freeze_states()
            for (role, state), control in panel.buttons.items():
                with self.subTest(case=case, role=role, state=state):
                    background, foreground = expected_colors(panel.tokens, role, state)
                    self.assertEqual(sampled_background(control), background)
                    self.assertEqual(control.font().pixelSize(), 12)
                    self.assertGreaterEqual(contrast(background, foreground), 4.5)
                    frame = control.grab().toImage()
                    # Text glyphs are to the right of the centered icon. Sample
                    # that half independently from the vector's own pixmap.
                    text_pixels = [frame.pixelColor(x, y).name()
                                   for x in range(frame.width()//2, frame.width()-24)
                                   for y in range(12, frame.height()-12)]
                    self.assertIn(foreground, text_pixels)
                    mode = QIcon.Mode.Disabled if state == 'disabled' else QIcon.Mode.Normal
                    glyph = control.icon().pixmap(QSize(20, 20), mode).toImage()
                    opaque = [glyph.pixelColor(x, y) for x in range(glyph.width())
                              for y in range(glyph.height()) if glyph.pixelColor(x, y).alpha() >= 250]
                    self.assertTrue(opaque, 'An empty/incorrect icon must fail the paint contract')
                    target = QColor(foreground)
                    self.assertTrue(all(max(abs(c.red()-target.red()), abs(c.green()-target.green()),
                                            abs(c.blue()-target.blue())) <= 1 for c in opaque))
            panel.close()

    def test_primary_fallback_uses_accent_instead_of_execution_colors(self):
        for case in CASES:
            panel = self.panel(case)
            control = panel.buttons[('Primary', 'normal')]
            with self.subTest(case=case):
                QTest.mouseMove(panel, QPoint(2, 2))
                self.assertEqual(sampled_background(control), panel.tokens['accent'])
                self.assertNotEqual(sampled_background(control), panel.tokens['run_background'])
                QTest.mouseMove(control, control.rect().center())
                self.assertEqual(sampled_background(control), panel.tokens['accent_hover'])
                QTest.mousePress(control, Qt.MouseButton.LeftButton, pos=control.rect().center())
                self.assertEqual(sampled_background(control), panel.tokens['accent_pressed'])
                QTest.mouseRelease(control, Qt.MouseButton.LeftButton, pos=control.rect().center())
            panel.close()


if __name__ == '__main__':
    unittest.main()
