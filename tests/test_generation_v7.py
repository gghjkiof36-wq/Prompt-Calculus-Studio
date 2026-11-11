"""B3-GEN: stored-wire compatibility and the formal document image-reuse path."""
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QLabel
from PySide6.QtTest import QTest
from prompt_calculus_studio import multi_output as model, clip_flow
from prompt_calculus_studio.core import validate_state
from prompt_calculus_studio.flow_data import add_image_input
from prompt_calculus_studio.generation import store_profile, validate_source, validate_generation
from prompt_calculus_studio.workflow_import import infer_mode, infer_profile, read_profile
from prompt_calculus_studio.image_input_routing import candidates, rewire_candidate
from prompt_calculus_studio.image_source_action import use_image, ImageInputChoice, NewImageInputDialog, ACTION
from prompt_calculus_studio.snapshots import make_snapshot, restore_snapshot, validate_snapshot
import test_workspace_shell as shell_fixture
from test_material_editor import state as new_state
from test_generation import workflow
from test_comfy_integration import png


def setup_inputs(state, count=1):
    profile=workflow(); profile['id']='gen-v7'; profile['name']='圖片工作流'
    for i in range(1,count):
        profile['graph']['load'+str(i)]=dict(class_type='LoadImage',inputs=dict(image='fixture.png'))
    store_profile(state,profile)
    data=state['multi_output']; output=data['current_output']
    clip=next(iter(data['clip_inputs']))
    clip_flow.set_binding(state,profile['id'],clip,('2','text'))
    if not any(c['source']==output and c['destination']==clip for c in data['connections']):
        model.connect(state,output,clip,'clip')
    keys=[]
    for i in range(count):
        key=add_image_input(state,(1250,450+i*320)); keys.append(key)
        data['image_inputs'][key].update(workflow=profile['id'],node='4' if i==0 else 'load'+str(i))
    return output,keys


def historical_edge(state, output):
    # Historical wire fixture, deliberately not created through the new editor.
    key='__source_historical'
    state.setdefault('canvas_functions',{}).setdefault('images',{})[key]=dict(source=None,attached=True)
    state.setdefault('text_positions',{})[key]=[-100,500]
    edge=dict(id='historical-wire',source=key,destination=output,kind='image')
    state['multi_output']['connections'].append(edge)
    return edge


class GenerationV7CoreTests(unittest.TestCase):
    def test_new_connect_rejects_all_image_origins_but_stored_edges_roundtrip(self):
        state=new_state(); output,inputs=setup_inputs(state)
        edge=historical_edge(state,output); validate_state(state)
        canvas=next(iter(state['multi_output']['canvases']))
        from prompt_calculus_studio.module_contracts import can_connect
        from prompt_calculus_studio.scheduler_wiring import connect as wire
        for source in (edge['source'],canvas,next(iter(state['multi_output']['stages']))):
            before=copy.deepcopy(state)
            self.assertFalse(model.valid_edge(state,source,output,'image'))
            self.assertFalse(can_connect(state,source,output,'image'))
            self.assertTrue(model.valid_edge(state,source,output,'image',stored=True))
            for operation in (model.connect,wire):
                with self.assertRaises(ValueError):operation(state,source,output,'image')
                self.assertEqual(state,before)
        self.assertTrue(model.valid_edge(state,canvas,output,'text'))
        for mode in ('txt2img','img2img'):
            state['generation']['mode']=mode
            # A legal old profile's mode is a compatibility value, not inferred.
            state['generation']['profiles'][0]['mode']=mode
            snap=make_snapshot(state); validate_snapshot(snap)
            restored=restore_snapshot(new_state(),snap)
            self.assertIn(edge,restored['multi_output']['connections'])
            self.assertEqual(restored['generation']['mode'],mode)
            self.assertEqual(restored['generation']['profiles'][0]['mode'],mode)
            validate_state(restored)

    def test_infer_whole_graph_and_new_writes_preserve_old_profiles_and_chosen(self):
        plain=workflow('txt2img')['graph']; offpath=copy.deepcopy(plain)
        offpath['offpath']=dict(class_type='LoadImage',inputs=dict(image='x.png'))
        multiple=copy.deepcopy(offpath); multiple['other_sampler']=copy.deepcopy(multiple['6'])
        multiple_images=copy.deepcopy(offpath);multiple_images['another_image']=dict(class_type='LoadImage',inputs=dict(image='y.png'))
        for graph,expected,binding in ((plain,'txt2img',None),(offpath,'img2img',None),
                (multiple,'img2img',None),(multiple_images,'img2img',None),(workflow()['graph'],'img2img','4')):
            with self.subTest(nodes=list(graph)):
                self.assertEqual(infer_mode(graph,binding),expected)
                self.assertEqual(infer_profile(graph,'fixture',modern=True)['mode'],expected)
                read=read_profile(graph,'fixture','new',dict(server='local',path='a.json'),modern=True)
                self.assertEqual(read['mode'],expected)
        self.assertEqual(infer_profile(offpath,'legacy')['mode'],'txt2img')
        state=new_state(); old=workflow('txt2img'); old['graph']=offpath
        state['generation']=dict(mode='img2img',profiles=[old],chosen={'txt2img':old['id']},source=None)
        before=copy.deepcopy(state['generation']); changed=copy.deepcopy(old);changed['name']='renamed'
        store_profile(state,changed)
        self.assertEqual(state['generation']['profiles'][0]['mode'],'txt2img')
        self.assertEqual(state['generation']['chosen'],before['chosen'])
        self.assertEqual(state['generation']['mode'],'txt2img')
        changed['id']='new-profile'; result=store_profile(state,changed)
        self.assertEqual(result['mode'],'img2img');self.assertEqual(state['generation']['chosen'],before['chosen'])
        validate_state(state)

    def test_exchange_uses_new_inference_without_rewriting_existing_profile_mode(self):
        from prompt_calculus_studio.workflow_transfer import apply_transfer
        state=new_state(); graph=workflow('txt2img')['graph']
        graph['offpath']=dict(class_type='LoadImage',inputs=dict(image='offpath.png'))
        transfer=dict(format='prompt_studio_workflow',version=1,id='exchange',name='交換工作流',graph=graph)
        candidate,profile=apply_transfer(state,transfer)
        self.assertEqual(profile['mode'],'img2img');self.assertEqual(candidate['generation']['mode'],'txt2img')
        self.assertNotIn('exchange',[p['id'] for p in state.get('generation',{}).get('profiles',[])])
        candidate['generation']['profiles'][0]['mode']='txt2img'
        candidate['generation']['chosen']={'txt2img':'exchange'};candidate['generation']['mode']='img2img'
        updated,profile=apply_transfer(candidate,transfer)
        self.assertEqual(profile['mode'],'txt2img');self.assertEqual(updated['generation']['mode'],'txt2img')
        self.assertEqual(updated['generation']['chosen'],candidate['generation']['chosen']);validate_state(updated)

    def test_source_validation_retains_none_and_rejects_same_unsafe_values(self):
        valid=dict(relative='originals/generation/source.png',name='source.png',sha256='a'*64,width=1,height=2)
        for source in (None,valid,dict(valid,relative='originals/generation/folder/fixture.png')):
            self.assertEqual(validate_source(source),source)
            validate_generation(dict(mode='img2img',profiles=[],chosen={},source=source))
        for field,value in [('width',True),('height',0),('name',None),('sha256','A'*64),('sha256','a'*63),
                ('relative','../escape.png'),('relative','other/image.png'),('relative','originals/generation/../x.png'),
                ('relative','originals/generation\\x.png'),('relative','C:/x.png'),('relative','')]:
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):validate_source(dict(valid,**{field:value}))
        for source in ('',[],False):
            with self.assertRaises(ValueError):validate_source(source)

    def test_candidates_reject_foreign_occupied_duplicate_invalid_and_group_ownership(self):
        state=new_state(); output,keys=setup_inputs(state,2); edge=historical_edge(state,output)
        self.assertEqual(len([r for r in candidates(state,output) if r['available']]),2)
        self.assertTrue(rewire_candidate(state,edge,keys[0]))
        model.connect(state,edge['source'],keys[0],'image')
        self.assertFalse(rewire_candidate(state,edge,keys[0]))
        data=state['multi_output']; data['image_inputs'][keys[1]]['node']='missing'
        self.assertFalse(any(r['available'] for r in candidates(state,output)))
        data['image_inputs'][keys[1]]['node']='4'
        self.assertFalse(any(r['available'] for r in candidates(state,output)))
        foreign=workflow();foreign['id']='foreign';store_profile(state,foreign)
        data['image_inputs'][keys[1]].update(workflow='foreign',node='4')
        self.assertNotIn(keys[1],[r['key'] for r in candidates(state,output)])
        clip=clip_flow.add(state);clip_flow.set_binding(state,'foreign',clip,('2','text'));model.connect(state,output,clip,'clip')
        self.assertEqual(candidates(state,output),[])
        self.assertFalse(rewire_candidate(state,edge,keys[1]))
        with self.assertRaises(ValueError):candidates(state,'removed-output')

    def test_v6_image_edge_and_direct_source_contract_remain_available(self):
        state=new_state();output,keys=setup_inputs(state);edge=historical_edge(state,output)
        state['multi_output']['connections'].remove(edge);state['multi_output']['version']=6
        self.assertTrue(model.valid_edge(state,edge['source'],output,'image'))
        model.connect(state,edge['source'],output,'image')
        self.assertTrue(any(e['destination']==output and e['kind']=='image' for e in state['multi_output']['connections']))
        state['generation']['mode']='img2img';changed=copy.deepcopy(state['generation']['profiles'][0])
        changed['mode']='txt2img';store_profile(state,changed)
        self.assertEqual(state['generation']['mode'],'img2img')
        self.assertEqual(state['generation']['profiles'][0]['mode'],'txt2img')


class GenerationV7UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):shell_fixture.WorkspaceShellTests.setUpClass()

    def setUp(self):
        from prompt_calculus_studio.comfy_client import ComfyClient
        self.network=patch.object(ComfyClient,'request',return_value=None);self.network.start();self.addCleanup(self.network.stop)
        self.f=shell_fixture.WorkspaceShellTests();self.f.setUp()
        self.tab=self.f.w.open_path(self.f.root/'Street.pcs-workflow');self.f.events()
        self.host=self.tab.host;self.c=self.host.canvas
        self.host.comfy.workflow_catalog.loaded=True;self.host.comfy.workflow_catalog.busy=False
        self.path=Path(self.f.area.name)/'synthetic.png'
        png(self.path,{'parameters':'DO NOT LOAD THIS PROMPT'})

    def tearDown(self):self.f.tearDown()

    def configure(self,count=1,legacy=False):
        result=[]
        def edit(state):
            output,keys=setup_inputs(state,count);result.extend((output,keys))
            if legacy:result.append(historical_edge(state,output))
        self.assertTrue(self.c.commit(edit));self.f.events();return result

    def assert_one_reuse(self,operation,target):
        before=copy.deepcopy(self.host.state); history=len(self.c.undo_stack)
        operation();self.f.events()
        added=set(self.host.state['canvas_functions']['images'])-set(before.get('canvas_functions',{}).get('images',{}))
        self.assertEqual(len(added),1);key=added.pop()
        self.assertEqual(len(self.c.undo_stack),history+1)
        self.assertTrue(any(e['source']==key and e['destination']==target for e in self.c.data()['connections']))
        self.assertNotIn('content',self.host.state['canvas_functions']['images'][key])
        self.assertEqual(self.c.data()['outputs'],before['multi_output']['outputs'])
        self.assert_disjoint(key)
        positions=copy.deepcopy(self.host.state['text_positions'])
        self.c.undo();self.f.events()
        self.assertEqual(self.c.data(),before['multi_output'])
        self.assertNotIn(key,self.host.state['canvas_functions'].get('images',{}))
        self.c.redo();self.f.events()
        self.assertEqual(self.host.state['text_positions'],positions);self.assert_disjoint(key)
        return key

    def assert_disjoint(self,*keys):
        cards={}
        for collection in (self.c.cards,self.c.containers,self.c.outputs,self.c.clips,
                           self.c.flow_cards,self.c.preview_cards,self.c.functions.cards):cards.update(collection)
        if self.c.order_card is not None:cards[self.c.order_card.key]=self.c.order_card
        for key in keys:
            rect=cards[key].sceneBoundingRect()
            for other,card in cards.items():
                if other!=key and card.parentItem() is None:
                    self.assertFalse(rect.intersects(card.sceneBoundingRect()),(key,other,rect,card.sceneBoundingRect()))

    def test_single_target_from_all_three_real_buttons_has_one_undo_and_no_prompt_import(self):
        output,keys=self.configure()
        from prompt_calculus_studio.pages import GalleryPage
        from prompt_calculus_studio.canvas_results import CanvasResults
        gallery=GalleryPage(self.host);self.addCleanup(gallery.deleteLater)
        preview=CanvasResults(self.host,'__preview_fixture__');self.addCleanup(preview.deleteLater)
        record=dict(id='synthetic',path=str(self.path),name='synthetic.png')
        for surface,button in ((gallery,gallery.img2img_button),(self.c.results,self.c.results.reuse),(preview,preview.reuse)):
            surface.record=record;button.setEnabled(True)
            self.assertEqual(button.text(),ACTION)
            self.assert_one_reuse(button.click,keys[0]);self.c.undo();self.f.events()

    def test_multiple_targets_require_choice_disabled_occupied_and_cancel_imports_nothing(self):
        output,keys=self.configure(3)
        self.assert_one_reuse(lambda:self.pick(keys[0]),keys[0])
        rows=candidates(self.host.state,output);dialog=ImageInputChoice(self.c,rows)
        dialog.show();self.f.events()
        self.assertEqual(dialog.selected(),keys[1]);self.assertTrue(dialog.submit.isEnabled())
        self.assertTrue(dialog.inputs.item(1).isSelected())
        occupied=dialog.inputs.item(0)
        self.assertFalse(occupied.flags() & (Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable))
        QTest.mouseClick(dialog.inputs.viewport(),Qt.MouseButton.LeftButton,pos=dialog.inputs.visualItemRect(occupied).center())
        self.assertFalse(occupied.isSelected());self.assertNotEqual(dialog.selected(),keys[0])
        QTest.keyClick(dialog.inputs,Qt.Key.Key_Down);self.assertEqual(dialog.selected(),keys[2])
        QTest.keyClick(dialog.inputs,Qt.Key.Key_Up);self.assertEqual(dialog.selected(),keys[1])
        QTest.keyClick(dialog.inputs,Qt.Key.Key_Up);self.assertEqual(dialog.selected(),keys[1])
        dialog.reject();dialog.deleteLater()
        before=copy.deepcopy(self.host.state);history=len(self.c.undo_stack)
        with patch.object(ImageInputChoice,'exec',return_value=QDialog.DialogCode.Rejected),patch('prompt_calculus_studio.image_bindings.import_source') as imported:
            self.assertIsNone(use_image(self.c,str(self.path),output));imported.assert_not_called()
        self.assertEqual(self.host.state,before);self.assertEqual(len(self.c.undo_stack),history)
        self.assert_one_reuse(lambda:self.pick(keys[2]),keys[2])

    def pick(self,key):
        def choose(dialog):
            index=next(i for i in range(dialog.inputs.count()) if dialog.inputs.item(i).data(Qt.ItemDataRole.UserRole)==key)
            dialog.inputs.setCurrentRow(index);self.assertTrue(dialog.submit.isEnabled())
            return QDialog.DialogCode.Accepted
        with patch.object(ImageInputChoice,'exec',choose):return use_image(self.c,str(self.path))

    def test_no_target_setup_cancel_and_atomic_create_with_one_undo(self):
        output,keys=self.configure(0);before=copy.deepcopy(self.host.state);history=len(self.c.undo_stack)
        with patch.object(NewImageInputDialog,'exec',return_value=QDialog.DialogCode.Rejected),patch('prompt_calculus_studio.image_bindings.import_source') as imported:
            self.assertFalse(self.host.generation_panel.set_source(str(self.path),output));imported.assert_not_called()
        self.assertEqual(self.host.state,before);self.assertEqual(len(self.c.undo_stack),history)
        def choose(dialog):
            self.assertFalse(dialog.save_button.isEnabled())
            dialog.workflow.setCurrentIndex(dialog.workflow.findData('gen-v7'))
            dialog.target.setCurrentIndex(dialog.target.findData('4'))
            self.assertTrue(dialog.hint.isHidden());self.assertEqual(dialog.hint.text(),'')
            self.assertTrue(dialog.can_submit());return QDialog.DialogCode.Accepted
        with patch.object(NewImageInputDialog,'exec',choose):key=use_image(self.c,str(self.path),output)
        self.assertTrue(key);self.assertEqual(len(self.c.undo_stack),history+1)
        inputs=set(self.c.data()['image_inputs'])-set(before['multi_output']['image_inputs']);self.assertEqual(len(inputs),1)
        target=inputs.pop();self.assertTrue(any(e['source']==key and e['destination']==target for e in self.c.data()['connections']))
        self.f.events();self.assert_disjoint(key,target);positions=copy.deepcopy(self.host.state['text_positions'])
        self.c.undo();self.f.events();self.assertEqual(self.c.data(),before['multi_output'])
        self.c.redo();self.f.events();self.assertIn(target,self.c.data()['image_inputs'])
        self.assertEqual(self.host.state['text_positions'],positions);self.assert_disjoint(key,target)
        self.assertTrue(self.f.w.save_tab(self.tab));path=self.tab.path
        self.f.w.close_tab(self.f.w.index_of(self.tab.key),pane=self.f.w.pane_for(self.tab),make_home=False)
        self.tab=self.f.w.open_path(path,new_tab=True);self.host=self.tab.host;self.c=self.host.canvas
        self.host.enter_canvas();self.f.events()
        self.assertEqual(self.host.state['text_positions'],positions);self.assert_disjoint(key,target)

    def test_source_prefers_empty_left_slot_and_escapes_oversized_canvas(self):
        output,keys=self.configure();self.host.enter_canvas();self.f.events()
        self.c.move_cards({keys[0]:[12000,0]});self.f.events()
        source=self.assert_one_reuse(lambda:use_image(self.c,str(self.path),output),keys[0])
        source_rect=self.c.functions.cards[source].sceneBoundingRect();target_rect=self.c.flow_cards[keys[0]].sceneBoundingRect()
        self.assertAlmostEqual(target_rect.left()-source_rect.right(),36)
        self.assertAlmostEqual(source_rect.top(),target_rect.top())
        self.c.undo();self.f.events()
        def cover(state):
            canvas=next(iter(state['multi_output']['canvases'].values()))
            canvas.update(position=[-5000,-5000],display_size=[10000,10000])
            state['text_positions'][keys[0]]=[0,0]
        self.c.commit(cover);self.f.events()
        self.assert_one_reuse(lambda:use_image(self.c,str(self.path),output),keys[0])

    def test_stale_choice_rejected_without_document_write_or_image_import(self):
        output,keys=self.configure(2)
        def choose(dialog):
            dialog.inputs.setCurrentRow(0)
            self.assertTrue(self.c.commit(lambda state:state['generation']['profiles'][0].update(name='changed during selection')))
            return QDialog.DialogCode.Accepted
        with patch.object(ImageInputChoice,'exec',choose),patch('prompt_calculus_studio.image_bindings.import_source') as imported:
            with self.assertRaisesRegex(ValueError,'圖片輸入已變更'):use_image(self.c,str(self.path),output)
            imported.assert_not_called()
        self.assertFalse(self.host.state.get('canvas_functions',{}).get('images'))

    def test_legacy_line_visible_delete_rewire_undo_second_view_and_reopen(self):
        output,keys,edge=self.configure(1,True)
        right=self.f.w.duplicate_view(self.tab,True);self.f.events()
        self.assertIn(edge['id'],right.host.canvas.lines)
        line=self.c.lines[edge['id']];self.assertTrue(line.legacy)
        self.assertEqual(line.legacy_label.text(),'舊連線 · 不送出圖片')
        source=self.c.line_port(edge,True);old_port=self.c.line_port(edge,False)
        self.assertIsNotNone(old_port);self.assertFalse(source.compatible(old_port))
        before=copy.deepcopy(self.c.data());history=len(self.c.undo_stack)
        gesture=self.c.connection_gesture;gesture.begin_line(edge,False,old_port.scenePos())
        gesture.finish(old_port.scenePos());self.f.events()
        self.assertEqual(self.c.data(),before);self.assertEqual(len(self.c.undo_stack),history)
        self.c.view.scene().clearSelection();line.setSelected(True);history=len(self.c.undo_stack)
        self.c.delete_selected();self.f.events();self.assertNotIn(edge['id'],self.c.lines)
        self.assertEqual(len(self.c.undo_stack),history+1);self.c.undo();self.f.events()
        self.assertIn(edge['id'],self.c.lines);self.assertIn(edge['id'],right.host.canvas.lines)
        target=next(p for p in self.c.ports.values() if p.key==keys[0] and p.kind=='image' and not p.output)
        gesture=self.c.connection_gesture
        gesture.begin_line(edge,False,self.c.line_port(edge,False).scenePos());self.assertTrue(target.highlight)
        gesture.finish(target.scenePos());self.f.events()
        self.assertNotIn(edge,self.c.data()['connections']);self.assertNotIn(edge['id'],right.host.canvas.lines)
        self.assertTrue(any(e['source']==edge['source'] and e['destination']==keys[0] for e in self.c.data()['connections']))
        self.c.undo();self.f.events();self.assertIn(edge,self.c.data()['connections'])
        self.c.redo();self.f.events();expected=copy.deepcopy(self.c.data()['connections'])
        self.assertTrue(self.f.w.save_tab(self.tab));path=self.tab.path
        for tab in (right,self.tab):self.f.w.close_tab(self.f.w.index_of(tab.key),pane=self.f.w.pane_for(tab),make_home=False)
        reopened=self.f.w.open_path(path,new_tab=True);self.f.events()
        self.assertEqual(reopened.host.state['multi_output']['connections'],expected)

    def test_new_prompt_has_no_image_port_in_card_preview_or_collapsed_form(self):
        from prompt_calculus_studio.module_preview import ModulePreview
        from prompt_calculus_studio.module_contracts import MODULES
        output,keys=self.configure();self.assertNotIn('image',MODULES['prompt'].inputs)
        self.assertFalse(any(p.key==output and p.kind=='image' and not p.output for p in self.c.ports.values()))
        self.c.commit(lambda s:s['multi_output']['outputs'][output].update(collapsed=True));self.f.events()
        self.assertFalse(any(p.key==output and p.kind=='image' and not p.output for p in self.c.ports.values()))
        preview=ModulePreview(self.c)
        try:
            preview.show_kind('output')
            self.assertFalse(any(p.kind=='image' and not p.output for p in preview.context.ports.values()))
        finally:preview.clear_preview();preview.deleteLater()

    def test_import_dialog_and_metadata_are_neutral_without_changing_embedded_mode(self):
        from prompt_calculus_studio.generation_panel import WorkflowDialog
        from prompt_calculus_studio.metadata_view import readable_metadata
        graph=workflow()['graph'];dialog=WorkflowDialog(self.host,graph,'txt2img')
        self.assertFalse(hasattr(dialog,'kind'));self.assertEqual(dialog.name.text(),'工作流')
        texts={control.text() for control in dialog.findChildren(QLabel)}
        self.assertFalse(texts & {'確認文字與圖片欄位。','文字欄由各最終 Prompt 的綁定按鈕指定。','未指定的參數沿用工作流。'})
        dialog.save();self.assertEqual(dialog.result_profile['mode'],'img2img');dialog.deleteLater()
        record=dict(metadata=dict(raw=dict(prompt_studio=dict(generation=dict(mode='img2img',workflow='fixture')))))
        before=copy.deepcopy(record);text=readable_metadata(record)
        self.assertNotIn('當次生成方式',text);self.assertIn('工作流：fixture',text);self.assertEqual(record,before)
        import json
        path=Path(self.f.area.name)/'中性名稱.json';path.write_text(json.dumps(graph),encoding='utf-8')
        feedback=[];self.host.generation_panel.importFeedback.connect(feedback.append)
        imported=self.host.generation_panel.import_workflow(path)
        self.assertEqual(imported['mode'],'img2img');self.assertEqual(feedback,['已匯入：中性名稱'])
        self.assertEqual(self.host.state['generation']['mode'],'txt2img')


if __name__=='__main__':unittest.main()
