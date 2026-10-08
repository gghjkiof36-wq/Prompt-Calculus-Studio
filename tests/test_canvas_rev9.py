"""Affected Canvas ownership and copy-editor contracts, isolated Qt only."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QToolTip
from prompt_calculus_studio import composition as comp, materials, multi_output as model
from prompt_calculus_studio.core import DEFAULT_SETTINGS
from prompt_calculus_studio.extension_page import ExtensionPage
from prompt_calculus_studio.window import Window

APP = QApplication.instance() or QApplication([])


class CanvasRev9Tests(unittest.TestCase):
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
        self.notices = []; self.w.notice = self.notices.append
        self.w.resize(1360, 900); self.w.show(); self.w.set_interface_mode('canvas')
        QTest.qWait(10)
        self.c = self.w.canvas
        self.cid = next(iter(self.c.data()['canvases']))
        self.other = self.c.add_canvas(QPointF(2200, 200))
        self.asset_id = None
        def create(state):
            self.asset_id = materials.save(state, dict(name='Clothing', prompt='red dress', module=None))
        self.c.commit(create)

    def tearDown(self):
        self.w.close(); APP.processEvents()

    def asset(self):
        return materials.item(self.w.state, self.asset_id)

    def add_copy(self):
        old = set(self.w.state['uses'])
        self.assertTrue(self.c.insert_asset_with_destination(self.asset(), QPointF(2240, 340), self.cid))
        return (set(self.w.state['uses']) - old).pop()

    def card_point(self, card, local, scale=.8):
        view=self.c.view; view.resetTransform(); view.scale(scale,scale); view.centerOn(card)
        APP.processEvents()
        point=view.mapFromScene(card.mapToScene(local))
        self.assertTrue(view.viewport().rect().contains(point))
        self.assertIs(view.interaction.card_at(point),card)
        self.assertIsNone(card.action_at(card.mapFromScene(view.mapToScene(point))))
        return point

    def double_click(self, point):
        viewport=self.c.view.viewport()
        # A native double click includes the first release. Sending only the
        # DblClick event misses actions accidentally fired by that release.
        QTest.mouseClick(viewport,Qt.MouseButton.LeftButton,pos=point)
        APP.processEvents()
        QTest.mouseDClick(viewport,Qt.MouseButton.LeftButton,pos=point)
        QTest.mouseRelease(viewport,Qt.MouseButton.LeftButton,pos=point)
        QTest.qWait(5)

    def test_routing_collapsed_card_body_edits_once_without_hidden_add(self):
        key=self.add_copy(); card=self.c.cards[key]; self.assertTrue(card.collapsed)
        before=self.c.history_state()
        for scale in (.55,.8,1.25):
            for local in (QPointF(28,card.header_height-12),QPointF(card.width/2,card.height-24),
                          QPointF(card.width/2,card.height-8)):
                with self.subTest(scale=scale,local=local):
                    point=self.card_point(card,local,scale)
                    with patch.object(self.c,'edit') as edit, patch.object(self.c,'palette') as add, \
                         patch.object(self.c,'quick_text') as quick, patch.object(self.c,'function_palette') as functions:
                        QTest.mouseClick(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point)
                        APP.processEvents()
                        add.assert_not_called(); edit.assert_not_called()
                        QTest.mouseDClick(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point)
                        QTest.mouseRelease(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point)
                        QTest.qWait(5)
                        edit.assert_called_once_with(key); add.assert_not_called()
                        quick.assert_not_called(); functions.assert_not_called()
        self.assertEqual(self.c.history_state(),before)

    def test_routing_nested_collapsed_card_and_expanded_padding_keep_their_target(self):
        key=self.add_copy(); child=comp.node('Nested copy','smile')
        self.c.commit(lambda state:state['uses'][key].update(collapsed=False,children=[child]))
        child_key=key+':'+child['id']; before=self.c.history_state()
        for target,local in ((child_key,QPointF(70,self.c.cards[child_key].height-10)),
                             (key,QPointF(self.c.cards[key].width/2,self.c.cards[key].height-48))):
            card=self.c.cards[target]; point=self.card_point(card,local)
            with patch.object(self.c,'edit') as edit, patch.object(self.c,'palette') as add, \
                 patch.object(self.c,'quick_text') as quick, patch.object(self.c,'function_palette') as functions:
                self.double_click(point)
                edit.assert_called_once_with(target); add.assert_not_called()
                quick.assert_not_called(); functions.assert_not_called()
        self.assertEqual(self.c.history_state(),before)

    def test_routing_true_blank_and_visible_add_button_keep_separate_actions(self):
        key=self.add_copy(); self.c.toggle_collapsed(key); card=self.c.cards[key]
        view=self.c.view; view.resetTransform(); view.scale(.8,.8); view.centerOn(card); APP.processEvents()
        point=view.mapFromScene(card.mapToScene(card.actions()['add'].center()))
        with patch.object(self.c,'palette') as add, patch.object(self.c,'edit') as edit:
            QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point); QTest.qWait(5)
            add.assert_called_once_with(target=key); edit.assert_not_called()
        before=self.c.history_state(); container=self.c.containers[self.cid]
        scene_point=container.mapToScene(QPointF(72,container.height-70))
        view.centerOn(scene_point); APP.processEvents(); point=view.mapFromScene(scene_point)
        self.assertIs(view.itemAt(point),container)
        with patch.object(self.c,'quick_text') as quick, patch.object(self.c,'palette') as add, \
             patch.object(self.c,'function_palette') as functions, patch.object(self.c,'edit') as edit:
            self.double_click(point)
            quick.assert_called_once(); self.assertEqual(quick.call_args.args[0],self.cid)
            add.assert_not_called(); functions.assert_not_called(); edit.assert_not_called()
        view.centerOn(8000,8000); APP.processEvents(); point=view.viewport().rect().center()
        self.assertIsNone(view.itemAt(point))
        with patch.object(self.c,'quick_text') as quick, patch.object(self.c,'palette') as add, \
             patch.object(self.c,'function_palette') as functions, patch.object(self.c,'edit') as edit:
            self.double_click(point)
            functions.assert_called_once(); add.assert_not_called(); quick.assert_not_called(); edit.assert_not_called()
        self.assertEqual(self.c.history_state(),before)

    def test_routing_collapsed_padding_opens_current_copy_dialog_and_cancel_is_isolated(self):
        key=self.add_copy(); before=self.c.history_state(); errors=[]; opened=[]; seen=[]
        card=self.c.cards[key]; point=self.card_point(card,QPointF(card.width/2,card.height-24))
        timer=QTimer(self.w); timer.setInterval(10)
        def cancel():
            dialog=getattr(self.c,'active_node_dialog',None)
            if dialog is None or dialog in seen: return
            seen.append(dialog)
            try:
                opened.append((dialog.windowTitle(),dialog.use_id))
                dialog.prompt.setPlainText('cancelled local edit')
                QTest.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel),
                                 Qt.MouseButton.LeftButton)
            except BaseException as error:
                errors.append(error)
                if dialog: dialog.reject()
        with patch.object(self.c,'palette') as add, patch.object(self.c,'quick_text') as quick, \
             patch.object(self.c,'function_palette') as functions:
            timer.timeout.connect(cancel); timer.start()
            self.double_click(point); QTest.qWait(25); timer.stop()
            add.assert_not_called(); quick.assert_not_called(); functions.assert_not_called()
        if errors: raise errors[0]
        self.assertEqual(opened,[('編輯當次模組',key)])
        self.assertEqual(self.c.history_state(),before); self.assertEqual(self.asset()['prompt'],'red dress')

    def edit_dialog(self, canvas, key, *, save):
        errors = []
        def input_and_close():
            try:
                dialog = canvas.active_node_dialog
                self.assertEqual(dialog.use_id, key.split(':')[0])
                dialog.name.setText('Local copy')
                dialog.prompt.setFocus(); dialog.prompt.selectAll(); QTest.keyClicks(dialog.prompt, 'blue dress')
                dialog.weight.setValue(1.7); dialog.enabled.setChecked(False)
                dialog.excludes.setPlainText('red dress\nsmile')
                buttons = dialog.findChild(QDialogButtonBox)
                choice = QDialogButtonBox.StandardButton.Save if save else QDialogButtonBox.StandardButton.Cancel
                QTest.mouseClick(buttons.button(choice), Qt.MouseButton.LeftButton)
            except BaseException as error:
                errors.append(error)
                canvas.active_node_dialog.reject()
        QTimer.singleShot(0, input_and_close)
        result = canvas.edit(key)
        if errors: raise errors[0]
        return result

    def test_fixed_destination_wins_over_position_and_copy_is_independent(self):
        first = self.add_copy(); second = self.add_copy()
        self.assertEqual(model.owner(self.w.state, first), self.cid)
        self.assertEqual(model.owner(self.w.state, second), self.cid)
        self.assertEqual(self.w.state['uses'][first]['source_revision'], materials.fingerprint(self.asset()))
        self.assertNotEqual(self.w.state['uses'][first]['id'], self.w.state['uses'][second]['id'])
        self.assertEqual(self.c.insertion_canvas, None)
        self.assertFalse(getattr(self.c, '_fixed_insertion_canvas', False))

    def test_deleted_destination_does_not_insert_or_create_history(self):
        before = copy.deepcopy(self.w.state); undo = len(self.c.undo_stack)
        self.assertFalse(self.c.insert_asset_with_destination(self.asset(), None, 'removed'))
        self.assertEqual(self.w.state, before); self.assertEqual(len(self.c.undo_stack), undo)

    def test_fixed_nested_target_preserves_path_and_rejects_foreign_owner(self):
        root = self.add_copy(); self.c.enter(root)
        self.assertTrue(self.c.add_tag_with_destination('smile', None, self.cid))
        parent = self.w.state['uses'][root]
        child = parent['children'][0]; key = root + ':' + child['id']
        self.c.enter(key); path = list(self.c.path)
        self.assertTrue(self.c.add_tag_with_destination('eyes', None, self.cid))
        self.assertEqual(self.c.root_id, root); self.assertEqual(self.c.path, path)
        self.assertEqual(comp.find(self.w.state['uses'][root], child['id'])['children'][0]['prompt'], 'eyes')
        before = copy.deepcopy(self.w.state)
        self.assertFalse(self.c.add_tag_with_destination('wrong', None, self.other))
        self.assertEqual(self.w.state, before)

    def test_root_dialog_cancel_then_save_is_one_undo_and_preserves_original(self):
        root = self.add_copy(); other = self.add_copy(); old = copy.deepcopy(self.w.state['uses'][root])
        before = len(self.c.undo_stack)
        self.assertFalse(self.edit_dialog(self.c, root, save=False))
        self.assertEqual(self.w.state['uses'][root], old); self.assertEqual(len(self.c.undo_stack), before)
        self.assertTrue(self.edit_dialog(self.c, root, save=True))
        saved = self.w.state['uses'][root]
        self.assertEqual((saved['prompt'], saved['weight'], saved['enabled'], saved['excludes']),
                         ('blue dress', 17, False, ['red dress', 'smile']))
        self.assertEqual(len(self.c.undo_stack), before + 1)
        self.assertEqual(self.asset()['prompt'], 'red dress')
        self.assertEqual(self.w.state['uses'][other]['prompt'], 'red dress')
        self.c.undo(); self.assertEqual(self.w.state['uses'][root], old)
        self.c.redo(); self.assertEqual(self.w.state['uses'][root], saved)

    def test_nested_dialog_save_and_undo_keeps_parent_and_original(self):
        root = self.add_copy(); self.c.enter(root)
        self.c.add_tag_with_destination('smile', None, self.cid)
        child = self.w.state['uses'][root]['children'][0]; key = root + ':' + child['id']
        old = copy.deepcopy(self.w.state['uses'][root]); before = len(self.c.undo_stack)
        self.assertTrue(self.edit_dialog(self.c, key, save=True))
        self.assertEqual(self.w.state['uses'][root]['prompt'], 'red dress')
        self.assertEqual(comp.find(self.w.state['uses'][root], child['id'])['prompt'], 'blue dress')
        self.assertEqual(len(self.c.undo_stack), before + 1)
        self.c.undo(); self.assertEqual(self.w.state['uses'][root], old)
        self.assertEqual(self.asset()['prompt'], 'red dress')

    def test_expanded_copy_editor_has_no_text_host_and_limits_destination(self):
        root = self.add_copy(); self.c.open_editor(self.cid); page = self.c.editor_page
        APP.processEvents()
        canvas = page.text_canvas
        self.assertFalse(canvas.add_tag_with_destination('wrong owner', None, self.other))
        self.assertTrue(self.edit_dialog(canvas, root, save=True))
        self.assertIsNone(page.prompt_editor); self.assertIsNone(page.text_sidebar)
        self.assertTrue(page.layer_toggle.isChecked())
        self.assertEqual(self.asset()['prompt'], 'red dress')
        page.close_editor(); APP.processEvents()
        self.assertIsNone(self.c.editor_page)

    def test_name_disclosure_toggles_root_and_long_nested_copy_without_edit_or_resize(self):
        key=self.add_copy(); original=copy.deepcopy(self.asset())
        def click_fold(target):
            card=self.c.cards[target]; before=card.collapsed
            rect=card.actions()['collapse']
            self.assertAlmostEqual(rect.center().y(),card.title_rect.center().y())
            self.assertGreater(rect.left(),card.title_rect.left())
            self.assertIsNone(card.resize_corner(rect.center()))
            for action in ('minus','plus','weight','add'):
                other=card.actions().get(action)
                if other is not None:self.assertFalse(rect.intersects(other),action)
            view=self.c.view;view.resetTransform();view.centerOn(card);APP.processEvents()
            point=view.mapFromScene(card.mapToScene(rect.center()))
            self.assertIs(view.interaction.card_at(point),card)
            with patch.object(self.c,'edit') as edit,patch.object(self.c,'palette') as add:
                QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point);QTest.qWait(5)
                edit.assert_not_called();add.assert_not_called()
            self.assertIs(self.c.cards[target].collapsed,not before)
            self.assertEqual(self.asset(),original)
        click_fold(key)
        child=comp.node('長名稱文字模組的折疊控制應留在名稱後方並保留新增與權重操作'*3,'smile')
        self.c.commit(lambda state:state['uses'][key].update(children=[child]))
        child_key=key+':'+child['id'];card=self.c.cards[child_key]
        self.assertNotEqual(card.title_text,child['name'])
        self.assertTrue(card.title_text.endswith('…'))
        click_fold(child_key);click_fold(child_key)
        click_fold(key)
        self.c.undo();self.assertFalse(self.c.cards[key].collapsed)
        self.c.redo();self.assertTrue(self.c.cards[key].collapsed)
        self.assertEqual(self.asset(),original)

    def test_module_hover_help_never_opens_a_tip_or_changes_card_geometry(self):
        key=self.add_copy();previous=set(self.c.data()['stages'])
        self.c.add_flow_node('stages',QPointF(3600,200));stage=(set(self.c.data()['stages'])-previous).pop()
        targets=[(self.c.cards[key],QPointF(80,45)),
                 (self.c.cards[key],self.c.cards[key].weight_rect.center()),
                 (self.c.containers[self.cid],QPointF(100,22)),
                 (self.c.flow_cards[stage],self.c.flow_cards[stage].actions()['size'].center())]
        before=self.c.history_state()
        for card,local in targets:
            view=self.c.view;view.resetTransform();view.centerOn(card);APP.processEvents()
            point=view.mapFromScene(card.mapToScene(local));size=card.boundingRect()
            QTest.mouseMove(view.viewport(),point);APP.processEvents()
            self.assertEqual(card.toolTip(),'')
            event=QHelpEvent(QEvent.Type.ToolTip,point,view.viewport().mapToGlobal(point))
            self.assertTrue(QApplication.sendEvent(view.viewport(),event));self.assertTrue(event.isAccepted())
            QTest.qWait(5)
            self.assertFalse(QToolTip.isVisible());self.assertEqual(card.boundingRect(),size)
        self.assertEqual(self.c.history_state(),before)


if __name__ == '__main__': unittest.main()
