"""Run deployment scripts only against inert packages and synthetic install trees."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from package_documents import package_manifest

ROOT=Path(__file__).resolve().parents[1]
SHELL=shutil.which('powershell.exe')

@unittest.skipUnless(os.name=='nt' and SHELL,'Windows PowerShell required')
class DeploymentTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(dir=ROOT/'qa');self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        # A child powershell.exe must use its own 5.1 modules, not the calling
        # tool's PowerShell 7 module path. This changes this subprocess only.
        self.env={**os.environ,'PSModulePath':str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/Modules')}
        self.source=self.root/'candidate'/'comfyui_prompt_calculus_studio';self.source.mkdir(parents=True)
        self.comfy=self.root/'comfy';(self.comfy/'custom_nodes').mkdir(parents=True);(self.comfy/'main.py').write_text('# inert fixture')
        self.data=self.root/'desktop-data';self.data.mkdir();(self.data/'studio.sqlite3').write_bytes(b'synthetic database')
        self.old=self.comfy/'custom_nodes'/'comfyui_prompt_studio';self.target=self.old.with_name('comfyui_prompt_calculus_studio')
        self.make_package(self.source,'comfyui')

    def make_package(self,path,kind):
        info=dict(release='--fixture-repair',version='fixture repair',git_head='a'*40,binary_git_head='a'*40,working_changes='')
        files={'SOURCE_MANIFEST.json':'[]','PromptCalculusStudio-source.zip':'inert source fixture'}
        if kind=='comfyui': files.update({'__init__.py':'# new extension','web/prompt_studio.js':'// new','web/style.css':'/* required styling */'})
        else: files.update({'PromptCalculusStudio.exe':'inert, never execute','_internal/prompt_studio/assets/build-info.json':json.dumps(info),'Start-fixture.vbs':'test fixture only'})
        for name,content in files.items():
            dest=path/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(content,encoding='utf-8')
        info['source_sha256']=hashlib.sha256((path/'PromptCalculusStudio-source.zip').read_bytes()).hexdigest()
        (path/'BUILD_INFO.json').write_text(json.dumps(info),encoding='utf-8');package_manifest(path,kind)

    def call(self,script='install_comfyui.ps1',extra=(),success=True):
        args=['-Source',str(self.source),'-ComfyUIRoot',str(self.comfy),'-DesktopData',str(self.data)] if script=='install_comfyui.ps1' else []
        result=subprocess.run([SHELL,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/script),*args,*map(str,extra)],env=self.env,capture_output=True,timeout=30)
        self.assertEqual(result.returncode==0,success,result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace'))
        return result

    def old_install(self,new_name=False):
        path=self.target if new_name else self.old;path.mkdir();(path/'obsolete.py').write_text('# obsolete')
        config=json.dumps(dict(library=str(self.data/'studio.sqlite3'),extra=dict(preserved=True)),indent=3).encode()
        (path/'local_library.json').write_bytes(config);return config

    def tree(self,path):return {p.relative_to(path).as_posix():p.read_bytes() for p in path.rglob('*') if p.is_file()}

    def test_update_renames_old_folder_preserves_full_config_and_complete_backup(self):
        config=self.old_install();before=self.tree(self.old);self.call(extra=['-Update'])
        self.assertFalse(self.old.exists());self.assertFalse((self.target/'obsolete.py').exists())
        self.assertEqual((self.target/'local_library.json').read_bytes(),config)
        backup=next((self.comfy/'.pcs-program-backups').glob('previous-*'));self.assertEqual(self.tree(backup),before)
        self.assertEqual([p.name for p in (self.comfy/'custom_nodes').iterdir()],['comfyui_prompt_calculus_studio'])

    def test_same_name_update_removes_obsolete_program_files_and_first_install_works(self):
        self.call();self.assertTrue(self.target.exists());(self.target/'obsolete.py').write_text('# old')
        self.call(extra=['-Update']);self.assertFalse((self.target/'obsolete.py').exists())

    def test_preflight_corrupt_missing_extra_wrong_version_or_missing_data_changes_nothing(self):
        self.old_install();before=self.tree(self.comfy);source=self.tree(self.source)
        for failure in ('corrupt','missing','extra','version','data'):
            with self.subTest(failure=failure):
                if failure=='corrupt': (self.source/'__init__.py').write_text('# altered')
                if failure=='missing': (self.source/'__init__.py').unlink()
                if failure=='extra': (self.source/'private.txt').write_text('synthetic private')
                if failure=='version':
                    info=json.loads((self.source/'BUILD_INFO.json').read_text());info['version']='other';(self.source/'BUILD_INFO.json').write_text(json.dumps(info))
                if failure=='data': (self.data/'studio.sqlite3').unlink()
                self.call(extra=['-Update'],success=False);self.assertEqual(self.tree(self.comfy),before)
                for p in self.source.rglob('*'):
                    if p.is_file() and p.relative_to(self.source).as_posix() not in source:p.unlink()
                for name,content in source.items():(self.source/name).write_bytes(content)
                (self.data/'studio.sqlite3').write_bytes(b'synthetic database')

    def test_duplicate_folders_and_conflicting_library_refuse_without_writes(self):
        self.old_install();self.target.mkdir();before=self.tree(self.comfy);self.call(extra=['-Update'],success=False);self.assertEqual(self.tree(self.comfy),before)
        self.target.rmdir();(self.old/'local_library.json').write_text(json.dumps(dict(library=str(self.root/'different.sqlite3'))))
        before=self.tree(self.comfy);self.call(extra=['-Update'],success=False);self.assertEqual(self.tree(self.comfy),before)

    def test_staging_copy_failure_keeps_old_installation(self):
        self.old_install();before=self.tree(self.old)
        # Fail the actual staging copy through PowerShell command resolution,
        # without a test-only switch in the product script.
        wrapper=self.root/'copy-failure.ps1'
        wrapper.write_text("function Copy-Item { throw 'synthetic copy failure' }\n& '"+str(ROOT/'install_comfyui.ps1')+"' -Source '"+str(self.source)+"' -ComfyUIRoot '"+str(self.comfy)+"' -DesktopData '"+str(self.data)+"' -Update",encoding='utf-8')
        result=subprocess.run([SHELL,'-NoProfile','-NonInteractive','-File',str(wrapper)],env=self.env,capture_output=True,timeout=30)
        self.assertNotEqual(result.returncode,0);self.assertEqual(self.tree(self.old),before);self.assertFalse(self.target.exists())

    def test_desktop_full_package_to_new_directory_and_refuses_existing(self):
        source=self.root/'desktop-package';source.mkdir();self.make_package(source,'desktop');destination=self.root/'new-version'
        self.call('update_desktop.ps1',['-Source',source,'-Destination',destination]);self.assertEqual(self.tree(source),self.tree(destination))
        before=self.tree(destination);self.call('update_desktop.ps1',['-Source',source,'-Destination',destination],success=False);self.assertEqual(self.tree(destination),before)

    def test_junction_target_refused_before_any_write(self):
        other=self.root/'redirected';other.mkdir();(other/'marker').write_text('preserve')
        subprocess.run([SHELL,'-NoProfile','-NonInteractive','-Command',"New-Item -ItemType Junction -Path '"+str(self.target)+"' -Target '"+str(other)+"' | Out-Null"],check=True,capture_output=True)
        try:
            before=self.tree(other);self.call(extra=['-Update'],success=False);self.assertEqual(self.tree(other),before)
        finally: os.rmdir(self.target)
