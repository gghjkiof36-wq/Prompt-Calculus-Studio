"""Approval preference UI never executes tools or changes text-generation state."""
import unittest
import tempfile
from unittest.mock import patch
from PySide6.QtCore import QObject, Signal, Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from prompt_calculus_studio.local_ai.approval_picker import ApprovalButton, ApprovalMenu
from prompt_calculus_studio.theme import visual_tokens
from prompt_calculus_studio.local_ai.service import LocalAIService


class Service(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.state, self.approval_mode, self.error = 'unloaded', 'ask', ''
        self.reject = False
        self.calls = []

    def configure_approval_mode(self, mode):
        self.calls.append(mode)
        if self.reject:
            self.error = '無法儲存設定。'
            return False
        self.approval_mode = mode
        self.changed.emit()
        return True


class ApprovalPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.service = Service()
        self.window = QWidget()
        self.window.resize(500, 400)
        self.control = ApprovalButton(self.service)
        layout = QVBoxLayout(self.window)
        layout.addWidget(self.control)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_keyboard_selection_and_reopen_checkmark_do_not_start_generation(self):
        menu = ApprovalMenu(self.control)
        menu.open_for(self.control)
        self.app.processEvents()
        self.assertEqual(menu.activeAction().data(), 'ask')
        QTest.keyClick(menu, Qt.Key.Key_Down)
        QTest.keyClick(menu, Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertEqual(self.service.calls, ['auto'])
        self.assertEqual(self.control.mode, 'auto')
        self.assertEqual(self.service.state, 'unloaded')
        reopened = ApprovalMenu(self.control)
        self.assertEqual([a.data() for a, _ in reopened.entries if a.isChecked()], ['auto'])
        reopened.close()
        reopened.deleteLater()

    def test_failed_save_retains_mode_and_reports_error(self):
        errors = []
        self.control.errorOccurred.connect(errors.append)
        self.service.reject = True
        self.control.choose('all')
        self.assertEqual(self.control.mode, 'ask')
        self.assertEqual(errors, ['無法儲存設定。'])

    def test_real_service_persistence_error_reaches_control_without_exception(self):
        with tempfile.TemporaryDirectory() as folder:
            service = LocalAIService(folder)
            control = ApprovalButton(service, self.window)
            errors = []
            control.errorOccurred.connect(errors.append)
            with patch.object(service._registry, 'update', side_effect=OSError('write denied')):
                control.choose('all')
            self.assertEqual(control.mode, 'ask')
            self.assertEqual(errors, [service.error])
            self.assertIn('write denied', errors[0])
            self.assertIsNone(service._worker)
            service.shutdown()
            control.deleteLater()
            service.deleteLater()
            self.app.processEvents()

    def test_state_theme_and_narrow_control_keep_accessible_mode(self):
        self.control.choose('all')
        tokens = visual_tokens({'visual_palette': 'paper'})
        self.control.apply_appearance(tokens)
        self.assertIn(tokens['permission_full'], self.control.styleSheet())
        self.control.setFixedWidth(36)
        self.app.processEvents()
        self.assertEqual(self.control.text(), '')
        self.assertIn('全部允許', self.control.accessibleName())
        self.service.state = 'closed'
        self.service.changed.emit()
        self.assertFalse(self.control.isEnabled())

    def test_popup_tracks_trigger_above_and_falls_below_near_top_edge(self):
        self.window.resize(620, 700)
        self.window.move(12, 12)
        self.window.layout().removeWidget(self.control)
        self.control.setGeometry(70, 590, 120, 36)
        self.app.processEvents()
        top = self.control.mapToGlobal(QPoint(0, 0))
        menu = ApprovalMenu(self.control)
        menu.open_for(self.control)
        self.app.processEvents()
        self.assertLessEqual(abs(menu.x() - top.x()), 2)
        self.assertLessEqual(abs(menu.y() + menu.height() + 8 - top.y()), 2)
        menu.close()
        self.app.processEvents()
        self.control.move(70, 12)
        self.app.processEvents()
        bottom = self.control.mapToGlobal(QPoint(0, self.control.height()))
        menu = ApprovalMenu(self.control)
        menu.open_for(self.control)
        self.app.processEvents()
        self.assertLessEqual(abs(menu.y() - bottom.y() - 8), 2)
        menu.close()
