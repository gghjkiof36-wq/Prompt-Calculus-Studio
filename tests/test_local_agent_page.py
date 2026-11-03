"""Project-first pages, real controls and persistence; no inference engine."""
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QPushButton

from prompt_calculus_studio.local_agents.store import AgentStore
from prompt_calculus_studio.local_ai.service import for_workspace
from prompt_calculus_studio.local_documents import chat, io
from test_local_agents import make_skill
from test_work_resources_page import ResourceWindowTests


@unittest.skipUnless(os.name == 'nt', 'Windows writer and reader')
class AgentWindowTests(unittest.TestCase):
    setUpClass = classmethod(ResourceWindowTests.setUpClass.__func__)
    setUp = ResourceWindowTests.setUp
    tearDown = ResourceWindowTests.tearDown
    open_shell = ResourceWindowTests.open_shell
    close_shell = ResourceWindowTests.close_shell
    wait = ResourceWindowTests.wait
    idle = ResourceWindowTests.idle
    click = ResourceWindowTests.click
    picture = ResourceWindowTests.picture

    def button(self, parent, title):
        return next(b for b in parent.findChildren(QPushButton) if b.text() == title)

    def mode(self, kind):
        self.w.open_route('explore', {'skill':'local_skills', 'agent':'local_agents', 'files':'work_resources'}[kind])
        self.app.processEvents()
        return self.page.agent_library

    def model(self):
        path = self.base/'fixture.gguf'
        path.write_bytes(b'GGUF'+(3).to_bytes(4, 'little'))
        return for_workspace(self.w).register_model(path)

    def select(self, library, identity):
        row = next(library.results.topLevelItem(i) for i in range(library.results.topLevelItemCount())
                   if library.results.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == identity)
        library.results.setCurrentItem(row)
        self.app.processEvents()

    def skill_idle(self, detail):
        self.wait(lambda: not self.page.jobs.active and detail.pending is None and not detail.read_timer.isActive())

    def test_context_sidebar_history_search_and_narrow_layout(self):
        from prompt_calculus_studio.settings_page import navigation_entries
        self.assertEqual([r[0] for r in navigation_entries('work_resources', workspace=True)[2]],
                         ['work_resources', 'local_skills', 'local_agents'])
        self.assertNotIn('local_skills', [r[0] for r in navigation_entries('explore', workspace=True)[2]])
        self.page.filter.setText('河岸'); self.idle()
        library = self.mode('skill')
        self.assertIs(self.page.filter.parentWidget(), library.list_panel)
        self.page.filter.setText('saving')
        self.mode('agent')
        self.assertEqual(self.page.title.text(), '專案')
        self.w.go(-1); self.app.processEvents()
        self.assertEqual(self.page.mode, 'skill')
        self.assertEqual(self.page.filter.text(), 'saving')
        self.w.go(1); self.app.processEvents()
        self.assertEqual(self.page.mode, 'agent')
        self.mode('files'); self.idle()
        self.assertEqual(self.page.title.text(), '檔案')
        self.assertEqual(self.page.filter.text(), '河岸')
        self.assertTrue(library.back_button.isHidden())
        self.assertIs(self.page.filter.parentWidget(), self.page.content)
        self.assertTrue(self.page.filter.isVisible())
        self.w.utility.settings_page.return_parent(); self.app.processEvents()
        self.assertEqual(self.w.utility.settings_page.navigation_area.heading.text(), '探索')
        self.assertIsNone(for_workspace(self.w)._worker)

    def test_project_first_agent_drafts_shared_sources_and_chat_reopen(self):
        store = AgentStore(self.w.data)
        skill = store.add_skill(make_skill(self.base/'skill'))
        model = self.model()
        library = self.mode('agent')
        self.click(self.page.add_button)
        project_editor = library.editor
        project_editor.name.setText('測試工作區'); project_editor.cwd.setText(str(self.root))
        project_editor.sources.add_row(str(self.docs/'河岸.md'), 'reference')
        self.click(project_editor.save_button)
        project = AgentStore(self.w.data).value['projects'][0]
        self.assertEqual(AgentStore(self.w.data).value['agents'], [])
        self.assertEqual(library.project_id, project['id'])
        self.click(self.page.add_button)
        editor = library.editor
        editor.name.setText('本地檢查')
        editor.model.setCurrentIndex(editor.model.findData(model['id']))
        skill_row = editor.skills.topLevelItem(0)
        skill_rect = editor.skills.visualItemRect(skill_row)
        QTest.mouseClick(editor.skills.viewport(), Qt.MouseButton.LeftButton, pos=skill_rect.center())
        self.assertEqual(editor.selected, [skill['id']])
        editor.skills.setFocus()
        QTest.keyClick(editor.skills, Qt.Key.Key_Space)
        self.assertEqual(editor.selected, [])
        QTest.keyClick(editor.skills, Qt.Key.Key_Space)
        self.assertEqual(editor.selected, [skill['id']])
        editor.skill_filter.setText('other')
        self.assertTrue(editor.skills.topLevelItem(0).isHidden())
        self.click(editor.save_button)
        agent = AgentStore(self.w.data).value['agents'][0]
        self.assertEqual(agent['skills'], [skill['id']])
        self.assertEqual(AgentStore(self.w.data).get('projects', project['id']), project)
        editor.name.setText('未保存草稿')
        with patch('prompt_calculus_studio.local_agents.editors.confirm_draft', return_value='cancelled'):
            self.mode('skill')
            self.assertEqual(self.page.mode, 'agent')
            self.assertIs(library.editor, editor)
            library.back()
            self.assertEqual(library.project_id, project['id'])
        with patch('prompt_calculus_studio.local_agents.editors.confirm_draft', return_value='discarded'):
            self.mode('skill')
        self.assertEqual(AgentStore(self.w.data).get('agents', agent['id'])['name'], '本地檢查')
        result = io.create(self.root, '工作.pcs-chat', chat.empty())
        self.assertEqual(result.status, 'saved', result.error)
        page = self.w.open_path(self.root/'工作.pcs-chat').widget
        page.open_agent_settings(); self.app.processEvents()
        binding = page._agent_dialog
        binding.agents.setCurrentIndex(binding.agents.findData(agent['id']))
        self.click(self.button(binding, '套用'))
        self.assertEqual(page.chat_document.payload['agent_id'], agent['id'])
        self.click(page.more_button)
        menu = page._actions_menu
        submenu = next(a.menu() for a in menu.actions() if a.text() == '指定 Skill')
        submenu.actions()[0].trigger(); menu.close()
        self.assertEqual(page.composer.toPlainText(), '@saving ')
        page.composer.insertPlainText('請核對保存流程')
        self.w.save_current()
        self.wait(lambda: not page.chat_document.dirty and not page.chat_document.draft_dirty)
        before = io.load(self.root, '工作.pcs-chat').document['payload']
        self.close_shell(); self.open_shell()
        page = self.w.open_path(self.root/'工作.pcs-chat').widget
        self.assertEqual(page.chat_document.payload, before)
        self.assertIsNone(for_workspace(self.w)._worker)

    def test_skill_selected_only_reads_copy_edit_and_stale_completion(self):
        store = AgentStore(self.w.data)
        original_path = make_skill(self.base/'skill')
        original = (original_path/'SKILL.md').read_bytes()
        first = store.add_skill(original_path)
        second = store.add_skill(make_skill(self.base/'second', 'other'))
        from prompt_calculus_studio.local_agents import skill_details as module
        entered, release = threading.Event(), threading.Event()
        actual = module.read_bytes
        def slow(path, *args):
            if Path(path).parent == original_path:
                entered.set(); release.wait(5)
            return actual(path, *args)
        with patch.object(module, 'read_bytes', side_effect=slow) as read:
            library = self.mode('skill'); detail = library.skill_details
            self.assertEqual(read.call_count, 0)
            self.select(library, first['id'])
            self.wait(entered.is_set)
            self.select(library, second['id'])
            release.set(); self.skill_idle(detail)
            self.assertEqual(detail.skill['id'], second['id'])
            self.assertEqual(detail.path.parent, Path(second['root']))
            self.assertEqual(read.call_count, 2)
        self.select(library, first['id']); self.skill_idle(detail)
        library.copy_skill(first['source']); self.wait(lambda: not self.page.jobs.active)
        managed = next(s for s in AgentStore(self.w.data).value['skills'] if s['managed'])
        self.select(library, managed['id']); self.skill_idle(detail)
        self.assertEqual(detail.pages.currentIndex(), 0)
        self.click(detail.read_button)
        self.click(detail.edit_button)
        self.wait(lambda: not detail.editor.buffer.opening) if hasattr(detail.editor, 'buffer') else None
        detail.editor.md_surface.editor.appendPlainText('\nKeep source unchanged.')
        with patch('prompt_calculus_studio.local_agents.skill_details.confirm_draft', return_value='cancelled'):
            self.select(library, first['id'])
            self.assertEqual(library.selected, managed['id'])
            self.assertTrue(detail.dirty)
        self.click(detail.save_button); self.skill_idle(detail)
        self.assertEqual((original_path/'SKILL.md').read_bytes(), original)
        self.assertIn('Keep source unchanged.', (Path(managed['root'])/'SKILL.md').read_text(encoding='utf8'))
        QTest.mouseClick(detail.enabled, Qt.MouseButton.LeftButton, pos=QPoint(10, detail.enabled.height()//2))
        self.assertFalse(AgentStore(self.w.data).get('skills', managed['id'])['enabled'])

    def test_new_project_validation_and_agent_conflict_keep_draft(self):
        library = self.mode('agent'); self.click(self.page.add_button)
        self.click(library.editor.save_button)
        self.assertTrue(library.editor.message.text())
        self.assertEqual(AgentStore(self.w.data).value['projects'], [])
        self.click(library.editor.cancel_button)
        store = AgentStore(self.w.data)
        project = store.save_project('Project A', self.root, [])
        agent = store.save_agent('A', project['id'], self.model()['id'], [])
        library.open_agent(agent['id']); editor = library.editor
        editor.name.setText('My draft')
        AgentStore(self.w.data).save_agent('Elsewhere', project['id'], agent['model'], [], agent['id'])
        self.click(editor.save_button)
        self.assertIn('其他頁面', editor.message.text())
        self.assertTrue(editor.dirty)
        self.assertEqual(editor.name.text(), 'My draft')
        self.assertEqual(AgentStore(self.w.data).get('agents', agent['id'])['name'], 'Elsewhere')
        with patch('prompt_calculus_studio.local_agents.editors.confirm_draft', return_value='discarded'):
            library.back()


if __name__ == '__main__':
    unittest.main()
