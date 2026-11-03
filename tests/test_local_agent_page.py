"""Native-capable controls and persistence; never starts an inference engine."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton

from prompt_calculus_studio.local_agents.store import AgentStore
from prompt_calculus_studio.local_ai.service import for_workspace
from prompt_calculus_studio.local_documents import chat, io
from test_local_agents import make_skill
from test_work_resources_page import ResourceWindowTests


@unittest.skipUnless(os.name == 'nt', 'Windows writer and reader')
class AgentWindowTests(unittest.TestCase):
    # Reuse the existing isolated workspace/window fixture, not its test cases.
    setUpClass = classmethod(ResourceWindowTests.setUpClass.__func__)
    setUp = ResourceWindowTests.setUp
    tearDown = ResourceWindowTests.tearDown
    open_shell = ResourceWindowTests.open_shell
    close_shell = ResourceWindowTests.close_shell
    wait = ResourceWindowTests.wait
    idle = ResourceWindowTests.idle
    click = ResourceWindowTests.click
    picture = ResourceWindowTests.picture

    def button(self, dialog, title):
        return next(b for b in dialog.findChildren(QPushButton) if b.text() == title)

    def mode(self, kind):
        self.page.mode.setCurrentIndex(self.page.mode.findData(kind))
        self.app.processEvents()
        return self.page.agent_library

    def test_skill_role_binding_menu_draft_and_restart(self):
        source = make_skill(self.base/'skill')
        library = self.mode('skill')
        with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(source)):
            self.click(self.page.add_button)
        self.wait(lambda: not self.page.jobs.active)
        self.assertFalse(library.message.text(), library.message.text())
        skill = AgentStore(self.w.data).value['skills'][0]
        original = (source/'SKILL.md').read_bytes()
        library.results.setCurrentItem(library.results.topLevelItem(0))
        QTest.keyClick(library.results, Qt.Key.Key_Return)
        self.app.processEvents()
        dialog = library.dialog
        QTest.mouseClick(dialog.enabled, Qt.MouseButton.LeftButton, pos=QPoint(10, dialog.enabled.height() // 2))
        self.assertFalse(AgentStore(self.w.data).get('skills', skill['id'])['enabled'])
        QTest.mouseClick(dialog.enabled, Qt.MouseButton.LeftButton, pos=QPoint(10, dialog.enabled.height() // 2))
        self.click(self.button(dialog, '建立管理副本'))
        self.wait(lambda: not self.page.jobs.active)
        copy = next(s for s in AgentStore(self.w.data).value['skills'] if s['managed'])
        row = next(library.results.topLevelItem(i) for i in range(library.results.topLevelItemCount())
                   if library.results.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == copy['id'])
        library.results.setCurrentItem(row)
        QTest.keyClick(library.results, Qt.Key.Key_Return)
        self.app.processEvents()
        dialog = library.dialog
        self.click(self.button(dialog, '閱讀 Skill'))
        self.wait(lambda: not dialog.reader.editor.buffer.opening)
        reader = dialog.reader
        self.click(self.button(reader, '編輯副本'))
        reader.surface.editor.appendPlainText('\nKeep source unchanged.')
        self.click(self.button(reader, '儲存'))
        self.assertEqual(reader.feedback.text(), '已儲存')
        reader.reject(); dialog.reject()
        self.assertEqual((source/'SKILL.md').read_bytes(), original)
        self.assertIn('Keep source unchanged.', (Path(copy['source'])/'SKILL.md').read_text(encoding='utf8'))
        service = for_workspace(self.w)
        model_path = self.base/'fixture.gguf'
        model_path.write_bytes(b'GGUF'+(3).to_bytes(4, 'little'))
        model = service.register_model(model_path)
        self.assertIsNotNone(model, service.error)
        self.mode('agent')
        self.click(self.page.add_button)
        agent_dialog = library.dialog
        agent_dialog.name.setText('本地檢查')
        agent_dialog.project_name.setText('測試工作區')
        with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(self.root)):
            self.click(self.button(agent_dialog, '選擇…'))
        agent_dialog.model.setCurrentIndex(agent_dialog.model.findData(model['id']))
        agent_dialog.skills.item(0).setCheckState(Qt.CheckState.Checked)
        self.click(self.button(agent_dialog, '儲存'))
        self.assertEqual(agent_dialog.result(), agent_dialog.DialogCode.Accepted, agent_dialog.message.text())
        agent = AgentStore(self.w.data).value['agents'][0]
        result = io.create(self.root, '工作.pcs-chat', chat.empty())
        self.assertEqual(result.status, 'saved', result.error)
        page = self.w.open_path(self.root/'工作.pcs-chat').widget
        page.open_agent_settings()
        self.app.processEvents()
        binding = page._agent_dialog
        binding.agents.setCurrentIndex(binding.agents.findData(agent['id']))
        self.click(self.button(binding, '套用'))
        self.assertEqual(page.chat_document.payload['agent_id'], agent['id'])
        self.click(page.more_button)
        menu = page._actions_menu
        submenu = next(a.menu() for a in menu.actions() if a.text() == '指定 Skill')
        action = submenu.actions()[0]
        # QAction delivery is the same connection used by the native submenu.
        action.trigger(); menu.close()
        self.assertEqual(page.composer.toPlainText(), '@saving ')
        page.composer.insertPlainText('請核對保存流程')
        self.w.save_current()
        self.wait(lambda: not page.chat_document.dirty and not page.chat_document.draft_dirty)
        self.picture('agent-bound-chat')
        before = io.load(self.root, '工作.pcs-chat').document['payload']
        self.assertEqual(before['draft'], '@saving 請核對保存流程')
        self.close_shell()
        self.open_shell()
        page = self.w.open_path(self.root/'工作.pcs-chat').widget
        self.assertEqual(page.chat_document.payload, before)
        store = AgentStore(self.w.data)
        self.assertTrue(store.get('skills', skill['id'])['enabled'])
        self.assertEqual(store.get('agents', agent['id'])['skills'], [skill['id']])
        self.assertIsNone(for_workspace(self.w)._worker)

    def test_empty_and_invalid_skill_are_actionable_and_do_not_register(self):
        library = self.mode('skill')
        self.assertEqual(library.message.text(), '尚未加入 Skill')
        invalid = self.base/'invalid'; invalid.mkdir()
        with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(invalid)):
            self.click(self.page.add_button)
        self.wait(lambda: not self.page.jobs.active)
        self.assertIn('沒有 SKILL.md', library.message.text())
        self.assertEqual(AgentStore(self.w.data).value['skills'], [])
        self.mode('agent')
        self.click(self.page.add_button)
        dialog = library.dialog
        self.click(self.button(dialog, '儲存'))
        self.assertIn('名稱並選擇本地模型', dialog.message.text())
        dialog.reject()
        self.assertEqual(AgentStore(self.w.data).value['agents'], [])


if __name__ == '__main__':
    unittest.main()
