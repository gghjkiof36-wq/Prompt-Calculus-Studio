import importlib.util
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

spec=importlib.util.spec_from_file_location('pcs_node_images',Path(__file__).parents[1]/'comfyui_prompt_studio/node_images.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class NodeImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.roots={k:Path(self.tmp.name)/k for k in ('input','output','temp')}
        for path in self.roots.values():path.mkdir(); (path/'one.png').write_bytes(b'one'); (path/'two.png').write_bytes(b'two')
        self.service=module.NodeImages(self.roots)
        self.query=dict(workflow='A',node='9',class_type='SaveImage')

    def history(self,name='one.png',workflow='A'):
        return dict(prompt=[0,'p',{},dict(extra_pnginfo=dict(prompt_studio_request=dict(generation=dict(workflow_id=workflow))))],status=dict(completed=True),outputs={'9':dict(images=[dict(filename=name,type='output',subfolder='')])})

    def test_current_browser_and_explicit_upstream_are_distinct(self):
        self.service.publish(dict(workflow='A',nodes={'9':dict(type='SaveImage',images=[dict(filename='two.png',type='output')],selected=0)}))
        history={'p':self.history()}
        self.assertEqual(self.service.resolve(self.query,history)['image']['filename'],'two.png')
        result=self.service.resolve(dict(self.query,prompt_id='p'),history)
        self.assertEqual(result['image']['filename'],'one.png'); self.assertEqual(result['origin'],'result')

    def test_multiple_images_require_selection_and_missing_workflow_never_uses_other(self):
        entry=self.history(); entry['outputs']['9']['images'].append(dict(filename='two.png',type='output'))
        with self.assertRaisesRegex(ValueError,'多張'):self.service.resolve(self.query,{'p':entry})
        with self.assertRaisesRegex(ValueError,'尚無'):self.service.resolve(dict(self.query,workflow='B'),{'p':entry})
        with self.assertRaisesRegex(ValueError,'尚無'):self.service.resolve(dict(self.query,prompt_id='missing'),{'p':entry})

    def test_new_completion_wins_over_old_browser_thumbnail(self):
        with patch.object(module.time,'time',return_value=10):
            self.service.publish(dict(workflow='A',nodes={'9':dict(type='SaveImage',images=[dict(filename='two.png',type='output')],selected=0)}))
        entry=self.history(); entry['status']['messages']=[('execution_success',dict(timestamp=11000))]
        self.assertEqual(self.service.resolve(self.query,{'p':entry})['image']['filename'],'one.png')
        with self.assertRaisesRegex(ValueError,'不屬於'):self.service.resolve(dict(self.query,workflow='B',prompt_id='p'),{'p':entry})

    def test_load_image_fallback_is_labelled_and_paths_cannot_escape(self):
        value=self.service.resolve(dict(workflow='A',node='10',class_type='LoadImage',image='one.png'),{})
        self.assertEqual(value['origin'],'workflow'); self.assertEqual(Path(value['path']).parent,self.roots['input'])
        for image in ('../secret.png','C:/secret.png',dict(filename='one.png',subfolder='../../',type='output')):
            with self.assertRaises(ValueError):module.reference(image)

    def test_changing_another_image_node_does_not_revive_stale_preview(self):
        from unittest.mock import patch
        value=dict(workflow='A',nodes={'9':dict(type='SaveImage',images=[dict(filename='two.png',type='output')],selected=0)})
        with patch.object(module.time,'time',return_value=10):self.service.publish(value)
        entry=self.history();entry['status']['messages']=[('execution_success',dict(timestamp=20000))]
        value['nodes']['10']=dict(type='LoadImage',images=['one.png'],selected=0)
        with patch.object(module.time,'time',return_value=30):self.service.publish(value)
        self.assertEqual(self.service.resolve(self.query,{'p':entry})['image']['filename'],'one.png')

    def test_actual_service_envelope_and_queue_tuple_resolve_without_browser(self):
        from test_comfy_integration import Service
        from test_multi_output import workspace
        from prompt_studio.snapshots import make_snapshot
        from prompt_studio.generation import submission
        # Pass through the real submission hook: it consumes the request marker.
        state,_,_=workspace(); p=state['generation']['profiles'][0]
        payload=submission(p,make_snapshot(state,'test','test'))
        service=Service(Path(self.tmp.name)/'service',self.roots['output'],self.roots['temp'])
        service.prepare_prompt(payload)
        self.assertNotIn('prompt_studio_request',payload['extra_data']['extra_pnginfo'])
        entry=self.history(); entry['prompt']=(0,payload['prompt_id'],payload['prompt'],payload['extra_data'])
        value=self.service.resolve(dict(self.query,workflow=p['id']),{payload['prompt_id']:entry})
        self.assertEqual(value['image']['filename'],'one.png')
        self.assertEqual(value['origin'],'result')
