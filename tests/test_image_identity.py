import copy
import json
import tempfile
import unittest
from pathlib import Path
from test_node_images import module


class ImageIdentityTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        for filename in ('A.png','B.png'): (root/filename).write_bytes(filename.encode())
        self.service=module.NodeImages({k:root for k in ('input','output','temp')})
        self.query=dict(workflow='shared',node='9',class_type='PreviewImage',origin=dict(path='folder/A.json'))

    def publish(self,path,native,filename):
        self.service.publish(dict(workflow='shared',path=path,frontend_id=native,nodes={'9':dict(type='PreviewImage',images=[dict(filename=filename,type='temp')],selected=0)}))

    def test_same_id_different_paths_coexist_and_wrong_native_cannot_bypass_path(self):
        self.publish('folder/A.json','native-A','A.png')
        self.publish('other/A.json','native-B','B.png')
        self.assertEqual(self.service.resolve(self.query,{})['image']['filename'],'A.png')
        query=copy.deepcopy(self.query); query['origin']['path']='other/A.json'
        self.assertEqual(self.service.resolve(query,{})['image']['filename'],'B.png')
        for query in (dict(self.query,frontend_id='native-B'),dict(self.query,origin=dict(path='missing/A.json'))):
            with self.assertRaisesRegex(ValueError,'尚無'): self.service.resolve(query,{})

    def test_same_path_multiple_native_instances_requires_exact_identity(self):
        self.publish('folder/A.json','native-A','A.png'); self.publish('folder/A.json','native-B','B.png')
        with self.assertRaisesRegex(ValueError,'多份'): self.service.resolve(self.query,{})
        self.assertEqual(self.service.resolve(dict(self.query,frontend_id='native-B'),{})['image']['filename'],'B.png')

    def test_pinned_prompt_id_does_not_bypass_missing_workflow_ownership(self):
        entry=dict(prompt=[0,'p',{},dict(extra_pnginfo=dict(prompt_studio=dict(generation=dict(workflow_id='shared',origin=dict(path='other/A.json')))))],status=dict(completed=True),outputs={'9':dict(images=[dict(filename='B.png',type='temp')])})
        with self.assertRaisesRegex(ValueError,'尚無'): self.service.resolve(self.query,{'p':entry})
        del entry['prompt'][3]['extra_pnginfo']['prompt_studio']['generation']['origin']
        with self.assertRaisesRegex(ValueError,'不屬於'):self.service.resolve(dict(self.query,prompt_id='p'),{'p':entry})

    def test_server_origin_is_checked_with_normal_default_ports(self):
        self.publish('folder/A.json','native-A','A.png')
        query=dict(self.query,origin=dict(path='folder/A.json',server='http://localhost:8188'))
        self.assertEqual(self.service.resolve(query,{},'http://127.0.0.1:8188')['image']['filename'],'A.png')
        with self.assertRaisesRegex(ValueError,'伺服器'):self.service.resolve(query,{},'http://127.0.0.1:8189')

    def desktop_query(self,kind='PreviewImage',completed=None):
        from prompt_calculus_studio.image_bindings import queries
        state=dict(generation=dict(profiles=[dict(id='shared',graph={'9':dict(class_type=kind,inputs=dict(image='A.png'))})]),
                   canvas_functions=dict(images={'image':dict(binding=dict(workflow='shared',node='9'))}))
        return json.loads(json.dumps(queries(state,['image'],completed)))[0]

    def test_actual_desktop_null_origin_can_read_live_and_imported_images(self):
        query=self.desktop_query(); self.assertIsNone(query['origin']); self.assertIsNone(query['frontend_id'])
        self.publish('folder/A.json','native-A','A.png')
        self.assertEqual(self.service.resolve(query,{})['image']['filename'],'A.png')
        self.service.live.clear()
        result=self.service.resolve(self.desktop_query('LoadImage'),{})
        self.assertEqual((result['origin'],result['image']['filename']),('workflow','A.png'))

    def test_missing_and_null_query_and_history_origin_can_read_completed_and_pinned_results(self):
        entry=dict(prompt=[0,'p',{},dict(extra_pnginfo=dict(prompt_studio=dict(generation=dict(workflow_id='shared'))))],
                   status=dict(completed=True),outputs={'9':dict(images=[dict(filename='B.png',type='temp')])})
        marker=entry['prompt'][3]['extra_pnginfo']['prompt_studio']['generation']
        for query_origin in ('missing',None,{}):
            for history_origin in ('missing',None,{}):
                for pinned in (False,True):
                    with self.subTest(query=query_origin,history=history_origin,pinned=pinned):
                        query=self.desktop_query('SaveImage',{'shared':'p'} if pinned else None)
                        query.pop('origin') if query_origin=='missing' else query.update(origin=query_origin)
                        marker.pop('origin',None) if history_origin=='missing' else marker.update(origin=history_origin)
                        result=self.service.resolve(query,{'p':entry})
                        self.assertEqual((result['origin'],result['prompt_id'],result['image']['filename']),('result','p','B.png'))

    def test_null_origin_still_rejects_ambiguity_and_pinned_result_stays_frozen(self):
        self.publish('folder/A.json','native-A','A.png'); self.publish('other/A.json','native-B','B.png')
        with self.assertRaisesRegex(ValueError,'多份'):self.service.resolve(self.desktop_query(),{})
        query=self.desktop_query(); query['frontend_id']='native-A'
        self.assertEqual(self.service.resolve(query,{})['image']['filename'],'A.png')
        entry=dict(prompt=[0,'p',{},dict(extra_pnginfo=dict(prompt_studio=dict(generation=dict(workflow_id='shared',origin=None))))],
                   status=dict(completed=True),outputs={'9':dict(images=[dict(filename='B.png',type='temp')])})
        result=self.service.resolve(self.desktop_query('SaveImage',{'shared':'p'}),{'p':entry})
        self.assertEqual((result['origin'],result['image']['filename']),('result','B.png'))

    def test_malformed_origin_is_rejected_without_hiding_errors_as_absent(self):
        for bad in ([],False,0,'',{'path':None},{'server':[]},{'path':'x'*1001}):
            with self.subTest(origin=bad):
                query=dict(self.desktop_query('LoadImage'),origin=bad)
                with self.assertRaisesRegex(ValueError,'來源格式'):self.service.resolve(query,{})
                with self.assertRaisesRegex(ValueError,'來源格式'):module.matches_source(query,dict(workflow='shared'))
                entry=dict(prompt=[0,'p',{},dict(extra_pnginfo=dict(prompt_studio=dict(generation=dict(workflow_id='shared',origin=bad))))])
                with self.assertRaisesRegex(ValueError,'來源格式'):module.history_identity(entry)
        for bad in ([],False,0,{},'x'*201):
            with self.subTest(frontend_id=bad):
                with self.assertRaisesRegex(ValueError,'身分'):self.service.resolve(dict(self.desktop_query('LoadImage'),frontend_id=bad),{})
