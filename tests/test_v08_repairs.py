import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QPointF,QPoint,QMimeData
from PySide6.QtGui import QImage,QColor,QDragEnterEvent,QDragMoveEvent,QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_studio.window import Window
from prompt_studio import multi_output as model
from prompt_studio.composition_image import freeze_source,canvas_document,render_image
from prompt_studio.core import validate_state,Storage
from prompt_studio.workflow_transfer import apply_transfer
from test_multi_output import workspace,workflow
from test_comfy_integration import Service
APP=QApplication.instance() or QApplication([])

class FlowRulesTests(unittest.TestCase):
    def test_unconnected_output_is_excluded_and_missing_text_blocks(self):
        state,cid,oid=workspace(); p=state['generation']['profiles'][0]
        edge=next(c for c in state['multi_output']['connections'] if c['source']=='out2' and c['kind']=='execution')
        model.disconnect(state,edge['id']); self.assertEqual([b['output'] for b in model.bound_texts(state,p)],[oid])
        model.connect(state,'out2',model.GENERATOR,'execution')
        edge=next(c for c in state['multi_output']['connections'] if c['destination']=='out2' and c['kind']=='text'); model.disconnect(state,edge['id'])
        with self.assertRaisesRegex(ValueError,'缺少來源'): model.bound_texts(state,p)
    def test_legacy_upgrade_preserves_text_and_does_not_reconnect_later(self):
        state,cid,oid=workspace(); state['multi_output']['version']=1
        state['multi_output']['connections']=[c for c in state['multi_output']['connections'] if c['kind']=='text']
        state['multi_output']['canvases'][cid]['name']='預設畫布'; state['canvas_functions']['images']={'__source_fixture':dict(source=None,attached=False)}
        model.connect(state,'__source_fixture',model.GENERATOR,'image'); before=model.compiled_outputs(state)
        upgraded=model.migrate(state); validate_state(upgraded); self.assertEqual(before,model.compiled_outputs(upgraded))
        self.assertEqual(upgraded['multi_output']['canvases'][cid]['name'],'畫布1')
        edge=next(c for c in upgraded['multi_output']['connections'] if c['kind']=='execution' and c['source']==oid)
        model.disconnect(upgraded,edge['id']); self.assertEqual(model.migrate(upgraded),upgraded)
    def test_workflow_transfer_is_atomic_and_queue_is_library_specific(self):
        state,cid,oid=workspace(); p=workflow(); value=dict(format='prompt_studio_workflow',version=1,id='flow',name='auto import',graph=p['graph'],bindings=copy.deepcopy(state['multi_output']['bindings']))
        candidate,profile=apply_transfer(state,value); self.assertEqual(profile['name'],'auto import'); self.assertEqual(state['generation']['profiles'][0]['name'],'flow')
        broken=copy.deepcopy(value); broken['bindings'][0]['node']='missing'
        with self.assertRaises(ValueError): apply_transfer(state,broken)
        with tempfile.TemporaryDirectory() as path:
            folder=Path(path); store=Storage(folder/'data'); store.save(state); store.close()
            service=Service(folder/'service',folder,folder); service.configure(library=str(folder/'data'))
            library=service.read_library()['library_id']; service.publish_workflow('id',library,value)
            service.publish_workflow('id',library,value)
            self.assertEqual(service.take_workflow('another'),{}); self.assertEqual(service.take_workflow(library)['id'],'id')
            restarted=Service(folder/'service',folder,folder); self.assertEqual(restarted.take_workflow(library)['value'],value)
            service.acknowledge_workflow('id',library); self.assertEqual(service.take_workflow(library),{}); self.assertEqual(service.workflow_status('id')['status'],'done')

class FlowUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1'}); self.env.start()
        self.w=Window(self.temp.name); self.w.state['settings'].update(online=False,material='solid',reduce_motion=True); self.w.show(); self.w.set_interface_mode('canvas'); QTest.qWait(20)
        self.c=self.w.canvas; self.cid=next(iter(self.c.containers)); self.oid=self.c.data()['current_output']; self.c.view.resetTransform()
    def tearDown(self): self.w.close(); APP.processEvents(); self.env.stop(); self.temp.cleanup()
    def drag(self,start,end):
        view=self.c.view; view.centerOn((start+end)/2); QTest.qWait(5)
        a=view.mapFromScene(start); b=view.mapFromScene(end)
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=a); QTest.mouseMove(view.viewport(),b,25)
        QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=b); QTest.qWait(20)
    def test_rewire_tail_to_blank_click_and_drag_then_undo(self):
        port=self.c.ports[(self.oid,'text',False)]; a=port.scenePos(); self.drag(a,a+QPointF(-130,-120))
        self.assertIsNone(self.c.data()['outputs'][self.oid]['canvas']); self.c.undo(); self.assertEqual(self.c.data()['outputs'][self.oid]['canvas'],self.cid)
        port=self.c.ports[(self.oid,'text',False)]; view=self.c.view; a=port.scenePos(); view.centerOn(a)
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=view.mapFromScene(a)); self.assertTrue(self.c.connection_gesture.latched)
        QTest.mouseMove(view.viewport(),view.mapFromScene(a+QPointF(-180,-130)),15)
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=view.mapFromScene(a+QPointF(-180,-130))); QTest.qWait(15)
        self.assertIsNone(self.c.data()['outputs'][self.oid]['canvas']); self.c.undo()
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=view.mapFromScene(self.c.ports[(self.oid,'text',False)].scenePos()))
        QTest.keyClick(view,Qt.Key.Key_Escape); self.assertEqual(self.c.data()['outputs'][self.oid]['canvas'],self.cid)
    def test_four_corners_keep_opposite_anchor_and_undo(self):
        cards=[self.c.outputs[self.oid],next(iter(self.c.clips.values())),self.c.preview_card]
        key=self.c.functions.add_image(); cards.append(self.c.functions.cards[key])
        for card in cards:
            self.c.move_cards({card.key:[3000,3000]})
            for corner in ('tl','tr','bl','br'):
                before=(card.x(),card.y(),card.width,card.height)
                left=corner.endswith('l'); top=corner.startswith('t')
                start=card.pos()+QPointF(5 if left else card.width-5,5 if top else card.height-5)
                self.drag(start,start+QPointF(-35 if left else 35,-30 if top else 30))
                self.assertAlmostEqual(card.width,before[2]+35,delta=2,msg=card.key+' '+corner); self.assertAlmostEqual(card.height,before[3]+30,delta=2,msg=card.key+' '+corner)
                self.assertAlmostEqual(card.x()+(card.width if left else 0),before[0]+(before[2] if left else 0),delta=2)
                self.assertAlmostEqual(card.y()+(card.height if top else 0),before[1]+(before[3] if top else 0),delta=2)
                self.c.undo(); self.assertAlmostEqual(card.width,before[2],delta=2)
            self.c.move_cards({card.key:[-3000,-3000]})
    def test_order_ghost_scale_grab_point_and_ratio_persistence(self):
        at=self.c.containers[self.cid].pos()+QPointF(25,80)
        self.c.add_tag('first',at); self.c.add_tag('second',at+QPointF(0,100)); panel=self.c.outputs[self.oid].panel; order=panel.order
        order.setCurrentRow(0); rect=order.visualItemRect(order.currentItem()); order._press_pos=rect.topLeft()+QPoint(23,9)
        for scale in (.45,1,1.8):
            self.c.view.resetTransform(); self.c.view.scale(scale,scale); image,hot=order.drag_preview()
            self.assertAlmostEqual(image.width()/image.devicePixelRatioF(),rect.width()*scale,delta=2); self.assertEqual(hot,QPoint(round(23*scale),round(9*scale)))
        key=order.item(0).data(Qt.ItemDataRole.UserRole); order.move_row(key,2); QTest.qWait(15); self.assertEqual(self.c.data()['canvases'][self.cid]['members'][-1],key)
        panel.split.setSizes([260,120]); panel.save_ratio(); ratio=self.c.data()['outputs'][self.oid]['panel_ratio']; self.w.persist()
        self.w.close(); APP.processEvents(); self.w=Window(self.temp.name); self.w.show(); self.w.set_interface_mode('canvas'); QTest.qWait(20); self.c=self.w.canvas
        panel=self.c.outputs[self.oid].panel; sizes=panel.split.sizes(); self.assertAlmostEqual(sizes[0]/sum(sizes),ratio,delta=.02)
    def test_images_need_outgoing_yellow_and_execution_and_embed_survives_disconnect(self):
        p=workflow(); self.w.generation_panel.save_profile(p); model.bind(self.w.state,'flow',next(iter(self.c.clips)),'6','text')
        path=Path(self.temp.name)/'photo.png'; image=QImage(80,40,QImage.Format.Format_RGB32); image.fill(QColor('red')); image.save(str(path))
        source=self.c.functions.add_image(path=path); self.c.commit(lambda s:model.connect(s,source,self.cid,'image'))
        with self.assertRaisesRegex(ValueError,'黃色|黄色'): freeze_source(self.w.state,self.w.store.directory)
        self.c.commit(lambda s:model.connect(s,self.cid,self.oid,'image')); frozen=freeze_source(self.w.state,self.w.store.directory)
        self.assertEqual((frozen['width'],frozen['height']),(80,40)); self.assertEqual(QImage(str(self.w.store.directory/frozen['relative'])).pixelColor(2,2).name(),'#ff0000')
        self.c.embed_source(source,self.cid); edge=next(c for c in self.c.data()['connections'] if c['destination']==self.cid and c['kind']=='image'); self.c.commit(lambda s:model.disconnect(s,edge['id']))
        doc=canvas_document(self.w.state,self.cid); self.assertEqual(len(doc['layers']),1); self.assertEqual(render_image(doc,self.w.store.directory).pixelColor(2,2).name(),'#ff0000')
        self.assertTrue(self.c.execution_bar.isVisible()); self.assertTrue(self.w.run_controls.activity.isHidden())
        self.assertEqual(self.w.run_controls.run_button.text(),'執行'); self.assertEqual(self.w.run_controls.stop.text(),'取消')
    def test_order_native_proxy_drop_uses_token_and_commits_new_order(self):
        at=self.c.containers[self.cid].pos()+QPointF(25,80)
        self.c.add_tag('first',at); self.c.add_tag('second',at+QPointF(0,100)); card=self.c.outputs[self.oid]; order=card.panel.order
        self.c.view.scale(.7,.7); self.c.view.centerOn(card); QTest.qWait(10)
        source=order.item(1).data(Qt.ItemDataRole.UserRole); order._source=source; order._drag_token=b'owned'; mime=QMimeData(); mime.setData(order.mime,b'owned')
        rect=order.visualItemRect(order.item(0)); local=rect.topLeft()+QPoint(15,3); point=self.c.view.mapFromScene(card.proxy.mapToScene(QPointF(order.viewport().mapTo(card.panel,local))))
        for cls in (QDragEnterEvent,QDragMoveEvent):
            event=cls(point,Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier); event.ignore(); APP.sendEvent(self.c.view.viewport(),event); self.assertTrue(event.isAccepted())
        event=QDropEvent(QPointF(point),Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier); APP.sendEvent(self.c.view.viewport(),event)
        self.assertTrue(event.isAccepted()); order._source=None; order._drag_token=b''; QTest.qWait(15)
        self.assertEqual(self.c.data()['canvases'][self.cid]['members'][0],source)
    def test_manager_bind_switch_remove_undo_and_canvas_names(self):
        c2=self.c.add_canvas(); self.assertEqual(self.c.data()['canvases'][self.cid]['name'],'畫布1'); self.assertEqual(self.c.data()['canvases'][c2]['name'],'畫布2')
        manager=self.w.settings_page.workflow_manager; self.w.generation_panel.save_profile(workflow()); manager.refresh()
        self.assertEqual(manager.list.count(),1); manager.bind_output('flow',next(iter(self.c.clips)),('7','text')); QTest.qWait(10)
        self.assertEqual(self.c.data()['bindings'][0]['node'],'7'); self.w.state['settings']['skip_workflow_delete_confirmation']=True; manager.remove(); self.assertEqual(manager.list.count(),0); manager.undo_remove(); self.assertEqual(manager.list.count(),1)
        self.assertEqual(self.c.data()['bindings'][0]['node'],'7'); self.w.settings_page.open('workflow_manager'); self.assertEqual(self.w.settings_page.comfy_tabs.currentIndex(),0)

    def test_preview_disconnect_gates_history_and_legacy_result_keeps_its_output(self):
        from prompt_studio.core import initial_state
        from prompt_studio.snapshots import make_snapshot
        path=Path(self.temp.name)/'old-result.png'; image=QImage(20,20,QImage.Format.Format_RGB32); image.fill(QColor('blue')); image.save(str(path))
        envelope=dict(schema_version=1,bindings=[dict(snapshot=make_snapshot(initial_state()))])
        self.w.catalog.put('recent',dict(id='old',name='old',path=str(path),created=1,metadata=dict(raw=dict(prompt_studio=envelope))))
        self.c.results.refresh(); self.assertEqual(self.c.results.ids,['old'])
        edge=next(c for c in self.c.data()['connections'] if c['kind']=='clip'); self.c.commit(lambda s:model.disconnect(s,edge['id']))
        self.assertEqual(self.c.results.ids,[]); self.assertFalse(self.c.results.preview.isEnabled()); self.assertIsNotNone(self.w.catalog.get('old'))
        self.c.undo(); self.assertEqual(self.c.results.ids,['old'])

if __name__=='__main__': unittest.main()


