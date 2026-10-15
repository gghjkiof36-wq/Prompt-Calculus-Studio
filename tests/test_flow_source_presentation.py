"""Read-flow controls keep their scene anchor and appearance without changing rounds."""
import copy
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtWidgets import QApplication, QMenu, QWidget

import test_editor_reorder as fixture
from prompt_calculus_studio import prompt_groups as groups, prompt_materials as pm, prompt_sources as sources
from prompt_calculus_studio.canvas_items import canvas_colors
from prompt_calculus_studio.module_chrome import source_controls_style
from prompt_calculus_studio.widgets import RoundMenu


class FlowSourcePresentationTests(unittest.TestCase):
    setUpClass = classmethod(fixture.EditorReorderTests.setUpClass.__func__)
    tearDown = fixture.EditorReorderTests.tearDown

    def setUp(self):
        fixture.EditorReorderTests.setUp(self)
        self.w.enter_canvas(); self.canvas = self.w.canvas
        root = pm.new('flow', 'Synthetic flow')
        rows = [groups.new_group(str(i), 'synthetic '+str(i)) for i in range(1, 7)]
        root['sequence'].update(groups=rows, start=rows[0]['id'], selected=rows[-1]['id'], mode='increment')
        keys = []
        self.canvas.commit(lambda state: keys.append(sources.add(state, (100, 100), root)))
        self.key = keys[0]; self.card = self.canvas.functions.cards[self.key]; self.panel = self.card.panel
        self.app.processEvents()

    def snapshot(self):
        return copy.deepcopy(sources.definitions(self.w.state)[self.key])

    def desktop_point(self, control, point):
        local = QPointF(control.mapTo(self.panel, point))
        viewport = self.canvas.view.viewportTransform().map(self.card.proxy.sceneTransform().map(local))
        return self.canvas.view.viewport().mapToGlobal(viewport.toPoint())

    def test_mode_popup_anchors_below_scaled_panned_button(self):
        view = self.canvas.view; before = self.snapshot()
        for scale in (.55, 1.0, 1.4):
            with self.subTest(scale=scale):
                view.resetTransform(); view.scale(scale, scale); view.centerOn(430, -170)
                self.app.processEvents()
                button = self.panel.mode
                # Keep the trigger away from screen edges so it needs no clamping.
                scene_point = self.card.proxy.mapToScene(QPointF(button.mapTo(self.panel, QPoint())))
                self.card.setPos(self.card.pos()+view.mapToScene(QPoint(150, 140))-scene_point)
                self.app.processEvents(); opened = []
                def record(menu, position, *unused):
                    opened.append((menu, QPoint(position)))
                with patch.object(RoundMenu, 'open_at', record), patch.object(QMenu, 'exec', record):
                    self.panel.choose_mode()
                self.assertEqual(len(opened), 1)
                menu, position = opened[0]
                try:
                    top = self.desktop_point(button, QPoint())
                    bottom = self.desktop_point(button, QPoint(button.width(), button.height()))
                    self.assertLessEqual(abs(position.x()-top.x()), 1)
                    self.assertLessEqual(abs(position.y()-bottom.y()-4), 1)
                    self.assertIsNone(menu.graphicsProxyWidget())
                    self.assertEqual([a.text() for a in menu.actions() if a.isChecked()], [groups.MODES['increment']])
                finally:
                    menu.deleteLater()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.panel.index.value(), 6)

    def test_theme_refresh_retains_compact_controls_and_sixth_round(self):
        self.panel.index.clearFocus(); before = self.snapshot()
        expected = self.w.styleSheet()+source_controls_style(canvas_colors(self.canvas))
        self.assertTrue(self.panel.styleSheet() == expected, 'Flow controls retain their specialized theme')
        for unused in range(3):
            self.w.apply_theme(preserve_layout=True); self.app.processEvents()
            self.assertTrue(self.panel.styleSheet() == expected, 'Theme refresh must retain the compact pager style')
            self.assertEqual(self.panel.index.value(), 6)
            self.assertEqual(self.panel.index.text(), '6 / 6')
            self.assertEqual(self.snapshot(), before)

    def test_activation_and_style_refresh_preserve_pending_pager_input(self):
        self.panel.index.setKeyboardTracking(False)
        self.panel.index.setFocus(Qt.FocusReason.OtherFocusReason)
        self.panel.index.lineEdit().setText('4 / 6')
        before = self.snapshot(); expected = self.panel.styleSheet()
        QApplication.sendEvent(self.w, QEvent(QEvent.Type.WindowDeactivate))
        QApplication.sendEvent(self.w, QEvent(QEvent.Type.WindowActivate))
        recovery = self.w.display_recovery
        recovery.application_state(Qt.ApplicationState.ApplicationActive)
        self.assertTrue(recovery.timer.isActive())
        recovery.timer.stop(); recovery.refresh(); self.app.processEvents()
        self.assertTrue(self.panel.styleSheet() == expected, 'Display recovery must retain the compact pager style')
        self.assertEqual(self.panel.index.lineEdit().text(), '4 / 6')
        self.assertEqual(self.panel.index.value(), 6)
        self.assertEqual(self.snapshot(), before)
        self.panel.index.lineEdit().setText('6 / 6')

    def test_unowned_proxy_still_gets_global_theme_without_touching_source(self):
        panel = QWidget(); panel.setStyleSheet('QWidget { color: red; }')
        proxy = self.canvas.view.scene().addWidget(panel)
        before = self.snapshot()
        try:
            self.canvas.refresh_visual_theme()
            self.assertTrue(panel.styleSheet() == self.w.styleSheet(), 'An unowned proxy still gets the global theme')
            self.assertTrue(self.panel.styleSheet() == self.w.styleSheet()+source_controls_style(canvas_colors(self.canvas)),
                            'A card-owned proxy must retain its specialized controls')
            self.assertEqual(self.snapshot(), before)
        finally:
            self.canvas.view.scene().removeItem(proxy); proxy.setWidget(None); panel.deleteLater()


if __name__ == '__main__': unittest.main()
