"""Stage intent -> native prepare -> backend receipt, without a live service."""
import copy
import unittest
import test_native_queue as native_tests
from stage_parameter_fixture import add_samplers,intention
from prompt_calculus_studio import stage_model
from prompt_calculus_studio.snapshots import make_snapshot


class NativeParameterTests(unittest.TestCase):
    setUp=native_tests.NativeQueueTests.setUp

    def configured(self,field='cfg',value=3):
        from prompt_calculus_studio.state_loading import prepare_state
        state=prepare_state(copy.deepcopy(self.snapshot['state']),multi=True);profile=add_samplers(state['generation']['profiles'][0])
        stage=stage_model.add(state,workflow=profile['id'])
        config=intention(profile,field=field,value=value)
        if field=='seed':config['patches'][0].update(seed_mode='increment',seed_timing='after')
        state['multi_output']['stages'][stage]['parameters']=config
        self.snapshot=make_snapshot(state);self.snapshot['chain']=dict(run='run',stage=stage,item='item',attempt='attempt',revision='revision',round=1)
        self.request['snapshot']=self.snapshot;self.live['parameters_protocol']=1;self.live['bindings_protocol']=1;self.queue.poll(self.live)
        return config

    def prepared(self,config):
        self.queue.start(self.request);command=self.queue.poll(self.live)['commands'][0]
        graph=copy.deepcopy(self.snapshot['state']['generation']['profiles'][0]['graph'])
        graph['5']['inputs']['width']=1024 # Unspecified native field stays live.
        for binding in command.get('texts',[]):
            if binding.get('text_source')!='web':graph[binding['node']]['inputs'][binding['field']]=binding['text']
        for p in config['patches']:graph[p['node']]['inputs'][p['field']]=p['value']
        proof=[dict(p,requested=p['value'],actual=p['value']) for p in config['patches']]
        visual=dict(id='native-A',nodes=[])
        value=dict(command,session='tab',client_id='real-web-sid',output=graph,workflow=visual,parameter_evidence=proof)
        return value,graph,proof,visual

    def test_native_payload_and_owned_receipt_preserve_live_fields_and_intent(self):
        config=self.configured();before=copy.deepcopy(self.snapshot)
        value,graph,proof,visual=self.prepared(config);self.queue.prepare(value)
        visual['extra']=dict(pcs_native_operation=self.request['id'])
        payload=dict(prompt=graph,client_id='real-web-sid',extra_data=dict(extra_pnginfo=dict(workflow=visual)))
        self.service.prepare_prompt(payload);self.assertTrue(payload['prompt'])
        self.queue.reply(dict(self.live,id=self.request['id'],prompt_id=payload['prompt_id'],parameter_evidence=proof))
        receipt=self.queue.status(self.request['id'])
        self.assertEqual(receipt['payload']['prompt']['35']['inputs']['cfg'],3)
        self.assertEqual(receipt['payload']['prompt']['58']['inputs']['cfg'],8)
        self.assertEqual(receipt['payload']['prompt']['5']['inputs']['width'],1024)
        self.assertEqual(receipt['parameter_receipt']['owner'],dict(stage=before['chain']['stage'],parent=None,workspace=before['state']['workspace']))
        self.assertEqual(self.snapshot,before)

    def test_mismatch_and_missing_proof_never_reach_submission(self):
        config=self.configured();value,graph,proof,_=self.prepared(config)
        for bad in ([],[dict(proof[0],requested=4)],[dict(proof[0],path=['58'])],[dict(proof[0],schema='other')]):
            with self.assertRaises(ValueError):self.queue.prepare(dict(value,parameter_evidence=bad))
        graph['35']['inputs']['cfg']=8
        with self.assertRaises(ValueError):self.queue.prepare(dict(value,output=graph))
        self.assertIsNone(self.queue.status(self.request['id']).get('prompt_id'))

    def test_next_seed_requires_same_prompt_session_and_typed_native_evidence(self):
        config=self.configured('seed',10);value,graph,proof,visual=self.prepared(config);self.queue.prepare(value)
        visual['extra']=dict(pcs_native_operation=self.request['id'])
        payload=dict(prompt=graph,client_id='real-web-sid',extra_data=dict(extra_pnginfo=dict(workflow=visual)))
        self.service.prepare_prompt(payload);prompt=payload['prompt_id']
        for bad in (dict(session='other'),dict(prompt_id='other'),dict(parameter_evidence=[dict(proof[0],next_value=True)])):
            reply=dict(self.live,id=self.request['id'],prompt_id=prompt,parameter_evidence=proof);reply.update(bad)
            with self.assertRaises(ValueError):self.queue.reply(reply)
        proof[0]['next_value']=11
        self.queue.reply(dict(self.live,id=self.request['id'],prompt_id=prompt,parameter_evidence=proof))
        self.assertEqual(self.queue.status(self.request['id'])['parameter_receipt']['fields'][0]['next_value'],11)
        self.assertEqual(self.queue.poll(self.live)['commands'],[])

    def test_old_extension_and_missing_stage_refuse_without_issuing(self):
        self.configured();self.live.pop('parameters_protocol');self.queue.poll(self.live)
        with self.assertRaisesRegex(ValueError,'擴充'):self.queue.start(self.request)
        self.live['parameters_protocol']=1;self.queue.poll(self.live)
        self.snapshot['chain']['stage']='missing'
        with self.assertRaisesRegex(ValueError,'Stage'):self.queue.start(self.request)
        self.assertEqual(self.queue.poll(self.live)['commands'],[])
