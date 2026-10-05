import copy
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
from PySide6.QtGui import QWheelEvent, QContextMenuEvent, QTextCursor
from prompt_calculus_studio.window import Window
from prompt_calculus_studio import composition as c
from prompt_calculus_studio.core import build_prompt, validate_state, item_prompt
from prompt_calculus_studio.text_canvas import NodeCard, NodeDialog, CanvasPalette, TextCard

APP = QApplication.instance() or QApplication([])


class CanvasUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'qa')
        self.w = Window(Path(self.tmp.name)/'data'); self.w.state['settings']['online'] = False
        self.w.state['settings']['separate_selections'] = False
        self.w.state['settings']['material'] = 'solid'; self.w.apply_theme()
        self.w.resize(1440, 900); self.w.show(); APP.processEvents()
        self.asset = self.w.state['items'][0]; self.ident = self.asset['id']
        self.w.state['selections'] = {self.asset['module']: [self.ident]}
        self.w.refresh_builder(); self.canvas = self.w.canvas

    def tearDown(self):
        self.w.close(); APP.processEvents(); self.tmp.cleanup()

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
        self.assertTrue(list(self.w.store.directory.glob('before-import-*.sqlite3')))
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

    def test_new_root_double_click_creates_data_and_persists(self):
        self.w.enter_canvas(); APP.processEvents()
        def accept(dialog):
            dialog.name.setText('新群組'); dialog.prompt.setPlainText('raw,  prompt'); dialog.weight.setValue(1.2)
            return QDialog.DialogCode.Accepted
        def choose_new(palette):
            self.assertGreater(palette.modules.count(),1); palette.new_module()
            return QDialog.DialogCode.Accepted
        with patch.object(NodeDialog, 'exec', accept), patch.object(CanvasPalette,'exec',choose_new):
            QTest.mouseClick(self.canvas.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(12,12))
            QTest.mouseDClick(self.canvas.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(12,12))
            QTest.mouseRelease(self.canvas.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(12,12))
            QTest.qWait(30)
        self.assertIn('(raw,  prompt:1.2)', self.w.final.toPlainText())
        validate_state(self.w.state); self.w.persist()
        stored = self.w.store.load()
        self.assertEqual(build_prompt(stored), build_prompt(self.w.state))
        self.assertTrue(stored['text_positions'])

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
            QTest.mouseDClick(self.canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=point); QTest.qWait(20)
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

    def test_direct_tags_form_requested_modules_without_wires_or_single_selection_loss(self):
        self.w.state['selections']={}; self.w.refresh_builder(); self.w.enter_canvas(); APP.processEvents()
        def add_tags(*tags):
            for tag in tags:
                palette=CanvasPalette(self.canvas,None); palette.show(); APP.processEvents()
                try:
                    palette.query.setPlainText(tag); palette.query.setFocus()
                    QTest.keyClick(palette.query,Qt.Key.Key_Return); APP.processEvents()
                    self.assertEqual(palette.query.toPlainText(),'')
                    self.assertFalse(palette.isVisible())
                finally: palette.reject()
        add_tags('1girl')
        girl=next(iter(self.w.state['uses']))
        self.canvas.enter(girl)
        self.assertIn(girl,self.canvas.cards)
        add_tags('red clothes','red eyes','long hair','smile','dancing')
        self.canvas.home(); add_tags('simple background')
        background=list(self.w.state['uses'])[-1]
        self.assertNotEqual(girl,background); self.canvas.enter(background); add_tags('white background')
        expected='(1girl, red clothes, red eyes, long hair, smile, dancing:1.0),\n\n(simple background, white background:1.0)'
        self.assertEqual(self.w.final.toPlainText(),expected)
        self.assertEqual(self.canvas.output.editor.toPlainText(),expected)
        self.assertFalse(any(isinstance(i,QGraphicsPathItem) for i in self.canvas.view.scene().items()))
        self.canvas.open_root(girl)
        dancing=self.w.state['uses'][girl]['children'][-1]
        self.canvas.toggle(girl+':'+dancing['id'])
        self.assertNotIn('dancing',self.w.final.toPlainText())
        self.canvas.toggle(girl+':'+dancing['id']); self.assertEqual(self.w.final.toPlainText(),expected)
        self.assertFalse(any(m.get('canvas_library') for m in self.w.state['modules']))
        self.assertFalse(any(i['id']==girl for i in self.w.state['items']))
        self.w.leave_canvas(); self.assertEqual(self.w.final.toPlainText(),expected)
        self.w.persist(); self.assertEqual(build_prompt(self.w.store.load()),expected)

    def test_canvas_candidate_acceptance_and_saved_asset_copy(self):
        self.w.state['selections']={}; self.w.refresh_builder(); self.w.enter_canvas(); APP.processEvents()
        palette=CanvasPalette(self.canvas,None); palette.show(); APP.processEvents()
        try:
            palette.query.setPlainText('微笑'); palette.query.setFocus(); QTest.keyClick(palette.query,Qt.Key.Key_End); APP.processEvents()
            palette.query.service.lookup()
            self.assertTrue(palette.query.candidates)
            label=next(iter(palette.query.candidates))
            self.assertEqual(palette.query.candidates[label][1]['source'],'自訂字典')
            palette.query.insert_completion(label); APP.processEvents()
            self.assertEqual(self.w.final.toPlainText(),'(smile:1.0)')
            self.assertEqual(palette.query.toPlainText(),'')
            self.assertFalse(palette.isVisible())
            palette=CanvasPalette(self.canvas,None); palette.show(); APP.processEvents()
            palette.query.setPlainText('long'); palette.query.setFocus(); QTest.keyClick(palette.query,Qt.Key.Key_End); APP.processEvents()
            palette.query.display(palette.query.context(),[dict(value='long_hair',category=0,count=123,source='Danbooru')])
            palette.query.insert_completion(next(iter(palette.query.candidates)))
            self.assertEqual(self.w.final.toPlainText(),'(smile:1.0),\n\n(long hair:1.0)')
        finally: palette.reject()
        before=copy.deepcopy(self.w.state['items'][0]); self.canvas.insert_asset(before)
        self.assertEqual(self.w.state['items'][0],before)
        self.assertTrue(self.w.final.toPlainText().endswith('(1girl, red dress:1.0)'))
        self.canvas.undo(); self.assertEqual(self.w.final.toPlainText(),'(smile:1.0),\n\n(long hair:1.0)')

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

    def test_single_asset_click_and_inline_double_click_keep_all_modules_visible(self):
        self.w.enter_canvas(); APP.processEvents()
        palette=CanvasPalette(self.canvas,None); palette.show(); APP.processEvents()
        palette.query.setPlainText(self.asset['name']); APP.processEvents()
        row=palette.assets.item(0); self.assertIsNotNone(row)
        QTest.mouseClick(palette.assets.viewport(),Qt.MouseButton.LeftButton,pos=palette.assets.visualItemRect(row).center())
        self.assertEqual(palette.result(),QDialog.DialogCode.Accepted)
        added=next(iter(self.w.state['uses']))
        before=self.canvas.cards[added].height
        def insert(popup):
            self.assertEqual(popup.canvas.current()['name'],self.asset['name'])
            popup.query.setPlainText('smile'); popup.submit_tag()
            return QDialog.DialogCode.Accepted
        card=self.canvas.cards[added]; position=self.canvas.view.mapFromScene(card.mapToScene(QPointF(40,25)))
        with patch.object(CanvasPalette,'exec',insert):
            QTest.mouseDClick(self.canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=position); QTest.qWait(20)
        root=self.w.state['uses'][added]; child=root['children'][0]
        self.assertIn(self.ident,self.canvas.cards); self.assertIn(added,self.canvas.cards)
        self.assertGreater(self.canvas.cards[added].height,before)
        nested=self.canvas.cards[added+':'+child['id']]
        self.assertIs(nested.parentItem(),self.canvas.cards[added])
        self.assertTrue(self.canvas.cards[added].boundingRect().contains(nested.mapRectToParent(nested.boundingRect())))
        self.assertIsNone(self.canvas.root_id)

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
        self.assertIs(self.w.split.widget(2),self.w.builder_scroll)
        self.assertIs(self.w.builder_scroll.widget(),self.w.builder_panel)
        self.assertIsNone(self.canvas.output.proxy.widget())
        self.w.enter_canvas(); APP.processEvents()
        self.assertIs(self.canvas.output.proxy.widget(),self.w.builder_panel)
        self.assertEqual(self.w.final.toPlainText(),build_prompt(self.w.state))

    def test_top_navigation_connection_backup_and_canvas_return(self):
        self.w.enter_canvas(); APP.processEvents()
        self.assertFalse(self.w.canvas_connect.isVisible()); self.assertFalse(self.w.canvas_backup.isVisible())
        self.assertIn('data',self.w.settings_page.index)
        self.assertEqual(list(self.w.studio_navigation.entries),['canvas','media','explore','export','settings'])
        self.assertTrue(all(not entry.icon().isNull() for entry,_ in self.w.studio_navigation.entries.values()))
        self.w.studio_navigation.entries['export'][0].click(); APP.processEvents()
        self.assertIs(self.w.canvas_content.currentWidget(),self.w.clean_export); self.assertFalse(self.canvas.isVisible())
        self.w.return_to_prompt(); APP.processEvents(); self.assertTrue(self.canvas.isVisible())
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


if __name__ == '__main__': unittest.main()
