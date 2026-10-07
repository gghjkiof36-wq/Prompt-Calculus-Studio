"""Synthetic Git tree: both document copies and source ZIP reject local files."""
import json,os,subprocess,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import package_documents as package
from prompt_calculus_studio.releases import SOURCE_ARCHIVE


class SourcePrivacyTests(unittest.TestCase):
    def test_tracked_sources_and_untracked_local_data_have_separate_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source';root.mkdir();destination=Path(directory)/'package';destination.mkdir()
            legitimate=['run.py','docs/README.md','comfyui_prompt_calculus_studio/service.py','comfyui_prompt_calculus_studio/web/style.css','tests/fixture.json','docs/images/canvas-public.png',
                        'docs/guide/getting-started.md','docs/releases/README.md','docs/releases/0.8.6/validation/alpha.1.md',
                        'docs/development/architecture.md','docs/archive/0.7.1/getting-started.txt']
            private=['comfyui_prompt_calculus_studio/local_library.json','comfyui_prompt_calculus_studio/credentials/key.json',
                     'comfyui_prompt_calculus_studio/fault.log','docs/studio.sqlite3','docs/error.log',
                     'AGENTS.md','tests/AGENTS.md','comfyui_prompt_calculus_studio/AGENTS.md',
                     'docs/AGENTS.md','prompt_calculus_studio/AGENTS.md','docs/agent-guides/build-release.md']
            for name in legitimate+private:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic',encoding='utf-8')
            git=['git','-c','safe.directory='+root.as_posix(),'-C',str(root)]
            subprocess.run([*git,'init','-q'],check=True,stdout=subprocess.DEVNULL)
            subprocess.run([*git,'add','.'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            (root/'docs/personal-notes.md').write_text('synthetic private note',encoding='utf-8')
            with patch.object(package,'ROOT',root),patch.dict(os.environ,{'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'safe.directory','GIT_CONFIG_VALUE_0':root.as_posix()}):
                package.bundle_documents(destination)
            with zipfile.ZipFile(destination/SOURCE_ARCHIVE) as archive:
                self.assertEqual(set(archive.namelist()),set(legitimate))
            manifest=json.loads((destination/'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
            self.assertEqual({m['path'] for m in manifest},set(legitimate))
            self.assertTrue((destination/'docs/README.md').exists())
            self.assertFalse((destination/'docs/personal-notes.md').exists())
            self.assertFalse((destination/'docs/error.log').exists())
            self.assertFalse((destination/'AGENTS.md').exists())
            self.assertFalse((destination/'docs/AGENTS.md').exists())
            self.assertFalse((destination/'docs/agent-guides').exists())

    def test_unknown_source_root_fails_before_writing_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source';root.mkdir();destination=Path(directory)/'package';destination.mkdir()
            (root/'personal.md').write_text('synthetic',encoding='utf-8')
            with patch.object(package,'ROOT',root),self.assertRaises(ValueError):package.bundle_documents(destination)
            self.assertEqual(list(destination.iterdir()),[])
