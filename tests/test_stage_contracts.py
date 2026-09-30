"""Port-direction/type boundaries and one-way historical state conversion."""
import copy
import unittest
from prompt_studio.module_contracts import DATA_TYPES,MODULES,can_connect,port_types
from prompt_studio.stage_model import upgrade


def state():
    return dict(multi_output=dict(version=7,canvases={'canvas':{}},outputs={'prompt':{}},clip_inputs={'clip':{}},
        image_inputs={'input':{}},stages={'stage':{}},schedulers={'q':dict(channels=[dict(id='t',type='clip'),dict(id='i',type='image')])}),
        canvas_functions=dict(images={'image':{},'source':dict(iterate=True)}))


class PortContractTests(unittest.TestCase):
    def test_control_reference_and_values_do_not_substitute_each_other(self):
        s=state()
        for source,destination,kind in [('clip','stage','control'),('input','stage','control'),('stage','q::flow','flow'),
            ('image','source','image'),('source','prompt','clip'),('q::t','clip','clip'),('stage','__result_preview__','image')]:
            self.assertTrue(can_connect(s,source,destination,kind),(source,destination,kind))
        for source,destination,kind in [('stage','clip','control'),('stage','input','flow'),('q::flow','clip','clip'),
            ('source','q::t','image'),('__result_preview__','input','image'),('q::i','q::i','image')]:
            self.assertFalse(can_connect(s,source,destination,kind),(source,destination,kind))
        self.assertEqual(port_types(s,'q::t',True),port_types(s,'q::t',False))
        self.assertEqual(set(DATA_TYPES),{'clip','image','content'})
        self.assertEqual(MODULES['stage'].category,'execution')
        self.assertFalse(port_types(s,'__result_preview__',True))

    def test_old_chain_and_native_image_binding_retained_without_execution(self):
        old=state();data=old['multi_output'];data.update(version=6,connections=[dict(id='old',source='clip',destination='legacy',kind='control')],
            workflow_order=dict(visible=True,chain=dict(enabled=True,stages=[dict(id='original')])))
        data.pop('stages');old['canvas_functions']['images']['image']['binding']={'workflow':'A','node':'9'}
        original=copy.deepcopy(old);new=upgrade(old)
        self.assertEqual(old,original);self.assertFalse(new['multi_output']['stages'])
        self.assertFalse(new['multi_output']['connections']);self.assertFalse(new['multi_output']['workflow_order']['chain']['enabled'])
        self.assertEqual(new['multi_output']['workflow_order']['legacy_control_connections'],original['multi_output']['connections'])
        self.assertEqual(new['canvas_functions']['images']['image']['legacy_binding'],{'workflow':'A','node':'9'})
        self.assertEqual(upgrade(new),new)
