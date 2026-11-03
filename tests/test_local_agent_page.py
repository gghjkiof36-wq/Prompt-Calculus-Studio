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
        self.w.open_route('explore', {'skill':'local_skills', 'agent':'local_agents'}[kind])
        self.app.processEvents()
        return self.page.agent_library

    def test_direct_sidebar_routes_history_and_independent_search(self):
        from prompt_calculus_studio.settings_page import navigation_entries
        routes = navigation_entries('work_resources', workspace=True)
        # The existing sidebar is the entry, and all three share one lazy controller.
        self.assertTrue(all(any(row[0] == key for row in routes[2])
                            for key in ('work_resources', 'local_skills', 'local_agents')))
        self.page.filter.setText('河岸')
        self.idle()
        self.mode('skill')
        self.assertEqual(self.page.title.text(), 'Skill')
        self.assertEqual(self.page.filter.text(), '')
        self.page.filter.setText('saving')
        library = self.mode('agent')
        self.assertEqual(self.page.title.text(), 'Agent')
        self.assertEqual(self.page.filter.text(), '')
        self.w.go(-1)
        self.app.processEvents()
        self.assertEqual(self.w.utility.settings_page.section, 'local_skills')
        self.assertEqual(self.page.mode, 'skill')
        self.assertEqual(self.page.filter.text(), 'saving')
        self.assertIs(self.page.agent_library, library)
        self.w.go(1)
        self.app.processEvents()
        self.assertEqual(self.page.mode, 'agent')
        self.w.open_route('explore', 'work_resources')
        self.idle()
        self.assertEqual(self.page.filter.text(), '河岸')
        self.assertEqual(self.page.title.text(), '工作資源')
        self.assertIs(self.page, self.w.utility.settings_page.resources_content.load())
        self.assertIsNone(for_workspace(self.w)._worker)

    def test_project_drafts_cancel_and_filtered_skill_selection(self):
        store = AgentStore(self.w.data)
        skill = store.add_skill(make_skill(self.base/'skill'))
        store.add_skill(make_skill(self.base/'other-skill', 'other'))
        project = store.save_project('Project A', self.root, [])
        second = store.save_project('Project B', self.docs, [])
        service = for_workspace(self.w)
        model_path = self.base/'fixture.gguf'
        model_path.write_bytes(b'GGUF'+(3).to_bytes(4, 'little'))
        model = service.register_model(model_path)
        agent = store.save_agent('A', project['id'], model['id'], [skill['id']])
        store.save_agent('Shared', project['id'], model['id'], [])
        before = store.path.read_bytes()
        library = self.mode('agent')
        library.open_agent(agent['id']); self.app.processEvents()
        dialog = library.dialog
        self.assertFalse(dialog.project_details.isVisible())
        self.assertIn('另 1 個 Agent', dialog.project_sharing.text())
        self.click(dialog.edit_project)
        dialog.project_name.setText('Draft A')
        dialog.add_source_row(str(self.docs/'河岸.md'), 'reference')
        dialog.project.setCurrentIndex(dialog.project.findData(second['id']))
        self.assertEqual(dialog.project_name.text(), 'Project B')
        self.assertEqual(dialog.sources.topLevelItemCount(), 0)
        self.assertEqual(dialog.project_sharing.text(), '')
        dialog.project.setCurrentIndex(dialog.project.findData(project['id']))
        self.assertEqual(dialog.project_name.text(), 'Draft A')
        self.assertEqual(dialog.sources.topLevelItemCount(), 1)
        dialog.reject()
        self.assertEqual(store.path.read_bytes(), before)
        library.open_agent(agent['id']); self.app.processEvents()
        dialog = library.dialog
        dialog.skill_filter.setText('other')
        self.assertTrue(dialog.skills.item(0).isHidden())
        self.assertEqual(dialog.skills.item(0).checkState(), Qt.CheckState.Checked)
        self.click(self.button(dialog, '儲存'))
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted, dialog.message.text())
        fresh = AgentStore(self.w.data)
        self.assertEqual(fresh.get('agents', agent['id'])['skills'], [skill['id']])
        self.assertEqual(fresh.get('projects', project['id'])['name'], 'Project A')
        self.assertIsNone(service._worker)

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
