"""Synthetic/offscreen editing; no real ComfyUI service or generated images."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt,QPoint,QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QCheckBox,QDialog,QLineEdit,QApplication,QPushButton
from PySide6.QtTest import QTest
from test_stage_parameter_ui import StageParameterUITests
from prompt_calculus_studio.stage_parameter_panel import open_parameters,ParameterSheet
from prompt_calculus_studio.stage_editor_drafts import read,write
from prompt_calculus_studio.stage_widgets import PendingList
from prompt_calculus_studio.widgets import widget_global_position


class ModuleUITests(StageParameterUITests):
    def test_close_keeps_invalid_and_empty_drafts_without_apply(self):
        before=copy.deepcopy(self.c.data()['stages'][self.stage])
        sheet=self.sheet();self.edit(sheet,'35','cfg','-')
        with patch.object(sheet,'confirm_close',return_value=True):sheet.request_close()
        QTest.qWait(10);sheet=self.sheet()
        self.assertEqual(sheet.findChild(QLineEdit,'parameter_35_cfg').text(),'-')
        self.assertFalse(sheet.apply_button.isEnabled())
        self.assertEqual(self.c.data()['stages'][self.stage],before)
        editor=self.edit(sheet,'35','cfg','');QTest.keyClick(editor,Qt.Key.Key_Backspace)
        write(self.w.store,'skip-close-reminder',True);sheet.request_close();QTest.qWait(10)
        sheet=self.sheet();self.assertEqual(sheet.findChild(QLineEdit,'parameter_35_cfg').text(),'')
        self.assertFalse(self.executor.submissions);sheet.finish()

    def test_actual_close_checkbox_and_outside_click_preserve_draft(self):
        sheet=self.sheet();self.edit(sheet,'35','cfg','3')
        observed=[]
        def accept_reminder():
            dialog=QApplication.activeModalWidget()
            if dialog:
                box=dialog.findChild(QCheckBox);observed.append(box.text());box.setChecked(True);dialog.accept()
        QTimer.singleShot(30,accept_reminder)
        QTest.mouseClick(sheet,Qt.MouseButton.LeftButton,pos=QPoint(2,2));QTest.qWait(20)
        self.assertEqual(observed,['不再提醒']);self.assertTrue(read(self.w.store,'skip-close-reminder'))
        sheet=self.sheet();self.assertEqual(sheet.findChild(QLineEdit,'parameter_35_cfg').text(),'3')
        with patch.object(sheet,'confirm_close',side_effect=AssertionError('must not prompt')):sheet.request_close()
        self.assertNotIn('parameters',self.c.data()['stages'][self.stage]);self.assertFalse(self.executor.submissions)

    def test_reopened_draft_conflict_is_not_silently_applied(self):
        sheet=self.sheet();self.edit(sheet,'35','cfg','3');write(self.w.store,'skip-close-reminder',True)
        sheet.request_close();QTest.qWait(10)
        self.c.commit(lambda state:state['multi_output']['stages'][self.stage].update(name='changed elsewhere'))
        sheet=self.sheet();sheet.apply();QTest.qWait(40)
        self.assertIn('已被修改',sheet.status.text());self.assertFalse(sheet.closed)
        self.assertNotIn('parameters',self.c.data()['stages'][self.stage]);sheet.finish()

    def test_apply_clears_stored_draft_only_after_success(self):
        sheet=self.sheet();self.edit(sheet,'35','cfg','3');write(self.w.store,'skip-close-reminder',True)
        key=sheet.draft_key;sheet.request_close();QTest.qWait(10);self.assertIsNotNone(read(self.w.store,key))
        sheet=self.sheet();sheet.apply();QTest.qWait(40)
        self.assertIsNone(read(self.w.store,key));self.assertEqual(self.c.data()['stages'][self.stage]['parameters']['patches'][0]['value'],3)
        self.assertFalse(self.executor.submissions)

    def test_draft_autosave_survives_independent_storage_reopen(self):
        from prompt_calculus_studio.core import Storage
        sheet=self.sheet();self.edit(sheet,'35','cfg','3');QTest.qWait(550)
        reopened=Storage(self.tmp.name)
        try:
            saved=read(reopened,sheet.draft_key)
            self.assertEqual(saved['fields'][0]['raw'],'3')
            self.assertNotIn('parameters',self.c.data()['stages'][self.stage])
        finally:reopened.db.close()
        sheet.finish();self.assertFalse(self.executor.submissions)

    def test_preparing_counts_as_activity_before_native_prompt_id(self):
        def only_configured(state):
            data=state['multi_output'];unused=set(data['stages'])-{self.stage}
            for key in unused:data['stages'].pop(key)
            data['connections']=[line for line in data['connections'] if line['source'] not in unused and line['destination'] not in unused]
        self.c.commit(only_configured)
        request=self.w.comfy.request;held=[]
        def delay(route,data=None,done=None,failed=None,**kwargs):
            if route=='workflow/native/start':held.append(data);return
            return request(route,data,done,failed,**kwargs)
        self.w.comfy.request=delay;self.click();self.controls.refresh()
        self.assertEqual(len(held),1,self.notices);self.assertEqual(self.controls.activity_count,1)
        self.assertFalse(self.executor.submissions)

    def test_output_preview_collapse_and_empty_manual_undo(self):
        panel=self.c.outputs[self.out].panel
        self.assertFalse(hasattr(panel,'more'));self.assertTrue(panel.editor.isVisible())
        old=panel.editor.toPlainText();panel.editor.setPlainText('one');panel.editor.setPlainText('')
        self.assertEqual(self.c.data()['outputs'][self.out]['draft'],'')
        self.c.undo();self.assertEqual(panel.editor.toPlainText(),old)
        self.c.redo();self.assertEqual(self.c.data()['outputs'][self.out]['draft'],'')
        panel.toggle_preview();QTest.qWait(5);self.assertFalse(panel.editor.isVisible())
        self.assertTrue(panel.copy_button.isVisible());self.assertEqual(panel.preview_toggle.text(),'展開預覽')
        panel.refresh();self.assertFalse(panel.editor.isVisible());self.assertFalse(self.executor.submissions)

    def test_schedule_item_context_uses_scaled_view_position(self):
        from prompt_calculus_studio.flow_data import add_scheduler
        keys=[];self.c.commit(lambda state:keys.append(add_scheduler(state)))
        panel=self.c.flow_cards[keys[0]].panel;panel.list.show();panel.empty.hide()
        self.c.view.resetTransform();self.c.view.scale(.62,.62);self.c.view.centerOn(self.c.flow_cards[keys[0]])
        QTest.qWait(10);panel.list.show();panel.list.addItem('synthetic');panel.list.item(0).setData(Qt.ItemDataRole.UserRole,'one')
        panel.list.doItemsLayout();position=panel.list.visualItemRect(panel.list.item(0)).center()
        event=QContextMenuEvent(QContextMenuEvent.Reason.Mouse,position,QPoint(999,999))
        expected=widget_global_position(panel.list.viewport(),position,self.c.view)
        with patch.object(panel,'item_menu') as menu:panel.list.contextMenuEvent(event)
        menu.assert_called_once_with('one',expected);self.assertTrue(event.isAccepted())
        panel.list.clear();self.assertFalse(self.executor.submissions)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ModuleUITests(name) for name in ModuleUITests.__dict__ if name.startswith('test_'))
