import copy,unittest
from prompt_studio import drafts,multi_output as model
from prompt_studio.core import activate_selection_view
from test_multi_output import workspace,workflow


class StateOperationsTests(unittest.TestCase):
    def test_current_draft_has_same_result_from_either_editor(self):
        s,c,o=workspace(); other=copy.deepcopy(s)
        drafts.edit(s,'manual\n原文'); drafts.edit(other,'manual\n原文',o)
        self.assertEqual(model.compile_output(s,o),model.compile_output(other,o))
        self.assertEqual(s['multi_output']['outputs'][o]['draft_base'],other['multi_output']['outputs'][o]['draft_base'])
        drafts.clear(s); drafts.clear(other,o)
        self.assertEqual(model.compiled_outputs(s),model.compiled_outputs(other))
    def test_list_draft_does_not_overwrite_canvas_or_other_output(self):
        s,c,o=workspace(); drafts.edit(s,'canvas',o); drafts.edit(s,'second','out2')
        activate_selection_view(s,'list'); drafts.edit(s,'list')
        self.assertEqual(s['multi_output']['outputs'][o]['draft'],'canvas')
        self.assertEqual(s['multi_output']['outputs'][o]['list_draft']['draft'],'list')
        drafts.clear(s); activate_selection_view(s,'canvas')
        self.assertEqual(s['draft'],'canvas'); self.assertEqual(model.compile_output(s,'out2')['final_prompt'],'second')
    def test_empty_manual_is_not_clear_and_keeps_original_base(self):
        s,c,o=workspace(); drafts.edit(s,''); base=s['draft_base']; s['uses']['one']['prompt']='changed'
        drafts.edit(s,''); self.assertEqual(model.compile_output(s,o)['final_prompt'],''); self.assertEqual(s['draft_base'],base)
        drafts.clear(s); self.assertEqual(model.compile_output(s,o)['final_prompt'],'changed')
    def test_removing_binding_preserves_other_workflows_and_outputs(self):
        s,c,o=workspace(); s['generation']['profiles'].append(workflow('other')); model.set_binding(s,'other',o,('6','text'))
        before=copy.deepcopy(s); model.set_binding(s,'flow',o,None)
        self.assertEqual(s['multi_output']['bindings'],[b for b in before['multi_output']['bindings'] if not (b['workflow']=='flow' and b['output']==o)])
        with self.assertRaises(ValueError):model.set_binding(s,'flow','out3',('7','text'))
        model.set_binding(s,'flow',o,('6','text')); self.assertCountEqual(model.bound_texts(s,s['generation']['profiles'][0]),model.bound_texts(before,before['generation']['profiles'][0]))

if __name__=='__main__':unittest.main()
