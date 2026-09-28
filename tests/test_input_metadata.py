import copy
import tempfile
import unittest
from pathlib import Path
from test_input_flow import canvas_state
from test_comfy_integration import png
from prompt_studio.image_source import read_content
from prompt_studio.flow_data import capture_inputs,add_scheduler,endpoint,materialize
from prompt_studio.multi_output import connect,compile_output
from prompt_studio.snapshots import make_snapshot


class InputMetadataTests(unittest.TestCase):
    def test_frozen_prompt_restores_its_modules_not_later_visible_canvas(self):
        state,canvas,out=canvas_state();sid=add_scheduler(state);port=endpoint(sid,'clip1');connect(state,out,port,'clip')
        state['uses']['a']['prompt']='B frozen';captured=capture_inputs(state,sid)
        state['uses']['a']['prompt']='C visible'
        marker=dict(schema_version=1,input_values=captured,texts=[dict(node='p',field='text',output=out,text='B frozen')],
                    bindings=[dict(node_id='p',field='text',snapshot=make_snapshot(state))])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'result.png';png(path,dict(prompt_studio=marker,prompt={'s':dict(inputs={'positive':['p',0]}),'p':dict(inputs={'text':'B frozen'})}))
            content=read_content(path)
        self.assertEqual(content['kind'],'modules');self.assertEqual(content['roots'][0]['prompt'],'B frozen')
        self.assertEqual(state['uses']['a']['prompt'],'C visible')

    def test_module_projection_preserves_user_roots_and_manual_blank(self):
        state,canvas,out=canvas_state();root=copy.deepcopy(state['uses']['b'])
        source=dict(relative='originals/generation/a.png',sha256='a'*64,width=1,height=1,name='A')
        images=state.setdefault('canvas_functions',dict(images={}))['images']
        images['__source_test']=dict(source=source,attached=False,content=dict(kind='modules',roots=[root],text='second',manual_text=None))
        connect(state,'__source_test',canvas,'content');materialize(state)
        self.assertEqual(compile_output(state,out)['final_prompt'],'second, first')
        generated=state['multi_output']['canvases'][canvas]['source_members'][0]
        state['uses'][generated]['prompt']='edited restored module';materialize(state)
        self.assertIn('edited restored module',compile_output(state,out)['final_prompt'])
        images['__source_test']['content']['manual_text']='';materialize(state)
        self.assertEqual(compile_output(state,out)['final_prompt'],'first')
        self.assertEqual(state['uses']['a']['prompt'],'first')
        state['draft']='';state['multi_output']['outputs'][out]['draft']=''
        self.assertEqual(compile_output(state,out)['final_prompt'],'')

    def test_ambiguous_positive_requires_choice_and_never_uses_negative(self):
        graph={'a':dict(inputs={'positive':['p',0],'negative':['n',0]}),'b':dict(inputs={'positive':['q',0]}),
               'p':dict(inputs={'text':'one'}),'q':dict(inputs={'text':'two'}),'n':dict(inputs={'text':'negative'})}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'result.png';png(path,dict(prompt=graph))
            with self.assertRaisesRegex(ValueError,'多個正面'):read_content(path)
            self.assertEqual(read_content(path,['q','text']),dict(kind='raw',text='two'))
            with self.assertRaises(ValueError):read_content(path,['n','text'])

    def test_malformed_optional_metadata_does_not_hide_valid_positive(self):
        graph={'s':dict(inputs={'positive':['p',0]}),'p':dict(inputs={'text':'valid'}),'broken':dict(inputs=[])}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'result.png'
            for marker in [dict(texts=None,input_values=[]),dict(texts=[],input_values={'bad':None})]:
                png(path,dict(prompt=graph,prompt_studio=marker))
                self.assertEqual(read_content(path),dict(kind='raw',text='valid'))
            png(path,dict(prompt=[],prompt_studio=None))
            with self.assertRaises(ValueError):read_content(path)

    def test_snapshot_restore_keeps_source_root_ownership_on_next_image(self):
        from prompt_studio.snapshots import restore_snapshot
        state,canvas,out=canvas_state();root=copy.deepcopy(state['uses']['b'])
        source=dict(relative='originals/generation/a.png',sha256='a'*64,width=1,height=1,name='A')
        state.setdefault('canvas_functions',dict(images={}))['images']['__source_test']=dict(source=source,attached=False,
            content=dict(kind='modules',roots=[root],text='second',manual_text=None))
        connect(state,'__source_test',canvas,'content');materialize(state)
        restored=restore_snapshot(state,make_snapshot(state))
        self.assertTrue(all(k in restored['uses'] for k in restored['multi_output']['canvases'][canvas]['source_members']))
        restored['canvas_functions']['images']['__source_test']['content']=dict(kind='raw',text='new image')
        materialize(restored)
        text=compile_output(restored,out)['final_prompt']
        self.assertIn('new image',text);self.assertNotIn('second',text);self.assertIn('first',text)


if __name__=='__main__':unittest.main()
