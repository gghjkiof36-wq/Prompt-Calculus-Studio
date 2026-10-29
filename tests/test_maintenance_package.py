import json,sys,subprocess,tempfile,unittest,zipfile,hashlib
from pathlib import Path
from prompt_calculus_studio.releases import CURRENT,RELEASES,select_release,write_launchers,SHARED_MODULES,APP_BASENAME,EXTENSION_FOLDER,SOURCE_ARCHIVE
from package_documents import bundle_documents,source_paths,validation_reference
from build_comfyui import build
ROOT=Path(__file__).resolve().parents[1]

class PackagingTests(unittest.TestCase):
    def test_historical_build_destinations_and_launch_arguments_remain_available(self):
        expected={'--v08':'v08-alpha','--v08-repair':'v08-alpha-2','--v08-alpha3':'v08-alpha-3','--v08-alpha3-hotfix1':'v08-alpha-3-hotfix-1','--v08-alpha3-hotfix2':'v08-alpha-3-hotfix-2','--v081-alpha1':'v081-alpha-1','--v081-alpha2':'v081-alpha-2'}
        for flag,folder in expected.items():self.assertEqual(select_release([flag]).folder,folder)
        self.assertEqual(select_release(['--v081-alpha2','--v08']).folder,'v081-alpha-2')
        repair=select_release(['--v0.8.9-beta.3.repair.1'])
        self.assertEqual(repair.version,'v0.8.9 Beta3 repair1')
        self.assertEqual(repair.argument,'--v0.8.9-beta.3')
        self.assertNotEqual(repair.folder,RELEASES['--v0.8.9-beta.3'].folder)
        with tempfile.TemporaryDirectory() as folder:
            release=RELEASES['--v081-maintenance']; write_launchers(folder,release)
            for ext in ('vbs','cmd'):
                content=(Path(folder)/(release.launcher+'.'+ext)).read_text(encoding='utf-8')
                self.assertIn(APP_BASENAME+'.exe',content); self.assertIn('--v081-alpha',content); self.assertIn('PROMPT_STUDIO_DATA',content)
    def test_source_bundle_hashes_match_and_contains_no_personal_data(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder); bundle_documents(folder); manifest=json.loads((folder/'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
            with zipfile.ZipFile(folder/SOURCE_ARCHIVE) as z:
                self.assertEqual(set(z.namelist()),{v['path'] for v in manifest})
                for entry in manifest:self.assertEqual(hashlib.sha256(z.read(entry['path'])).hexdigest(),entry['sha256'])
                for name in z.namelist():
                    self.assertFalse(name.startswith(('data/','qa/','.git/','vendor/','.builder/'))); self.assertFalse(name.endswith(('.sqlite3','.token')))
            info=json.loads((folder/'BUILD_INFO.json').read_text(encoding='utf-8'))
            required_validation={'--v0.8.9-beta.2':'docs/releases/0.8.9/validation/beta.2.md',
                                 '--v0.8.9-beta.3':'docs/releases/0.8.9/validation/beta.3.md',
                                 '--v0.8.10-pre-alpha.1':'docs/releases/0.8.10/validation/pre-alpha.1.md'}
            self.assertIn(CURRENT.flag,required_validation,'A new current release needs its explicit validation contract')
            self.assertEqual(info['release'],CURRENT.flag)
            self.assertEqual(info['validation'],required_validation[CURRENT.flag])
            self.assertTrue((folder/info['validation']).is_file())
            notice=(folder/'BUILD_NOTICE.txt').read_text(encoding='utf-8')
            for name in ('docs/guide/getting-started.md','docs/releases/README.md','docs/guide/licensing.md'):
                self.assertIn(name,notice);self.assertTrue((folder/name).is_file())
    def test_connector_sources_and_extension_assets_are_in_source_allowlist(self):
        packaged={path.relative_to(ROOT).as_posix() for path in source_paths(ROOT)}
        required={'tools/connector_host.py','tools/install_chrome_connector.py','tools/build_connector.py',
                  'prompt_calculus_studio/connector_capture.py','prompt_calculus_studio/connector_store.py',
                  'prompt_calculus_studio/connector_destinations.py',
                  'prompt_calculus_studio/connector_service.py','prompt_calculus_studio/connector_dialog.py',
                  'prompt_calculus_studio/connector_page.py','docs/development/chrome-connector.md',
                  'tests/test_connector_capture.py','tests/test_connector_recovery.py','tests/test_connector_host.py',
                  'tests/test_connector_destinations.py','tests/test_connector_direct_save.py'}
        extension=ROOT/'extensions'/'chrome'
        self.assertTrue((extension/'manifest.json').is_file())
        required.update(path.relative_to(ROOT).as_posix() for path in extension.rglob('*') if path.is_file())
        self.assertFalse(required-packaged,'Connector sources missing from tracked allowlist: '+str(sorted(required-packaged)))
    def test_connector_transport_capture_and_store_import_without_qt(self):
        code="""import sys,importlib
class NoQt:
 def find_spec(self,fullname,*args):
  if fullname.startswith(('PySide6','PyQt','shiboken6')):raise RuntimeError('Connector data/transport imported Qt')
sys.meta_path.insert(0,NoQt())
for name in sys.argv[1:]:importlib.import_module(name)
"""
        modules=('prompt_calculus_studio.connector_capture','prompt_calculus_studio.connector_store',
                 'prompt_calculus_studio.connector_destinations',
                 'tools.connector_host','tools.install_chrome_connector','tools.build_connector')
        run=subprocess.run([sys.executable,'-c',code,*modules],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
        self.assertEqual(run.returncode,0,run.stdout.decode('utf-8',errors='replace'))
    def test_validation_reference_does_not_assign_new_release_results_to_old_builds(self):
        paths=source_paths(ROOT)
        examples={
            '--v081-maintenance':'docs/releases/0.8.1/validation/maintenance.md',
            '--v082-alpha1-repair5':'docs/releases/0.8.2/validation/repair-5.md',
            '--v083-direct':'docs/releases/0.8.3/validation/alpha.1.md',
            '--v0.8.4-alpha.1':'docs/releases/0.8.4/validation/alpha.1.md',
            '--v0.8.5-alpha.1':'docs/releases/0.8.5/validation/alpha.1.md',
            '--v0.8.7-alpha.1':'docs/releases/0.8.7/validation/alpha.1.md',
            '--v0.8.8-alpha.1':'docs/releases/0.8.8/validation/alpha.1.md',
            '--v086-revision12':'docs/releases/0.8.6/validation/revision12.md',
            '--v087-candidate1':'docs/releases/0.8.7/development/candidate-validation.md',
            '--v087-candidate2':'docs/releases/0.8.7/development/candidate-validation.md',
            '--v087-candidate26':'docs/releases/0.8.7/development/candidate-validation.md',
        }
        for flag,path in examples.items():
            self.assertEqual(validation_reference(RELEASES[flag],paths),dict(validation=path))
        for release in RELEASES.values():
            value=validation_reference(release,paths)
            self.assertTrue((ROOT/value['validation']).is_file())
            if release.flag!='--v0.8.6-alpha.1':
                self.assertNotEqual(value['validation'],'docs/releases/0.8.6/validation/alpha.1.md')
            if value['validation']=='docs/releases/README.md':self.assertIn('validation_note',value)
        without_current_report=[path for path in paths if path.relative_to(ROOT).as_posix()!='docs/releases/0.8.6/validation/alpha.1.md']
        self.assertEqual(validation_reference(RELEASES['--v0.8.6-alpha.1'],without_current_report)['validation'],'docs/releases/README.md')
    def test_packaged_shared_core_restores_old_snapshot_without_qt(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder); destination=folder/EXTENSION_FOLDER; archive=folder/'extension.zip'; build(destination,archive)
            with zipfile.ZipFile(archive) as packaged:
                self.assertTrue(all(name.startswith(EXTENSION_FOLDER+'/') for name in packaged.namelist()))
            self.assertTrue(archive.is_file()); shared=destination/'shared'; self.assertTrue(all((shared/(name+'.py')).is_file() for name in SHARED_MODULES))
            code="""import sys,importlib
class NoQt:
 def find_spec(self,fullname,*args):
  if fullname.startswith('PySide6'):raise RuntimeError('Shared core imported Qt')
sys.meta_path.insert(0,NoQt())
for name in sys.argv[1:]:importlib.import_module('shared.'+name)
from shared.core import initial_state,build_prompt
from shared.snapshots import make_snapshot,restore_snapshot
from shared.state_loading import prepare_state
from shared.workspace_scene import create,switch
s=initial_state(); s['draft']='exact historical text'; snapshot=make_snapshot(s)
restored=restore_snapshot(s,snapshot); assert restored['draft']=='exact historical text'
assert build_prompt(prepare_state(restored))==snapshot['generated_prompt']
s=prepare_state(initial_state(),multi=True)
workspace=create(s,'Packaged starter'); switch(s,workspace)
assert len(s['multi_output']['schedulers'])==1 and len(s['multi_output']['stages'])==1
assert len(s['multi_output']['connections'])==5
from shared.prompt_sources import add,definitions,build,reserve,read_library
from shared.prompt_groups import new_group
from shared.composition import node
from shared.materials import save,add_category
root=node('group material');group=new_group('one','exact group text')
root['sequence']=dict(version=1,mode='increment',start=group['id'],selected=group['id'],groups=[group])
material=save(s,dict(name='group material',prompt='',module=add_category(s,'groups'),composition=root))
key=add(s);read_library(s,key,material);root=definitions(s)[key]['root']
# Explicit material types and independent progress require schema 7.
snapshot=make_snapshot(s);assert snapshot['schema_version']==7
restored=restore_snapshot(s,snapshot);assert definitions(restored)[key]['root']['sequence']==root['sequence']
plan=build(restored,[key],count=1);reserve(restored,plan)
assert definitions(restored)[key]['root']['selection_cursor']['position']['group']==root['sequence']['groups'][0]['id']
assert not definitions(restored)[key]['root']['selection_cursor']['position']['exhausted']
"""
            run=subprocess.run([sys.executable,'-c',code,*SHARED_MODULES],cwd=destination,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
            self.assertEqual(run.returncode,0,run.stdout.decode('utf-8',errors='replace'))

if __name__=='__main__':unittest.main()
