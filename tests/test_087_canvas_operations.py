import copy
import unittest
from prompt_calculus_studio.core import initial_state,validate_state
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.canvas_starter import initialize,guide
from prompt_calculus_studio.composition import node
from prompt_calculus_studio.canvas_operations import capture,paste,remove
from prompt_calculus_studio.multi_output import compile_output,new_canvas,new_output,connect


class SelectionDataTests(unittest.TestCase):
    def setUp(self):
        self.state=initialize(prepare_state(initial_state(),multi=True),fresh_install=True)
        self.refs=guide(self.state);cid=self.refs['canvas']
        self.state['uses']['one']=node('one','red dress, smile')
        self.state['multi_output']['canvases'][cid]['members'].append('one')
        self.state.setdefault('text_positions',{})['one']=[40,150]

    def test_copy_container_and_output_preserves_internal_edge_relative_position_new_ids(self):
        s=self.state;before=copy.deepcopy(s);c=self.refs['canvas'];o=self.refs['output']
        payload=capture(s,[c,o,'one']);ids=paste(s,payload,(4000,2000))
        validate_state(s)
        canvases=[k for k in ids if k in s['multi_output']['canvases']]
        outputs=[k for k in ids if k in s['multi_output']['outputs']]
        self.assertEqual(len(canvases),1);self.assertEqual(len(outputs),1)
        child=s['multi_output']['canvases'][canvases[0]]['members'][0]
        self.assertNotEqual(child,'one');self.assertNotEqual(s['uses'][child]['id'],s['uses']['one']['id'])
        self.assertEqual(compile_output(s,outputs[0])['final_prompt'],compile_output(before,o)['final_prompt'])
        self.assertFalse(any(e['destination'] in ids and e['source'] not in ids for e in s['multi_output']['connections']))
        self.assertEqual(s['uses']['one'],before['uses']['one'])
        s['uses'][child]['prompt']='variant';self.assertEqual(s['uses']['one']['prompt'],'red dress, smile')

    def test_atomic_delete_container_output_and_text_keeps_document_valid(self):
        remove(self.state,[self.refs['canvas'],self.refs['output'],'one'])
        validate_state(self.state)
        self.assertNotIn('one',self.state['uses'])

    def test_delete_container_alone_retains_its_text_and_other_output_draft(self):
        output=self.state['multi_output']['outputs'][self.refs['output']];output['draft']=''
        self.state['draft']=''
        remove(self.state,[self.refs['canvas']]);validate_state(self.state)
        self.assertEqual(self.state['uses']['one']['prompt'],'red dress, smile')
        self.assertEqual(output['draft'],'')

    def test_selected_parent_child_only_cloned_once(self):
        root=self.state['uses']['one'];root['children']=[node('child','eyes')]
        payload=capture(self.state,['one','one:'+root['children'][0]['id']])
        self.assertEqual(len(payload['roots']),1);self.assertFalse(payload['nodes'])
        ids=paste(self.state,payload,(0,0),self.refs['canvas']);validate_state(self.state)
        self.assertEqual(len(ids),1)

    def test_copy_clip_preserves_template_without_duplicate_active_binding(self):
        c=self.refs['clip'];s=self.state
        # Retained template is inert, and is never included in submission bindings.
        s['multi_output']['clip_inputs'][c]['workflow']='missing-workflow'
        s['multi_output']['bindings'].append(dict(clip=c,workflow='missing-workflow',node='9',field='text'))
        payload=capture(s,[c]);new=paste(s,payload,(9000,0))[0]
        self.assertIsNone(s['multi_output']['clip_inputs'][new]['workflow'])
        self.assertEqual(s['multi_output']['clip_inputs'][new]['binding_template'][0]['field'],'text')
        self.assertFalse(any(b['clip']==new for b in s['multi_output']['bindings']))

    def test_delete_nested_parent_and_descendant_once_retains_unselected_sibling(self):
        s=self.state;root=s['uses']['one'];parent=node('parent','parent');child=node('child','child')
        sibling=node('keep','keep');parent['children']=[child];root['children']=[parent,sibling]
        # Both ancestors and descendants may be selected; set iteration order
        # must not determine whether the whole transaction can be applied.
        remove(s,['one:'+parent['id'],'one:'+child['id']]);validate_state(s)
        self.assertEqual(root['children'],[sibling])
        self.assertIn('one',s['uses'])

    def test_delete_current_output_restores_other_selections_and_manual_empty(self):
        for view in ('canvas','list'):
            with self.subTest(view=view):
                s=copy.deepcopy(self.state);s['selection_view']=view
                s['temporary']=['old-list'];s['draft']='old-draft';s['draft_base']='old-base'
                other=new_output('other');other.update(draft='',draft_base='canvas-base',
                    list_draft=dict(draft='',draft_base='list-base'),
                    list_state=dict(selections={},weights={},instances={},temporary=['other-list']))
                s['multi_output']['outputs']['other']=other
                remove(s,[self.refs['output']]);validate_state(s)
                self.assertEqual(s['multi_output']['current_output'],'other')
                self.assertEqual(s['temporary'],['other-list'])
                self.assertEqual(other['list_state']['temporary'],['other-list'])
                self.assertEqual(s['draft'],'')
                self.assertEqual(s['draft_base'],view+'-base')

    def test_copy_preserves_explicit_text_order_and_excludes_external_or_stale_sources(self):
        s=self.state;data=s['multi_output'];c=self.refs['canvas'];o=self.refs['output']
        for key,text in (('second','second-text'),('external','external-text')):
            data['canvases'][key]=new_canvas(key);s['uses'][key+'-text']=node(key,text)
            data['canvases'][key]['members']=[key+'-text'];connect(s,key,o,'text')
        data['outputs'][o]['text_sources']=['second',c,'external']
        data['outputs'][o].update(canvases=['second',c,'external'],canvas='second')
        payload=capture(s,[c,'second',o])
        payload['collections']['outputs'][o]['text_sources'].append('already-removed')
        ids=paste(s,payload,(4000,4000));validate_state(s)
        added=next(k for k in ids if k in data['outputs'])
        self.assertEqual(compile_output(s,added)['final_prompt'],'second-text\nred dress, smile')
        self.assertTrue(all(k in ids for k in data['outputs'][added]['text_sources']))
        self.assertFalse(any(e['destination']==added and e['source'] not in ids for e in data['connections']))


if __name__=='__main__':unittest.main()
