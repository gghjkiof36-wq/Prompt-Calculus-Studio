"""Read-only browsing, atomic binding and stale-response protection; offline."""
import copy,unittest
from unittest.mock import patch
from PySide6.QtCore import Qt,QEvent
from PySide6.QtWidgets import QPushButton
from PySide6.QtTest import QTest
from shiboken6 import isValid
from prompt_studio.clip_widgets import ClipBindingDialog
from prompt_studio.image_binding_dialog import ImageBindingDialog
from prompt_studio.generation import active_profile
from prompt_studio.core import validate_state
import test_multi_canvas as canvas_tests
from test_multi_canvas import APP
from test_multi_output import workflow


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.transport=patch('prompt_studio.comfy_client.ComfyClient.request'); self.transport.start(); self.addCleanup(self.transport.stop)
        canvas_tests.MultiCanvasTests.setUp(self)
    tearDown=canvas_tests.MultiCanvasTests.tearDown

    def picker(self,image=False):
        manager=self.w.settings_page.workflow_manager
        manager.catalog.loaded=True; manager.catalog.files=['A.json','B.json']
        self.pending=[]
        def read(path,done,failed):self.pending.append((path,done,failed))
        self.mock=patch.object(manager.catalog,'read',read); self.mock.start(); self.addCleanup(self.mock.stop)
        self.key=self.canvas.functions.add_image() if image else next(iter(self.canvas.clips))
        dialog=ImageBindingDialog(self.canvas,self.key) if image else ClipBindingDialog(self.canvas,self.key)
        self.addCleanup(lambda:dialog.reject() if isValid(dialog) else None)
        return dialog

    def test_remote_nodes_read_in_picker_and_only_saved_with_binding(self):
        d=self.picker(); before=copy.deepcopy(self.w.state)
        self.assertEqual(d.workflow.count(),2); self.assertTrue(d.loading)
        self.pending[0][1](workflow()['graph'])
        self.assertEqual(self.w.state,before); self.assertGreater(d.target.count(),1)
        d.target.setCurrentIndex(d.target_index(('6','text'))); d.apply()
        p=d.profile(); self.assertIn(dict(workflow=p['id'],clip=self.key,node='6',field='text'),self.canvas.data()['bindings'])
        self.assertEqual(self.w.state['generation']['profiles'][0]['origin']['path'],'A.json')
        self.assertIsNone(active_profile(self.w.state))
        self.canvas.undo(); self.assertEqual(self.w.state.get('generation'),before.get('generation'))
        self.canvas.redo(); validate_state(self.w.state)
        self.w.persist(); self.assertEqual(self.w.store.load()['generation']['profiles'][0]['id'],p['id'])

    def test_switch_cancel_and_connection_change_discard_late_reads(self):
        d=self.picker(); before=copy.deepcopy(self.w.state)
        d.workflow.setCurrentIndex(1); old,new=self.pending
        old[1](workflow()['graph']); self.assertTrue(d.loading); self.assertIsNone(d.profile())
        new[1](workflow()['graph']); self.assertFalse(d.loading); self.assertEqual(d.profile()['origin']['path'],'B.json')
        d.reload_workflows()
        d.reject(); old[1](workflow()['graph']); self.assertEqual(self.w.state,before)

    def test_failure_retry_and_changed_workspace_do_not_apply_old_nodes(self):
        d=self.picker(); self.pending[0][2]('offline')
        self.assertIn('offline',d.hint.text()); self.assertFalse(d.target.isEnabled())
        d.read_current(); self.pending[-1][1](workflow()['graph']); self.assertTrue(d.target.isEnabled())
        self.w.comfy.epoch+=1; before=copy.deepcopy(self.w.state); d.apply(); self.assertEqual(self.w.state,before)
        d.staged.clear(); d.read_current(); request=self.pending[-1]
        self.w.state['workspace']='changed-workspace'; before=copy.deepcopy(self.w.state)
        request[1](workflow()['graph']); self.assertEqual(self.w.state,before)
        self.w.state['workspace']=d.owner[1]

    def test_image_picker_uses_same_catalog_and_manual_option(self):
        d=self.picker(image=True); self.assertIsNone(d.workflow.currentData()); self.assertFalse(self.pending)
        d.workflow.setCurrentIndex(1)
        graph=workflow()['graph']; graph['9']=dict(class_type='PreviewImage',inputs={})
        self.pending[-1][1](graph); d.target.setCurrentIndex(d.target.findData('9')); d.apply()
        binding=self.w.state['canvas_functions']['images'][self.key]['binding']
        self.assertEqual(binding['node'],'9'); self.assertEqual(self.w.state['generation']['profiles'][0]['id'],binding['workflow'])
        self.assertIsNone(active_profile(self.w.state))

    def test_manager_has_no_primary_workflow_and_read_does_not_choose(self):
        m=self.w.settings_page.workflow_manager
        m.catalog.loaded=True; m.catalog.files=['A.json']; m.rebuild()
        with patch.object(m.catalog,'read',lambda path,done,failed:done(workflow()['graph'])):m.read_selected()
        self.w.settings('workflows'); APP.processEvents()
        self.assertFalse(any(b.isVisible() and '活動工作流' in b.text() for b in m.findChildren(QPushButton)))
        self.assertIsNone(active_profile(self.w.state))

    def test_same_saved_workflow_is_one_profile_for_positive_and_negative_clips(self):
        d=self.picker(); self.pending[-1][1](workflow()['graph'])
        d.target.setCurrentIndex(d.target_index(('6','text'))); d.apply(); d.reject()
        self.w.state['generation']['profiles'][0]['values']['cfg']=3.25
        prior=copy.deepcopy(self.w.state['generation']['profiles'][0])
        key=self.canvas.add_clip(output=self.oid); other=ClipBindingDialog(self.canvas,key); self.addCleanup(other.reject)
        self.pending[-1][1](workflow()['graph'])
        self.assertFalse(other.target.model().item(other.target_index(('6','text'))).isEnabled())
        other.target.setCurrentIndex(other.target_index(('7','text'))); other.apply()
        self.assertEqual(len(self.w.state['generation']['profiles']),1)
        self.assertEqual(self.w.state['generation']['profiles'][0],prior)
        self.assertEqual(len(self.canvas.data()['bindings']),2); validate_state(self.w.state)

    def test_cancelled_and_invalid_reads_never_import_a_profile(self):
        d=self.picker(); before=copy.deepcopy(self.w.state); done=self.pending[-1][1]
        done({'nodes':[dict(id=1,type='UnknownCustomNode',inputs=[],widgets_values=[])]})
        self.assertIn('API',d.hint.text()); self.assertFalse(d.target.isEnabled()); self.assertEqual(self.w.state,before)
        d.read_current(); done=self.pending[-1][1]; d.reject(); done(workflow()['graph'])
        self.assertEqual(self.w.state,before); self.assertFalse(d.staged)

    def test_refresh_reads_four_unsaved_clips_from_native_without_saving_or_submitting(self):
        d=self.picker();self.pending[-1][1](workflow()['graph'])
        before=copy.deepcopy(self.w.state);routes=[];self.w.comfy.native_inspect_supported=True
        graph=copy.deepcopy(workflow()['graph'])
        graph['91']=dict(class_type='CLIPTextEncode',inputs=dict(text='third unsaved'))
        graph['92']=dict(class_type='CLIPTextEncode',inputs=dict(text='fourth unsaved'))
        def request(route,data=None,done=None,failed=None,**_):
            routes.append(route)
            if route.startswith('/userdata?'):done(['A.json','B.json'])
            elif route=='workflow/native/inspect':done(dict(id=data['id'],state='pending'))
            elif route=='workflow/native/status':done(dict(state='inspected',inspection=dict(epoch=2,identity=dict(path='A.json',frontend_id='live-A'),workflow=dict(id='live-A',nodes=[]),output=graph)))
            else:raise AssertionError(route)
        with patch.object(self.w.comfy,'request',request):
            refresh=next(b for b in d.findChildren(QPushButton) if b.text()=='重新整理')
            QTest.mouseClick(refresh,Qt.MouseButton.LeftButton);QTest.qWait(150)
        self.assertFalse(d.loading);self.assertEqual(len([d.target.itemData(i) for i in range(d.target.count()) if d.target.itemData(i)]),4)
        self.assertEqual(d.profile()['frontend_id'],'live-A');self.assertEqual(self.w.state,before)
        self.assertEqual(routes,['/userdata?dir=workflows&recurse=true','workflow/native/inspect','workflow/native/status'])

    def test_saved_fallback_is_visible_and_ambiguous_live_draft_never_falls_back(self):
        d=self.picker();self.pending[-1][1](workflow()['graph'])
        self.assertIn('已儲存版本',d.source_hint.text())
        d.staged.clear();self.w.comfy.native_inspect_supported=True
        before=len(self.pending)
        with patch.object(self.w.comfy,'request',lambda route,data=None,done=None,failed=None,**_:failed('ambiguous [native_ambiguous]')):
            d.read_current()
        self.assertEqual(len(self.pending),before);self.assertIn('native_ambiguous',d.hint.text());self.assertFalse(d.target.isEnabled())

    def test_deleted_picker_ignores_late_native_receipt_without_touching_qt(self):
        d=self.picker();self.pending[-1][1](workflow()['graph']);d.staged.clear()
        self.w.comfy.native_inspect_supported=True;callbacks=[]
        with patch.object(self.w.comfy,'request',lambda route,data=None,done=None,failed=None,**_:callbacks.append((done,failed))):d.read_current()
        before=copy.deepcopy(self.w.state);d.deleteLater();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(d))
        callbacks[0][0](dict(state='inspected',inspection=dict(epoch=0,identity=dict(path='A.json',frontend_id='live-A'),workflow=dict(id='live-A',nodes=[]),output=workflow()['graph'])))
        self.assertEqual(self.w.state,before)



if __name__=='__main__':unittest.main()
