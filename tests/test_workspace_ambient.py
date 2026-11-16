"""The optional decoration preserves editing, input, preferences and idle work."""
from _qt_wait import wait_ms
import json
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtGui import QFont, QFontDatabase, QInputMethodEvent, QPainter
from PySide6.QtWidgets import QApplication, QWidget, QCheckBox, QDialog, QMenu
from shiboken6 import isValid
from _ambient_probe import PaintCounter, canvas_fixture, measure
import test_workspace_panes as fixture
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from prompt_calculus_studio.workspace_ambient import Particle, WorkspaceAmbient


def offscreen_surface(ambient):
    # Qt 6.11 QOffscreenWindow::setVisible activates every non-ToolTip window,
    # ignoring WindowDoesNotAcceptFocus/WA_ShowWithoutActivating. Keep the real
    # QWidget/backing-store paints, but omit that simulated native mapping.
    # This is test-only; native activation/stacking still needs desktop QA.
    if QApplication.platformName() == 'offscreen':
        ambient.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)


class WorkspaceAmbientTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def setUp(self):
        self.motion = patch('prompt_calculus_studio.workspace_ambient.system_motion_enabled', return_value=True)
        self.motion.start()
        fixture.WorkspacePaneTests.setUp(self)
        offscreen_surface(self.w.ambient)
        self.w.activateWindow()
        self.events()

    def tearDown(self):
        fixture.WorkspacePaneTests.tearDown(self)
        self.motion.stop()

    def assert_surface_geometry(self):
        stack = self.w.workspace_stack
        self.assertEqual(self.w.ambient.geometry(), QRect(stack.mapToGlobal(QPoint()), stack.size()))

    def assert_input_transparent(self):
        effect = self.w.ambient
        self.assertTrue(effect.isWindow())
        self.assertIs(effect.parentWidget(), self.w)
        self.assertEqual(effect.windowType(), Qt.WindowType.Tool)
        for flag in (Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowTransparentForInput,
                     Qt.WindowType.WindowDoesNotAcceptFocus):
            self.assertTrue(effect.windowFlags() & flag)
        self.assertFalse(effect.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        for attr in (Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                     Qt.WidgetAttribute.WA_ShowWithoutActivating,
                     Qt.WidgetAttribute.WA_TranslucentBackground):
            self.assertTrue(effect.testAttribute(attr))
        self.assertEqual(effect.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertFalse(effect.acceptDrops())

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
        # Content hit-testing plus the owned window's input flags: QtTest sends
        # to a widget directly, so this does not certify OS-level click-through.
        self.assert_input_transparent()
        point = editor.viewport().mapTo(self.w, editor.viewport().rect().center())
        target = self.w.childAt(point)
        self.assertIsNot(target, self.w.ambient)
        self.assertTrue(target is editor or editor.isAncestorOf(target))
        QTest.mouseClick(editor.viewport(), Qt.MouseButton.LeftButton,
                         pos=editor.viewport().rect().center())
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
        offscreen_surface(self.w.ambient)
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

    def test_rejected_close_keeps_effect_alive(self):
        self.w.set_ambient_effect('sakura')
        self.events()
        with patch.object(self.w, '_close_workspace_event', side_effect=lambda event: event.ignore()):
            self.assertFalse(self.w.close())
        self.events()
        self.assertTrue(self.w.ambient.isVisible())
        self.assertTrue(self.w.ambient.timer.isActive())
        self.assertIn(self.w.ambient, self.app.topLevelWidgets())

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
                wait_ms(80)
                if folder := os.environ.get('PCS_AMBIENT_SCREENSHOTS'):
                    destination = Path(folder)
                    destination.mkdir(parents=True, exist_ok=True)
                    # QWidget.grab excludes separate windows; composite the
                    # decoration at its actual global offset for offscreen QA.
                    shot = self.w.grab()
                    painter = QPainter(shot)
                    painter.drawPixmap(self.w.mapFromGlobal(self.w.ambient.pos()), self.w.ambient.grab())
                    painter.end()
                    self.assertTrue(shot.save(str(destination/f'{mode}-{effect}.png')))
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

    def test_sidebar_layout_keeps_documents_below_decoration_without_resetting_it(self):
        left = self.open('Notes.md')
        right = self.w.duplicate_view(left, True)
        self.events()
        self.w.set_ambient_effect('rain')
        self.events()
        effect = self.w.ambient
        effect.advance()
        particles = list(effect.particles)
        # Keep motion fixed so this checks stacking/layout, not random positions.
        effect.timer.blockSignals(True)
        for _ in range(3):
            self.w.toggle_sidebar()
            self.events()
            self.w.pane_split.setSizes([450, 650])
            self.events()
            self.assert_input_transparent()
            self.assertNotIn(effect, self.w.workspace_stack.children())
            for tab in (left, right):
                center = effect.mapFromGlobal(tab.widget.mapToGlobal(tab.widget.rect().center()))
                self.assertTrue(effect.visibleRegion().contains(center))
            self.assertIs(effect.windowHandle().transientParent(), self.w.windowHandle())
            self.assert_surface_geometry()
            self.assertEqual(effect.particles, particles)
        self.assertEqual(len(effect.findChildren(QTimer)), 1)

    def test_canvas_remains_visible_and_decorated_after_sidebar_and_page_changes(self):
        tab = self.open('Street.pcs-workflow')
        viewport = tab.host.canvas.view.viewport()
        self.w.set_ambient_effect('snow')
        for _ in range(3):
            self.w.toggle_sidebar()
            self.events()
            center = self.w.ambient.mapFromGlobal(viewport.mapToGlobal(viewport.rect().center()))
            self.assertTrue(viewport.isVisible())
            self.assertTrue(self.w.ambient.visibleRegion().contains(center))
            hit = self.w.childAt(viewport.mapTo(self.w, viewport.rect().center()))
            self.assertTrue(hit is viewport or viewport.isAncestorOf(hit))
            self.w.open_route('settings')
            self.events()
            self.w.open_route('home')
            self.events()
        self.assertEqual(len(self.w.ambient.findChildren(QTimer)), 1)

    def test_global_preferences_do_not_expose_or_rewrite_legacy_selection_drafts(self):
        tab = self.open('Street.pcs-workflow')
        state = tab.host.state
        state['settings']['separate_selections'] = False
        state['draft'] = ''
        before = copy.deepcopy(state)
        self.w.open_route('settings')
        self.events()
        preferences = self.w.utility.settings_page.preferences
        self.assertFalse(any('分開保存' in box.text() for box in self.w.utility.settings_page.findChildren(QCheckBox)))
        self.assertEqual(preferences.ambient_effect.toolTip(),
                         '輕量的畫面點綴，切到背景或啟用減少動態效果時暫停。')
        preferences.save(accept=False)
        self.events()
        for key in ('draft', 'draft_base', 'view_drafts', 'selection_view', 'selections'):
            self.assertEqual(state.get(key), before.get(key), key)
        self.assertFalse(state['settings']['separate_selections'])

    def test_fixed_frames_do_not_repaint_canvas(self):
        canvas = canvas_fixture(self)
        off = measure(self, canvas.view.viewport(), 'off')
        for effect in ('sakura', 'rain', 'snow'):
            with self.subTest(effect=effect):
                result = measure(self, canvas.view.viewport(), effect)
                self.assertLessEqual(result['content_paints']-off['content_paints'], 2, result)
                self.assertEqual(result['advances'], 60)
                self.assertEqual(result['ambient_paints'], 60, result)
                self.assertGreater(result['particles'], 0)
                self.assertLessEqual(result['particles'], 36)

    def test_fixed_frames_do_not_repaint_editor(self):
        tab = self.open('Notes.md')
        tab.widget.md_surface.set_mode('write')
        viewport = tab.widget.md_surface.editor.viewport()
        off = measure(self, viewport, 'off')
        result = measure(self, viewport, 'sakura')
        self.assertLessEqual(result['content_paints']-off['content_paints'], 2, result)
        self.assertEqual(result['ambient_paints'], 60, result)

    def test_canvas_click_and_editor_ime_with_active_effect(self):
        canvas = canvas_fixture(self)
        self.w.set_ambient_effect('sakura')
        self.events()
        self.assert_input_transparent()
        card = next(iter(canvas.cards.values()))
        canvas.view.centerOn(card)
        self.events()
        canvas.view.scene().clearSelection()
        pos = canvas.view.mapFromScene(card.mapToScene(QPointF(70, 12)))
        self.assertTrue(canvas.view.viewport().rect().contains(pos))
        QTest.mouseClick(canvas.view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        self.events()
        self.assertTrue(card.isSelected())
        tab = self.open('Notes.md')
        tab.widget.md_surface.set_mode('write')
        editor = tab.widget.md_surface.editor
        QTest.mouseClick(editor.viewport(), Qt.MouseButton.LeftButton,
                         pos=editor.viewport().rect().center())
        QTest.keyClicks(editor, 'input')
        event = QInputMethodEvent()
        event.setCommitString('中文')
        self.app.sendEvent(editor, event)
        self.assertIn('input中文', editor.toPlainText())
        self.assertIs(self.app.activeWindow(), self.w)
        self.assertTrue(self.w.ambient.isVisible())

    def test_showing_effect_preserves_active_window_and_editor_focus(self):
        tab = self.open('Notes.md')
        tab.widget.md_surface.set_mode('write')
        editor = tab.widget.md_surface.editor
        editor.setFocus()
        self.events()
        self.assertIs(self.app.activeWindow(), self.w)
        for choice in ('sakura', 'off', 'snow', 'rain', 'off', 'sakura'):
            self.w.set_ambient_effect(choice)
            self.events()
            self.assertIs(self.app.activeWindow(), self.w)
            self.assertIs(self.app.focusWidget(), editor)
            self.assert_input_transparent()

    def test_move_resize_maximize_restore_and_sidebar_geometry(self):
        self.open('Notes.md')
        self.w.set_ambient_effect('sakura')
        self.events()
        self.assert_surface_geometry()
        for point, size in ((QPoint(80, 70), (1280, 850)), (QPoint(210, 140), (1450, 950))):
            self.w.move(point)
            self.w.resize(*size)
            self.events()
            self.assert_surface_geometry()
        self.w.showMaximized()
        self.events()
        self.assert_surface_geometry()
        self.w.showNormal()
        self.events()
        self.assert_surface_geometry()
        self.w.sidebar_preference = True
        self.w.sync_sidebar()
        self.events()
        for width in (260, 390):
            self.w.sidebar_resize.resize_to(width)
            self.events()
            self.assertEqual(self.w.sidebar_width, width)
            self.assertEqual(self.w.sidebar_target().width(), width)
            self.assert_surface_geometry()
        # Exercise notification paths; a real monitor/DPI transition remains
        # a desktop check, not something these synthetic events can prove.
        self.w.windowHandle().screenChanged.emit(self.w.screen())
        self.app.sendEvent(self.w, QEvent(QEvent.Type.DevicePixelRatioChange))
        self.events()
        self.assert_surface_geometry()

    def test_dialog_and_background_window_pause_then_resume(self):
        self.w.set_ambient_effect('sakura')
        self.events()
        for window in (QDialog(self.w), QWidget()):
            try:
                window.show()
                window.activateWindow()
                self.events()
                self.assertIs(self.app.activeWindow(), window)
                self.assertFalse(self.w.ambient.isVisible())
                self.assertFalse(self.w.ambient.timer.isActive())
            finally:
                window.close()
                window.deleteLater()
            self.w.activateWindow()
            self.events()
            self.assertTrue(self.w.ambient.isVisible())
            self.assertTrue(self.w.ambient.timer.isActive())
        menu = QMenu(self.w)
        menu.addAction('Menu')
        try:
            menu.popup(self.w.workspace_stack.mapToGlobal(QPoint(50, 50)))
            self.events()
            self.assertIs(self.app.activePopupWidget(), menu)
            QTest.keyClick(menu, Qt.Key.Key_Escape)
            self.events()
            self.assertIsNone(self.app.activePopupWidget())
        finally:
            menu.close()
            menu.deleteLater()
        self.w.hide()
        self.events()
        self.assertFalse(self.w.ambient.timer.isActive())
        self.assertFalse(self.w.ambient.isVisible())

    def test_close_destroys_owned_window_and_single_timer(self):
        effect = self.w.ambient
        timer = effect.timer
        for _ in range(6):
            for choice in ('sakura', 'off'):
                self.w.set_ambient_effect(choice)
                self.events()
                self.assertEqual([w for w in self.app.topLevelWidgets() if isinstance(w, WorkspaceAmbient)], [effect])
                self.assertEqual(effect.findChildren(QTimer), [timer])
        self.w.set_ambient_effect('sakura')
        self.events()
        self.assertTrue(self.w.close())
        self.assertFalse(timer.isActive())
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(effect))
        self.assertFalse(isValid(timer))
        self.assertFalse(any(isinstance(w, WorkspaceAmbient) for w in self.app.topLevelWidgets()))

    def test_owner_destruction_also_destroys_overlay(self):
        from PySide6.QtWidgets import QStackedWidget
        owner = QWidget()
        owner.workspace_stack = QStackedWidget(owner)
        owner.notification = QWidget(owner.workspace_stack)
        overlay = WorkspaceAmbient(owner)
        timer = overlay.timer
        offscreen_surface(overlay)
        owner.resize(600, 400)
        owner.workspace_stack.setGeometry(owner.rect())
        owner.show()
        owner.activateWindow()
        overlay.effect = 'snow'
        self.events()
        overlay.sync()
        self.assertTrue(timer.isActive())
        owner.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(overlay))
        self.assertFalse(isValid(timer))
        self.w.activateWindow()
        self.events()

    def test_notification_excludes_particles_and_invalidates_old_region(self):
        effect, notice = self.w.ambient, self.w.notification
        self.w.set_ambient_effect('sakura')
        self.events()
        effect.timer.blockSignals(True)
        notice.present('Notification stays above particles')
        self.events()
        region = QRect(notice.mapTo(self.w.workspace_stack, QPoint()), notice.size())
        inside = region.center()
        outside = QPoint(inside.x(), region.bottom()+45)
        counter = PaintCounter(effect)

        def alpha_at(point):
            shot = effect.grab().toImage()
            ratio = shot.devicePixelRatio()
            rect = QRect(point-QPoint(8, 8), point+QPoint(8, 8))
            return sum(shot.pixelColor(round(x*ratio), round(y*ratio)).alpha()
                       for x in range(rect.left(), rect.right()+1)
                       for y in range(rect.top(), rect.bottom()+1))

        for choice in ('sakura', 'rain', 'snow'):
            with self.subTest(effect=choice):
                self.w.set_ambient_effect(choice)
                effect.particles = [Particle(p.x(), p.y(), 5, 0, 0, 0) for p in (inside, outside)]
                self.assertEqual(alpha_at(inside), 0)
                self.assertGreater(alpha_at(outside), 0)
        painted = counter.count
        notice.move(notice.x()+50, notice.y()+100)
        self.events()
        self.assertGreater(counter.count, painted)
        self.assertGreater(alpha_at(inside), 0)
        notice.move(region.topLeft())
        self.events()
        self.assertEqual(alpha_at(inside), 0)
        notice.hide()
        self.events()
        self.assertGreater(alpha_at(inside), 0)
        effect.removeEventFilter(counter)
