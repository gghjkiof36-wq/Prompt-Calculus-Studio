"""The optional decoration preserves editing, input, preferences and idle work."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtGui import QFont, QFontDatabase
import test_workspace_panes as fixture
from prompt_calculus_studio.workspace_shell import WorkspaceShell


class WorkspaceAmbientTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def setUp(self):
        self.motion = patch('prompt_calculus_studio.workspace_ambient.system_motion_enabled', return_value=True)
        self.motion.start()
        fixture.WorkspacePaneTests.setUp(self)
        self.w.activateWindow()
        self.events()

    def tearDown(self):
        fixture.WorkspacePaneTests.tearDown(self)
        self.motion.stop()

    def test_default_is_idle_and_choice_preserves_editor_and_roundtrips(self):
        tab = self.open('Notes.md')
        editor = tab.widget.md_surface.editor
        tab.widget.md_surface.set_mode('write')
        self.assertEqual(self.w.ambient_effect, 'off')
        self.assertFalse(self.w.ambient.timer.isActive())
        before = tab.widget.encoded()
        self.w.open_route('settings')
        self.events()
        control = self.w.utility.settings_page.preferences.ambient_effect
        self.assertEqual([control.itemText(i) for i in range(control.count())], ['關閉', '櫻花', '下雨', '雪花'])
        for effect in ('sakura', 'rain', 'snow'):
            control.setCurrentIndex(control.findData(effect))
            self.events()
            self.assertEqual(self.w.ambient.effect, effect)
            self.assertTrue(self.w.ambient.timer.isActive())
            self.assertLessEqual(len(self.w.ambient.particles), 36)
            self.assertEqual(tab.widget.encoded(), before)
            saved = json.loads((self.w.data/'workspace-ui.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['ambient_effect'], effect)
        self.w.open_route('home')
        self.events()
        # Native hit-testing must continue to find the editor beneath the effect.
        point = editor.viewport().mapTo(self.w, editor.viewport().rect().center())
        target = self.w.childAt(point)
        self.assertIsNot(target, self.w.ambient)
        self.assertTrue(target is editor or editor.isAncestorOf(target))
        editor.setFocus()
        QTest.keyClicks(editor, 'editable')
        self.assertIn('editable', tab.widget.toPlainText())
        tab.widget.undo()
        self.assertEqual(tab.widget.encoded(), before)
        tab.widget.redo()
        self.assertIn('editable', tab.widget.toPlainText())
        self.assertTrue(self.w.save_tab(tab))
        self.assertTrue(self.w.close())
        self.assertFalse(self.w.ambient.timer.isActive())
        self.w.deleteLater()
        self.app.processEvents()
        self.w = WorkspaceShell(self.root, Path(self.area.name)/'data')
        self.w.show()
        self.w.activateWindow()
        self.events()
        self.assertEqual(self.w.ambient_effect, 'snow')
        self.assertIn('editable', (self.root/'Notes.md').read_text(encoding='utf-8-sig'))
        self.assertEqual(self.errors, [])

    def test_pause_resume_reduce_motion_and_off_do_not_accumulate_timers(self):
        effect = self.w.ambient
        self.w.set_ambient_effect('sakura')
        self.events()
        self.assertTrue(effect.timer.isActive())
        self.w.showMinimized()
        self.events()
        self.assertFalse(effect.timer.isActive())
        self.w.showNormal()
        self.w.activateWindow()
        self.events()
        self.assertTrue(effect.timer.isActive())
        changed = dict(self.w.settings, reduce_motion=True)
        self.w.sync_display_settings(changed)
        self.assertFalse(effect.timer.isActive())
        self.assertTrue(effect.isHidden())
        self.w.sync_display_settings(dict(self.w.settings, reduce_motion=False))
        self.events()
        self.assertTrue(effect.timer.isActive())
        for _ in range(10):
            for choice in ('off', 'sakura', 'rain', 'snow'):
                self.w.set_ambient_effect(choice)
        self.assertEqual(len(effect.findChildren(QTimer)), 1)
        self.w.set_ambient_effect('off')
        self.assertFalse(effect.timer.isActive())
        self.assertEqual(effect.particles, [])
        self.assertTrue(effect.isHidden())
        self.assertEqual(self.errors, [])

    def test_visuals_remain_sparse_and_follow_theme(self):
        for name in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
        self.w.sync_display_settings(dict(self.w.settings, font_family='Microsoft JhengHei UI'))
        (self.root/'Notes.md').write_text('# 海岸的早晨\n\n在同一個工作區寫筆記、整理素材，再把想法放進畫布。\n\n## 今日計畫\n\n- 選擇場景與光線\n- 比較不同構圖\n- 保存一份自己的素材\n\n慢慢整理，每次保留一點新的想法。', encoding='utf-8')
        self.open('Notes.md')
        for mode in ('graphite', 'paper'):
            self.w.sync_display_settings(dict(self.w.settings, visual_palette=mode))
            for effect in ('sakura', 'rain', 'snow'):
                self.w.set_ambient_effect(effect)
                self.events()
                QTest.qWait(80)
                if folder := os.environ.get('PCS_AMBIENT_SCREENSHOTS'):
                    destination = Path(folder)
                    destination.mkdir(parents=True, exist_ok=True)
                    self.assertTrue(self.w.grab().save(str(destination/f'{mode}-{effect}.png')))
                self.assertTrue(self.w.ambient.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
                self.assertEqual(self.w.ambient.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertEqual(self.errors, [])

    def test_background_and_system_motion_pause_without_changing_choice(self):
        effect = self.w.ambient
        self.w.set_ambient_effect('snow')
        self.events()
        with patch.object(self.w, 'isActiveWindow', return_value=False):
            effect.sync()
            self.assertFalse(effect.timer.isActive())
        effect.system_motion = False
        effect.sync()
        self.assertFalse(effect.timer.isActive())
        self.assertEqual(self.w.ambient_effect, 'snow')
        effect.system_motion = True
        effect.sync()
        self.assertTrue(effect.timer.isActive())
