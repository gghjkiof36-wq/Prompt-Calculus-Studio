"""All bound images reach PCS; downstream input requires an explicit choice."""
import copy
import unittest
from pathlib import Path
from unittest.mock import patch
import test_node_images as fixtures
module=fixtures.module


class CollectionTests(unittest.TestCase):
    setUp=fixtures.NodeImageTests.setUp
    history=fixtures.NodeImageTests.history

    def test_collection_returns_both_and_explicit_selection_is_tied_to_the_result(self):
        entry=self.history();entry['outputs']['9']['images'].append(dict(filename='two.png',type='output',subfolder=''))
        query=dict(self.query,explicit_selection=True)
        self.service.publish(dict(workflow='A',nodes={'9':dict(type='SaveImage',images=entry['outputs']['9']['images'],selected=1)}))
        rows=self.service.resolve(dict(query,prompt_id='p',collection=True),{'p':entry})
        self.assertEqual(len(rows['items']),2);self.assertIsNone(rows['selected'])
        for index in (0,1):
            choice=dict(collection=rows['collection'],image=rows['items'][index]['image'])
            result=self.service.resolve(dict(query,prompt_id='p',selection=choice),{'p':entry})
            self.assertEqual(Path(result['path']).read_bytes(),b'one' if index==0 else b'two')
        newer=copy.deepcopy(entry);newer['prompt'][1]='p2'
        with self.assertRaisesRegex(ValueError,'多張'):
            self.service.resolve(dict(query,prompt_id='p2',selection=choice),{'p2':newer})

    def test_browser_selection_only_change_does_not_revive_an_older_image_set(self):
        old=dict(workflow='A',nodes={'9':dict(type='SaveImage',images=[dict(filename='two.png',type='output'),dict(filename='one.png',type='output')],selected=0)})
        with patch.object(module.time,'time',return_value=10):self.service.publish(old)
        entry=self.history();entry['status']['messages']=[('execution_success',dict(timestamp=20000))]
        old['nodes']['9']['selected']=1
        with patch.object(module.time,'time',return_value=30):self.service.publish(old)
        result=self.service.resolve(dict(self.query,collection=True,explicit_selection=True),{'p':entry})
        self.assertEqual(len(result['items']),1);self.assertEqual(result['items'][0]['origin'],'result')

    def test_oversized_set_and_wrong_native_identity_never_return_partial_results(self):
        entry=self.history();entry['outputs']['9']['images']*=65
        with self.assertRaisesRegex(ValueError,'64 張'):self.service.resolve(dict(self.query,collection=True),{'p':entry})
        entry=self.history()
        entry['prompt'][3]['extra_pnginfo']['prompt_studio_request']['generation']['frontend_id']='native-copy'
        with self.assertRaisesRegex(ValueError,'不屬於'):
            self.service.resolve(dict(self.query,prompt_id='p',frontend_id='native-bound',collection=True),{'p':entry})
        self.service.publish(dict(workflow='A',nodes={'9':dict(type='SaveImage',images=[],selected=None,overflow=True)}))
        with self.assertRaisesRegex(ValueError,'64 張'):self.service.resolve(dict(self.query,collection=True),{'p':entry})

    def test_native_completion_without_pcs_marker_replaces_stale_browser_batch(self):
        query=dict(self.query,origin=dict(path='A.json'),frontend_id='native-A',collection=True)
        old=self.history();old['prompt'][3]['extra_pnginfo']['prompt_studio_request']['generation'].update(frontend_id='native-A',origin=dict(path='A.json'))
        latest=self.history('two.png');latest['prompt'][1]='native-direct'
        latest['prompt'][3]=dict(extra_pnginfo=dict(workflow=dict(id='native-A',nodes=[])))
        latest['outputs']['9']['images']=[dict(filename=n,type='output',subfolder='') for n in ('two.png','one.png')]
        # Publishing the previous one-image preview after completion must not
        # turn receipt time into evidence that the old preview is newer.
        self.service.publish(dict(workflow='A',path='A.json',frontend_id='native-A',nodes={'9':dict(type='SaveImage',images=old['outputs']['9']['images'],selected=0)}))
        rows=self.service.resolve(query,{'p':old,'native-direct':latest})
        self.assertEqual([v['image']['filename'] for v in rows['items']],['two.png','one.png'])
        self.assertTrue(all(v['prompt_id']=='native-direct' and v['origin']=='result' for v in rows['items']))
        exact=self.service.resolve(dict(query,prompt_id='native-direct'),{'native-direct':latest})
        self.assertEqual(exact['collection'],rows['collection'])
        # Same filenames/node numbers in another native workflow do not match.
        latest['prompt'][3]['extra_pnginfo']['workflow']['id']='native-B'
        rows=self.service.resolve(query,{'p':old,'native-direct':latest})
        self.assertEqual(len(rows['items']),1);self.assertEqual(rows['items'][0]['prompt_id'],'p')


class BoundCollectionUITests(unittest.TestCase):
    from test_multi_canvas import MultiCanvasTests as _Fixture
    setUp=_Fixture.setUp
    tearDown=_Fixture.tearDown

    def test_bound_preview_shows_both_and_second_choice_drives_output_without_browser_selection(self):
        from PySide6.QtGui import QImage,QColor
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from test_multi_canvas import APP
        from test_multi_output import workflow
        from prompt_studio import multi_output,workflow_flow
        p=workflow('A');p['graph']['9']=dict(class_type='PreviewImage',inputs=dict(images=['3',0]));self.w.generation_panel.save_profile(p)
        key=self.canvas.functions.add_image()
        self.canvas.commit(lambda state:workflow_flow.bind_image(state,key,'A','9'))
        self.canvas.commit(lambda state:multi_output.connect(state,key,multi_output.PREVIEW,'image'))
        root=Path(self.tmp.name)/'backend';root.mkdir()
        for color in ('red','blue'):
            image=QImage(32,24,QImage.Format.Format_RGB32);image.fill(QColor(color));self.assertTrue(image.save(str(root/(color+'.png'))))
        backend=module.NodeImages(dict(input=root,output=root,temp=root))
        entry=dict(prompt=[0,'p',p['graph'],dict(extra_pnginfo=dict(prompt_studio=dict(generation=dict(workflow_id='A'))))],
                   status=dict(completed=True),outputs={'9':dict(images=[dict(filename=color+'.png',type='output',subfolder='') for color in ('red','blue')])})
        client=self.w.comfy;client.connected=True;client.images_supported=True
        def request(route,data=None,done=None,**kwargs):
            self.assertEqual(route,'desktop/images')
            done({query['key']:backend.resolve(query,{entry['prompt'][1]:entry}) for query in data['queries']})
        with patch.object(client,'request',request):
            client.images.poll();APP.processEvents();panel=self.canvas.results
            self.assertEqual(panel.images.count(),2);self.assertTrue(panel.images.isHidden())
            self.assertEqual([p.toImage().pixelColor(0,0) for p in panel.preview.pictures],[QColor('red'),QColor('blue')])
            self.assertIsNone(client.images.source(key))
            collection=client.images.collection(key)['collection']
            for index,color in ((0,'red'),(1,'blue')):
                client.images.choose(key,collection,index);APP.processEvents()
                self.assertEqual(client.images.source(key)['reference']['image']['filename'],color+'.png')
                self.assertEqual(len(panel.preview.pictures),2)
            client.images.poll();self.assertEqual(client.images.source(key)['reference']['image']['filename'],'blue.png')
            self.canvas.undo();self.assertEqual(client.images.source(key)['reference']['image']['filename'],'red.png')
            self.canvas.redo();self.assertEqual(client.images.source(key)['reference']['image']['filename'],'blue.png')
            resolved=[]
            client.images.resolve(self.w.state,[key],self.w.store.directory,{'A':'p'},lambda values,errors:resolved.append((values,errors)),self.fail)
            self.assertEqual(resolved[0][0][key]['reference']['image']['filename'],'blue.png');self.assertEqual(resolved[0][1],{})
            entry['prompt'][1]='p2';entry['outputs']['9']['images'].reverse();client.images.poll();self.assertIsNone(client.images.source(key))
            self.assertEqual(len(panel.preview.pictures),2);self.assertEqual(panel.images.currentRow(),-1)
            self.assertEqual([p.toImage().pixelColor(0,0) for p in panel.preview.pictures],[QColor('blue'),QColor('red')])
            client.images.choose(key,collection,0);self.assertIsNone(client.images.source(key))
            # A response begun for native A must not fill a replacement A-copy,
            # even if the PCS profile and node identifiers have not changed.
            pending=[]
            def delayed(route,data=None,done=None,**kwargs):
                pending.append((done,{q['key']:backend.resolve(q,{'p2':entry}) for q in data['queries']}))
            with patch.object(client,'request',delayed):client.images.poll()
            profile=next(p for p in self.w.state['generation']['profiles'] if p['id']=='A')
            profile['frontend_id']='native-copy'
            self.assertIsNone(client.images.collection(key))
            pending[0][0](pending[0][1]);self.assertIsNone(client.images.collection(key));self.assertIsNone(client.images.source(key))
            profile.pop('frontend_id')
            # Older extensions returning a single picked row cannot claim to
            # have supplied the complete batch.
            def old_extension(route,data=None,done=None,**kwargs):
                done({q['key']:dict(path=str(root/'red.png'),image=dict(filename='red.png',subfolder='',type='output')) for q in data['queries']})
            with patch.object(client,'request',old_extension):client.images.poll()
            self.assertIsNone(client.images.source(key));self.assertIn('更新 ComfyUI 擴充',client.images.errors[key])


if __name__=='__main__':unittest.main()
