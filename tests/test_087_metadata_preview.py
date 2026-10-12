"""Synthetic image metadata, explicit text targets and display-only batch scope."""
import copy
import tempfile
import unittest
from pathlib import Path
from test_comfy_integration import png
from test_multi_output import workflow,workspace
from prompt_calculus_studio.image_source import read_content,metadata_sources
from prompt_calculus_studio.preview_scope import current_iteration
from prompt_calculus_studio.generation import profile_text_fields,submission
from prompt_calculus_studio.stage_parameters import identity


def describe(profile):
    profile.update(frontend_id='native-A',origin=dict(server='local',path='a.json'))
    profile['graph']['custom']=dict(class_type='ComposeString',inputs=dict(caption='untouched',mode='choose',linked=['6',0]))
    profile['parameter_description']=dict(version=1,identity=identity(profile),nodes=[dict(id='custom',path=['custom'],class_type='ComposeString',fields=[
        dict(field='caption',type='STRING',editable=True),dict(field='mode',type='ENUM',editable=True),dict(field='linked',type='STRING',editable=True)])])
    return profile


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'sample.png'
        self.graph={'sampler':dict(inputs=dict(positive=['p',0],negative=['n',0])),
                    'p':dict(inputs=dict(text='  positive,\n(kept:1.2), ')),
                    'n':dict(inputs=dict(text='negative,  raw')),
                    'u':dict(inputs=dict(caption='unclassified source'))}

    def test_explicit_polarity_and_unknown_preserve_original(self):
        png(self.path,dict(prompt=self.graph))
        self.assertEqual(read_content(self.path)['text'],self.graph['p']['inputs']['text'])
        self.assertEqual(read_content(self.path,['negative','n','text']),dict(kind='raw',text='negative,  raw'))
        self.assertEqual(read_content(self.path,['unknown','u','caption'])['text'],'unclassified source')
        with self.assertRaises(ValueError):read_content(self.path,['n','text'])

    def test_multiple_routes_require_choice_and_stale_choice_never_falls_back(self):
        self.graph['second']=dict(inputs=dict(positive=['q',0]));self.graph['q']=dict(inputs=dict(text='second'))
        png(self.path,dict(prompt=self.graph))
        with self.assertRaisesRegex(ValueError,'多個正面'):read_content(self.path)
        self.assertEqual(read_content(self.path,['positive','q','text'])['text'],'second')
        with self.assertRaisesRegex(ValueError,'缺少指定'):read_content(self.path,['positive','missing','text'])

    def test_parameters_negative_blank_is_valid_and_labels_are_not_inferred(self):
        import struct,zlib
        png(self.path,{})
        value=b'parameters\0original tags\nNegative prompt: \nSteps: 20, CFG scale: 7'
        chunk=struct.pack('>I',len(value))+b'tEXt'+value+struct.pack('>I',zlib.crc32(b'tEXt'+value))
        data=self.path.read_bytes();self.path.write_bytes(data[:-12]+chunk+data[-12:])
        self.assertEqual(read_content(self.path,['negative','__parameters__','negative'])['text'],'')
        sources=metadata_sources(dict(raw={'prompt':{'positive name':dict(inputs=dict(text='still unknown'))}}))
        self.assertEqual(sources[0]['role'],'unknown')

    def test_cyclic_conditioning_and_same_field_both_polarities_are_explicit(self):
        graph={'s':dict(inputs=dict(positive=['a',0],negative=['a',0])),
               'a':dict(inputs=dict(text='shared',conditioning=['b',0])),
               'b':dict(inputs=dict(conditioning=['a',0]))}
        sources=metadata_sources(dict(raw={'prompt':graph}))
        self.assertEqual([s['role'] for s in sources],['positive','negative'])

    def test_scheduled_polarity_is_frozen_while_visible_choice_changes(self):
        from test_input_flow import canvas_state
        from prompt_calculus_studio.image_bindings import import_source
        from prompt_calculus_studio.image_source import set_items
        from prompt_calculus_studio.stage_context import capture,project
        png(self.path,dict(prompt=self.graph));state,canvas,out=canvas_state()
        source=import_source(self.path,Path(self.tmp.name));key='__source_metadata'
        state.setdefault('canvas_functions',dict(images={}))['images'][key]=dict(prompt_choice=['negative','n','text'])
        set_items(state,key,[source],self.tmp.name)
        state['multi_output'].setdefault('stages',{})['s']=dict(name='S')
        plan=dict(stages={'s':dict(bindings=[dict(key='input',source=key,kind='clip')],scheduler=None,auto=None)})
        saved=capture(state,plan,['s'],self.tmp.name)
        state['canvas_functions']['images'][key]['prompt_choice']=['positive','p','text']
        projected=project(state,self.tmp.name,source_values={key:source},readers=saved['readers'])
        self.assertEqual(projected['canvas_functions']['images'][key]['content']['text'],'negative,  raw')
        self.assertEqual(state['canvas_functions']['images'][key]['prompt_choice'],['positive','p','text'])


class TextTargetTests(unittest.TestCase):
    def test_only_native_string_capability_extends_legacy_targets(self):
        profile=describe(workflow());fields=profile_text_fields(profile)
        self.assertIn(('custom','caption'),fields);self.assertNotIn(('custom','mode'),fields);self.assertNotIn(('custom','linked'),fields)
        del profile['parameter_description'];self.assertNotIn(('custom','caption'),profile_text_fields(profile))
        self.assertIn(('8','text_g'),profile_text_fields(profile))

    def test_changed_identity_class_or_capability_is_rejected(self):
        profile=describe(workflow())
        for change in ('identity','class','editable','projection','subgraph'):
            p=copy.deepcopy(profile);desc=p['parameter_description'];node=desc['nodes'][0]
            if change=='identity':p['frontend_id']='other'
            elif change=='class':p['graph']['custom']['class_type']='OtherClass'
            elif change=='editable':node['fields'][0]['editable']=False
            elif change=='projection':desc['projection_reason']='unsupported'
            else:node['path']=['parent','custom']
            with self.subTest(change=change):self.assertNotIn(('custom','caption'),profile_text_fields(p))

    def test_generic_binding_unique_and_frozen_empty_draft_is_preserved(self):
        from prompt_calculus_studio import clip_flow,workflow_flow,multi_output
        from prompt_calculus_studio.snapshots import make_snapshot
        s,c,o=workspace();s=workflow_flow.upgrade(clip_flow.upgrade(s));p=describe(s['generation']['profiles'][0])
        original=copy.deepcopy(p['graph']);clip=next(b['clip'] for b in s['multi_output']['bindings'] if b['node']=='6')
        clip_flow.set_binding(s,p['id'],clip,('custom','caption'))
        other=clip_flow.add(s)
        with self.assertRaisesRegex(ValueError,'占用'):clip_flow.set_binding(s,p['id'],other,('custom','caption'))
        from prompt_calculus_studio.drafts import edit
        edit(s,'',o);snap=make_snapshot(s)
        texts=clip_flow.bound_texts(s,p)
        self.assertEqual(next(t['text'] for t in texts if t['node']=='custom'),'')
        payload=submission(p,snap)
        self.assertEqual(payload['prompt']['custom']['inputs']['caption'],'')
        self.assertEqual(payload['prompt']['custom']['inputs']['mode'],'choose')
        self.assertEqual(p['graph'],original)


class NativeTextTargetTests(unittest.TestCase):
    def test_custom_string_reaches_native_payload_without_touching_unbound_fields(self):
        import test_native_queue as native
        from prompt_calculus_studio import clip_flow,workflow_flow
        from prompt_calculus_studio.snapshots import make_snapshot
        fixture=native.NativeQueueTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        state=workflow_flow.upgrade(clip_flow.upgrade(fixture.snapshot['state']))
        profile=state['generation']['profiles'][0];origin=copy.deepcopy(profile['origin']);describe(profile)
        profile['origin']=origin;profile['parameter_description']['identity']=identity(profile)
        key=next(b['clip'] for b in state['multi_output']['bindings'] if b['node']=='6')
        clip_flow.set_binding(state,profile['id'],key,('custom','caption'))
        fixture.request['snapshot']=make_snapshot(state);fixture.live['bindings_protocol']=1;fixture.queue.poll(fixture.live)
        fixture.queue.start(fixture.request);command=fixture.queue.poll(fixture.live)['commands'][0]
        graph=copy.deepcopy(profile['graph'])
        for binding in command['texts']:graph[binding['node']]['inputs'][binding['field']]=binding['text']
        fixture.queue.prepare(dict(command,session='tab',client_id='real-web-sid',output=graph,workflow=dict(id='native-A',nodes=[])))
        stored=fixture.queue.read(command['id'])
        self.assertEqual(stored['graph']['custom']['inputs']['caption'],'eyes, blue')
        self.assertEqual(stored['graph']['custom']['inputs']['mode'],'choose')
        self.assertEqual(stored['graph']['6']['inputs']['text'],'original positive')


def image(attempt,parent='entry1',index=0,round=0):
    return dict(name=attempt+str(index),reference=dict(run='run',stage='stage',attempt=attempt,parent=parent,round=round,prompt_id=attempt,node='9',index=index))


class PreviewTests(unittest.TestCase):
    def test_late_old_result_cannot_replace_visible_iteration(self):
        from types import SimpleNamespace
        from prompt_calculus_studio.stage_runner import StageRunner
        runner=SimpleNamespace(preview_results={},preview_stamps={})
        older=dict(id='old',owner='run',stage='stage',round=0,parent='first',created=1)
        latest=dict(older,id='new',parent='second',created=2)
        StageRunner.remember_preview(runner,older,dict(images=[image('old','first')],attempts=['old']))
        StageRunner.remember_preview(runner,latest,dict(images=[],attempts=[]))
        StageRunner.remember_preview(runner,older,dict(images=[image('old','first')],attempts=['old']))
        self.assertEqual([i['name'] for i in runner.preview_results['stage']['images']],['old0'])
        StageRunner.remember_preview(runner,latest,dict(images=[image('new','second'),image('new','second',1)],attempts=['new']))
        self.assertEqual(len(runner.preview_results['stage']['images']),2)
        self.assertEqual([i['name'] for i in runner.preview_results['stage']['images']],['new0','new1'])
        StageRunner.remember_preview(runner,older,dict(images=[image('old','first')],attempts=['old']))
        self.assertEqual([i['name'] for i in runner.preview_results['stage']['images']],['new0','new1'])

    def test_current_iteration_retains_all_variants_outputs_and_source(self):
        items=[image('old','entry0'),image('a'),image('a',index=1),image('b')];before=copy.deepcopy(items)
        self.assertEqual([i['name'] for i in current_iteration(items)],['a0','a1','b0'])
        self.assertEqual(items,before)

    def test_outer_round_and_legacy_submission_batch_are_separate(self):
        items=[image('one',None,round=0),image('two',None,round=1),image('two',None,1,1)]
        self.assertEqual(len(current_iteration(items)),2)
        for item in items:item['reference'].pop('parent');item['reference'].pop('round')
        self.assertEqual(len(current_iteration(items)),2)
        self.assertEqual(current_iteration([dict(name='manual')]),[dict(name='manual')])

    def test_only_current_images_are_decoded_no_catalog_history_loaded(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QImage,QImageReader
        from prompt_calculus_studio.canvas_results import CanvasResults
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'image.png';picture=QImage(12,12,QImage.Format.Format_RGB32);picture.fill(0xff224466);picture.save(str(path))
            items=[dict(image(str(i),'old'+str(i)),relative='missing-old.png',sha256=str(i)) for i in range(100)]
            items += [dict(image('current',index=i),relative='image.png',sha256='new'+str(i)) for i in range(2)]
            collection=dict(items=items);images=SimpleNamespace(collection=lambda key:collection,source=lambda key:None)
            window=SimpleNamespace(state=dict(multi_output=dict(version=7,connections=[dict(source='stage',destination='__result_preview__',kind='image')],stages={'stage':{}})),
                store=SimpleNamespace(directory=Path(folder)),comfy=SimpleNamespace(images=images))
            reads=[]
            def reader(name):reads.append(name);return QImageReader(name)
            with patch('prompt_calculus_studio.canvas_results.QImageReader',side_effect=reader):
                view=CanvasResults(window);view.refresh()
            self.assertEqual(reads,[str(path)]*2);self.assertEqual(len(view.preview.pictures),2)
            self.assertEqual(len(collection['items']),102);self.assertEqual(view.preview.text(),'');view.close()

    def test_viewer_uses_ninety_percent_and_keeps_controls_inside(self):
        from PySide6.QtWidgets import QApplication,QWidget
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage
        from prompt_calculus_studio.image_viewer import ImageViewerDialog,viewer_size
        app=QApplication.instance() or QApplication([])
        for size in (QSize(2560,1440),QSize(1280,720),QSize(640,480)):
            result=viewer_size(size)
            self.assertLessEqual(result.width(),size.width()-40);self.assertLessEqual(result.height(),size.height()-40)
            self.assertGreaterEqual(result.width()/size.width(),.89);self.assertGreaterEqual(result.height()/size.height(),.89)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'large.png';picture=QImage(1280,960,QImage.Format.Format_RGB32);picture.fill(0xff334466);picture.save(str(path))
            parent=QWidget();parent.resize(640,480);parent.show()
            dialog=ImageViewerDialog(parent,path,'A long image name '*20);dialog.show();app.processEvents()
            available=dialog.screen().availableGeometry()
            self.assertTrue(available.contains(dialog.frameGeometry()))
            point=dialog.close_button.mapTo(dialog,dialog.close_button.rect().center())
            self.assertTrue(dialog.rect().contains(point));self.assertFalse(dialog.display.pixmap().isNull())
            self.assertLessEqual(dialog.display.pixmap().height(),dialog.display.height())
            self.assertLessEqual(dialog.display.pixmap().width(),dialog.display.width());dialog.close();parent.close()


if __name__=='__main__':unittest.main()
