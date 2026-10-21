"""Canvas binding dialogs keep the document client and survive tab changes."""
import copy
from pathlib import Path
import sys
import traceback
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QEvent, QTimer, QPoint
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from shiboken6 import isValid
from prompt_calculus_studio.comfy_client import ComfyClient
from prompt_calculus_studio.clip_widgets import ClipBindingDialog
from prompt_calculus_studio.image_binding_dialog import ImageBindingDialog
from prompt_calculus_studio.stage_parameter_panel import open_parameters
from prompt_calculus_studio.workspace_files import create_file
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from test_multi_output import workflow


class WorkspaceBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.area=tempfile.TemporaryDirectory(prefix='pcs-workspace-binding-')
        self.root=Path(self.area.name)/'files';self.root.mkdir()
        self.errors=[];self.notices=[];self.requests=[]
        self.old_hook=sys.excepthook
        sys.excepthook=lambda kind,value,trace:self.errors.append(''.join(traceback.format_exception(kind,value,trace)))
        self.transport=patch.object(ComfyClient,'request',lambda client,*args,**kw:self.request(client,*args,**kw))
        self.transport.start()
        self.w=WorkspaceShell(self.root,Path(self.area.name)/'data')
        self.w.report_error=self.errors.append;self.w.show_notice=self.notices.append
        self.w.resize(1440,900);self.w.show()
        self.a=self.make('A');self.dialogs=[]

    def make(self,name):
        tab=self.w.open_path(create_file(self.root,'workflow',name),new_tab=True)
        self.assertIsNotNone(tab,self.errors)
        tab.host.notice=self.notices.append;tab.host.error=self.errors.append
        tab.host.comfy.timer.stop()
        return tab

    def request(self,client,route,data=None,done=None,failed=None,raw=None,content_type='application/json'):
        self.requests.append((client,route,copy.deepcopy(data)))
        if route.startswith('/userdata?') and done:done(['A.json'])
        elif route.startswith('/userdata/') and done:
            graph=workflow()['graph'];graph['9']=dict(class_type='PreviewImage',inputs={})
            done(graph)

    def tearDown(self):
        for dialog in self.dialogs:
            if isValid(dialog):dialog.reject()
        for tab in self.w.tabs_by_key.values():
            sheet=getattr(tab.host,'stage_parameter_sheet',None) if tab.host else None
            if sheet and isValid(sheet) and not sheet.closed:sheet.finish()
        QTest.qWait(30);self.w.close();QTest.qWait(30);self.w.deleteLater()
        self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents()
        self.transport.stop();sys.excepthook=self.old_hook;self.area.cleanup()
        self.assertFalse(self.errors,self.errors)

    def test_document_dialogs_do_not_require_settings_page(self):
        host=self.a.host;canvas=host.canvas
        self.assertFalse(hasattr(host,'settings_page'))
        clip=next(iter(canvas.data()['clip_inputs']))
        dialog=ClipBindingDialog(canvas,clip);self.dialogs.append(dialog)
        self.assertGreater(dialog.target.count(),1)
        image=canvas.functions.add_image()
        image_dialog=ImageBindingDialog(canvas,image);self.dialogs.append(image_dialog)
        image_dialog.workflow.setCurrentIndex(1)
        self.assertGreaterEqual(image_dialog.target.findData('9'),0)
        stage=next(iter(canvas.data()['stages']))
        sheet=open_parameters(canvas,stage)
        self.assertIs(dialog.catalog,image_dialog.catalog)
        self.assertIs(dialog.catalog,sheet.catalog)
        self.assertIs(dialog.catalog.client,host.comfy)
        self.assertFalse(hasattr(host,'settings_page'))

    def bind_from_button(self,control,target,*,image=False):
        seen=[];failures=[]
        def select():
            dialog=self.app.activeModalWidget()
            try:
                self.assertIsInstance(dialog,ImageBindingDialog if image else ClipBindingDialog)
                seen.append(dialog.catalog.client)
                if image:dialog.workflow.setCurrentIndex(1)
                index=dialog.target.findData(target) if image else dialog.target_index(target)
                self.assertGreaterEqual(index,0)
                dialog.target.setCurrentIndex(index)
                self.assertTrue(dialog.save_button.isEnabled())
                QTest.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton)
            except Exception as exc:
                failures.append(exc)
                if dialog:dialog.reject()
        deadline=QTimer(self.w);deadline.setSingleShot(True)
        deadline.timeout.connect(lambda:self.app.activeModalWidget().reject() if self.app.activeModalWidget() else None)
        deadline.start(2000);QTimer.singleShot(0,select)
        QTest.mouseClick(control,Qt.MouseButton.LeftButton)
        deadline.stop();deadline.deleteLater()
        if failures:raise failures[0]
        self.assertEqual(len(seen),1)
        self.app.processEvents()
        return seen[0]

    def test_clip_button_saves_reopens_and_keeps_undo_and_other_document(self):
        b=self.make('B');before=copy.deepcopy(b.host.state)
        self.w.focus_tab(self.a);canvas=self.a.host.canvas;clip=next(iter(canvas.data()['clip_inputs']))
        client=self.bind_from_button(canvas.clips[clip].panel.binding,('6','text'))
        self.assertIs(client,self.a.host.comfy)
        binding=copy.deepcopy(canvas.data()['bindings'])
        self.assertEqual(binding[0]['node'],'6');self.assertEqual(binding[0]['clip'],clip)
        self.assertEqual(b.host.state,before)
        self.w.activateWindow();QTest.qWait(20)
        QTest.mouseClick(canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(5,5))
        QTest.keyClick(canvas.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(canvas.data()['bindings'],[])
        QTest.keyClick(canvas.view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(canvas.data()['bindings'],binding)
        self.assertTrue(self.a.document.save(),self.a.document.error)
        path=self.a.path
        self.assertTrue(self.w.close_tab(self.w.index_of(self.a.key),pane=self.w.pane_for(self.a)))
        QTest.qWait(30)
        reopened=self.w.open_path(path,new_tab=True)
        self.assertEqual(reopened.host.canvas.data()['bindings'][0]['node'],'6')
        dialog=ClipBindingDialog(reopened.host.canvas,next(iter(reopened.host.canvas.data()['clip_inputs'])))
        self.dialogs.append(dialog)
        self.assertEqual(dialog.target.currentData(),('6','text'))
        self.assertFalse(any(route in ('/prompt','workflow/native/start') for _,route,_ in self.requests))

    def test_tab_switch_and_cancel_do_not_apply_late_reads_to_another_document(self):
        a=self.a.host;b=self.make('B').host;pending=[]
        catalog=a.comfy.workflow_catalog;catalog.loaded=True;catalog.files=['A.json']
        before_a=copy.deepcopy(a.state);before_b=copy.deepcopy(b.state)
        with patch.object(catalog,'read_draft',side_effect=lambda path,profile,done,failed:pending.append(done)):
            dialog=ClipBindingDialog(a.canvas,next(iter(a.canvas.data()['clip_inputs'])))
            self.dialogs.append(dialog)
            self.assertIsNot(catalog,b.comfy.workflow_catalog)
            dialog.reject();pending[0](workflow()['graph'],'saved')
        self.assertEqual(a.state,before_a);self.assertEqual(b.state,before_b)
        duplicate=self.w.duplicate_view(self.a,True)
        self.assertIs(duplicate.host.comfy.workflow_catalog,catalog)

    def test_image_button_and_stage_parameters_use_the_document_catalog(self):
        from PySide6.QtWidgets import QLineEdit
        from stage_parameter_fixture import add_samplers,inspection
        canvas=self.a.host.canvas;host=self.a.host
        image=canvas.functions.add_image()
        self.bind_from_button(canvas.functions.cards[image].panel.binding,'9',image=True)
        binding=host.state['canvas_functions']['images'][image]['binding']
        self.assertEqual(binding['node'],'9')
        profile=add_samplers(copy.deepcopy(host.state['generation']['profiles'][0]))
        profile['frontend_id']='native-A';host.comfy.connected=True
        stage=next(iter(canvas.data()['stages']))
        with patch.object(host.comfy.workflow_catalog,'read_draft',side_effect=lambda path,prior,done,failed:done(inspection(profile),'native')):
            QTest.mouseClick(canvas.flow_cards[stage].panel.edit_button,Qt.MouseButton.LeftButton)
            sheet=host.stage_parameter_sheet
            sheet.workflow.setCurrentIndex(sheet.workflow.findData(profile['id']))
            QTest.qWait(20);self.assertFalse(sheet.loading,sheet.status.text())
            sheet.jump(['35']);QTest.qWait(10)
            field=sheet.findChild(QLineEdit,'parameter_35_cfg');self.assertIsNotNone(field)
            field.setFocus();QTest.keyClick(field,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier);QTest.keyClicks(field,'3')
            QTest.mouseClick(sheet.apply_button,Qt.MouseButton.LeftButton)
            self.assertTrue(sheet.closed,sheet.status.text())
        stage_state=canvas.data()['stages'][stage]
        self.assertEqual(stage_state['workflow'],profile['id'])
        self.assertEqual(stage_state['parameters']['patches'][0]['value'],3.0)
        self.assertTrue(self.a.document.save(),self.a.document.error)
        self.assertFalse(any(route in ('/prompt','workflow/native/start') for _,route,_ in self.requests))


if __name__=='__main__':unittest.main()
