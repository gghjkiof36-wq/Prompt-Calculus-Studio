"""Shared workspace controls against the real, isolated desktop Window."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from prompt_calculus_studio import drafts, materials, workspace_scene
from prompt_calculus_studio.core import DEFAULT_SETTINGS
from prompt_calculus_studio.extension_page import ExtensionPage
from prompt_calculus_studio.material_editor_browser import PromptEditor
from prompt_calculus_studio.recent_sidebar import CanvasWorkspaceBar, CanvasWorkspaceCombo
from prompt_calculus_studio.window import Window

APP = QApplication.instance() or QApplication([])


class WorkspaceBarRev9Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for context in (patch.dict(os.environ, PROMPT_STUDIO_V08='1'),
                        patch.dict(DEFAULT_SETTINGS, online=False, comfy_enabled=False, reduce_motion=True),
                        patch.object(ExtensionPage, 'startup')):
            context.start(); self.addCleanup(context.stop)
        self.w = Window(self.temp.name)
        self.w.comfy.timer.stop(); self.w.comfy.enabled = False
        self.w.state['settings'].update(online=False, comfy_enabled=False, material='solid', reduce_motion=True)
        self.w.notice = lambda message: None
        self.w.resize(1440, 900); self.w.show(); self.w.set_interface_mode('list')
        QTest.qWait(15)
        self.page = self.w.prompt_editor

    def tearDown(self):
        popup = APP.activePopupWidget()
        if popup is not None: popup.close()
        if self.page.active_dialog is not None: self.page.active_dialog.reject()
        self.w.close(); APP.processEvents()

    def test_canvas_and_editor_share_real_header_and_mode_navigation(self):
        w = self.w; page = self.page
        self.assertIsInstance(page.workspace_bar, CanvasWorkspaceBar)
        self.assertIsInstance(page.workspace_combo, CanvasWorkspaceCombo)
        self.assertEqual(page.workspace_header.objectName(), w.canvas_header.objectName())
        self.assertEqual(page.workspace_header.styleSheet(), w.canvas_header.styleSheet())
        self.assertEqual(page.workspace_bar.objectName(), w.canvas_workspace_bar.objectName())
        self.assertEqual(page.workspace_combo.currentData(), w.state['workspace'])
        self.assertEqual(page.workspace_combo.currentText(), w.canvas_workspace.currentText())
        self.assertTrue(page.workspace_combo.isEnabled())
        self.assertTrue(page.workspace_bar.menu_button.isEnabled())
        self.assertFalse(page.workspace_bar.menu_button.icon().isNull())
        self.assertTrue(page.workspace_bar.history.isEnabled())
        for bar in (page.workspace_bar, w.canvas_workspace_bar):
            self.assertNotIn('素材', [control.text() for control in bar.findChildren(QPushButton)])
        self.assertTrue(page.workspace_switch.editor_button.isChecked())
        QTest.mouseClick(page.workspace_switch.canvas_button, Qt.MouseButton.LeftButton)
        APP.processEvents()
        self.assertIs(w.surface_stack.currentWidget(), w.canvas_shell)
        self.assertTrue(w.canvas_workspace_bar.workspace_switch.canvas_button.isChecked())
        QTest.mouseClick(w.canvas_workspace_bar.workspace_switch.editor_button, Qt.MouseButton.LeftButton)
        APP.processEvents()
        self.assertIs(w.surface_stack.currentWidget(), page)
        self.assertTrue(page.workspace_switch.editor_button.isChecked())

    def test_editor_workspace_switch_preserves_empty_manual_draft_list_draft_and_history(self):
        w = self.w; first = w.state['workspace']
        output_id = w.state['multi_output']['current_output']
        list_draft = dict(draft='我的清單手動草稿', draft_base='原清單內容')
        w.state['multi_output']['outputs'][output_id]['list_draft'] = copy.deepcopy(list_draft)
        self.assertTrue(w.canvas.commit(lambda state: drafts.edit(state, ''), label='保留空白手動稿'))
        first_history = copy.deepcopy(w.canvas.undo_stack)
        workspace_scene.upgrade(w.state)
        second = workspace_scene.create(w.state, '另一個工作區')
        w.refresh_workspaces()
        self.page.workspace_combo.setCurrentIndex(self.page.workspace_combo.findData(second))
        APP.processEvents()
        self.assertEqual(w.state['workspace'], second)
        self.assertEqual(w.canvas.undo_stack, [])
        self.assertTrue(w.canvas.commit(lambda state: drafts.edit(state, '第二份手動草稿'), label='另一份草稿'))
        second_history = copy.deepcopy(w.canvas.undo_stack)
        self.page.workspace_combo.setCurrentIndex(self.page.workspace_combo.findData(first))
        APP.processEvents()
        self.assertEqual(w.state['draft'], '')
        self.assertEqual(w.state['multi_output']['outputs'][output_id]['list_draft'], list_draft)
        self.assertEqual(w.canvas.undo_stack, first_history)
        self.assertEqual(w.canvas.redo_stack, [])
        self.assertEqual(w.canvas_workspace.currentData(), first)
        self.assertEqual(w.workspace.currentData(), first)
        w.canvas.undo(); self.assertIsNone(w.state['draft'])
        w.canvas.redo(); self.assertEqual(w.state['draft'], '')
        w.set_interface_mode('canvas')
        w.canvas_workspace.setCurrentIndex(w.canvas_workspace.findData(second))
        APP.processEvents()
        self.assertEqual(w.state['workspace'], second)
        self.assertEqual(w.state['draft'], '第二份手動草稿')
        self.assertEqual(w.canvas.undo_stack, second_history)
        self.assertEqual(self.page.workspace_combo.currentData(), second)

    def test_rejected_workspace_switch_restores_combo_without_mutating_draft_or_history(self):
        w = self.w
        workspace_scene.upgrade(w.state)
        second = workspace_scene.create(w.state, '尚不能切換')
        w.refresh_workspaces()
        before = copy.deepcopy(w.state); history = copy.deepcopy((w.canvas.undo_stack, w.canvas.redo_stack))
        with patch.object(w, 'flush_editor_drafts', return_value=False) as flush:
            self.page.workspace_combo.setCurrentIndex(self.page.workspace_combo.findData(second))
        flush.assert_called_once_with()
        self.assertEqual(w.state, before)
        self.assertEqual((w.canvas.undo_stack, w.canvas.redo_stack), history)
        for combo in (self.page.workspace_combo, w.canvas_workspace, w.workspace):
            self.assertEqual(combo.currentData(), before['workspace'])

    def test_workspace_actions_use_existing_menu_and_recent_overlay(self):
        with patch('prompt_calculus_studio.window.WorkspaceDialog.exec', return_value=0) as settings:
            QTest.mouseClick(self.page.workspace_bar.menu_button, Qt.MouseButton.LeftButton)
            APP.processEvents(); menu = APP.activePopupWidget()
            self.assertIsNotNone(menu)
            self.assertEqual([action.text() for action in menu.actions() if not action.isSeparator()],
                             ['工作區設定', '新增預設流程工作區…', '複製目前工作區…', '刪除目前工作區…'])
            menu.close(); menu.actions()[0].trigger(); settings.assert_called_once_with()
        QTest.mouseClick(self.page.workspace_bar.history, Qt.MouseButton.LeftButton)
        APP.processEvents()
        self.assertIsNotNone(self.w.recent_sheet)
        self.assertTrue(self.w.recent_sheet.isVisible())
        self.w.close_recent_sheet()

    def test_visible_browser_refreshes_materials_after_commit_undo_and_redo(self):
        ident = self.w.state['items'][0]['id']; self.page.set_browser_category(None)
        original = materials.item(self.w.state, ident)['name']
        def displayed_name():
            entry = next(self.page.listing.item(i) for i in range(self.page.listing.count())
                         if self.page.listing.item(i).data(Qt.ItemDataRole.UserRole) == ident)
            return entry.text().partition('\n')[0]
        self.assertEqual(displayed_name(), original)
        def rename(state):
            value = copy.deepcopy(materials.item(state, ident)); value['name'] = '已更新的模組'
            materials.save(state, value)
        self.assertTrue(self.w.canvas.commit(rename, label='更新模組名稱'))
        self.assertEqual(displayed_name(), '已更新的模組')
        self.w.canvas.undo(); self.assertEqual(displayed_name(), original)
        self.w.canvas.redo(); self.assertEqual(displayed_name(), '已更新的模組')

    def test_minimal_widget_host_disables_missing_workspace_capabilities(self):
        host = QWidget(); host.state = copy.deepcopy(self.w.state)
        page = PromptEditor(host); page.resize(1024, 768); page.show()
        try:
            APP.processEvents(); page.refresh()
            self.assertEqual(page.workspace_combo.currentData(), host.state['workspace'])
            self.assertFalse(page.workspace_combo.isEnabled())
            self.assertFalse(page.workspace_bar.menu_button.isEnabled())
            self.assertFalse(page.workspace_bar.history.isEnabled())
        finally:
            page.close(); page.deleteLater(); host.close(); APP.processEvents()


if __name__ == '__main__': unittest.main()
