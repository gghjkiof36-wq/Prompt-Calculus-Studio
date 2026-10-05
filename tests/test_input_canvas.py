"""Offscreen Canvas checks with generated 1-pixel fixtures only."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QDialog
from PySide6.QtCore import QPointF
from PySide6.QtTest import QTest
from prompt_calculus_studio.window import Window
from prompt_calculus_studio import multi_output as model,clip_flow
from prompt_calculus_studio.flow_data import endpoint,capture_inputs
from prompt_calculus_studio.flow_widgets import ImageInputDialog
from test_multi_output import workflow
from test_comfy_integration import png
APP=QApplication.instance() or QApplication([])


class InputCanvasTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1'});self.env.start();self.addCleanup(self.env.stop)
        self.w=Window(self.tmp.name);self.w.comfy.enabled=False;self.w.comfy.timer.stop();self.notices=[];self.w.notice=self.notices.append
        self.w.state['settings'].update(online=False,material='solid');self.w.show();self.w.set_interface_mode('canvas');QTest.qWait(30)
        self.c=self.w.canvas;self.cid=next(iter(self.c.data()['canvases']));self.out=self.c.data()['current_output']
    def tearDown(self):self.w.close();APP.processEvents()

    def scheduler(self):
        self.c.add_flow_node('schedulers',QPointF(1100,100));self.sid=next(iter(self.c.data()['schedulers']));return self.sid

    def profile(self):
        p=workflow();p['frontend_id']='native-A';p['graph']['20']=dict(class_type='LoadImage',inputs={'image':'input.png'})
        self.w.generation_panel.save_profile(p);return p

    def test_canvas_nodes_visible_edit_waiting_remove_undo_does_not_execute(self):
        self.profile();self.c.add_tag('first',self.c.containers[self.cid].pos()+QPointF(50,160));self.scheduler()
        self.notices.clear()
        self.c.commit(lambda s:model.connect(s,self.out,endpoint(self.sid,'clip1'),'clip'))
        clip=next(iter(self.c.data()['clip_inputs']))
        self.c.commit(lambda s:model.connect(s,endpoint(self.sid,'clip1'),clip,'clip'))
        self.c.commit(lambda s:clip_flow.set_binding(s,'flow',clip,('6','text')))
        self.assertIn(self.sid,self.c.flow_cards);panel=self.c.flow_cards[self.sid].panel
        self.w.comfy.connected=True;self.w.comfy.running=1;self.w.comfy.input_flow.execute(2);panel.refresh();self.assertEqual(panel.listing.count(),2)
        panel.listing.setCurrentRow(1);panel.selection_changed();panel.editor.setPlainText('edited waiting');panel.save_text()
        rows=self.w.comfy.input_flow.store.rows(self.sid)
        self.assertEqual(rows[1]['inputs'][endpoint(self.sid,'clip1')]['value'],'edited waiting')
        self.c.remove_flow_node('schedulers',self.sid);self.assertTrue(self.w.comfy.input_flow.store.control(self.sid)['paused'])
        self.c.undo();self.assertIn(self.sid,self.c.data()['schedulers']);self.assertEqual(len(self.w.comfy.input_flow.store.rows(self.sid)),2)
        self.assertIsNone(self.w.comfy.input_flow.current);self.assertFalse(hasattr(self.w,'canvas_queue'))
        self.assertEqual(self.notices,['已保存 2 項至預排程。','已保存這一項的修改。'])

    def test_image_source_raw_metadata_canvas_and_single_file_controls(self):
        path=Path(self.tmp.name)/'test.png';png(path,{'prompt':{'s':dict(inputs={'positive':['p',0]}),'p':dict(inputs={'text':'opaque, natural sentence'})}})
        key=self.c.functions.add_image(path,position=QPointF(-600,100))
        self.assertIsNotNone(key);self.c.commit(lambda s:model.connect(s,key,self.cid,'content'))
        self.assertEqual(model.compile_output(self.w.state,self.out)['final_prompt'],'opaque, natural sentence')
        members=self.c.data()['canvases'][self.cid]['source_members'];self.assertEqual(len(members),1)
        root=self.w.state['uses'][members[0]]
        self.assertEqual(root['name'],'test.png');self.assertEqual(root['prompt'],'opaque, natural sentence')
        self.assertIn(members[0],self.c.cards)
        self.assertTrue(self.c.functions.cards[key].panel.source_controls.isVisible())
        self.c.functions.cards[key].panel.live_control('pause')
        self.assertFalse(self.notices,self.notices)

    def test_image_input_lists_only_receivers_and_persists_all_new_nodes(self):
        self.profile();self.scheduler();self.c.add_flow_node('image_inputs',QPointF(1900,100))
        key=next(iter(self.c.data()['image_inputs']));dialog=ImageInputDialog(self.c,key)
        dialog.workflow.setCurrentIndex(dialog.workflow.findData('flow'))
        self.assertEqual([dialog.target.itemData(i) for i in range(dialog.target.count())],[None,'20'])
        dialog.target.setCurrentIndex(1);dialog.apply();dialog.close()
        self.assertEqual(self.c.data()['image_inputs'][key]['node'],'20')
        self.w.persist();self.w.close();APP.processEvents();self.w=Window(self.tmp.name);self.c=self.w.canvas
        self.assertIn(self.sid,self.c.data()['schedulers']);self.assertEqual(self.c.data()['image_inputs'][key]['node'],'20')

    def test_execute_button_accepts_more_while_native_running(self):
        self.profile();clip=next(iter(self.c.data()['clip_inputs']));self.c.commit(lambda s:clip_flow.set_binding(s,'flow',clip,('6','text')))
        client=self.w.comfy;client.connected=True;client.native_supported=True;client.flow_supported=True;client.snapshot_versions=[1,2,3,4]
        client.running=1;client.run_id='old-run';client.stateChanged.emit()
        from prompt_calculus_studio.run_controls import RunControls
        controls=self.c.execution_bar.controls
        controls.refresh();self.assertTrue(controls.run_button.isEnabled());self.assertEqual(controls.run_button.text(),'執行')

    def test_retained_inputs_can_restore_removed_panel_without_submission(self):
        from prompt_calculus_studio.flow_widgets import retained_records
        from PySide6.QtWidgets import QPushButton
        self.profile();self.scheduler();port=endpoint(self.sid,'clip1')
        self.c.commit(lambda s:model.connect(s,self.out,port,'clip'))
        clip=next(iter(self.c.data()['clip_inputs']))
        self.c.commit(lambda s:model.connect(s,port,clip,'clip'))
        self.c.commit(lambda s:clip_flow.set_binding(s,'flow',clip,('6','text')))
        self.w.comfy.connected=True;self.w.comfy.running=1;self.w.comfy.input_flow.execute();self.c.remove_flow_node('schedulers',self.sid)
        def display(dialog):
            next(b for b in dialog.findChildren(QPushButton) if b.text()=='顯示／恢復這份預排程').click()
            return QDialog.DialogCode.Accepted
        with patch('prompt_calculus_studio.flow_widgets.StudioDialog.exec',display):retained_records(self.w)
        self.assertIn(self.sid,self.c.data()['schedulers']);self.assertIsNone(self.w.comfy.input_flow.current)
        self.assertTrue(self.w.comfy.input_flow.store.control(self.sid)['paused'])
        self.assertEqual(len(self.w.comfy.input_flow.store.rows(self.sid)),1)

    def test_completed_result_view_rejects_foreign_result_and_displays_whole_own_batch(self):
        from prompt_calculus_studio.flow_widgets import show_job_images
        path=Path(self.tmp.name)/'result.png';png(path,{})
        images=[dict(filename='a.png'),dict(filename='b.png')]
        record=dict(route=dict(server=self.w.comfy.url),prompt_id='actual',outputs={'9':dict(images=images)})
        calls=[]
        def request(route,done,failed):calls.append(route);done([dict(prompt_id='foreign',path=str(path),node_id='9',image=images[0])])
        with patch.object(self.w.comfy,'request',request),patch('prompt_calculus_studio.flow_widgets.StudioDialog.exec') as shown:
            show_job_images(self.w,record);shown.assert_not_called()
        self.assertEqual(calls,['desktop/results?prompt_id=actual']);self.assertIn('沒有改用其他圖片',self.notices[-1])
        from PySide6.QtWidgets import QLabel
        captions=[]
        def display(dialog):captions.extend(w.text() for w in dialog.findChildren(QLabel));return QDialog.DialogCode.Accepted
        def own_request(route,done,failed):done([dict(prompt_id='actual',path=str(path),node_id='9',image=image) for image in images])
        with patch.object(self.w.comfy,'request',own_request),patch('prompt_calculus_studio.flow_widgets.StudioDialog.exec',display):show_job_images(self.w,record)
        self.assertTrue(any('a.png' in t for t in captions));self.assertTrue(any('b.png' in t for t in captions))


if __name__=='__main__':unittest.main()
