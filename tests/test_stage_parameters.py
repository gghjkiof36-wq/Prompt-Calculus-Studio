"""Pure parameter intentions, queue capture and exact native payload evidence."""
import copy
import sqlite3
import unittest
from prompt_calculus_studio import stage_parameters as p
from prompt_calculus_studio.stage_store import StageStore


def config(value=3):
    return dict(version=1,identity=dict(workflow='A',frontend_id='native-A',origin=dict(server='http://fixture',path='A.json')),
        patches=[dict(node='35',path=['35'],class_type='KSampler',field='cfg',type='FLOAT',schema='schema-1',base=8,value=value,min=0,max=100)])


def description():
    field=dict(config()['patches'][0]);field['value']=8;field['editable']=True
    return dict(version=1,identity=config()['identity'],nodes=[dict(id='35',path=['35'],class_type='KSampler',title='First',fields=[field])])


class ParameterContractTests(unittest.TestCase):
    def test_queue_capture_and_task_priority_do_not_backfill_live_values(self):
        state=dict(multi_output=dict(stages={'one':dict(parameters=config(3))}))
        saved=dict(parameters=p.capture(state,['one']))
        state['multi_output']['stages']['one']['parameters']=config(7)
        self.assertEqual(p.effective(saved,'one')['patches'][0]['value'],3)
        saved['parameter_overrides']={'one':config(4)}
        self.assertEqual(p.effective(saved,'one')['patches'][0]['value'],4)
        self.assertIsNone(p.effective({},'one'))
        self.assertEqual(state['multi_output']['stages']['one']['parameters']['patches'][0]['value'],7)

    def test_identity_schema_link_ownership_and_external_same_field_conflicts(self):
        desc=description();p.verify(config(),desc)
        desc['nodes'][0]['title']='Renamed';p.verify(config(),desc)
        for change in ('value','schema','editable','class_type'):
            bad=copy.deepcopy(desc)
            if change=='class_type':bad['nodes'][0][change]='Other'
            else:bad['nodes'][0]['fields'][0][change]={'value':9,'schema':'other','editable':False}[change]
            with self.assertRaises(ValueError):p.verify(config(),bad)
        with self.assertRaises(ValueError):p.verify(config(),desc,owned={('35','cfg')})

    def test_invalid_editor_intermediate_states_and_large_integer_are_not_clamped(self):
        field=dict(field='width',type='INT',min=64,max=4096,step=8,enforce_step=True)
        for text in ('','-','1.','12e2','65','999999','١٢'):
            with self.assertRaises(ValueError):p.parse(text,field)
        self.assertEqual(p.parse('512',field),512)
        integer=dict(field='seed',type='INT',min=0,max=p.SAFE_INTEGER)
        self.assertEqual(p.parse(str(p.SAFE_INTEGER),integer),p.SAFE_INTEGER)
        with self.assertRaises(ValueError):p.parse(str(p.SAFE_INTEGER+1),integer)
        self.assertEqual(p.parse('',dict(field='text',type='STRING')),'')
        with self.assertRaises(ValueError):p.validate_value(True,integer)

    def test_actual_payload_is_verified_and_does_not_modify_graph(self):
        cfg=config();graph={'35':dict(class_type='KSampler',inputs=dict(cfg=3,seed=10))}
        proof=[dict(node='35',path=['35'],class_type='KSampler',field='cfg',schema='schema-1',actual=3,requested=3)]
        before=copy.deepcopy(graph);self.assertEqual(p.payload_evidence(cfg,graph,proof),proof);self.assertEqual(graph,before)
        for bad in ([],[dict(proof[0],actual=4)],[dict(proof[0],field='seed')]):
            with self.assertRaises(ValueError):p.payload_evidence(cfg,graph,bad)

    def test_seed_receipt_is_owned_monotonic_and_recoverable_without_another_draw(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);store=StageStore(db)
        cfg=config();field=cfg['patches'][0];field.update(field='seed',type='INT',value=10,base=500,min=0,max=2**50,seed_mode='increment',seed_timing='after',intent_id='intent')
        attempt=dict(id='attempt-1',workspace='workspace',stage='one',parent='entry',created=1,
            snapshot_state={'multi_output':{'stages':{'one':{'parameters':cfg}}}})
        proof=dict(node='35',path=['35'],class_type='KSampler',field='seed',schema='schema-1',requested=10,actual=10,seed_mode='increment',seed_timing='after')
        job=dict(prompt_id='prompt-1',payload={'prompt':{'35':{'class_type':'KSampler','inputs':{'seed':10}}}},
            parameter_receipt=dict(version=1,identity=cfg['identity'],owner=dict(workspace='workspace',stage='one',parent='entry'),fields=[proof]))
        bad=copy.deepcopy(job);bad['parameter_receipt']['owner']['stage']='other';store.record_parameters(attempt,bad)
        self.assertFalse(store.rows('parameter_state'))
        store.record_parameters(attempt,job)
        with self.assertRaisesRegex(ValueError,'回執'):store.resolve_parameters('workspace','one',cfg)
        proof['next_value']=11;store.record_parameters(attempt,job)
        self.assertEqual(store.resolve_parameters('workspace','one',cfg)['patches'][0]['resolved'],11)
        later=copy.deepcopy(attempt);later.update(id='attempt-2',created=2)
        next_job=copy.deepcopy(job);next_job['prompt_id']='prompt-2';next_job['payload']['prompt']['35']['inputs']['seed']=11
        next_job['parameter_receipt']['fields'][0].update(actual=11,next_value=12)
        store.record_parameters(later,next_job);store.record_parameters(attempt,job)
        self.assertEqual(store.resolve_parameters('workspace','one',cfg)['patches'][0]['resolved'],12)
        replay=copy.deepcopy(later);replay.update(created=3,parameter_replay=True)
        store.record_parameters(replay,job);self.assertEqual(store.resolve_parameters('workspace','one',cfg)['patches'][0]['resolved'],12)
        reopened=StageStore(db);self.assertEqual(reopened.resolve_parameters('workspace','one',cfg)['patches'][0]['resolved'],12)
        changed=copy.deepcopy(cfg);changed['patches'][0]['intent_id']='new-intent'
        self.assertNotIn('resolved',reopened.resolve_parameters('workspace','one',changed)['patches'][0])

    def test_invalid_target_range_and_receipt_shapes_are_rejected(self):
        for mutate in (lambda f:f.update(path=['58']),lambda f:f.update(step=0),lambda f:f.update(min=True),lambda f:f.update(max=10**1000),lambda f:f.update(base=[])):
            bad=config();mutate(bad['patches'][0])
            with self.assertRaises(ValueError):p.validate(bad)

    def test_editing_a_started_or_concurrently_changed_task_preserves_both_drafts(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);store=StageStore(db)
        item=store.add('entry','workspace','owner',status='waiting',scheduler='q',stages=['one'],saved={'parameters':{'one':config(3)}})
        saved=copy.deepcopy(item['saved']);store.edit_parameters(item['id'],'one',config(4),saved)
        self.assertEqual(p.effective(store.read(item['id'])['saved'],'one')['patches'][0]['value'],4)
        with self.assertRaises(ValueError):store.edit_parameters(item['id'],'one',config(5),saved)
        store.update(item['id'],status='preparing')
        with self.assertRaises(ValueError):store.edit_parameters(item['id'],'one',config(6),store.read(item['id'])['saved'])
        self.assertEqual(p.effective(store.read(item['id'])['saved'],'one')['patches'][0]['value'],4)
