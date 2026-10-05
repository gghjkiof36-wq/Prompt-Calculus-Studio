"""Synthetic native receipts. No server, native browser or GPU is contacted."""
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from test_input_flow import canvas_state
from test_multi_output import workflow
from test_comfy_integration import png
from prompt_calculus_studio.flow_data import add_scheduler,add_image_input,endpoint,capture_inputs
from prompt_calculus_studio.image_source import set_items
from prompt_calculus_studio.multi_output import connect,disconnect
from prompt_calculus_studio.clip_flow import set_binding,bound_texts
from prompt_calculus_studio.input_runner import InputRunner


class InputRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.directory=Path(self.tmp.name);self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.state,self.cid,self.out=canvas_state();p=workflow();p['frontend_id']='native-A'
        self.state['generation']=dict(mode='txt2img',profiles=[p],source=None)
        self.clip=next(iter(self.state['multi_output']['clip_inputs']));set_binding(self.state,'flow',self.clip,('6','text'))
        self.window=NS(state=self.state,store=NS(db=self.db,directory=self.directory),notice=lambda x:None)
        self.calls=[];self.jobs={};self.records={};self.timers=[]
        self.client=NS(window=self.window,url='http://local',connected=True,running=0,pending=0,
                       stateChanged=NS(emit=lambda:None),request=lambda *a,**k:self.calls.append((a,k)))
        def submit(snapshot,workflow,queued,failed,valid,**kwargs):
            ident=kwargs['ident'];job=dict(id=ident,prompt_id='prompt-'+ident,state='queued',native_operation=ident)
            self.jobs[ident]=job;self.records[ident]=job
            self.submits.append(dict(snapshot=copy.deepcopy(snapshot),images=kwargs['images'],job=job,queued=queued,failed=failed,valid=valid))
            queued(job)
        self.submits=[]
        self.client.generation=NS(status=lambda x:None,jobs=self.jobs,native_waiting=set(),submit_native=submit,
            record=lambda i:self.records.get(i),save=lambda j:self.records.update({j['id']:j}))
        self.client.queue=NS(upload=lambda image,done,failed:done('input/'+image['name']))
        self.runner=InputRunner(self.client);self.client.input_flow=self.runner
        timer=patch('prompt_calculus_studio.input_runner.QTimer.singleShot',lambda _,fn:self.timers.append(fn));timer.start();self.addCleanup(timer.stop)

    def schedule(self):
        self.sid=add_scheduler(self.state);self.port=endpoint(self.sid,'clip1')
        connect(self.state,self.out,self.port,'clip');connect(self.state,self.port,self.clip,'clip')
        return self.sid

    def images(self,n):
        import hashlib
        self.state.setdefault('canvas_functions',dict(images={}))['images']['__source_images']=dict(enhanced=True,source=None,attached=False)
        sources=[]
        folder=self.directory/'originals/generation';folder.mkdir(parents=True,exist_ok=True)
        for i in range(n):
            path=folder/f'{i+1}.png'
            graph={'s':dict(inputs={'positive':['p',0]}),'p':dict(inputs={'text':'image '+str(i+1)})}
            png(path,{'prompt':graph})
            sources.append(dict(relative=path.relative_to(self.directory).as_posix(),name=path.name,width=1,height=1,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        set_items(self.state,'__source_images',sources,self.directory)
        connect(self.state,'__source_images',self.cid,'content')
        self.state['multi_output']['canvases'][self.cid]['members']=[]
        return sources

    def finish(self,success=True):
        job=next(item['job'] for item in self.submits if item['job']['id'] in self.jobs);self.jobs.pop(job['id'],None)
        job.update(state='complete' if success else 'failed',outputs={})
        self.runner.finished(job,{})
        while self.timers:self.timers.pop(0)()

    def text(self,index=-1):
        state=self.submits[index]['snapshot']['state'];return bound_texts(state,state['generation']['profiles'][0])[0]['text']

    def test_live_three_clicks_submit_click_values_without_gpu_wait(self):
        self.runner.execute();self.state['uses']['a']['prompt']='second';self.runner.execute()
        self.state['uses']['a']['prompt']='third';self.runner.execute()
        self.assertEqual(len(self.submits),3)
        self.assertEqual([self.text(i) for i in range(3)],['first','second','third'])
        for _ in range(3):self.finish()
        self.assertIsNone(self.runner.current)
        self.runner.execute();self.assertEqual(len(self.submits),4)

    def test_schedule_freezes_connected_text_but_not_workflow_parameter(self):
        self.schedule();self.runner.execute()
        self.state['uses']['a']['prompt']='B';self.runner.execute();self.state['uses']['a']['prompt']='C';self.runner.execute()
        self.state['uses']['a']['prompt']='D';self.state['generation']['profiles'][0]['graph']['3']['inputs']['steps']=99
        self.finish();self.assertEqual(self.text(),'B')
        self.assertEqual(self.submits[-1]['snapshot']['state']['generation']['profiles'][0]['graph']['3']['inputs']['steps'],99)
        self.finish();self.assertEqual(self.text(),'C');self.finish()
        self.assertEqual(len(self.runner.store.rows(self.sid)),0)

    def test_live_folder_submits_only_clicked_images_without_replaying_later_edits(self):
        self.images(4);self.runner.execute();self.assertEqual(self.text(),'image 1');self.finish()
        self.assertEqual(len(self.submits),1)
        self.runner.execute(2)
        self.state['draft']='override';self.state['multi_output']['outputs'][self.out]['draft']='override'
        self.assertEqual([self.text(i) for i in range(3)],['image 1','image 2','image 3'])
        self.finish();self.finish();self.assertEqual(len(self.submits),3)

    def test_large_folder_window_refill_paired_images_and_edit_time(self):
        self.images(13);self.schedule();target=add_image_input(self.state)
        self.state['generation']['profiles'][0]['graph']['20']=dict(class_type='LoadImage',inputs={'image':'original.png'})
        self.state['multi_output']['image_inputs'][target].update(workflow='flow',node='20')
        image_port=endpoint(self.sid,'image1');connect(self.state,'__source_images',image_port,'image');connect(self.state,image_port,target,'image')
        self.runner.execute();self.assertEqual(len(self.runner.store.rows(self.sid))+self.runner.direct_count(self.runner.store.scoped(self.sid)),10)
        self.runner.execute();self.assertEqual(len(self.runner.store.rows(self.sid))+self.runner.direct_count(self.runner.store.scoped(self.sid)),10)
        # Fill does not move the visible canvas/source to item ten.
        self.assertEqual(self.state['canvas_functions']['images']['__source_images']['index'],0)
        for i in range(13):
            self.assertEqual(self.text(),'image '+str(i+1));self.assertEqual(self.submits[-1]['images'][0]['image'],'input/'+str(i+1)+'.png')
            self.assertLessEqual(len(self.runner.store.rows(self.sid))+self.runner.direct_count(self.runner.store.scoped(self.sid)),10);self.finish()
        self.assertEqual(len(self.submits),13);self.assertEqual(len(self.runner.store.rows(self.sid)),0)

    def test_pause_no_refill_click_does_not_resume_and_restart_preserves(self):
        self.schedule();self.runner.execute();self.runner.pause(self.sid);self.state['uses']['a']['prompt']='B';self.runner.execute()
        self.finish();self.assertEqual(len(self.submits),1)
        recovered=InputRunner(self.client);self.assertTrue(recovered.store.control(self.sid)['paused'])
        self.assertEqual(len(recovered.store.rows(self.sid)),1)
        self.runner.resume(self.sid);self.assertEqual(self.text(),'B')

    def test_late_failure_and_complete_cannot_touch_next_job(self):
        self.schedule();self.runner.execute(2);old=self.submits[0];self.finish();current=self.runner.current['operation']
        old['failed']('delayed old failure');old['queued'](old['job']);self.runner.finished(old['job'],{})
        self.assertEqual(self.runner.current['operation'],current);self.assertFalse(self.runner.store.control(self.sid)['paused'])

    def test_error_pauses_without_success_advance(self):
        self.images(3);self.schedule();self.runner.execute();self.finish(False)
        control=self.runner.store.control(self.sid)
        self.assertTrue(control['paused']);self.assertEqual(self.state['canvas_functions']['images']['__source_images']['index'],0);self.assertEqual(len(self.submits),1)

    def test_cancel_addresses_exact_operation_and_preserves_waiting(self):
        self.schedule();self.runner.execute(2);self.runner.cancel(self.sid)
        args,kwargs=self.calls[-1];self.assertEqual(args[0],'workflow/native/cancel');self.assertEqual(args[1]['id'],self.submits[0]['job']['id'])
        kwargs['done'](dict(state='failed'))
        self.assertEqual(len(self.runner.store.rows(self.sid)),1);self.assertTrue(self.runner.store.control(self.sid)['paused'])

    def test_unconnected_prompt_remains_live(self):
        from prompt_calculus_studio.clip_flow import add
        from prompt_calculus_studio.multi_output import new_output
        self.schedule();other='negative';self.state['multi_output']['outputs'][other]=new_output('Negative')
        connect(self.state,'b',other,'text');clip=add(self.state);connect(self.state,other,clip,'clip');set_binding(self.state,'flow',clip,('7','text'))
        self.runner.execute(2);self.state['uses']['b']['prompt']='changed negative';self.finish()
        state=self.submits[-1]['snapshot']['state'];texts=bound_texts(state,state['generation']['profiles'][0])
        self.assertEqual(next(t['text'] for t in texts if t['node']=='7'),'changed negative')

    def test_disconnect_never_replays_previously_submitted_live_clicks(self):
        self.images(3);self.runner.execute(3);owner=self.runner.owner(None,'flow')
        self.assertEqual(len(self.submits),3)
        self.runner.disconnected()
        recovered=InputRunner(self.client);recovered.observe({});recovered.observe({})
        recovered.resume(owner);self.assertEqual(len(self.submits),3)
        self.assertEqual(recovered.store.control(owner).get('credits',0),0)

    def test_cancel_after_reopening_uses_restored_operation_not_foreign_job(self):
        self.schedule();self.runner.execute(2);operation=self.submits[0]['job']['id'];self.runner.disconnected()
        recovered=InputRunner(self.client);recovered.observe(dict(running_ids=[self.submits[0]['job']['prompt_id']],queued_ids=[]));recovered.cancel(self.sid)
        args,kwargs=self.calls[-1];self.assertEqual(args[1],dict(id=operation))
        kwargs['done'](dict(state='failed'))
        self.assertEqual(recovered.store.rows(self.sid)[0]['state'],'waiting')
        self.assertEqual(self.records[operation]['state'],'failed')


if __name__=='__main__':unittest.main()
