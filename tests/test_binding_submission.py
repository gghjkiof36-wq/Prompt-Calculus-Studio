"""End-to-end request validation with fixture transport; no real ComfyUI/GPU."""
import copy
import unittest
import test_native_queue as native
from prompt_studio import clip_flow, workflow_flow
from prompt_studio.snapshots import make_snapshot


class BindingSubmissionTests(unittest.TestCase):
    setUp=native.NativeQueueTests.setUp

    def upgrade(self):
        self.snapshot=make_snapshot(workflow_flow.upgrade(clip_flow.upgrade(self.snapshot['state'])))
        self.request['snapshot']=self.snapshot
        self.live['bindings_protocol']=1
        self.queue.poll(self.live)

    def command(self):
        self.queue.start(self.request)
        command=self.queue.poll(self.live)['commands'][0]
        graph=copy.deepcopy(self.snapshot['state']['generation']['profiles'][0]['graph'])
        graph['5']['inputs'].update(width=1216,height=832,batch_size=2)
        return command,dict(command,session='tab',client_id='real-web-sid',output=graph,workflow=dict(id='native-A',nodes=[]))

    def test_pcs_click_time_text_required_in_actual_payload_with_live_parameters(self):
        self.upgrade(); command,value=self.command()
        with self.assertRaisesRegex(ValueError,'提示詞'):
            self.queue.prepare(value)
        for field in command['texts']:
            self.assertEqual(field['text_source'],'pcs')
            value['output'][field['node']]['inputs'][field['field']]=field['text']
        self.queue.prepare(value)
        stored=self.queue.read(command['id'])
        self.assertEqual(stored['graph']['5']['inputs'],dict(width=1216,height=832,batch_size=2))
        self.assertEqual(stored['graph']['6']['inputs']['text'],'eyes, blue')
        self.assertEqual(stored['graph']['7']['inputs']['text'],'closed eyes')
        self.assertEqual(stored['graph']['8']['inputs']['text_g'],'third original')

    def test_explicit_web_keeps_empty_and_negative_fields_independent(self):
        self.upgrade()
        state=self.snapshot['state']
        for clip in state['multi_output']['clip_inputs'].values():clip['text_source']='web'
        command,value=self.command(); value['output']['6']['inputs']['text']=''
        value['output']['7']['inputs']['text']='manual negative'
        self.queue.prepare(value)
        self.assertEqual([b['text'] for b in self.queue.read(command['id'])['marker']['texts']],['','manual negative'])

    def test_uploaded_image_reaches_only_selected_loadimage_and_mismatch_blocks(self):
        self.upgrade()
        profile=self.snapshot['state']['generation']['profiles'][0]
        profile['graph']['20']=dict(class_type='LoadImage',inputs=dict(image='original.png'))
        profile['graph']['21']=dict(class_type='LoadImage',inputs=dict(image='untouched.png'))
        profile['image']='20'; self.request['image']='prompt_calculus_studio/input.png'
        command,value=self.command()
        self.assertEqual(command['images'],[dict(node='20',field='image',class_type='LoadImage',text=self.request['image'])])
        for field in command['texts']:value['output'][field['node']]['inputs'][field['field']]=field['text']
        with self.assertRaisesRegex(ValueError,'圖片'):self.queue.prepare(value)
        value['output']['20']['inputs']['image']=self.request['image']; self.queue.prepare(value)
        self.assertEqual(self.queue.read(command['id'])['graph']['21']['inputs']['image'],'untouched.png')

    def test_old_frontend_cannot_silently_ignore_binding_commands(self):
        self.upgrade(); self.live.pop('bindings_protocol'); self.queue.poll(self.live)
        with self.assertRaisesRegex(ValueError,'重新整理'):self.queue.start(self.request)

    def test_closed_web_reports_requirement_without_saved_graph_submission(self):
        self.upgrade(); self.queue.sessions.clear()
        with self.assertRaisesRegex(ValueError,'ComfyUI 分頁'):self.queue.start(self.request)
        with self.service.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM native_operations').fetchone()[0],0)
