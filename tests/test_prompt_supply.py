import copy
import unittest
from PySide6.QtTest import QTest
from prompt_calculus_studio import prompt_sources as supply, prompt_groups as groups, composition as comp
from prompt_calculus_studio import multi_output as model
from prompt_calculus_studio.flow_data import add_scheduler, resolve
import test_084_stages as stage_tests


def definition(counts=(2, 1, 3)):
    root=comp.node('組合來源')
    rows=[groups.new_group(chr(65+i),f'prompt {i}') for i in range(len(counts))]
    for row,count in zip(rows,counts):row['repeat']=count
    root['sequence']=dict(version=1,mode='increment',start=rows[0]['id'],selected=rows[0]['id'],groups=rows)
    return root


class SupplyTests(unittest.TestCase):
    def setUp(self):
        stage_tests.StageTests.setUp(self)
        self.w.comfy.snapshot_versions=[1,2,3,4,5]
    tearDown=stage_tests.StageTests.tearDown
    stages=stage_tests.StageTests.stages
    click=stage_tests.StageTests.click
    workflows=stage_tests.StageTests.workflows
    image_batch=stage_tests.StageTests.image_batch

    def group_source(self,counts=(2,1,3),batch=False,scheduler=True,through_canvas=True):
        self.stages();result={}
        def setup(state):
            key=supply.add(state,root=definition(counts));result['source']=key
            supply.definitions(state)[key]['supply']='batch' if batch else 'step'
            if through_canvas:
                model.connect(state,key,self.cid,'content');state['uses'].pop('root')
                state['multi_output']['canvases'][self.cid]['members']=[]
                origin=self.out
            else:origin=key
            if scheduler:
                schedule=add_scheduler(state);result['scheduler']=schedule
                model.connect(state,origin,schedule+'::clip1','clip');model.connect(state,schedule+'::clip1',self.clip,'clip')
            else:model.connect(state,origin,self.clip,'clip')
        self.assertTrue(self.c.commit(setup),self.notices)
        return result

    def submitted_texts(self):return [v['prompt']['6']['inputs']['text'] for v in self.executor.submissions]

    def test_manual_click_reserves_next_repeat_before_first_finishes(self):
        config=self.group_source()
        for _ in range(3):self.click()
        rows=self.runner.store.rows('entry')
        self.assertEqual([r['saved']['inputs'][config['scheduler']+'::clip1']['value'] for r in rows],['prompt 0','prompt 0','prompt 1'])
        self.assertEqual(len(self.executor.submissions),1)
        for _ in range(3):self.executor.finish()
        self.assertEqual(self.submitted_texts(),['prompt 0','prompt 0','prompt 1'])

    def test_direct_manual_click_and_same_round_edit(self):
        config=self.group_source(scheduler=False)
        source=supply.definitions(self.w.state)[config['source']]
        ident=source['root']['sequence']['groups'][0]['id'];source['root']['round_drafts']={ident:'one edited round'}
        self.click();self.click();self.click()
        self.assertEqual(self.submitted_texts(),['one edited round','prompt 0','prompt 1'],self.notices)

    def test_twelve_batch_refill_frozen_after_edit_and_cancel_one(self):
        config=self.group_source((12,),batch=True)
        self.click();rows=self.runner.store.rows('entry',active=True)
        self.assertEqual(len(rows),10,self.notices)
        removed=rows[1]
        self.runner.remove_entry(removed['id'])
        source=supply.definitions(self.w.state)[config['source']];source['root']['sequence']['groups'][0]['text']='later edit'
        for _ in range(11):self.executor.finish()
        self.assertEqual(self.submitted_texts(),['prompt 0']*11,self.notices)
        self.assertIsNone(self.runner.current());self.assertEqual(self.runner.store.read(removed['id'])['status'],'removed')

    def test_batch_preparation_does_not_move_visible_group_to_last_reserved(self):
        config=self.group_source((2,1,3),batch=True)
        root=supply.definitions(self.w.state)[config['source']]['root'];seq=root['sequence']
        ids=[g['id'] for g in seq['groups']]
        self.click();self.assertEqual(len(self.runner.store.rows('entry')),6)
        self.assertEqual(seq['selected'],ids[0])
        self.executor.finish();self.assertEqual(seq['selected'],ids[0])
        self.executor.finish();self.assertEqual(seq['selected'],ids[1])
        self.executor.finish();self.assertEqual(seq['selected'],ids[2])

    def test_image_and_text_zip_counts_after_repeats(self):
        image,items,config=self.image_batch(count=5)
        key=[]
        def setup(state):
            source=supply.add(state,root=definition((2,1)));key.append(source);supply.definitions(state)[source]['supply']='batch'
            # The B workflow has an image binding; inspect queued text separately.
            model.connect(state,source,config['inner']+'::clip1','clip')
        self.assertTrue(self.c.commit(setup),self.notices);self.click()
        entries=self.runner.store.rows('entry');self.assertEqual(len(entries),3,self.notices)
        self.assertEqual([e['saved']['inputs'][config['inner']+'::clip1']['value'] for e in entries],['prompt 0','prompt 0','prompt 1'])
        for _ in range(3):self.executor.finish()
        self.assertEqual(len(self.executor.submissions),3);self.assertEqual(self.w.state['canvas_functions']['images'][image]['index'],3)
        self.assertIsNone(self.runner.current())

    def test_bypass_image_fixed_for_whole_text_batch_then_advances_once(self):
        image,items,config=self.image_batch(count=3,auto=False)
        source=[];schedule=[]
        def setup(state):
            key=supply.add(state,root=definition((3,)));source.append(key);supply.definitions(state)[key]['supply']='batch'
            profile=next(p for p in state['generation']['profiles'] if p['id']=='B')
            profile['graph']['6']=dict(class_type='CLIPTextEncode',inputs=dict(text='native B'))
            self.executor.graphs['B']['6']=copy.deepcopy(profile['graph']['6'])
            q=add_scheduler(state);schedule.append(q);model.connect(state,key,q+'::clip1','clip')
            # An image-only Stage still needs a connected input through the
            # scheduler. Bind text to B's CLIP and keep the image bypass intact.
            from prompt_calculus_studio.clip_flow import add,set_binding
            clip=add(state);set_binding(state,'B',clip,('6','text'))
            model.connect(state,q+'::clip1',clip,'clip');model.connect(state,clip,config['stage'],'control')
        self.assertTrue(self.c.commit(setup),self.notices);self.click()
        for _ in range(3):self.executor.finish()
        self.assertEqual(len(self.executor.submissions),3,self.notices)
        pictures=[p['prompt']['10']['inputs']['image'] for p in self.executor.submissions]
        self.assertEqual(len(set(pictures)),1);self.assertEqual(self.w.state['canvas_functions']['images'][image]['index'],1)

    def test_source_fanout_counts_once_and_copy_independent(self):
        from prompt_calculus_studio.canvas_operations import capture,paste
        config=self.group_source(batch=True)
        def setup(state):model.connect(state,config['source'],config['scheduler']+'::content1','content')
        self.assertTrue(self.c.commit(setup),self.notices)
        ids=supply.scheduler_sources(self.w.state,config['scheduler']);self.assertEqual(ids,[config['source']])
        state=copy.deepcopy(self.w.state);payload=capture(state,[config['source']]);cloned=paste(state,payload,(500,500))[0]
        supply.definitions(state)[cloned]['root']['sequence']['groups'][0]['text']='clone'
        self.assertEqual(resolve(state,config['source'],'clip')['value'],'prompt 0')
        self.click();self.assertEqual(len(self.runner.store.rows('entry')),6,self.notices)

    def test_image_exhaustion_cancel_gap_and_explicit_restart(self):
        from prompt_calculus_studio.image_source import select
        image,items,config=self.image_batch(count=4)
        self.click();rows=self.runner.store.rows('entry',active=True)
        self.runner.remove_entry(rows[1]['id'])
        for _ in range(3):self.executor.finish()
        self.assertIsNone(self.runner.current());self.assertEqual(len(self.executor.submissions),3)
        self.assertEqual(self.w.state['canvas_functions']['images'][image]['index'],3)
        self.click();self.assertEqual(len(self.executor.submissions),3)
        self.assertTrue(any('已供應完畢' in note for note in self.notices),self.notices)
        # Selecting a specific image intentionally starts a new reservation.
        self.assertTrue(self.c.commit(lambda s:select(s,image,2,self.w.store.directory)))
        self.click()
        for _ in range(2):self.executor.finish()
        self.assertEqual(len(self.executor.submissions),5,self.notices)

    def test_image_step_reserves_different_images_before_completion(self):
        image,items,config=self.image_batch(count=3)
        self.assertTrue(self.c.commit(lambda s:s['canvas_functions']['images'][image].update(supply='step')))
        for _ in range(3):self.click()
        entries=self.runner.store.rows('entry');self.assertEqual(len(entries),3,self.notices)
        self.assertEqual([e['saved']['source_positions'][image]['index'] for e in entries],[0,1,2])
        self.runner.remove_entry(entries[1]['id'])
        for _ in range(2):self.executor.finish()
        self.assertEqual(len(self.executor.submissions),2,self.notices)
        pictures=[p['prompt']['10']['inputs']['image'] for p in self.executor.submissions]
        self.assertEqual(len(set(pictures)),2)

    def test_older_extension_cannot_silently_render_group_schema(self):
        self.group_source()
        self.assertEqual(self.w.state['version'],5)
        self.w.comfy.snapshot_versions=[1,2,3,4]
        self.assertFalse(self.w.comfy.can_run)
        self.w.comfy.snapshot_versions.append(5)
        self.assertTrue(self.w.comfy.can_run)


if __name__=='__main__':unittest.main()
