"""Scoped module UI checks using real Qt events and disposable state."""
import copy
import sys
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QInputMethodEvent, QFontDatabase
from PySide6.QtTest import QTest
from stage_fixture import StageFixture, APP
from prompt_calculus_studio.core import validate_state
from prompt_calculus_studio.composition import node


class ModuleRedesignTests(StageFixture):
    def setUp(self):
        self.qt_errors = []
        hook = patch.object(sys, 'excepthook', lambda k, v, tb: self.qt_errors.append(repr(v)))
        hook.start(); self.addCleanup(hook.stop)
        for name in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + name)
        super().setUp()
        self.view = self.c.view
        self.view.resetTransform()

    def tearDown(self):
        self.assertFalse(self.executor.submissions)
        super().tearDown()
        self.assertFalse(self.qt_errors, self.qt_errors)

    def click_card(self, card, rect):
        self.view.centerOn(card); QTest.qWait(5)
        point = self.view.mapFromScene(card.mapToScene(rect.center()))
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        QTest.qWait(5)

    def weight_editor(self, key='root'):
        card = self.c.cards[key]
        with patch.object(self.c, 'weight_dialog') as dialog:
            self.click_card(card, card.weight_rect)
            dialog.assert_not_called()
        self.assertIsNotNone(card.weight_editor)
        edit = card.weight_editor.widget()
        self.assertTrue(edit.hasFocus())
        return edit

    def test_weight_enter_single_transaction_undo_redo_save_reopen(self):
        original = copy.deepcopy(self.w.state['items'])
        count = len(self.c.undo_stack)
        edit = self.weight_editor()
        QTest.keyClicks(edit, '-1.5')
        self.assertEqual(self.w.state['uses']['root']['weight'], 10)
        QTest.keyClick(edit, Qt.Key.Key_Return); QTest.qWait(15)
        self.assertEqual(self.w.state['uses']['root']['weight'], -15)
        self.assertEqual(len(self.c.undo_stack), count + 1)
        self.c.undo(); self.assertEqual(self.w.state['uses']['root']['weight'], 10)
        self.c.redo(); self.assertEqual(self.w.state['uses']['root']['weight'], -15)
        self.w.store.save(self.w.state)
        reopened = self.w.store.load()
        self.assertEqual(reopened['uses']['root']['weight'], -15)
        self.assertEqual(reopened['items'], original)
        validate_state(reopened)
        self.w.set_interface_mode('list'); self.w.set_interface_mode('canvas')
        self.assertEqual(self.c.cards['root'].value['weight'], -15)

    def test_weight_invalid_bounds_and_focus_commit(self):
        for text, expected in [('hello', 10), ('中文', 10), ('', 10), ('101', 1000), ('-101', -1000), ('-2.35', -24), ('NaN', 10)]:
            edit = self.weight_editor(); edit.setText(text)
            QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton,
                             pos=self.view.viewport().rect().bottomRight() - QPoint(10,10))
            QTest.qWait(15)
            self.assertEqual(self.w.state['uses']['root']['weight'], expected, text)

    def test_weight_escape_and_input_method_do_not_edit_canvas(self):
        count = len(self.c.undo_stack)
        edit = self.weight_editor()
        QTest.keyClick(edit, Qt.Key.Key_Backspace)
        QTest.keyClicks(edit, '-')
        APP.sendEvent(edit, QInputMethodEvent('中', []))
        QTest.keyClick(edit,Qt.Key.Key_Return)
        self.assertFalse(edit.finished)
        self.assertEqual(self.w.state['uses']['root']['weight'], 10)
        QTest.keyClick(edit, Qt.Key.Key_Escape); QTest.qWait(10)
        self.assertEqual(len(self.c.undo_stack), count)
        self.assertEqual(self.w.state['uses']['root']['weight'], 10)
        edit = self.weight_editor()
        QTest.keyClick(edit, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(edit, Qt.Key.Key_Delete)
        self.assertIn('root', self.w.state['uses'])
        QTest.keyClick(edit, Qt.Key.Key_Escape)

    def test_nested_weight_and_legacy_workspace_validation(self):
        child = node('Child', 'smile')
        self.c.commit(lambda s: s['uses']['root'].update(collapsed=False, children=[child]))
        key = 'root:' + child['id']
        edit = self.weight_editor(key); QTest.keyClicks(edit, '-100')
        QTest.keyClick(edit, Qt.Key.Key_Return); QTest.qWait(15)
        self.assertEqual(self.w.state['uses']['root']['children'][0]['weight'], -1000)
        self.assertEqual(self.w.state['uses']['root']['weight'], 10)
        self.click_card(self.c.cards[key], self.c.cards[key].minus_rect)
        self.assertEqual(self.w.state['uses']['root']['children'][0]['weight'], -1000)
        self.click_card(self.c.cards[key], self.c.cards[key].plus_rect)
        self.assertEqual(self.w.state['uses']['root']['children'][0]['weight'], -999)
        value = copy.deepcopy(self.w.state)
        value.setdefault('weights', {})['legacy'] = -1000
        value['workspaces'][0].setdefault('weights', {})['legacy'] = -15
        validate_state(value)

    def test_function_collapse_retains_ports_connections_and_size(self):
        from prompt_calculus_studio.module_chrome import module_value
        stage = next(iter(self.c.data()['stages']))
        cards = [self.c.outputs[self.out], self.c.clips[self.clip], self.c.flow_cards[stage], self.c.preview_card]
        lines = copy.deepcopy(self.c.data()['connections'])
        for card in cards:
            size = (card.width, card.height)
            geometry = copy.deepcopy(self.w.state.get('text_sizes', {}))
            ports = [(p.kind,p.output) for p in self.c.ports.values() if p.parentItem() is card]
            self.click_card(card, card.actions()['collapse'])
            self.assertTrue(card.collapsed)
            self.assertFalse(card.proxy.isVisible())
            self.assertLess(card.height, size[1])
            self.assertTrue(all(p.isVisible() and p.y()<card.height for p in self.c.ports.values() if p.parentItem() is card))
            self.assertEqual(self.c.data()['connections'], lines)
            self.assertEqual(self.w.state.get('text_sizes', {}),geometry)
            self.click_card(card,card.actions()['collapse'])
            self.assertFalse(card.collapsed)
            self.assertTrue(card.proxy.isVisible())
            self.assertEqual((card.width,card.height),size)
            self.assertEqual([(p.kind,p.output) for p in self.c.ports.values() if p.parentItem() is card],ports)

    def test_container_collapse_restores_members_and_does_not_mutate_positions(self):
        container=self.c.containers[self.cid]
        prior=copy.deepcopy(self.c.data()['canvases'][self.cid]); positions=copy.deepcopy(self.w.state['text_positions'])
        self.click_card(container,container.actions()['collapse'])
        self.assertTrue(container.collapsed);self.assertFalse(self.c.cards['root'].isVisible())
        self.assertEqual(self.c.data()['canvases'][self.cid]['display_size'],prior['display_size'])
        self.assertEqual(self.w.state['text_positions'],positions)
        self.c.undo();self.assertFalse(container.collapsed);self.assertTrue(self.c.cards['root'].isVisible())
        self.c.redo();self.assertTrue(container.collapsed)
        self.click_card(container,container.actions()['collapse'])
        self.assertEqual(self.w.state['text_positions'],positions)

    def test_more_preserves_text_actions_and_targets_same_context(self):
        from prompt_calculus_studio.widgets import RoundMenu
        menus=[]
        with patch.object(RoundMenu,'open_at',lambda menu,pos:menus.append(menu)):
            self.click_card(self.c.cards['root'],self.c.cards['root'].actions()['more'])
        titles=[a.text() for a in menus[-1].actions()]
        for name in ('編輯當次內容…','調整權重…','調整模組尺寸…','恢復自動尺寸','加入子元素…','使用素材覆蓋…','啟用／停用','將選取項目組合','拆解群組','另存為複合素材…','移除當次組合'):
            self.assertIn(name,titles)
        with patch.object(self.c,'clip_menu') as menu:
            self.click_card(self.c.clips[self.clip],self.c.clips[self.clip].actions()['more'])
            self.assertEqual(menu.call_args.args[0],self.clip)

    def test_shadow_bounds_do_not_enlarge_body_hit_region(self):
        for card in (self.c.cards['root'],self.c.clips[self.clip],self.c.containers[self.cid]):
            point=QPointF(card.width/2,card.height+10)
            self.assertTrue(card.boundingRect().contains(point))
            self.assertFalse(card.shape().contains(point))

    def test_function_picker_previews_are_read_only_and_share_real_cards(self):
        from prompt_calculus_studio.function_picker import FunctionPicker, FUNCTIONS
        from prompt_calculus_studio.canvas_items import TextCard
        from prompt_calculus_studio.flow_items import CanvasContainer
        QTest.qWait(400);self.w.persist()
        before=copy.deepcopy(self.w.state); count=len(self.c.undo_stack)
        dialog=FunctionPicker(self.c,QPointF(2100,1200));dialog.show();QTest.qWait(15)
        try:
            self.assertEqual(dialog.list.count(),10)
            for i,kind in enumerate(FUNCTIONS):
                dialog.list.setCurrentRow(i);QTest.qWait(5)
                self.assertIsInstance(dialog.preview.card,(TextCard,CanvasContainer))
                self.assertFalse(dialog.preview.isInteractive())
                self.assertEqual(dialog.name.text(),kind[1])
            self.assertEqual(self.w.state,before);self.assertEqual(len(self.c.undo_stack),count)
            QTest.mouseClick(dialog.filters['圖片'],Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.list.count(),4)
            dialog.search.setText('不存在的功能');self.assertFalse(dialog.add_button.isEnabled())
            self.assertTrue(dialog.empty.isVisible());self.assertIsNone(dialog.preview.card)
            dialog.search.clear();dialog.reject()
            self.assertEqual(self.w.state,before)
        finally:dialog.close();dialog.deleteLater();QTest.qWait(1)

    def test_function_picker_enter_inserts_once_at_requested_position(self):
        from prompt_calculus_studio.function_picker import FunctionPicker
        dialog=FunctionPicker(self.c,QPointF(2100,1200));dialog.show();QTest.qWait(10)
        existing=set(self.c.data()['canvases']);count=len(self.c.undo_stack)
        dialog.search.setText('文字畫布');QTest.keyClick(dialog.search,Qt.Key.Key_Return);QTest.qWait(15)
        added=set(self.c.data()['canvases'])-existing;self.assertEqual(len(added),1)
        self.assertEqual(self.c.data()['canvases'][added.pop()]['position'],[2100,1200])
        self.assertEqual(len(self.c.undo_stack),count+1)
        dialog.insert_current();self.assertEqual(len(self.c.undo_stack),count+1)
        self.c.undo();self.assertEqual(set(self.c.data()['canvases']),existing)
        dialog.deleteLater()

    def test_function_picker_double_click_and_add_button_insert_once(self):
        from prompt_calculus_studio.function_picker import FunctionPicker
        for by_button in (False,True):
            dialog=FunctionPicker(self.c,QPointF(2100,1200));dialog.show();QTest.qWait(10)
            dialog.search.setText('CLIP 輸入');existing=set(self.c.data()['clip_inputs'])
            if by_button:QTest.mouseClick(dialog.add_button,Qt.MouseButton.LeftButton)
            else:
                point=dialog.list.visualItemRect(dialog.list.item(0)).center()
                QTest.mouseClick(dialog.list.viewport(),Qt.MouseButton.LeftButton,pos=point)
                self.assertEqual(set(self.c.data()['clip_inputs']),existing)
                QTest.mouseDClick(dialog.list.viewport(),Qt.MouseButton.LeftButton,pos=point)
            QTest.qWait(15)
            self.assertEqual(len(set(self.c.data()['clip_inputs'])-existing),1)
            dialog.deleteLater()

    def test_container_footer_opens_editor_without_panning(self):
        self.w.state['settings']['reduce_motion']=True
        container=self.c.containers[self.cid]
        center=container.mapToScene(container.actions()['editor'].center())
        self.view.centerOn(center);QTest.qWait(10)
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.view.mapFromScene(center));QTest.qWait(20)
        self.assertIsNone(self.view.pan)
        self.assertIsNotNone(self.c.editor_page)
        self.assertTrue(self.c.editor_page.isVisible())
        self.c.editor_page.close_editor();QTest.qWait(15)
        self.assertIsNone(self.c.editor_page)

    def test_container_size_menu_and_collapsed_function_resize_preserve_expanded_size(self):
        from prompt_calculus_studio.widgets import RoundMenu
        menus=[]
        with patch.object(RoundMenu,'open_at',lambda menu,pos:menus.append(menu)):
            self.c.container_menu(self.cid,QPoint(0,0))
        self.assertIn('調整模組尺寸…',[a.text() for a in menus[0].actions()])
        self.c.resize_card(self.cid,920,740)
        self.assertEqual(self.c.data()['canvases'][self.cid]['display_size'],[920,740])
        self.c.undo();self.assertNotEqual(self.c.data()['canvases'][self.cid]['display_size'],[920,740])
        card=self.c.clips[self.clip]
        self.click_card(card,card.actions()['collapse'])
        self.assertIsNone(card.resize_corner(QPointF(card.width-3,card.height-3)))

    def test_binding_save_cancel_reopen_and_unavailable_targets(self):
        from PySide6.QtWidgets import QDialog
        from prompt_calculus_studio.clip_widgets import ClipBindingDialog
        self.w.settings_page.workflow_manager.catalog.loaded=True
        d=ClipBindingDialog(self.c,self.clip);d.show();QTest.qWait(10)
        self.assertTrue(d.save_button.isEnabled())
        d.workflow.setCurrentIndex(d.workflow.findData('B'))
        self.assertFalse(d.save_button.isEnabled());QTest.mouseClick(d.save_button,Qt.MouseButton.LeftButton)
        self.assertTrue(d.isVisible())
        d.workflow.setCurrentIndex(d.workflow.findData('flow'));d.target.setCurrentIndex(d.target_index(('7','text')))
        QTest.mouseClick(d.save_button,Qt.MouseButton.LeftButton);self.assertEqual(d.result(),QDialog.DialogCode.Accepted);d.apply();d.deleteLater()
        binding=next(b for b in self.c.data()['bindings'] if b['clip']==self.clip)
        self.assertEqual(binding['node'],'7');self.assertIn('#7',self.c.clips[self.clip].panel.binding.text())
        d=ClipBindingDialog(self.c,self.clip)
        self.assertEqual(d.target.currentData(),('7','text'))
        d.target.setCurrentIndex(d.target_index(('6','text')));d.reject();d.deleteLater()
        self.assertEqual(next(b for b in self.c.data()['bindings'] if b['clip']==self.clip)['node'],'7')
        self.c.undo();self.assertEqual(next(b for b in self.c.data()['bindings'] if b['clip']==self.clip)['node'],'6')
        self.c.redo();self.w.persist();self.assertEqual(next(b for b in self.w.store.load()['multi_output']['bindings'] if b['clip']==self.clip)['node'],'7')

    def test_binding_can_explicitly_remove_a_stale_target_after_refresh(self):
        from prompt_calculus_studio.clip_widgets import ClipBindingDialog
        self.w.settings_page.workflow_manager.catalog.loaded=True
        dialog=ClipBindingDialog(self.c,self.clip)
        from stage_fixture import pure_profile
        profile=pure_profile(dialog.profile()['id'])
        profile['frontend_id']=dialog.profile()['frontend_id']
        dialog.staged[profile['id']]=profile;dialog.refresh_targets();dialog.update_submit()
        self.assertEqual(dialog.target.count(),1)
        self.assertIsNone(dialog.target.currentData())
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.accept();dialog.apply();dialog.deleteLater()
        self.assertFalse(any(binding['clip']==self.clip for binding in self.c.data()['bindings']),self.notices)
        self.c.undo();self.assertTrue(any(binding['clip']==self.clip for binding in self.c.data()['bindings']))

    def test_image_binding_pending_and_error_survive_collapse(self):
        from prompt_calculus_studio.flow_data import add_image_input
        from prompt_calculus_studio.flow_widgets import ImageInputDialog
        self.w.settings_page.workflow_manager.catalog.loaded=True
        created=[];self.c.commit(lambda s:created.append(add_image_input(s)))
        key=created[0];panel=self.c.flow_cards[key].panel
        self.assertEqual(panel.choose.text(),'未綁定 · 選擇節點');self.assertTrue(panel.choose.property('modulePending'))
        d=ImageInputDialog(self.c,key);d.show();QTest.qWait(5)
        d.workflow.setCurrentIndex(d.workflow.findData('B'));d.target.setCurrentIndex(d.target.findData('10'))
        self.assertTrue(d.save_button.isEnabled());QTest.mouseClick(d.save_button,Qt.MouseButton.LeftButton);d.apply();d.deleteLater()
        self.assertEqual(self.c.data()['image_inputs'][key]['node'],'10');self.assertIn('#10',panel.choose.text())
        self.assertFalse(panel.choose.property('modulePending'))
        d=ImageInputDialog(self.c,key);self.assertEqual(d.target.currentData(),'10');d.reject();d.deleteLater()
        self.c.commit(lambda s:next(p for p in s['generation']['profiles'] if p['id']=='B')['graph'].pop('10'))
        card=self.c.flow_cards[key]
        self.assertIn('重新選擇',card.panel.choose.text());self.assertTrue(card.panel.status.text())
        self.click_card(card,card.actions()['collapse'])
        self.assertTrue(card.fold_message);self.assertFalse(card.proxy.isVisible())
        self.assertGreater(card.height,max(p.y() for p in self.c.ports.values() if p.parentItem() is card)+40)
