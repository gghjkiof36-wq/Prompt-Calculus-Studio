"""Real Qt input events and independent workspace round trips."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt,QPointF,QMimeData,QTimer
from PySide6.QtGui import QDragEnterEvent,QDragMoveEvent,QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import test_083_runtime_repair as runtime
from prompt_studio import multi_output as model
from prompt_studio.core import validate_state
from prompt_studio.snapshots import make_snapshot
from prompt_studio.window import Window

APP=QApplication.instance() or QApplication([])


class EditingRepairTests(unittest.TestCase):
    setUp=runtime.RuntimeRepairTests.setUp
    tearDown=runtime.RuntimeRepairTests.tearDown

    def switch(self,key):
        self.w.workspace.setCurrentIndex(self.w.workspace.findData(key));QTest.qWait(20)

    def test_empty_new_duplicate_edit_switch_reopen_and_snapshot_scope(self):
        first=self.w.state['workspace'];first_graph=copy.deepcopy(self.c.data())
        self.c.outputs[self.out].panel.editor.setPlainText('')
        with patch('prompt_studio.window.QInputDialog.getText',return_value=('01',True)):self.w.new_workspace()
        second=self.w.state['workspace'];self.assertNotEqual(first,second)
        self.assertFalse(self.w.state.get('uses'));self.assertFalse(self.c.data()['bindings'])
        self.assertNotEqual(set(first_graph['canvases']),set(self.c.data()['canvases']))
        c2=next(iter(self.c.data()['canvases']))
        self.c.add_tag('second only',self.c.containers[c2].pos()+QPointF(50,150))
        second_uses=copy.deepcopy(self.w.state['uses'])
        self.switch(first);self.assertEqual(self.w.state['uses']['test-root']['prompt'],'A')
        self.assertEqual(self.w.state['draft'],'');self.assertEqual(self.c.data()['bindings'],first_graph['bindings'])
        snapshot=make_snapshot(self.w.state);self.assertNotIn('workspace_scenes',snapshot['state'])
        self.assertEqual(len(snapshot['state']['workspaces']),1)
        self.switch(second);self.assertEqual(self.w.state['uses'],second_uses)
        with patch('prompt_studio.window.QInputDialog.getText',return_value=('copy',True)):self.w.new_workspace(duplicate=True)
        self.assertEqual(self.w.state['uses'],second_uses)
        self.assertEqual(len(self.w.comfy.generation.records()),0)
        self.w.persist();self.w.close();APP.processEvents();self.w=Window(self.tmp.name);self.c=self.w.canvas
        self.w.show();self.w.set_interface_mode('canvas');QTest.qWait(20)
        self.switch(first);self.assertEqual(self.w.state['uses']['test-root']['prompt'],'A');self.assertEqual(self.w.state['draft'],'')
        self.switch(second);self.assertEqual(self.w.state['uses'],second_uses)
        validate_state(self.w.state)

    def test_delete_undo_after_async_refresh_and_redo_with_keys(self):
        self.c.view.setFocus();self.c.view.scene().clearSelection();self.c.cards['test-root'].setSelected(True)
        QTest.keyClick(self.c.view,Qt.Key.Key_Delete);QTest.qWait(10)
        self.assertNotIn('test-root',self.w.state['uses'])
        self.w.state['generation']['profiles'][0]['graph']['3']['inputs']['seed']=999
        self.c.refresh();self.w.comfy.stateChanged.emit()
        QTest.keyClick(self.c.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(10)
        self.assertIn('test-root',self.w.state['uses'],self.notices)
        self.assertEqual(model.owner(self.w.state,'test-root'),self.cid)
        self.assertEqual(self.w.state['generation']['profiles'][0]['graph']['3']['inputs']['seed'],999)
        QTest.keyClick(self.c.view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier);QTest.qWait(10)
        self.assertNotIn('test-root',self.w.state['uses'])

    def test_drag_connected_input_blank_disconnect_esc_and_undo(self):
        view=self.c.view;view.resetTransform();view.scale(.6,.6)
        port=self.c.ports[(self.clip,'clip',False)]
        view.centerOn(port.scenePos()+QPointF(-160,-80));APP.processEvents()
        start=view.mapFromScene(port.scenePos());end=start+QPointF(-100,-140).toPoint()
        before=copy.deepcopy(self.c.data()['connections'])
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=start)
        QTest.mouseMove(view.viewport(),end,20);QTest.keyClick(view,Qt.Key.Key_Escape)
        QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=end);QTest.qWait(10)
        self.assertEqual(self.c.data()['connections'],before)
        start=view.mapFromScene(self.c.ports[(self.clip,'clip',False)].scenePos());end=start+QPointF(-100,-140).toPoint()
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=start);QTest.mouseMove(view.viewport(),end,20)
        QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=end);QTest.qWait(15)
        self.assertEqual(len(self.c.data()['connections']),len(before)-1,self.notices)
        QTest.keyClick(view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(10)
        self.assertEqual(self.c.data()['connections'],before)

    def test_direct_image_text_port_and_visible_raw_source_no_duplicate(self):
        from pathlib import Path
        from test_comfy_integration import png
        p=Path(self.tmp.name)/'metadata.png';png(p,{'prompt':{'s':dict(inputs={'positive':['p',0]}),'p':dict(inputs={'text':'opaque, full sentence'})}})
        key=self.c.functions.add_image(p,position=QPointF(-500,-1200))
        # Exercise the actual generic blue Prompt input with a clip/text alias.
        view=self.c.view;self.c.fit();APP.processEvents()
        source=self.c.ports[(key,'clip',True)];dest=self.c.ports[(self.out,'text',False)]
        a=view.mapFromScene(source.scenePos());b=view.mapFromScene(dest.scenePos())
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=a);QTest.mouseMove(view.viewport(),b,25)
        QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=b);QTest.qWait(15)
        self.assertEqual(model.compile_output(self.w.state,self.out)['final_prompt'],'A\nopaque, full sentence',self.notices)
        self.c.commit(lambda s:model.connect(s,key,self.cid,'clip'))
        members=self.c.data()['canvases'][self.cid]['source_members'];self.assertEqual(len(members),1)
        self.assertEqual(self.w.state['uses'][members[0]]['name'],'metadata.png')
        # Each explicitly connected source contributes once; remove the direct
        # path to verify the source card has no hidden duplicate raw text.
        line=next(c for c in self.c.data()['connections'] if c['source']==key and c['destination']==self.out)
        self.c.commit(lambda s:model.disconnect(s,line['id']))
        self.assertEqual(model.compile_output(self.w.state,self.out)['final_prompt'],'opaque, full sentence\nA')

    def test_schedule_drop_selection_and_full_item_editor(self):
        from prompt_studio.flow_data import add_scheduler,endpoint
        keys=[]
        def connect(state):
            sid=add_scheduler(state);keys.append(sid);port=endpoint(sid,'clip1')
            model.connect(state,self.out,port,'clip');model.connect(state,port,self.clip,'clip')
        self.c.commit(connect);sid=keys[0];runner=self.w.comfy.input_flow;self.w.comfy.running=1
        for text in ('A full prompt','B full prompt','C full prompt'):
            self.c.commit(lambda s:s['uses']['test-root'].update(prompt=text));runner.execute()
        panel=self.c.flow_cards[sid].panel;listing=panel.listing;panel.refresh();self.c.fit();APP.processEvents()
        rows=runner.store.rows(sid);ids=[r['id'] for r in rows]
        listing.setCurrentRow(2);selected=listing.currentItem();panel.refresh();self.assertIs(listing.currentItem(),selected)
        self.assertNotIn('full prompt',selected.text())
        listing._source=ids[2];listing._drag_token=b'real-schedule-event';listing.dragging=True
        mime=QMimeData();mime.setData(listing.mime,listing._drag_token)
        point=listing.visualItemRect(listing.item(0)).topLeft()+QPointF(12,3).toPoint()
        for event in (QDragEnterEvent(point,Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier),
                      QDragMoveEvent(point,Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier),
                      QDropEvent(QPointF(point),Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)):
            event.ignore();APP.sendEvent(listing.viewport(),event);self.assertTrue(event.isAccepted())
        listing._source=None;listing._drag_token=b'';listing.dragging=False;QTest.qWait(10);panel.refresh()
        self.assertEqual([r['id'] for r in runner.store.rows(sid)],[ids[2],ids[0],ids[1]])
        self.assertEqual(listing.currentItem().data(Qt.ItemDataRole.UserRole),ids[2])
        results=[]
        def edit():
            results.append(panel.editor.toPlainText());panel.editor.setPlainText('only this item')
            QTest.mouseClick(panel.save_button,Qt.MouseButton.LeftButton);panel.detail.accept()
        QTimer.singleShot(20,edit)
        QTest.mouseClick(listing.viewport(),Qt.MouseButton.LeftButton,pos=listing.visualItemRect(listing.currentItem()).center())
        self.assertEqual(results,['C full prompt'])
        self.assertEqual(next(iter(runner.store.read(ids[2])['inputs'].values()))['value'],'only this item')
        self.assertEqual(next(iter(runner.store.read(ids[0])['inputs'].values()))['value'],'A full prompt')
        transitioned=[]
        def submit_while_open():
            self.w.comfy.running=0;runner.pump();panel.refresh()
            self.executor.finish();panel.refresh()
            transitioned.append((panel.detail_item()['id'],panel.editor.isReadOnly(),panel.save_button.isEnabled(),panel.editor.toPlainText()))
            panel.detail.accept()
        QTimer.singleShot(20,submit_while_open)
        QTest.mouseClick(listing.viewport(),Qt.MouseButton.LeftButton,pos=listing.visualItemRect(listing.currentItem()).center())
        self.assertEqual(transitioned,[(ids[2],True,False,'only this item')])

    def test_selected_line_endpoint_rewire_preserves_other_fanout(self):
        second=self.c.add_clip(QPointF(1900,700),self.out)
        line=next(v for v in self.c.data()['connections'] if v['source']==self.out and v['destination']==self.clip)
        other=next(v for v in self.c.data()['connections'] if v['source']==self.out and v['destination']==second)
        graphics=self.c.lines[line['id']];self.c.fit();APP.processEvents();graphics.setSelected(True)
        view=self.c.view;start=view.mapFromScene(graphics.mapToScene(graphics.path().pointAtPercent(.88)))
        end=start+QPointF(10,-130).toPoint()
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=start);QTest.mouseMove(view.viewport(),end,25)
        QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=end);QTest.qWait(15)
        self.assertNotIn(line['id'],[c['id'] for c in self.c.data()['connections']])
        self.assertIn(other,self.c.data()['connections']);QTest.keyClick(view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertIn(line,self.c.data()['connections'])

    def test_png_restore_undo_keeps_workspace_scenes_and_async_catalog(self):
        first=self.w.state['workspace'];snapshot=make_snapshot(self.w.state)
        self.w.gallery.record={'metadata':{'raw':{'prompt_studio':{'schema_version':1,'bindings':[{'node_id':'6','snapshot':snapshot}]}}}}
        with patch('prompt_studio.pages.ask',return_value=True):self.w.gallery.restore_combination()
        restored=self.w.state['workspace'];self.assertNotEqual(first,restored)
        self.w.state['generation']['profiles'][0]['graph']['3']['inputs']['seed']=987
        self.c.view.setFocus();QTest.keyClick(self.c.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.w.state['workspace'],first,self.notices);validate_state(self.w.state)
        self.assertNotIn(restored,self.w.state['workspace_scenes']['items'])
        self.assertEqual(self.w.state['generation']['profiles'][0]['graph']['3']['inputs']['seed'],987)
        QTest.keyClick(self.c.view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.w.state['workspace'],restored,self.notices);validate_state(self.w.state)

    def test_shared_workflow_removal_and_restore_updates_all_workspace_bindings(self):
        from prompt_studio.workflow_deletion import prepare_remove,prepare_restore
        first=self.w.state['workspace']
        with patch('prompt_studio.window.QInputDialog.getText',return_value=('copy',True)):self.w.new_workspace(duplicate=True)
        second=self.w.state['workspace'];state,record=prepare_remove(self.w.state,'flow')
        self.assertFalse(state['multi_output']['bindings'])
        self.assertFalse(state['workspace_scenes']['items'][first]['multi_output']['bindings'])
        restored=prepare_restore(state,record);validate_state(restored)
        for key in (first,second):self.assertEqual(restored['workspace_scenes']['items'][key]['multi_output']['bindings'][0]['workflow'],'flow')


if __name__=='__main__':unittest.main()
