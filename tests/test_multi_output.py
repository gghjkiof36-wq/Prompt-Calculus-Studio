import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from prompt_studio.core import initial_state,build_prompt,validate_state,Storage,activate_selection_view
from prompt_studio.composition import node
from prompt_studio.multi_output import (migrate,new_canvas,new_output,ident,assign,connect,disconnect,
    bind,bound_texts,compile_output,select_output,GENERATOR)
from prompt_studio.generation import submission,validate_profile
from prompt_studio.snapshots import make_snapshot,validate_snapshot,restore_snapshot


def workflow(name='flow'):
    return dict(id=name,name=name,mode='txt2img',prompt=['6','text'],multi_text=True,sampler='3',size='5',values={},graph={
        '3':dict(class_type='KSampler',inputs=dict(positive=['6',0],negative=['7',0],latent_image=['5',0],seed=2,steps=1,cfg=1,denoise=1,sampler_name='euler',scheduler='normal')),
        '5':dict(class_type='EmptyLatentImage',inputs=dict(width=64,height=64,batch_size=1)),
        '6':dict(class_type='CLIPTextEncode',inputs=dict(text='original positive')),
        '7':dict(class_type='CLIPTextEncode',inputs=dict(text='original negative')),
        '8':dict(class_type='OtherText',inputs=dict(text_g='third original',text_l=['6',0]))})


def workspace():
    s=initial_state(); s.update(version=3,selection_view='canvas',uses={'one':node('first','eyes, blue')},prompt_layout='paragraphs')
    s=migrate(s); data=s['multi_output']; cid=next(iter(data['canvases'])); out=data['current_output']
    s['generation']=dict(mode='txt2img',profiles=[workflow()],chosen={'txt2img':'flow'},source=None)
    for c,o,t in [('canvas2','out2','closed eyes'),('canvas3','out3','spare')]:
        data['canvases'][c]=new_canvas(c); data['outputs'][o]=new_output(o)
        s['uses'][c]=node(c,t); assign(s,c,c); connect(s,c,o,'text')
        connect(s,o,GENERATOR,'execution')
    bind(s,'flow',out,'6','text'); bind(s,'flow','out2','7','text')
    return s,cid,out


class MultiOutputTests(unittest.TestCase):
    def test_three_canvases_only_two_sent_and_submission_is_frozen(self):
        s,c,o=workspace(); original=copy.deepcopy(s); snap=make_snapshot(s)
        payload=submission(s['generation']['profiles'][0],snap)
        self.assertEqual(payload['prompt']['6']['inputs']['text'],'eyes, blue')
        self.assertEqual(payload['prompt']['7']['inputs']['text'],'closed eyes')
        self.assertEqual(payload['prompt']['8']['inputs']['text_g'],'third original')
        s['uses']['one']['prompt']='edited'; disconnect(s,s['multi_output']['connections'][0]['id'])
        self.assertEqual(payload['prompt']['6']['inputs']['text'],'eyes, blue')
        self.assertEqual(snap['state']['uses'],original['uses'])

    def test_exclusions_are_local_and_order_is_explicit(self):
        s,c,o=workspace(); s['uses']['canvas2']['excludes']=['eyes']
        self.assertEqual(compile_output(s,o)['final_prompt'],'eyes, blue')
        s['uses']['last']=node('last','green'); assign(s,'last',c)
        s['text_positions']={'one':[999,200],'last':[-99,-200]}
        self.assertEqual(compile_output(s,o)['final_prompt'],'eyes, blue,\n\ngreen')

    def test_empty_is_valid_missing_source_is_not(self):
        s,c,o=workspace(); s['uses']['canvas2']['prompt']=''
        self.assertEqual(bound_texts(s,s['generation']['profiles'][0])[1]['text'],'')
        line=next(x for x in s['multi_output']['connections'] if x['destination']=='out2')
        disconnect(s,line['id']); validate_state(s)
        with self.assertRaisesRegex(ValueError,'缺少來源'): bound_texts(s,s['generation']['profiles'][0])

    def test_duplicate_target_linked_input_and_wrong_types_rejected(self):
        s,c,o=workspace()
        with self.assertRaisesRegex(ValueError,'占用'): bind(s,'flow','out3','7','text')
        with self.assertRaisesRegex(ValueError,'上游'): bind(s,'flow','out3','8','text_l')
        with self.assertRaises(ValueError): connect(s,o,c,'text')
        with self.assertRaises(ValueError): connect(s,c,'out2','text')
        with self.assertRaises(ValueError): connect(s,GENERATOR,c,'image')
        bind(s,'flow','out3','8','text_g')
        self.assertEqual(len(bound_texts(s,s['generation']['profiles'][0])),3)

    def test_profile_identity_and_removed_node_never_reuse_old_target(self):
        s,c,o=workspace(); other=workflow('another'); s['generation']['profiles'].append(other)
        with self.assertRaisesRegex(ValueError,'綁定'): bound_texts(s,other)
        profile=s['generation']['profiles'][0]; del profile['graph']['7']; validate_state(s)
        with self.assertRaisesRegex(ValueError,'失效'): bound_texts(s,profile)

    def test_negative_is_independent_of_sampler_but_size_path_is_checked(self):
        p=workflow(); p['multi_text']=False; p['prompt']=['7','text']; validate_profile(p)
        p['size']='9'; p['graph']['9']=dict(class_type='EmptyLatentImage',inputs=dict(width=64,height=64))
        with self.assertRaisesRegex(ValueError,'尺寸節點'): validate_profile(p)

    def test_each_manual_draft_and_list_choice_survives_output_switch(self):
        s,c,o=workspace(); s['draft']='first manual'; select_output(s,'out2'); s['draft']=''
        select_output(s,o); self.assertEqual(s['draft'],'first manual')
        self.assertEqual(compile_output(s,'out2')['final_prompt'],'')
        activate_selection_view(s,'list'); item=s['items'][0]; s['selections']={item['module']:[item['id']]}; s['draft']='list manual'
        select_output(s,'out2'); self.assertEqual(s['selections'],{}); self.assertIsNone(s['draft'])
        select_output(s,o); self.assertEqual(s['selections'][item['module']],[item['id']]); self.assertEqual(s['draft'],'list manual')
        activate_selection_view(s,'canvas'); self.assertEqual(s['draft'],'first manual')

    def test_legacy_migration_storage_and_snapshot_restore_are_exact(self):
        s=initial_state(); s.update(version=3,selection_view='canvas',uses={'one':node('one','a, b')},draft='manual',draft_base='a, b',text_positions={'one':[12,34]})
        original=copy.deepcopy(s); migrated=migrate(s)
        self.assertEqual(s,original); self.assertEqual(build_prompt(s),build_prompt(migrated)); self.assertEqual(migrated['draft'],'manual')
        with tempfile.TemporaryDirectory() as directory:
            store=Storage(directory); store.save(s); backup=store.backup(); store.save(migrated); store.close()
            store=Storage(directory); loaded=store.load(); store.close(); self.assertEqual(build_prompt(loaded),build_prompt(s))
            db=sqlite3.connect(backup); self.assertEqual(json.loads(db.execute('SELECT body FROM document').fetchone()[0])['version'],3); db.close()
        snap=make_snapshot(migrated); validate_snapshot(snap); restored=restore_snapshot(initial_state(),snap)
        self.assertEqual(restored['draft'],'manual'); self.assertEqual(build_prompt(restored),build_prompt(s))
        root=next(iter(restored['uses'])); self.assertEqual(restored['text_positions'][root],[12,34])

    def test_isolated_list_outputs_submit_their_visible_drafts_and_restore_canvas(self):
        s,c,o=workspace(); s['settings']['separate_selections']=True
        activate_selection_view(s,'list'); s['draft']='list positive'
        select_output(s,'out2'); s['draft']='list negative'
        first=bound_texts(s,s['generation']['profiles'][0])
        self.assertEqual([v['text'] for v in first],['list positive','list negative'])
        select_output(s,o)
        self.assertEqual(bound_texts(s,s['generation']['profiles'][0]),first)
        snap=make_snapshot(s); validate_snapshot(snap)
        self.assertEqual(submission(s['generation']['profiles'][0],snap)['prompt']['7']['inputs']['text'],'list negative')
        activate_selection_view(s,'canvas')
        self.assertEqual([v['text'] for v in bound_texts(s,s['generation']['profiles'][0])],['eyes, blue','closed eyes'])

    def test_migrate_from_isolated_list_keeps_both_view_drafts(self):
        old=initial_state(); old.update(version=3,selection_view='list',uses={'one':node('one','canvas original')},draft='list manual',view_drafts={'canvas':dict(draft='canvas manual',draft_base='canvas original')})
        new=migrate(old); self.assertEqual(new['draft'],'list manual')
        activate_selection_view(new,'canvas'); self.assertEqual(new['draft'],'canvas manual'); self.assertEqual(build_prompt(new),'canvas original')

    def test_fixed_workspace_restores_canvas_ownership_without_stale_roots(self):
        from prompt_studio.core import apply_workspace
        s,c,o=workspace(); fixed=s['workspaces'][0]; fixed.update(fixed_uses=['one','restored'],uses={'restored':node('restored','restored content')},canvas_owners={'restored':c})
        apply_workspace(s,fixed['id']); validate_state(s)
        self.assertNotIn('one',s['multi_output']['canvases'][c]['members'])
        self.assertEqual(compile_output(s,o)['final_prompt'],'restored content')
        self.assertEqual(compile_output(s,'out2')['final_prompt'],'closed eyes')
        s['multi_output']['canvases'][c]['edit_positions']={'restored':[315,215]}
        restored=restore_snapshot(s,make_snapshot(s)); member=restored['multi_output']['canvases'][c]['members'][0]
        self.assertEqual(restored['multi_output']['canvases'][c]['edit_positions'][member],[315,215])
        apply_workspace(restored,restored['workspace']); validate_state(restored)
        self.assertEqual(compile_output(restored,o)['final_prompt'],'restored content')

    def test_unassigned_is_excluded_and_two_canvases_cannot_own_same_use(self):
        s,c,o=workspace(); s['uses']['outside']=node('outside','outside')
        self.assertNotIn('outside',compile_output(s,o)['final_prompt'])
        assign(s,'one','canvas2'); self.assertEqual(compile_output(s,o)['final_prompt'],'')
        s['multi_output']['canvases'][c]['members'].append('one')
        with self.assertRaisesRegex(ValueError,'重複歸屬'): validate_state(s)

    def test_snapshot_tampering_and_unknown_version_fail(self):
        s,c,o=workspace(); snap=make_snapshot(s); snap['outputs']['out2']['final_prompt']='wrong'
        with self.assertRaisesRegex(ValueError,'多輸出'): validate_snapshot(snap)
        s['multi_output']['version']=20
        with self.assertRaises(ValueError): validate_state(s)
