import copy
import hashlib
import json
import os
import sys
import tempfile
import zipfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from prompt_studio.core import initial_state,validate_state
from prompt_studio.generation import api_graph,validate_profile,submission,uploaded_image,suggested_text
from prompt_studio.snapshots import make_snapshot,validate_snapshot
from prompt_studio.window import Window
from prompt_studio.generation_panel import WorkflowDialog,ParametersDialog
from test_comfy_integration import Service,png
APP=QApplication.instance() or QApplication([])


def workflow(mode='img2img'):
    graph={'1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'fixture.safetensors'}},
           '2':{'class_type':'CLIPTextEncode','inputs':{'text':'workflow positive','clip':['1',1]}},
           '3':{'class_type':'CLIPTextEncode','inputs':{'text':'negative untouched','clip':['1',1]}},
           '4':{'class_type':'LoadImage','inputs':{'image':'old.png'}},
           '5':{'class_type':'VAEEncode','inputs':{'pixels':['4',0],'vae':['1',2]}},
           '6':{'class_type':'KSampler','inputs':{'model':['1',0],'positive':['2',0],'negative':['3',0],'latent_image':['5',0],
                     'seed':2**63+123,'steps':28,'cfg':2.0,'sampler_name':'euler_ancestral','scheduler':'karras','denoise':.65}},
           '7':{'class_type':'VAEDecode','inputs':{'samples':['6',0],'vae':['1',2]}},
           '8':{'class_type':'SaveImage','inputs':{'images':['7',0],'filename_prefix':'fixture'}}}
    if mode=='txt2img': graph['5']={'class_type':'EmptyLatentImage','inputs':{'width':512,'height':512,'batch_size':1}}; graph.pop('4')
    return dict(id='fixture-'+mode,name='測試工作流',mode=mode,graph=graph,prompt=['2','text'],image='4' if mode=='img2img' else '',sampler='6',size='',values={'denoise':.65},seed_mode='fixed')


class GenerationCoreTests(unittest.TestCase):
    def test_bindings_preserve_negative_model_and_graph_and_exact_seed(self):
        profile=workflow(); before=copy.deepcopy(profile); state=initial_state(); state['draft']='current prompt\n\nsecond paragraph'
        source=dict(name='source.png',sha256='a'*64,width=940,height=1200)
        result=submission(profile,make_snapshot(state),source,'prompt_studio/input.png')
        self.assertEqual(result['prompt']['2']['inputs']['text'],state['draft'])
        self.assertEqual(result['prompt']['3'],before['graph']['3']); self.assertEqual(result['prompt']['1'],before['graph']['1'])
        self.assertEqual(result['prompt']['6']['inputs']['seed'],2**63+123); self.assertEqual(profile,before)
        self.assertEqual(result['prompt']['4']['inputs']['image'],'prompt_studio/input.png')
        self.assertEqual(suggested_text(profile['graph'],'6'),('2','text'))

    def test_seed_rules_and_linked_parameters(self):
        profile=workflow('txt2img'); snap=make_snapshot(initial_state())
        profile['seed_mode']='increment'; result=submission(profile,snap,index=4)
        self.assertEqual(result['prompt']['6']['inputs']['seed'],2**63+127)
        profile['seed_mode']='random'
        with patch('prompt_studio.generation.secrets.randbits',return_value=987): self.assertEqual(submission(profile,snap)['prompt']['6']['inputs']['seed'],987)
        profile['graph']['6']['inputs']['denoise']=['9',0]
        with self.assertRaises(ValueError): validate_profile(profile)

    def test_invalid_workflow_source_mapping_and_parameters_fail_before_submission(self):
        with self.assertRaises(ValueError): api_graph({'nodes':[]})
        profile=workflow(); profile['prompt']=['3','clip']
        with self.assertRaises(ValueError): validate_profile(profile)
        profile=workflow(); profile['values']['denoise']=1.1
        with self.assertRaises(ValueError): validate_profile(profile)
        for name in ('../outside.png','C:/file.png','a\\b.png'):
            with self.assertRaises(ValueError): uploaded_image(dict(name=name,subfolder=''))

    def test_direct_submission_gets_verified_metadata_with_exact_inputs(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'qa') as folder:
            service=Service(Path(folder)/'service',folder,folder); state=initial_state(); state['draft']='landscape'
            profile=workflow(); source=dict(name='test.png',sha256='a'*64,width=512,height=512)
            payload=submission(profile,make_snapshot(state),source,'prompt_studio/test.png')
            service.prepare_prompt(payload); extra=payload['extra_data']['extra_pnginfo']; envelope=extra['prompt_studio']
            self.assertNotIn('prompt_studio_request',extra); self.assertEqual(envelope['submission_id'],payload['prompt_id'])
            validate_snapshot(envelope['bindings'][0]['snapshot']); self.assertEqual(envelope['generation']['source']['sha256'],source['sha256'])
            bad=submission(profile,make_snapshot(state),source,'prompt_studio/test.png'); bad['prompt']['2']['inputs']['text']='changed'
            service.prepare_prompt(bad); self.assertFalse(bad['extra_data']['extra_pnginfo']['prompt_studio']['bindings'])


class GenerationUiTests(unittest.TestCase):
    def setUp(self):
        # These fixtures exercise the retained pre-CLIP generation interface.
        # Current Canvas execution is covered separately by test_workflow_flow.
        mode=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'0'})
        mode.start(); self.addCleanup(mode.stop)
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'qa'); self.root=Path(self.tmp.name)
        self.w=Window(self.root/'data'); self.w.state['settings'].update(online=False,material='solid'); self.w.apply_theme()
        self.w.resize(1440,900); self.w.show(); APP.processEvents()
        self.image=self.root/'source.png'; self.other=self.root/'other.png'
        for file,color in ((self.image,0xff4488aa),(self.other,0xffaa8844)):
            picture=QImage(640,480,QImage.Format.Format_RGB32); picture.fill(color); picture.save(str(file))
    def tearDown(self): self.w.close(); APP.processEvents(); self.tmp.cleanup()

    def configure(self):
        panel=self.w.generation_panel; panel.set_source(str(self.image)); panel.save_profile(workflow())
        self.w.comfy.connected=True; self.w.comfy.direct_supported=True; self.w.comfy.ready=False
        self.w.comfy.stateChanged.emit(); return panel

    def test_switching_mode_source_and_views_preserves_drafts_and_original_bytes(self):
        self.w.final.setPlainText('list draft'); before=self.image.read_bytes(); panel=self.configure()
        self.assertEqual(self.w.final.toPlainText(),'list draft'); panel.mode.setCurrentIndex(0)
        self.assertEqual(self.w.final.toPlainText(),'list draft'); self.w.enter_canvas(); self.w.final.setPlainText('canvas draft')
        panel.set_source(str(self.other)); self.assertEqual(self.w.final.toPlainText(),'canvas draft')
        self.w.leave_canvas(); self.assertEqual(self.w.final.toPlainText(),'list draft'); self.assertEqual(self.image.read_bytes(),before)
        self.w.persist(); stored=self.w.store.load(); self.assertEqual(stored['generation']['source'],self.w.state['generation']['source']); validate_state(stored)

    def test_submission_freezes_prompt_image_and_parameters_before_upload(self):
        panel=self.configure(); self.w.final.setPlainText('original manual'); source=copy.deepcopy(self.w.state['generation']['source']); calls=[]
        def request(route,data=None,done=None,failed=None,**kwargs): calls.append((route,copy.deepcopy(data),done,failed,kwargs))
        with patch.object(self.w.comfy,'request',side_effect=request):
            self.w.copy_final(); self.assertEqual(calls[0][0],'/upload/image')
            self.w.final.setPlainText('later edit'); panel.set_source(str(self.other)); self.w.state['generation']['profiles'][0]['values']['denoise']=.9
            calls[0][2](dict(name='first.png',subfolder='prompt_studio',type='input'))
            payload=calls[1][1]; self.assertEqual(payload['prompt']['2']['inputs']['text'],'original manual')
            self.assertEqual(payload['prompt']['6']['inputs']['denoise'],.65)
            self.assertEqual(payload['extra_data']['extra_pnginfo']['prompt_studio_request']['generation']['source']['sha256'],source['sha256'])
            calls[1][2](dict(prompt_id=payload['prompt_id']))
        self.assertEqual(self.w.state['generation']['source']['name'],'other.png'); self.assertEqual(self.w.final.toPlainText(),'later edit')
        self.assertEqual(len(self.w.comfy.generation.records()),1)

    def test_uncertain_submission_stops_batch_without_resending(self):
        self.configure(); calls=[]
        def request(route,data=None,done=None,failed=None,**kwargs): calls.append((route,data,done,failed))
        with patch.object(self.w.comfy,'request',side_effect=request):
            self.w.comfy.run(3); calls[0][2](dict(name='first.png',subfolder='',type='input'))
            calls[1][3]('timeout')
            self.assertIsNone(self.w.comfy.generation.batch); self.assertEqual(self.w.comfy.run_id,'')
            self.assertEqual([v[0] for v in calls],['/upload/image','/prompt'])
        self.assertEqual(self.w.comfy.generation.records()[0]['state'],'unconfirmed')

    def test_mapping_and_parameter_dialog_preserve_imported_values(self):
        profile=workflow(); dialog=WorkflowDialog(self.w,profile['graph'],'img2img')
        self.assertEqual(dialog.prompt.currentData(),['2','text']); self.assertEqual(dialog.image.currentData(),'4')
        dialog.save(); self.assertEqual(dialog.result_profile['values']['denoise'],.65)
        params=ParametersDialog(self.w,dialog.result_profile); params.fields['denoise'].setValue(.4); params.save()
        self.assertEqual(params.result_profile['values']['seed'],2**63+123); self.assertEqual(params.result_profile['values']['denoise'],.4)

    def test_result_reuse_is_explicit_and_does_not_load_metadata_prompt(self):
        self.w.final.setPlainText('keep my prompt'); panel=self.configure(); prior=copy.deepcopy(self.w.state['generation']['source'])
        self.w.show_latest_generated(dict(thumb='')); self.assertEqual(panel.settings()['source'],prior)
        self.w.use_image_for_generation(str(self.other)); self.assertEqual(self.w.final.toPlainText(),'keep my prompt')
        self.assertEqual(panel.settings()['source']['name'],'other.png')

    def test_cancelled_upload_callback_cannot_stop_a_new_batch(self):
        self.configure(); calls=[]
        with patch.object(self.w.comfy,'request',side_effect=lambda *args,**kwargs:calls.append((args,kwargs))):
            self.w.comfy.run(1); self.w.comfy.generation.finish_batch(); self.w.comfy.run(1)
            token=self.w.comfy.generation.batch['token']; calls[0][1]['failed']('old upload timed out')
            self.assertEqual(self.w.comfy.generation.batch['token'],token)

    def test_pending_job_recovers_and_cached_completion_does_not_claim_a_new_image(self):
        from prompt_studio.generation_runner import GenerationRunner
        self.configure(); runner=self.w.comfy.generation; calls=[]
        with patch.object(self.w.comfy,'request',side_effect=lambda *args,**kwargs:calls.append((args,kwargs))):
            self.w.comfy.run(1); calls[0][1]['done'](dict(name='test.png',subfolder='',type='input'))
            args,_=calls[1]; payload=args[1]; args[2](dict(prompt_id=payload['prompt_id']))
            ident=payload['prompt_id']; restored=GenerationRunner(self.w.comfy)
            self.assertIn(ident,restored.jobs)
            restored.observe(dict(running_ids=[],queued_ids=[])); args,kwargs=calls[-1]
            kwargs['done']({ident:dict(status=dict(completed=True,status_str='success'),outputs={})})
            self.assertEqual(restored.records()[0]['state'],'complete')
            self.assertEqual(restored.records()[0]['output_count'],0)
            self.assertIn('未回傳新圖片',restored.message)

    def test_zip_backup_contains_source_profiles_and_exact_job_payload(self):
        from prompt_studio.backup import archive_data
        from prompt_studio.core import Storage
        self.configure(); calls=[]
        with patch.object(self.w.comfy,'request',side_effect=lambda *args,**kwargs:calls.append((args,kwargs))):
            self.w.comfy.run(1); calls[0][1]['done'](dict(name='test.png',subfolder='',type='input'))
        self.w.persist(); archive=self.root/'backup.zip'
        archive_data(self.w.store.directory,self.w.store.backup(),archive)
        with zipfile.ZipFile(archive) as z:
            relative=self.w.state['generation']['source']['relative']
            self.assertEqual(z.read('data/'+relative),self.image.read_bytes()); z.extractall(self.root/'restore')
        restored=Storage(self.root/'restore/data')
        try:
            self.assertEqual(restored.load()['generation'],self.w.state['generation'])
            body=json.loads(restored.db.execute('SELECT body FROM generation_jobs').fetchone()[0])
            self.assertEqual(body['payload'],calls[1][0][1])
        finally: restored.close()

    def test_generation_controls_remain_accessible_in_small_list_and_large_canvas(self):
        from PySide6.QtTest import QTest
        self.configure(); self.w.resize(960,650); QTest.qWait(100)
        panel=self.w.generation_panel
        self.w.builder_scroll.ensureWidgetVisible(self.w.run_controls,0,0)
        self.assertGreaterEqual(panel.height(),panel.minimumSizeHint().height())
        self.assertGreaterEqual(self.w.final.height(),130)
        self.assertGreaterEqual(panel.preview.height(),104)
        self.w.enter_canvas(); self.w.state['settings']['ui_size']=18; self.w.apply_theme(); QTest.qWait(100)
        self.w.canvas.refresh(); QTest.qWait(100)
        self.assertTrue(self.w.canvas.output.boundingRect().contains(self.w.canvas.output.proxy.geometry()))

if __name__=='__main__': unittest.main()
