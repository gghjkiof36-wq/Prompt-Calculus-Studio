"""A missing job, replacement Stage, other-document inspect and recovery."""
import copy
import unittest
import test_aux_event_acceptance as auxiliary
from prompt_calculus_studio import stage_model, multi_output


class OtherDocumentTests(unittest.TestCase):
    setUpClass=auxiliary.AuxiliaryAcceptanceTests.setUpClass
    setUp=auxiliary.AuxiliaryAcceptanceTests.setUp
    client=auxiliary.AuxiliaryAcceptanceTests.client
    aux=auxiliary.AuxiliaryAcceptanceTests.aux
    store=auxiliary.AuxiliaryAcceptanceTests.store
    bind=auxiliary.AuxiliaryAcceptanceTests.bind
    wait=auxiliary.AuxiliaryAcceptanceTests.wait
    drain=auxiliary.AuxiliaryAcceptanceTests.drain
    complete=auxiliary.AuxiliaryAcceptanceTests.complete

    def test_replacement_cannot_bypass_but_one_exit_restores_other_inspect_and_stage(self):
        self.h.schedule(3)
        ident,_=self.h.start()
        attempt=self.store.read(ident)
        self.client.input_flow.chain.pause(attempt['owner'],reason='route')
        self.peer.pending.clear()
        self.client.generation.recheck(ident);self.client.events.reconcile.tick()
        self.h.clock.now=31;self.client.events.reconcile.tick()
        self.client.events.health(dict(state='receiving',needs_reconcile=False))
        self.f.a.host.canvas.remove_flow_node('stages',attempt['stage'])
        def replace(state):
            new=stage_model.add(state,workflow='flow')
            state['multi_output']['stages'][new].update(output='9',text_output=['6','text'])
            multi_output.connect(state,next(iter(state['multi_output']['clip_inputs'])),new,'control')
        self.f.a.host.canvas.commit(replace)
        self.f.click(self.f.a)
        self.assertEqual(len(self.peer.submissions),1)
        for run in self.client.input_flow.chain.runs(True):
            if run['id']!=attempt['owner']:self.client.input_flow.chain.pause(run['id'])
        other=self.f.b.host.comfy
        profile=copy.deepcopy(other.window.state['generation']['profiles'][0])
        values=[];errors=[]
        other.workflow_catalog.read_draft('folder/A.json',profile,lambda *v:values.append(v),errors.append)
        self.assertEqual(values,[])
        self.assertTrue(errors)
        self.assertIn('A',str(errors[0]))
        # The surviving scheduler menu / generation history owns the old job,
        # even after its original Stage card was removed.
        self.client.generation.abandon(ident)
        self.assertEqual(self.client.generation.record(ident)['state'],'retired')
        self.hold=False;errors.clear()
        other.workflow_catalog.read_draft('folder/A.json',profile,lambda *v:values.append(v),errors.append)
        self.drain()
        self.assertEqual(errors,[])
        self.assertTrue(values,'the other document can read live parameters again')
        self.f.click(self.f.b);self.drain()
        self.assertEqual(len(self.peer.submissions),2)


if __name__=='__main__':unittest.main()
