"""Read-only version navigation and source-lifetime guards, without an engine."""
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject, QPoint, Signal, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from prompt_calculus_studio.local_ai.revision_dialog import MessageVersionsDialog


class Source(QWidget):
    composing = False
    has_uncommitted_draft = False


class Owner(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.key, self.owner_epoch, self.path = 'doc', 'epoch', Path('test.pcs-chat')
        self._disposed, self.edit_locked = False, False
        self.values = [dict(id='new', number=2, current=True, text='目前訊息 🙂', attachments=[], turn_id='new'),
                       dict(id='old', number=1, current=False, text='原本訊息 👍🏽', attachments=[], turn_id='old')]
        self.payload = {'turns': [{'id': 'new'}]}

    def message_versions(self, turn_id):
        if turn_id != 'new':
            raise ValueError('Missing source')
        return copy.deepcopy(self.values)

    def message_version_count(self, turn_id):
        if turn_id != 'new':
            raise ValueError('Missing source')
        return len(self.values)


class MessageVersionUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.rich = patch('prompt_calculus_studio.local_ai.chat_render.needs_rich', return_value=False)
        self.rich.start()
        self.source = Source(); self.source.resize(900, 700); self.source.show()
        self.owner = Owner()
        self.original = copy.deepcopy(self.owner.values)
        self.dialog = MessageVersionsDialog(self.owner, 'new', self.source)
        self.finished = []
        self.dialog.finished.connect(self.finished.append)
        self.dialog.show(); self.app.processEvents()

    def tearDown(self):
        if not self.finished:
            self.dialog.reject()
        self.source.close(); self.source.deleteLater()
        self.app.processEvents()
        self.rich.stop()

    def test_navigation_current_and_branch_never_restore_source(self):
        branches=[]; self.dialog.branchRequested.connect(lambda *args: branches.append(args))
        self.assertEqual(self.dialog.version_label.text(), '版本 2')
        self.assertFalse(self.dialog.current_button.isEnabled())
        self.dialog.previous_button.click()
        self.assertEqual(self.dialog.preview._content[0], '原本訊息 👍🏽')
        self.assertEqual(self.dialog.version_label.text(), '版本 1')
        self.assertFalse(self.dialog.previous_button.isEnabled())
        self.dialog.current_button.click()
        self.assertEqual(self.dialog.preview._content[0], '目前訊息 🙂')
        self.dialog.previous_button.click(); self.dialog.branch_button.click()
        self.assertEqual(branches, [('new', 'old')])
        self.assertEqual(self.owner.values, self.original)
        self.assertEqual(len(self.finished), 1)

    def test_owner_retarget_dismisses_without_branch(self):
        branches=[]; self.dialog.branchRequested.connect(lambda *args: branches.append(args))
        self.owner.owner_epoch='changed'; self.owner.changed.emit()
        self.assertEqual(len(self.finished), 1)
        self.assertFalse(branches)

    def test_version_change_dismisses_but_ordinary_changed_preserves_preview(self):
        self.dialog.previous_button.click()
        self.owner.changed.emit()
        self.assertFalse(self.finished)
        self.assertEqual(self.dialog.selected_index, 1)
        self.owner.values.pop(); self.owner.changed.emit()
        self.assertEqual(len(self.finished), 1)

    def test_same_version_count_but_new_active_payload_dismisses(self):
        self.owner.payload = copy.deepcopy(self.owner.payload)
        self.owner.changed.emit()
        self.assertEqual(len(self.finished), 1)

    def test_restored_older_current_retains_its_original_version_number(self):
        self.dialog.reject()
        self.owner.values[0]['current'] = False
        self.owner.values[1]['current'] = True
        self.dialog = MessageVersionsDialog(self.owner, 'new', self.source)
        self.finished = []
        self.dialog.finished.connect(self.finished.append)
        self.dialog.show(); self.app.processEvents()
        self.assertEqual(self.dialog.version_label.text(), '版本 1')
        self.assertFalse(self.dialog.current_button.isEnabled())
        self.dialog.next_button.click()
        self.assertEqual(self.dialog.version_label.text(), '版本 2')
        self.dialog.current_button.click()
        self.assertEqual(self.dialog.version_label.text(), '版本 1')

    def test_escape_and_source_hide_dismiss(self):
        QTest.keyClick(self.dialog, Qt.Key.Key_Escape)
        self.assertEqual(len(self.finished), 1)

    def test_outside_click_dismisses(self):
        QTest.mouseClick(self.source, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
        self.assertEqual(len(self.finished), 1)

    def test_small_window_footer_reflows_and_source_hide_closes(self):
        self.dialog.resize(580, 500); self.app.processEvents()
        self.assertTrue(self.dialog._narrow_footer)
        self.assertEqual(self.dialog.footer_layout.getItemPosition(
            self.dialog.footer_layout.indexOf(self.dialog.branch_button))[:2], (1, 0))
        self.assertTrue(self.dialog.rect().contains(self.dialog.branch_button.mapTo(
            self.dialog, self.dialog.branch_button.rect().center())))
        self.source.hide()
        self.assertEqual(len(self.finished), 1)


if __name__ == '__main__':
    unittest.main()
