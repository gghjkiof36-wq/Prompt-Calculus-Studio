import copy,json,sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from prompt_calculus_studio.core import Storage,build_prompt
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.multi_output import compiled_outputs
from test_canvas_refinement import legacy
from test_multi_output import workspace


class LoadingTests(unittest.TestCase):
    def test_old_default_canvas_label_updates_all_workspaces_once_without_changing_content(self):
        from prompt_calculus_studio.core import initial_state
        from prompt_calculus_studio.canvas_starter import initialize
        from prompt_calculus_studio.multi_output import new_canvas
        from prompt_calculus_studio.workspace_scene import capture,create
        from prompt_calculus_studio.drafts import edit
        state=initialize(prepare_state(initial_state(),multi=True),fresh_install=True)
        canvases=state['multi_output']['canvases'];key=next(iter(canvases))
        canvases[key]['name']='文字畫布';canvases['custom']=new_canvas('我的文字畫布')
        edit(state,'',state['multi_output']['current_output']);capture(state);create(state,'另一工作區',duplicate=True)
        original=copy.deepcopy(state);expected=copy.deepcopy(state)
        expected['multi_output']['canvases'][key]['name']='畫布'
        for saved in expected['workspace_scenes']['items'].values():saved['multi_output']['canvases'][key]['name']='畫布'
        with tempfile.TemporaryDirectory() as folder:
            store=Storage(folder)
            try:
                store.save(state);converted=store.load_current(multi=True)
                self.assertEqual(converted,expected);self.assertEqual(state,original)
                self.assertEqual(build_prompt(converted),build_prompt(original))
                backups=list(Path(folder).glob('before-import-*.sqlite3'));self.assertEqual(len(backups),1)
                db=sqlite3.connect(backups[0])
                try:self.assertEqual(json.loads(db.execute('SELECT body FROM document WHERE id=1').fetchone()[0]),original)
                finally:db.close()
                self.assertEqual(store.load_current(multi=True),expected)
                self.assertEqual(list(Path(folder).glob('before-import-*.sqlite3')),backups)
            finally:store.close()

    def test_old_document_conversion_is_once_and_backup_has_original(self):
        original=legacy()
        with tempfile.TemporaryDirectory() as folder, closing(Storage(folder)) as store:
            store.save(original); converted=store.load_current(multi=True)
            backups=list(Path(folder).glob('before-import-*.sqlite3')); self.assertEqual(len(backups),1)
            db=sqlite3.connect(backups[0])
            try:saved=json.loads(db.execute('SELECT body FROM document WHERE id=1').fetchone()[0])
            finally:db.close()
            self.assertEqual(saved,original); self.assertEqual(converted['draft'],original['draft'])
            text=compiled_outputs(converted); self.assertEqual(build_prompt(original),build_prompt(converted))
            again=store.load_current(multi=True); self.assertEqual(compiled_outputs(again),text); self.assertEqual(len(list(Path(folder).glob('before-import-*.sqlite3'))),1)
    def test_current_roundtrip_keeps_binding_order_drafts_and_references(self):
        from prompt_calculus_studio.drafts import edit
        from prompt_calculus_studio.workspace_scene import capture
        state,c,o=workspace();state=prepare_state(state,multi=True)
        self.assertEqual(state['version'],7)
        edit(state,'手動\nexact',o);state['settings']['dictionary_buffer']='unfinished =';capture(state)
        original=copy.deepcopy(state)
        with tempfile.TemporaryDirectory() as folder, closing(Storage(folder)) as store:
            store.save(state);saved=store.load_current(multi=True)
            self.assertEqual(saved,original)
            self.assertEqual(compiled_outputs(saved),compiled_outputs(original))
            self.assertEqual(store.load_current(multi=True),saved)
            self.assertEqual(list(Path(folder).glob('before-import-*.sqlite3')),[])
        self.assertEqual(state,original)

    def test_version4_roundtrip_adds_explicit_types_and_preserves_old_bindings(self):
        from prompt_calculus_studio.clip_flow import source
        from prompt_calculus_studio.multi_output import capture_current
        from prompt_calculus_studio.composition import walk
        state,c,o=workspace();self.assertEqual(state['version'],4)
        state['draft']='';state['draft_base']='old base';capture_current(state);original=copy.deepcopy(state)
        expected_uses=copy.deepcopy(state['uses'])
        for root in expected_uses.values():
            for part in walk(root):part['material_type']='text'
        with tempfile.TemporaryDirectory() as folder, closing(Storage(folder)) as store:
            store.save(state);saved=store.load_current(multi=True)
            self.assertEqual(saved['version'],7)
            self.assertEqual(saved['uses'],expected_uses)
            for field in ('output_order','settings','draft','draft_base'):
                self.assertEqual(saved.get(field),original.get(field))
            self.assertEqual(compiled_outputs(saved),compiled_outputs(original))
            bindings=[dict(workflow=b['workflow'],output=source(saved,b['clip']),node=b['node'],field=b['field']) for b in saved['multi_output']['bindings']]
            self.assertEqual(bindings,original['multi_output']['bindings'])
            backups=list(Path(folder).glob('before-import-*.sqlite3'));self.assertEqual(len(backups),1)
            with closing(sqlite3.connect(backups[0])) as db:
                self.assertEqual(json.loads(db.execute('SELECT body FROM document WHERE id=1').fetchone()[0]),original)
            self.assertEqual(store.load_current(multi=True),saved)
            self.assertEqual(list(Path(folder).glob('before-import-*.sqlite3')),backups)
        self.assertEqual(state,original)
    def test_unsupported_version_does_not_replace_document_or_create_migration_backup(self):
        state,c,o=workspace(); state['version']=999
        with tempfile.TemporaryDirectory() as folder, closing(Storage(folder)) as store:
            body=json.dumps(state); store.db.execute('INSERT OR REPLACE INTO document VALUES (1,?)',(body,)); store.db.commit()
            with self.assertRaises(ValueError):store.load_current(multi=True)
            self.assertEqual(store.db.execute('SELECT body FROM document WHERE id=1').fetchone()[0],body)
            self.assertEqual(list(Path(folder).glob('before-import-*.sqlite3')),[])

if __name__=='__main__':unittest.main()
