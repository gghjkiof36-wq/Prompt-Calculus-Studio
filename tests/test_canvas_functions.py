import copy,json,os,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QPoint,QPointF,QMimeData,QUrl
from PySide6.QtGui import QImage,QDragEnterEvent,QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_studio.window import Window
from prompt_studio.core import build_prompt,validate_state
from prompt_studio.generation import active_profile,submission,validate_profile
from prompt_studio.workflow_import import import_graph,infer_profile,WIDGETS
from prompt_studio.snapshots import make_snapshot
from prompt_studio.text_canvas import CanvasPalette
from prompt_studio.media import import_image
from prompt_studio import composition as comp
from test_generation import workflow
APP=QApplication.instance() or QApplication([])

def canvas_workflow(mode='img2img'):
    graph=workflow(mode)['graph']; nodes=[]; links=[]
    for ident,node in graph.items():
        inputs=[]; values=[]
        for name,value in node['inputs'].items():
            if isinstance(value,list):
                link=len(links)+1; links.append([link,int(value[0]),value[1],int(ident),len(inputs),'ANY']); inputs.append(dict(name=name,link=link))
        for field in WIDGETS[node['class_type']]:
            if field=='control_after_generate': values.append('randomize')
            elif field=='upload': values.append('image')
            else: values.append(node['inputs'][field])
        nodes.append(dict(id=int(ident),type=node['class_type'],inputs=inputs,widgets_values=values))
    return dict(nodes=nodes,links=links)

class WorkflowImportTests(unittest.TestCase):
    def test_canvas_json_detects_modes_and_positive_image_bindings(self):
        for mode in ('txt2img','img2img'):
            graph=import_graph(canvas_workflow(mode)); profile=infer_profile(graph,'example'); validate_profile(profile)
            self.assertEqual(profile['mode'],mode); self.assertEqual(profile['prompt'],['2','text'])
            self.assertEqual(profile['image'],'4' if mode=='img2img' else '')
            self.assertEqual(graph['6']['inputs']['seed'],2**63+123); self.assertEqual(graph['6']['inputs']['steps'],28)
            self.assertNotIn('control_after_generate',graph['6']['inputs']); self.assertEqual(graph['3']['inputs']['text'],'negative untouched')
    def test_custom_widget_order_is_not_guessed(self):
        data=canvas_workflow(); data['nodes'][0]['type']='UnknownDynamicNode'
        with self.assertRaisesRegex(ValueError,'API'): import_graph(data)
    def test_malformed_canvas_reports_an_import_error(self):
        for value in ({'nodes':[None]},{'nodes':[{'id':1,'inputs':[None]}]}, {'nodes':[{'id':1}],'definitions':3}, {'nodes':[{'id':1}],'links':{}}):
            with self.assertRaises(ValueError): import_graph(value)

class FunctionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'qa'); self.root=Path(self.tmp.name)
        self.w=Window(self.root/'data'); self.w.state['settings'].update(online=False,material='solid'); self.w.apply_theme(); self.w.show()
        self.w.set_interface_mode('canvas'); QTest.qWait(35); self.c=self.w.canvas
        self.image=self.root/'來源.png'; picture=QImage(320,240,QImage.Format.Format_RGB32); picture.fill(0xff426078); picture.save(str(self.image))
    def tearDown(self): self.w.close(); APP.processEvents(); self.tmp.cleanup()
    def tags(self):
        self.c.add_tag('1girl'); self.c.add_tag('smile'); return list(self.w.state['uses'])
    def test_drag_nested_module_out_to_canvas_then_undo_and_reload(self):
        parent,child=self.tags(); self.c.nest(child,parent)
        self.c.move_cards({parent:[-600,0]}); nested=next(k for k in self.c.cards if ':' in k)
        before=build_prompt(self.w.state); self.c.fit(); view=self.c.view
        card=self.c.cards[nested]; anchor=QPointF(80,18)
        start=view.mapFromScene(card.mapToScene(anchor))
        finish=view.mapFromScene(self.c.cards[parent].sceneBoundingRect().topLeft()-QPointF(220,160))
        # Fit the source and destination together, without changing the drop.
        view.centerOn(view.mapToScene(start+finish)/2)
        start=view.mapFromScene(card.mapToScene(anchor))
        finish=view.mapFromScene(self.c.cards[parent].sceneBoundingRect().topLeft()-QPointF(220,160))
        destination=view.mapToScene(finish)-anchor
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=start)
        QTest.mouseMove(view.viewport(),finish,30); QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=finish); QTest.qWait(40)
        self.assertEqual(len(self.w.state['uses']),2)
        new_id=next(k for k in self.w.state['uses'] if k!=parent)
        self.assertEqual(self.w.state['uses'][new_id]['prompt'],'smile'); self.assertFalse(self.w.state['uses'][parent]['children'])
        self.assertIsNone(self.c.cards[new_id].parentItem()); self.assertLess((self.c.cards[new_id].pos()-destination).manhattanLength(),3)
        after=build_prompt(self.w.state); self.assertEqual(self.w.final.toPlainText(),after)
        self.c.undo(); self.assertEqual(build_prompt(self.w.state),before); self.assertEqual(len(self.w.state['uses']),1)
        self.c.redo(); self.assertEqual(build_prompt(self.w.state),after)
        self.w.persist(); self.assertEqual(build_prompt(self.w.store.load()),after)
    def test_extract_preserves_subtree_weights_disabled_state_and_sizes(self):
        parent,child=self.tags(); self.c.nest(child,parent); nested=next(k for k in self.c.cards if ':' in k)
        part=self.c.selected_node(self.w.state,nested)[2]
        part.update(weight=17,enabled=False,children=[comp.node('detail','detail')],overlays=[comp.node('replacement','replacement')])
        expected=copy.deepcopy(part); self.c.refresh()
        self.w.state.setdefault('text_sizes',{})[nested]=[440,290]
        child_key=parent+':'+part['children'][0]['id']; self.w.state['text_sizes'][child_key]=[360,180]
        self.assertTrue(self.c.extract_node(nested,QPointF(-350,150)))
        new_id=next(k for k in self.w.state['uses'] if k!=parent)
        expected['grouped']=True; self.assertEqual(self.w.state['uses'][new_id],expected)
        self.assertEqual(self.w.state['text_sizes'][new_id],[440,290])
        self.assertEqual(self.w.state['text_sizes'][new_id+':'+part['children'][0]['id']],[360,180])
        self.assertEqual(self.w.state['output_order'],[parent,new_id]); validate_state(self.w.state)
    def test_child_released_inside_parent_stays_nested(self):
        parent,child=self.tags(); self.c.nest(child,parent); nested=next(k for k in self.c.cards if ':' in k)
        card=self.c.cards[nested]; card.setPos(card.pos()+QPointF(20,10))
        self.assertFalse(self.c.finish_node_drop(card,self.c.cards[parent].sceneBoundingRect().center()))
        self.assertEqual(len(self.w.state['uses']),1)
    def test_drag_module_into_module_then_undo_keeps_order_and_weights(self):
        parent,child=self.tags(); before=build_prompt(self.w.state); self.c.move_cards({parent:[-500,-220],child:[-500,120]}); self.c.fit()
        view=self.c.view; a=view.mapFromScene(self.c.cards[child].mapToScene(QPointF(45,45))); b=view.mapFromScene(self.c.cards[parent].mapToScene(QPointF(45,45)))
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=a); QTest.mouseMove(view.viewport(),b,30); QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=b); QTest.qWait(40)
        self.assertEqual(build_prompt(self.w.state),'(1girl, smile:1.0)'); self.assertEqual(len(self.w.state['uses']),1)
        self.c.undo(); self.assertEqual(build_prompt(self.w.state),before); self.c.redo(); self.assertEqual(build_prompt(self.w.state),'(1girl, smile:1.0)')
    def test_receiving_preview_expands_then_returns_and_cycles_reject(self):
        parent,child=self.tags(); target=self.c.cards[parent]; moving=self.c.cards[child]; height=target.height
        self.c.preview_node_drop(moving,target.mapToScene(QPointF(30,30))); self.assertGreater(target.height,height)
        self.c.clear_drop_preview(); self.assertEqual(target.height,height)
        self.c.nest(child,parent); nested=next(k for k in self.c.cards if ':' in k); before=copy.deepcopy(self.w.state)
        self.assertFalse(self.c.nest(parent,nested)); self.assertEqual(self.w.state,before)
    def test_standalone_image_docking_uses_saved_workflow_and_detach_keeps_image(self):
        self.w.generation_panel.save_profile(workflow('txt2img')); self.w.generation_panel.save_profile(workflow())
        self.w.generation_panel.mode.setCurrentIndex(0); self.w.final.setPlainText('keep current prompt')
        key=self.c.functions.add_image(path=self.image,position=QPointF(700,200))
        self.assertEqual(self.w.state['generation']['mode'],'txt2img')
        self.c.functions.attach(key); profile=active_profile(self.w.state)
        self.assertEqual(profile['mode'],'img2img'); self.assertEqual(self.w.state['generation']['source']['name'],'來源.png')
        self.assertEqual(self.w.final.toPlainText(),'keep current prompt')
        payload=submission(profile,make_snapshot(self.w.state),self.w.state['generation']['source'],'prompt_studio/source.png')
        self.assertEqual(payload['prompt']['4']['inputs']['image'],'prompt_studio/source.png')
        self.assertEqual(payload['prompt']['2']['inputs']['text'],'keep current prompt')
        self.c.functions.attach(key,False); self.assertEqual(self.w.state['generation']['mode'],'txt2img')
        self.assertIsNone(self.w.state['generation']['source']); self.assertIsNotNone(self.c.functions.data()['images'][key]['source'])
    def test_drop_picture_creates_independent_card_and_survives_reopen(self):
        self.w.final.setPlainText('manual'); mime=QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(self.image))])
        point=QPoint(30,40); enter=QDragEnterEvent(point,Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        APP.sendEvent(self.c.view.viewport(),enter); self.assertTrue(enter.isAccepted())
        drop=QDropEvent(QPointF(point),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier); APP.sendEvent(self.c.view.viewport(),drop)
        self.assertEqual(len(self.c.functions.data()['images']),1); self.assertEqual(self.w.final.toPlainText(),'manual')
        self.assertNotEqual(self.w.state.get('generation',{}).get('mode'),'img2img'); validate_state(self.w.state)
        self.w.close(); APP.processEvents(); self.w=Window(self.root/'data'); self.w.show(); QTest.qWait(60)
        self.assertEqual(len(self.w.canvas.functions.cards),1)
    def test_empty_preview_has_no_dead_thumbnail_or_save_controls(self):
        record=import_image(self.image,self.w.store.directory,''); record['path']=str(self.root/'deleted.png'); self.w.catalog.put('recent',record)
        self.c.results.refresh(); self.assertEqual(self.c.results.images.count(),0); self.assertTrue(self.c.results.images.isHidden())
        self.assertTrue(self.c.results.save_button.isHidden()); self.assertEqual(self.c.results.preview.text(),'尚無圖片')
    def test_img2img_source_bottom_aligns_and_temporary_txt2img_keeps_puzzle(self):
        self.w.generation_panel.mode.setCurrentIndex(1); key=next(iter(self.c.functions.cards)); card=self.c.functions.cards[key]
        self.assertAlmostEqual(card.y()+card.height,self.c.output.y()+self.c.output.height)
        self.c.functions.replace(key,self.image); source=copy.deepcopy(self.w.state['generation']['source'])
        self.w.generation_panel.mode.setCurrentIndex(0)
        self.assertTrue(self.c.functions.data()['images'][key]['attached']); self.assertEqual(self.w.state['generation']['source'],source)
        self.assertTrue(self.c.functions.replace(key,self.image)); self.assertEqual(self.w.state['generation']['mode'],'txt2img')
        self.c.output.setPos(180,100); self.c.functions.layout()
        self.assertAlmostEqual(card.y()+card.height,self.c.output.y()+self.c.output.height)
        self.w.generation_panel.mode.setCurrentIndex(1); self.assertEqual(len(self.c.functions.cards),1)
    def test_current_picture_expands_and_recent_sheet_preserves_canvas_view(self):
        record=import_image(self.image,self.w.store.directory,''); self.w.catalog.put('recent',record); self.c.results.refresh()
        card=self.c.preview_card; card.requested_size=[540,760]; card.layout_card(); QTest.qWait(40)
        large=self.c.results.preview.iconSize(); self.assertGreater(self.c.results.preview.height(),self.c.results.height()*.65)
        card.requested_size=[400,360]; card.layout_card(); QTest.qWait(40)
        self.assertLess(self.c.results.preview.iconSize().height(),large.height()); self.assertTrue(self.c.results.images.isHidden())
        self.c.view.scale(.8,.8); transform=self.c.view.transform(); center=self.c.view.mapToScene(self.c.view.viewport().rect().center())
        self.w.show_page(self.w.recent); QTest.qWait(40); sheet=self.w.recent_sheet
        self.assertTrue(self.c.isVisible()); self.assertIs(self.w.canvas_content.currentWidget(),self.c)
        self.assertEqual(sheet.geometry(),self.w.surface_stack.rect())
        self.assertLess(sheet.surface.width(),sheet.width());self.assertLess(sheet.surface.height(),sheet.height())
        self.assertGreater(sheet.surface.x(),0);self.assertGreater(sheet.surface.y(),0)
        self.assertLess(sheet.surface.geometry().right(),sheet.width()-1)
        self.assertLess(sheet.surface.geometry().bottom(),sheet.height()-1)
        self.assertTrue(sheet.isAncestorOf(self.w.recent)); self.assertTrue(self.w.recent.images.isVisible())
        QTest.keyClick(sheet,Qt.Key.Key_Escape); QTest.qWait(30)
        self.assertIsNone(self.w.recent_sheet); self.assertEqual(self.c.view.transform(),transform)
        self.assertEqual(self.c.view.mapToScene(self.c.view.viewport().rect().center()),center)
        self.assertGreaterEqual(self.w.tabs.indexOf(self.w.recent),0)
    def test_settings_top_tabs_back_forward_and_escape(self):
        w=self.w; w.settings('workflows'); w.settings_page.comfy_tabs.setCurrentIndex(1)
        self.assertIs(w.settings_page.comfy_tabs.currentWidget(),w.models)
        w.go_back(); self.assertEqual(w.settings_page.comfy_tabs.currentIndex(),0)
        w.go_forward(); self.assertEqual(w.settings_page.comfy_tabs.currentIndex(),1)
        QTest.keyClick(w.settings_page,Qt.Key.Key_Escape); QTest.qWait(30)
        self.assertIs(w.surface_stack.currentWidget(),w.canvas_shell); w.go_back(); self.assertIs(w.surface_stack.currentWidget(),w.settings_page)
    def test_workflow_drop_autobinds_and_preserves_prompt(self):
        path=self.root/'圖生圖.json'; path.write_text(json.dumps(canvas_workflow()),encoding='utf-8')
        self.w.final.setPlainText('preserved'); self.w.settings('workflows'); target=self.w.settings_page.workflows
        mime=QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path))]); point=QPoint(40,40)
        enter=QDragEnterEvent(point,Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier); APP.sendEvent(target,enter)
        drop=QDropEvent(QPointF(point),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier); APP.sendEvent(target,drop)
        self.assertEqual(active_profile(self.w.state)['mode'],'img2img'); self.assertEqual(self.w.final.toPlainText(),'preserved'); self.assertIn('已匯入圖生圖',target.feedback.text())
    def test_palette_search_shrinks_after_long_text(self):
        palette=CanvasPalette(self.c,None); palette.show(); QTest.qWait(20)
        short=palette.query.height(); palette.query.setPlainText('long tag '*100); self.assertGreater(palette.query.height(),short)
        palette.query.clear(); self.assertEqual(palette.query.height(),short); palette.reject()

if __name__=='__main__': unittest.main()
