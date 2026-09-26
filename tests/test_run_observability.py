"""Offline regressions at real service/desktop boundaries, with isolated data."""
import copy,importlib,unittest
from unittest.mock import patch
from PySide6.QtWidgets import QFormLayout
import test_workflow_picker as picker_tests
from test_multi_output import workspace
from test_comfy_integration import Service
import test_node_images as image_tests
from prompt_studio.generation import submission
from prompt_studio.snapshots import make_snapshot
from prompt_studio.job_details import describe
from prompt_studio.workflow_transfer import apply_transfer

sync=importlib.import_module('integration_test.workflow_state')


class RunStateTests(unittest.TestCase):
    history=image_tests.NodeImageTests.history
    def setUp(self):
        image_tests.NodeImageTests.setUp(self)
        self.state,_,_=workspace(); self.profile=self.state['generation']['profiles'][0]
        self.profile['origin']=dict(server='http://127.0.0.1:8188',path='folder/A.json')
        self.payload=submission(self.profile,make_snapshot(self.state))
        self.raw=copy.deepcopy(self.payload)
        Service(self.roots['input']/'service',self.roots['output'],self.roots['temp']).prepare_prompt(self.payload)
        self.entry=self.history(); self.entry['prompt']=(0,'actual',self.payload['prompt'],self.payload['extra_data'])
        self.entry['prompt'][2]['9']=dict(class_type='SaveImage',inputs={})

    def test_browser_matches_full_path_not_display_name_and_only_bound_fields(self):
        value=sync.latest_run(dict(path='folder/A.json'),{'actual':self.entry})
        self.assertEqual(value['prompt_id'],'actual')
        self.assertEqual([t['node'] for t in value['texts']],['6','7'])
        self.assertNotIn('8',[t['node'] for t in value['texts']])
        self.assertEqual(value['outputs']['9']['images'][0]['filename'],'one.png')
        self.assertEqual(sync.latest_run(dict(path='other/A.json'),{'actual':self.entry}),{})
        self.assertEqual(sync.latest_run(dict(workflow='flow'),{'actual':self.entry}),{})

    def test_running_run_has_priority_and_native_id_mismatch_is_rejected(self):
        item=copy.deepcopy(list(self.entry['prompt'])); item[1]='running'
        value=sync.latest_run(dict(path='folder/A.json'),{'old':self.entry},[item])
        self.assertEqual(value['prompt_id'],'running'); self.assertEqual(value['phase'],'running')
        item[3]['extra_pnginfo']['prompt_studio']['generation']['frontend_id']='native-A'
        self.assertEqual(sync.latest_run(dict(path='folder/A.json',frontend_id='native-B'),{},[item]),{})

    def test_diagnostics_use_submitted_fields_and_actual_output_metadata(self):
        record=dict(id='local',prompt_id='actual',server='http://127.0.0.1:8188',created=10,started=12,node='3',node_started=13,
                    last_seen=15,state='running',payload=self.raw,outputs=self.entry['outputs'])
        text=describe(record,20)
        for expected in ('folder/A.json','actual','KSampler','8 秒','7 秒','one.png','eyes, blue','closed eyes'):self.assertIn(expected,text)
        self.state['draft']='later UI'; self.assertNotIn('later UI',describe(record))


class ConnectionRepairTests(unittest.TestCase):
    setUp=picker_tests.PickerTests.setUp
    tearDown=picker_tests.PickerTests.tearDown
    picker=picker_tests.PickerTests.picker
    def test_reconnect_keeps_same_server_list_refreshes_and_rejects_stale_callback(self):
        catalog=self.w.settings_page.workflow_manager.catalog; client=self.w.comfy
        catalog.files=['A.json','B.json']; catalog.loaded=True; catalog.refresh_on_connect=False
        pending=[]
        with patch.object(client,'request',lambda route,**kw:pending.append(kw)):
            client.epoch+=1; client.enabled=True; client.connected=True; client.stateChanged.emit()
            self.assertEqual(catalog.files,['A.json','B.json']); self.assertEqual(len(pending),1)
            first=pending.pop(); client.epoch+=1; client.stateChanged.emit()
            first['done'](['stale.json']); self.assertEqual(catalog.files,['A.json','B.json'])
            pending.pop()['done'](['A.json','C.json']); self.assertEqual(catalog.files,['A.json','C.json'])
            client.stateChanged.emit(); self.assertFalse(pending)
            client.url='http://127.0.0.1:8189'; client.epoch+=1; client.stateChanged.emit()
            self.assertEqual(catalog.files,[]); self.assertEqual(len(pending),1)

    def test_settings_never_construct_binding_form_and_transfer_preserves_canvas_bindings(self):
        d=self.picker(); self.pending[-1][1](__import__('test_multi_output').workflow()['graph'])
        d.target.setCurrentIndex(d.target_index(('6','text'))); d.apply()
        before=copy.deepcopy(self.w.state['multi_output']); p=d.profile()
        m=self.w.settings_page.workflow_manager; m.rebuild()
        self.assertFalse(m.findChildren(QFormLayout))
        from PySide6.QtCore import Qt
        target=next(i for i in range(m.list.count()) if m.list.item(i).data(Qt.ItemDataRole.UserRole)=='comfy:B.json')
        m.list.setCurrentRow(target); self.assertEqual(m.info.text(),'B.json')
        transfer=dict(format='prompt_studio_workflow',version=1,id=p['id'],name=p['name'],graph=p['graph'],bindings=[])
        state,_=apply_transfer(self.w.state,transfer)
        self.assertEqual(state['multi_output'],before)
        self.assertEqual(state['generation']['profiles'][0]['origin'],p['origin'])

    def test_cancel_is_enabled_for_own_submission_and_other_comfy_work(self):
        client=self.w.comfy; client.connected=True; client.run_id='submitted'
        self.w.run_controls.refresh(); self.assertTrue(self.w.run_controls.stop.isEnabled())
        client.run_id=''; client.running=1; self.w.run_controls.refresh(); self.assertTrue(self.w.run_controls.stop.isEnabled())
        client.running=0; self.w.run_controls.refresh(); self.assertFalse(self.w.run_controls.stop.isEnabled())

    def test_completed_backend_result_reaches_canvas_preview_without_browser(self):
        from test_multi_output import workflow
        from test_comfy_integration import png
        from prompt_studio import workflow_flow,multi_output
        from pathlib import Path
        p=workflow('flow'); p['graph']['9']=dict(class_type='PreviewImage',inputs={})
        self.w.generation_panel.save_profile(p)
        key=self.canvas.functions.add_image()
        self.canvas.commit(lambda s:workflow_flow.bind_image(s,key,'flow','9'))
        self.canvas.commit(lambda s:multi_output.connect(s,key,multi_output.PREVIEW,'image'))
        client=self.w.comfy; client.connected=True; client.images_supported=True
        from integration_test.node_images import NodeImages
        root=Path(self.tmp.name); png(root/'complete.png',{})
        state,_,_=workspace(); state['generation']['profiles'][0]['graph']['9']=p['graph']['9']
        payload=submission(state['generation']['profiles'][0],make_snapshot(state))
        Service(root/'server',root,root).prepare_prompt(payload)
        entry=dict(prompt=(0,'finished',payload['prompt'],payload['extra_data']),status=dict(completed=True),outputs={'9':dict(images=[dict(filename='complete.png',type='output',subfolder='')])})
        service=NodeImages(dict(input=root,output=root,temp=root))
        def request(route,data=None,done=None,**_):
            self.assertEqual(route,'desktop/images')
            done({q['key']:service.resolve(q,{'finished':entry}) for q in data['queries']})
        with patch.object(client,'request',request):client.images.poll()
        source=client.images.source(key)
        self.assertEqual(source['reference']['prompt_id'],'finished')
        self.assertEqual(self.canvas.results.input_record['path'],str(root/source['relative']))

    def test_running_node_and_error_survive_in_existing_journal(self):
        from prompt_studio.generation_runner import GenerationRunner
        state,_,_=workspace(); payload=submission(state['generation']['profiles'][0],make_snapshot(state))
        client=self.w.comfy; runner=client.generation; pending=[]
        def request(route,data=None,done=None,failed=None,**_):pending.append((route,done))
        with patch.object(client,'request',request):
            runner.submit_payload(payload,lambda job:None,self.fail)
            pending.pop()[1](dict(prompt_id='actual'))
            runner.observe(dict(running_ids=['actual'],queued_ids=[],executing_node='3'))
            record=runner.record(payload['prompt_id']); self.assertEqual(record['node'],'3'); self.assertIn('started',record)
            runner.observe(dict(running_ids=[],queued_ids=[]))
            pending.pop()[1]({'actual':dict(status=dict(completed=False,status_str='error',messages=[['execution_error',dict(node_id='3',node_type='KSampler',exception_type='FixtureError',exception_message='fixture failure')]]),outputs={})})
            saved=GenerationRunner(client).record(payload['prompt_id'])
            self.assertEqual(saved['state'],'failed'); self.assertIn('FixtureError',describe(saved)); self.assertIn('fixture failure',saved['error'])
