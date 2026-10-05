import copy
import sqlite3
import unittest
from prompt_calculus_studio.core import initial_state, validate_state
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.composition import node
from prompt_calculus_studio.multi_output import new_canvas, connect, disconnect, compile_output
from prompt_calculus_studio.flow_data import (add_scheduler,endpoint,capture_inputs,resolve,value,materialize)
from prompt_calculus_studio.input_queue import InputQueue
from prompt_calculus_studio.image_source import positive_fields,natural_key
from prompt_calculus_studio.snapshots import make_snapshot,validate_snapshot


def canvas_state():
    state=initial_state();state.update(version=3,selection_view='canvas',uses={'a':node('A','first')})
    state=prepare_state(state,multi=True)
    data=state['multi_output'];first=next(iter(data['canvases']));out=data['current_output']
    data['canvases'][first]['members']=['a']
    state['uses']['b']=node('B','second');data['canvases']['b']=new_canvas('B');data['canvases']['b']['members']=['b']
    return state,first,out


class InputFlowTests(unittest.TestCase):
    def test_multi_canvas_order_and_independent_edits_roundtrip(self):
        state,first,out=canvas_state();connect(state,'b',out,'text')
        self.assertEqual(compile_output(state,out)['final_prompt'],'first\nsecond')
        state['uses']['a']['prompt']='changed'
        self.assertEqual(state['uses']['b']['prompt'],'second')
        validate_state(state);validate_snapshot(make_snapshot(state))
        restored=prepare_state(state,multi=True)
        self.assertEqual(compile_output(restored,out)['final_prompt'],'changed\nsecond')
        line=next(c for c in state['multi_output']['connections'] if c['source']==first and c['destination']==out)
        disconnect(state,line['id']);self.assertEqual(compile_output(state,out)['final_prompt'],'second')

    def test_snapshot_only_connected_input_and_empty_text(self):
        state,_,out=canvas_state();sched=add_scheduler(state);port=endpoint(sched,'clip1')
        connect(state,out,port,'clip');inputs=capture_inputs(state,sched)
        self.assertEqual(list(inputs),[port]);self.assertNotIn('graph',inputs[port])
        state['uses']['a']['prompt']='later';state['_execution_inputs']=inputs
        self.assertEqual(resolve(state,port,'clip')['value'],'first')
        self.assertEqual(compile_output(state,out)['final_prompt'],'later')
        state['_execution_inputs'][port]=value('clip','')
        self.assertEqual(resolve(state,port,'clip')['value'],'')

    def test_capacity_history_edit_reorder_and_recovery(self):
        db=sqlite3.connect(':memory:');queue=InputQueue(db)
        items=[queue.add('one',{'text':value('clip',str(i))},route={'workflow':'f'}) for i in range(10)]
        with self.assertRaises(ValueError):queue.add('one',{'text':value('clip','overflow')},route={})
        queue.update(items[0]['id'],state='submitted')
        with self.assertRaises(ValueError):queue.edit(items[0]['id'],{'text':value('clip','bad')})
        queue.edit(items[2]['id'],{'text':value('clip','edited')})
        queue.edit_pending(items[2]['id'],'next')
        self.assertEqual(queue.rows('one')[1]['inputs']['text']['value'],'edited')
        queue.update(items[0]['id'],state='complete')
        queue.add('one',{'text':value('clip','next')},route={})
        self.assertEqual(len(queue.rows('one')),10)
        queue.recover();self.assertTrue(queue.control('one')['paused'])
        self.assertEqual(len(queue.rows('one',history=True)),11)

    def test_type_and_cycle_fail(self):
        state,first,out=canvas_state();sched=add_scheduler(state)
        with self.assertRaises(ValueError):connect(state,out,endpoint(sched,'image1'),'clip')
        connect(state,out,endpoint(sched,'clip1'),'clip')
        with self.assertRaises(ValueError):connect(state,endpoint(sched,'clip1'),first,'clip')

    def test_positive_conditioning_not_negative(self):
        graph={'s':{'inputs':{'positive':['p',0],'negative':['n',0]}},'p':{'inputs':{'text':'positive'}},'n':{'inputs':{'text':'negative'}}}
        self.assertEqual(positive_fields(graph),[('p','text','positive')])
        self.assertEqual(sorted(['10.png','2.png','1.png'],key=natural_key),['1.png','2.png','10.png'])

    def test_raw_metadata_not_a_tag_module_and_no_stale_fallback(self):
        state,first,out=canvas_state();source=dict(relative='originals/generation/a.png',sha256='a'*64,width=1,height=1,name='A')
        state.setdefault('canvas_functions',dict(images={}))['images']['img']=dict(source=source,content=dict(kind='raw',text='raw positive'))
        connect(state,'img',first,'content');materialize(state)
        self.assertEqual(compile_output(state,out)['final_prompt'],'raw positive\nfirst')
        members=state['multi_output']['canvases'][first]['source_members']
        self.assertEqual(len(members),1);self.assertTrue(state['uses'][members[0]]['opaque_source'])
        self.assertEqual(state['uses'][members[0]]['name'],'A')
        state['canvas_functions']['images']['img'].pop('content');materialize(state)
        with self.assertRaises(ValueError):compile_output(state,out)


if __name__=='__main__':unittest.main()
