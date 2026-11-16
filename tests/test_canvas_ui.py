from _qt_cleanup import close_window
import copy
from _dialog_driver import drive_dialog
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]; sys.path[:0] = [str(ROOT/'vendor'), str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QDialog, QGraphicsPathItem
from PySide6.QtCore import Qt, QPointF, QPoint
from PySide6.QtTest import QTest
from _qt_wait import wait_ms
from PySide6.QtGui import QWheelEvent, QContextMenuEvent, QTextCursor
from prompt_calculus_studio.window import Window
from prompt_calculus_studio import composition as c
from prompt_calculus_studio.core import build_prompt, validate_state, item_prompt
from prompt_calculus_studio.text_canvas import NodeCard, NodeDialog, CanvasPalette, TextCard

APP = QApplication.instance() or QApplication([])


class CanvasUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = Window(Path(self.tmp.name)/'data'); self.w.state['settings']['online'] = False
        self.w.state['settings']['separate_selections'] = False
        self.w.state['settings']['material'] = 'solid'; self.w.apply_theme()
        self.w.resize(1440, 900); self.w.show(); APP.processEvents()
        self.asset = self.w.state['items'][0]; self.ident = self.asset['id']
        self.w.state['selections'] = {self.asset['module']: [self.ident]}
        self.w.refresh_builder(); self.canvas = self.w.canvas

    def tearDown(self):
        close_window(self.w); self.tmp.cleanup()

    def test_view_switch_and_move_preserve_output_and_sidebar(self):
        before = build_prompt(self.w.state); modules = copy.deepcopy(self.w.state['modules'])
        self.w.enter_canvas(); APP.processEvents()
        self.canvas.move_cards({self.ident: [345, 223]})
        self.w.leave_canvas(); APP.processEvents()
        self.assertEqual(self.w.final.toPlainText(), before)
        self.assertEqual(self.w.state['modules'], modules)
        self.assertEqual(self.w.state['text_positions'][self.ident], [345, 223])
        self.canvas.undo(); self.assertNotIn('text_positions', self.w.state)
        self.canvas.redo(); self.assertEqual(self.w.state['text_positions'][self.ident], [345, 223])

    def test_edit_overlay_and_removal_update_both_views_with_backup(self):
        original = copy.deepcopy(self.asset)
        def prepare(state):
            root = c.instance(state, self.ident); root['prompt'] = '1girl'
            root['children'].append(c.node('眼睛', 'red eyes'))
        self.canvas.commit(prepare)
        self.assertFalse(list(self.w.store.directory.glob('before-import-*.sqlite3')),
                         'Current-version edits do not trigger migration backups')
        root = self.w.state['instances'][self.ident]; eyes = root['children'][0]
        self.canvas.commit(lambda s: c.instance(s,self.ident)['children'][0]['overlays'].append(c.node('眼罩','blindfold')))
        self.assertEqual(self.w.final.toPlainText(), '1girl, blindfold')
        item = self.w.selected.topLevelItem(0).child(0)
        self.assertEqual(item.childCount(), 1); self.assertEqual(item.child(0).childCount(), 1)
        overlay = self.w.state['instances'][self.ident]['children'][0]['overlays'][0]
        self.w.remove_builder_entry(('node', (self.ident, overlay['id'])))
        self.assertEqual(self.w.final.toPlainText(), '1girl, red eyes')
        self.assertEqual(self.w.state['items'][0], original)

    def test_manual_draft_kept_and_unrelated_edit_invalidates_undo(self):
        self.w.state['draft'] = 'untouched\nmanual'; self.w.refresh_builder()
        self.canvas.commit(lambda s: c.instance(s,self.ident).update(prompt='changed auto'))
        self.assertEqual(self.w.final.toPlainText(), 'untouched\nmanual')
        self.assertEqual(build_prompt(self.w.state), 'changed auto')
        self.w.state['temporary'].append('independent edit'); self.w.refresh_builder()
        self.assertFalse(self.canvas.undo_stack)
        self.canvas.undo(); self.assertIn('independent edit', build_prompt(self.w.state))

    def test_blank_double_click_does_not_create_material(self):
        self.w.enter_canvas();APP.processEvents();before=copy.deepcopy(self.w.state)
        with patch.object(NodeDialog,'exec') as editor:
            QTest.mouseDClick(self.canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(12,12))
            QTest.mouseRelease(self.canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(12,12));wait_ms(20)
            editor.assert_not_called()
        self.assertEqual(self.w.state,before)
        self.w.persist();self.assertEqual(build_prompt(self.w.store.load()),build_prompt(before))

    def test_canvas_occupies_window_without_list_columns_and_text_is_shared(self):
        self.w.state['selections']={}; self.w.refresh_builder(); self.w.enter_canvas(); APP.processEvents()
        self.assertFalse(self.w.module_panel.isVisible()); self.assertTrue(self.w.selected.isVisible())
        self.assertIs(self.canvas.output.proxy.widget(),self.w.builder_panel)
        self.assertFalse(self.w.tabs.isVisible()); self.assertTrue(self.canvas.isVisible())
        self.assertGreater(self.canvas.view.width(),self.w.width()*.95)
        self.assertGreater(self.canvas.view.height(),self.w.height()*.78)
        self.assertEqual(sum(type(i) is TextCard for i in self.canvas.view.scene().items()),1)
        self.assertIsNotNone(self.canvas.preview_card)
        self.assertFalse(any(isinstance(i,NodeCard) for i in self.canvas.view.scene().items()))
        output=self.canvas.output
        centered=self.canvas.view.mapFromScene(self.canvas.view.scene().itemsBoundingRect().center())
        self.assertLess((centered-self.canvas.view.viewport().rect().center()).manhattanLength(),6)
        output.editor.setPlainText('中央 Text\n原文保留')
        self.assertEqual(self.w.state['draft'],'中央 Text\n原文保留')
        with patch.object(self.canvas,'palette') as popup:
            point=self.canvas.view.mapFromScene(output.pos()+QPointF(40,75))
            QTest.mouseDClick(self.canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=point); wait_ms(20)
            popup.assert_not_called()
        self.w.leave_canvas(); self.assertEqual(self.w.final.toPlainText(),'中央 Text\n原文保留')

    def test_nested_list_reorder_and_disabled_root(self):
        def prepare(state):
            root = c.instance(state,self.ident); root['prompt'] = ''
            root['children'] = [c.node('A','a'), c.node('B','b')]
        self.canvas.commit(prepare)
        root = self.w.state['instances'][self.ident]; a,b = root['children']
        self.w.reorder_builder(('node',(self.ident,a['id'])), ('node',(self.ident,b['id'])), True)
        self.assertEqual(self.w.final.toPlainText(), 'b, a')
        self.canvas.home(); self.canvas.toggle(self.ident)
        self.assertEqual(self.w.final.toPlainText(), '')
        self.canvas.toggle(self.ident); self.assertEqual(self.w.final.toPlainText(), 'b, a')

    def test_group_and_detach_root_keep_order_weights_and_sources(self):
        self.w.enter_canvas(); APP.processEvents()
        mid = self.asset['module']; module = next(m for m in self.w.state['modules'] if m['id'] == mid)
        module['mode'] = 'multiple'; second = self.w.state['items'][1]
        self.w.state['selections'][mid] = [self.ident, second['id']]
        self.w.state['weights'] = {self.ident: 13}; self.w.refresh_builder()
        before = build_prompt(self.w.state); originals = copy.deepcopy(self.w.state['items'])
        for card in self.canvas.view.scene().items():
            if isinstance(card, NodeCard): card.setSelected(True)
        with patch('prompt_calculus_studio.text_canvas.InputDialog.getText', return_value=('新複合', True)):
            self.canvas.group()
        self.assertEqual(build_prompt(self.w.state), '('+before+':1.0)')
        self.assertEqual(self.w.state['items'][:len(originals)], originals)
        combined = next(iter(self.w.state['uses']))
        self.assertEqual(self.w.state['selections'][mid], [])
        self.canvas.detach(combined)
        expected=',\n\n'.join(item_prompt({'weights':{self.ident:13}},asset,grouped=True) for asset in originals[:2])
        self.assertEqual(build_prompt(self.w.state), expected)
        self.assertEqual(len(self.w.state['uses']), 2)
        self.canvas.undo(); self.assertEqual(list(self.w.state['uses']), [combined])



    def test_root_instance_edits_survive_prototype_change_and_reopen(self):
        self.canvas.commit(lambda s: c.instance(s,self.ident).update(prompt='current use'))
        self.w.state['items'][0]['prompt'] = 'new source'
        self.w.refresh_builder(); self.assertEqual(self.w.final.toPlainText(), 'current use')
        self.w.persist(); state = self.w.store.load(); self.assertEqual(build_prompt(state), 'current use')
        self.w.state['selections'][self.asset['module']] = []; self.w.refresh_builder()
        self.w.state['selections'][self.asset['module']] = [self.ident]; self.w.refresh_builder()
        self.assertEqual(self.w.final.toPlainText(), 'new source')

    def test_pan_wheel_ctrl_marquee_and_right_click(self):
        self.w.enter_canvas(); APP.processEvents(); view=self.canvas.view
        before=view.mapFromScene(QPointF())
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(15,15))
        QTest.mouseMove(view.viewport(),QPoint(95,65))
        QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(95,65))
        delta=view.mapFromScene(QPointF())-before
        self.assertAlmostEqual(delta.x(),80,delta=2); self.assertAlmostEqual(delta.y(),50,delta=2)
        scale=view.transform().m11(); local=QPointF(15,15)
        wheel=QWheelEvent(local,QPointF(view.viewport().mapToGlobal(local.toPoint())),QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
        QApplication.sendEvent(view.viewport(),wheel); self.assertGreater(view.transform().m11(),scale)
        self.canvas.fit(); card=self.canvas.cards[self.ident]
        rect=view.mapFromScene(card.mapRectToScene(card.selectionRect())).boundingRect().adjusted(-3,-3,3,3)
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,rect.topLeft())
        QTest.mouseMove(view.viewport(),rect.bottomRight())
        QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,rect.bottomRight())
        self.assertTrue(card.isSelected()); self.assertFalse(self.canvas.output.isSelected())
        with patch.object(self.canvas,'tools_menu') as menu:
            event=QContextMenuEvent(QContextMenuEvent.Reason.Mouse,QPoint(15,15),view.viewport().mapToGlobal(QPoint(15,15)))
            QApplication.sendEvent(view.viewport(),event); menu.assert_called_once()


    def test_delete_children_parent_and_editing_text_are_separate(self):
        self.canvas.commit(lambda s:c.instance(s,self.ident).update(children=[c.node('A','smile'),c.node('B','dancing')]))
        self.w.enter_canvas(); APP.processEvents()
        root=self.w.state['instances'][self.ident]; a,b=root['children']
        child=self.canvas.cards[self.ident+':'+a['id']]; child.setSelected(True)
        self.canvas.view.setFocus(); self.canvas.view.scene().clearFocus()
        QTest.keyClick(self.canvas.view,Qt.Key.Key_Delete)
        self.assertEqual([n['prompt'] for n in self.w.state['instances'][self.ident]['children']],['dancing'])
        self.canvas.undo(); self.assertEqual(len(self.w.state['instances'][self.ident]['children']),2)
        self.canvas.cards[self.ident].setSelected(True)
        self.w.final.setPlainText('abc'); self.w.final.moveCursor(QTextCursor.MoveOperation.End); self.w.final.setFocus()
        self.assertIsNotNone(self.canvas.view.scene().focusItem())
        QTest.keyClick(self.canvas.view,Qt.Key.Key_Backspace)
        self.assertEqual(self.w.state['draft'],'ab'); self.assertIn(self.ident,self.canvas.cards)
        self.canvas.view.setFocus(); self.canvas.view.scene().clearFocus()
        self.canvas.cards[self.ident+':'+a['id']].setSelected(True)
        QTest.keyClick(self.canvas.view,Qt.Key.Key_Backspace)
        self.assertNotIn(self.ident,self.w.state['selections'][self.asset['module']])
        self.assertTrue(any(i['id']==self.ident for i in self.w.state['items']))
        self.assertEqual(self.w.state['draft'],'ab')

    def test_shared_output_controls_sort_clear_copy_and_return_to_list(self):
        self.w.state['temporary']=['alpha','beta']; self.w.refresh_builder(); self.w.enter_canvas(); APP.processEvents()
        def click(widget):
            point=widget.mapTo(self.w.builder_panel,widget.rect().center())
            target=self.canvas.view.mapFromScene(self.canvas.output.proxy.mapToScene(QPointF(point)))
            QTest.mouseClick(self.canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=target); APP.processEvents()
        self.assertIs(self.canvas.output.editor,self.w.final)
        self.assertTrue(self.canvas.output.boundingRect().contains(self.canvas.output.proxy.geometry()))
        group=self.w.selected.topLevelItem(self.w.selected.topLevelItemCount()-1)
        self.w.selected.setCurrentItem(group.child(0)); QTest.keyClick(self.w.selected,Qt.Key.Key_Down,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.w.state['temporary'],['beta','alpha'])
        self.assertTrue(self.w.final.toPlainText().endswith('beta,\nalpha'))
        self.w.final.setPlainText('manual'); self.w.state['settings']['confirm_clear_draft']=False
        click(self.w.clear_draft); self.assertIsNone(self.w.state['draft'])
        click(self.w.copy_button); self.assertEqual(APP.clipboard().text(),build_prompt(self.w.state))
        with patch.object(self.w,'new_item') as save:
            click(self.w.save_prompt); save.assert_called_once_with(build_prompt(self.w.state))
        self.w.leave_canvas(); APP.processEvents()
        self.assertIs(self.w.surface_stack.currentWidget(),self.w.prompt_editor)
        self.w.enter_canvas(); APP.processEvents()
        self.assertIs(self.canvas.output.proxy.widget(),self.w.builder_panel)
        self.assertEqual(self.w.final.toPlainText(),build_prompt(self.w.state))

    def test_top_navigation_connection_backup_and_canvas_return(self):
        self.w.enter_canvas(); APP.processEvents()
        self.assertFalse(self.w.canvas_connect.isVisible()); self.assertFalse(self.w.canvas_backup.isVisible())
        self.assertIn('data',self.w.settings_page.index)
        self.assertEqual(list(self.w.studio_navigation.entries),['canvas','media','explore','export','settings'])
        self.assertTrue(all(entry.text() for entry,_ in self.w.studio_navigation.entries.values()))
        self.w.studio_navigation.entries['export'][0].click(); APP.processEvents()
        self.assertIs(self.w.canvas_content.currentWidget(),self.w.clean_export); self.assertFalse(self.canvas.isVisible())
        self.w.studio_navigation.entries['canvas'][0].click();APP.processEvents()
        self.assertIs(self.w.surface_stack.currentWidget(),self.w.prompt_editor)
        self.w.enter_canvas();APP.processEvents();self.assertTrue(self.canvas.isVisible())
        self.assertIs(self.canvas.output.proxy.widget(),self.w.builder_panel)

    def test_existing_run_count_stop_and_copy_work_inside_output(self):
        self.w.enter_canvas(); APP.processEvents()
        client=self.w.comfy
        with patch.object(client,'request'),patch.object(client,'run') as run,patch.object(client,'interrupt') as stop,patch.object(self.w.recent,'ensure_destination',return_value=True):
            client.ready=True; client.connected=True; client.running=1; client.pending=2; self.w.comfy_changed(); APP.processEvents()
            self.assertTrue(self.w.run_controls.count.isVisible()); self.assertTrue(self.w.run_controls.stop.isVisible())
            self.w.run_controls.count.setValue(3)
            def click(widget):
                point=widget.mapTo(self.w.builder_panel,widget.rect().center())
                target=self.canvas.view.mapFromScene(self.canvas.output.proxy.mapToScene(QPointF(point)))
                QTest.mouseClick(self.canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=target); APP.processEvents()
            click(self.w.run_controls.run_button); run.assert_called_once_with(3)
            click(self.w.run_controls.stop); stop.assert_called_once_with()
            self.assertFalse(hasattr(self.w,'copy_prompt'))
            run.assert_called_once_with(3)
            self.assertIn('3',self.w.run_controls.activity.text())
            client.ready=False; client.connected=False; client.running=client.pending=0; self.w.comfy_changed()

    def test_inline_sibling_grouping_and_large_font_output_geometry(self):
        self.canvas.commit(lambda s:c.instance(s,self.ident).update(children=[c.node('A','smile'),c.node('B','dancing')]))
        self.w.enter_canvas(); APP.processEvents()
        root=self.w.state['instances'][self.ident]
        for child in root['children']: self.canvas.cards[self.ident+':'+child['id']].setSelected(True)
        with patch('prompt_calculus_studio.text_canvas.InputDialog.getText',return_value=('動作組',True)):
            self.canvas.group()
        root=self.w.state['instances'][self.ident]
        self.assertEqual(len(root['children']),1); self.assertEqual(len(root['children'][0]['children']),2)
        self.assertIn('(smile, dancing:1.0)',self.w.final.toPlainText())
        self.assertIsNone(self.canvas.root_id)
        self.w.state['settings']['ui_size']=18; self.w.apply_theme(); APP.processEvents()
        self.canvas.refresh(); APP.processEvents()
        self.assertTrue(self.canvas.output.boundingRect().contains(self.canvas.output.proxy.geometry()))



import stage_fixture as current_fixture
from prompt_calculus_studio import multi_output as model


class CurrentPickerUiTests(current_fixture.StageFixture):
    def choose_asset(self,palette,asset):
        palette.set_browser_category(asset['module']);palette.search.setText(asset['name'])
        self.assertTrue(palette.restore_selection(palette.assets,asset['id']))
        item=palette.assets.currentItem();palette.assets.scrollToItem(item);APP.processEvents()
        QTest.mouseClick(palette.assets.viewport(),Qt.MouseButton.LeftButton,pos=palette.assets.visualItemRect(item).center())
        self.assertTrue(palette.inserted)

    def test_category_search_and_saved_asset_copy_preserve_original(self):
        asset=copy.deepcopy(self.w.state['items'][0]);before=self.c.history_state()
        point=self.c.containers[self.cid].pos()+QPointF(60,150)
        palette=self.c.palette(point);self.choose_asset(palette,asset)
        added=set(self.w.state['uses'])-set(before['uses']);self.assertEqual(len(added),1)
        ident=added.pop();self.assertEqual(self.w.state['uses'][ident]['prompt'],asset['prompt'])
        self.assertEqual(model.owner(self.w.state,ident),self.cid)
        self.assertEqual(self.w.state['items'][0],asset)
        self.c.undo();self.assertEqual(self.c.history_state(),before)

    def test_blank_text_and_nested_insert_keep_order_and_undo(self):
        before=self.c.history_state();point=self.c.containers[self.cid].pos()+QPointF(60,150)
        palette=self.c.palette(point)
        def save(dialog):
            dialog.name.setText('new root');dialog.prompt.setPlainText('new prompt');dialog.accept()
        drive_dialog(palette.new_module,NodeDialog,save)
        root=(set(self.w.state['uses'])-set(before['uses'])).pop()
        palette=self.c.palette(target=root)
        def child(dialog):
            dialog.name.setText('child');dialog.prompt.setPlainText('nested prompt');dialog.accept()
        drive_dialog(palette.new_module,NodeDialog,child)
        self.assertEqual([n['prompt'] for n in self.w.state['uses'][root]['children']],['nested prompt'])
        self.assertEqual(self.w.state['uses']['root'],before['uses']['root'])
        rendered=model.compile_output(self.w.state,self.out)['final_prompt']
        self.assertIn('new prompt',rendered);self.assertIn('nested prompt',rendered)
        self.c.undo();self.assertEqual(self.w.state['uses'][root]['children'],[])
        self.c.redo();self.assertEqual(model.compile_output(self.w.state,self.out)['final_prompt'],rendered)
        self.w.persist();self.assertEqual(self.w.store.load()['uses'],self.w.state['uses'])

    def test_single_asset_click_and_add_child_keep_existing_modules(self):
        point=self.c.containers[self.cid].pos()+QPointF(60,150)
        asset=self.w.state['items'][0];before=set(self.w.state['uses'])
        palette=self.c.palette(point);self.choose_asset(palette,asset)
        root=(set(self.w.state['uses'])-before).pop()
        if self.c.cards[root].collapsed:self.c.toggle_collapsed(root)
        palette=self.c.palette(target=root)
        child_asset=next(a for a in self.w.state['items'] if a['prompt']=='smile')
        self.choose_asset(palette,child_asset)
        child=self.w.state['uses'][root]['children'][0]
        self.assertEqual(child['prompt'],'smile');self.assertIn('root',self.c.cards)
        nested=self.c.cards[root+':'+child['id']]
        self.assertIs(nested.parentItem(),self.c.cards[root])
        self.assertTrue(self.c.cards[root].boundingRect().contains(nested.mapRectToParent(nested.boundingRect())))


if __name__ == '__main__': unittest.main()
