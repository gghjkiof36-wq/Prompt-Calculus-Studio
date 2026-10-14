"""Document compatibility is independent of the installed ComfyUI release."""
import hashlib,json,sqlite3,sys,types,unittest
from contextlib import closing
from unittest.mock import patch
from pathlib import Path
import test_comfy_integration as integration
from prompt_calculus_studio.core import SUPPORTED_STATE_VERSIONS,Storage,initial_state
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.snapshots import make_snapshot,restore_snapshot
from prompt_calculus_studio.releases import COMFYUI_COMPATIBILITY_PROFILES
from integration_test.compatibility import runtime_versions


class ComfyCompatibilityTests(unittest.TestCase):
    setUp=integration.IntegrationTests.setUp

    def test_both_profiles_keep_version_six_and_old_documents_readable_without_rewriting(self):
        for profile in COMFYUI_COMPATIBILITY_PROFILES:
            for state in (initial_state(),prepare_state(initial_state(),multi=True)):
                state['draft']='';store=Storage(self.root/'library');store.save(state);store.db.close()
                path=self.root/'library'/'studio.sqlite3';before=hashlib.sha256(path.read_bytes()).digest()
                result=self.service.read_library(path)
                self.assertIn(result['state']['version'],SUPPORTED_STATE_VERSIONS)
                self.assertEqual(result['state']['draft'],'')
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),before,profile)
                snapshot=make_snapshot(state);restored=restore_snapshot(state,snapshot)
                self.assertEqual(restored['version'],state['version'])

    def test_future_document_is_rejected_with_actionable_extension_message_and_unchanged_bytes(self):
        path=self.root/'library'/'studio.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE document (id INTEGER PRIMARY KEY,body TEXT)')
            db.execute('INSERT INTO document VALUES (1,?)',(json.dumps(dict(version=99)),))
            db.commit()
        before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'PCS 擴充.*99.*更新配套擴充'):
            self.service.read_library(path)
        self.assertEqual(path.read_bytes(),before)

    def test_backend_frontend_and_pcs_document_version_are_separate(self):
        for profile in COMFYUI_COMPATIBILITY_PROFILES.values():
            module=types.ModuleType('comfyui_version');module.__version__=profile['backend']
            runtime_versions.cache_clear()
            with patch.dict(sys.modules,comfyui_version=module),patch('integration_test.compatibility.version',return_value=profile['frontend']):
                self.assertEqual(runtime_versions(),dict(backend=profile['backend'],frontend=profile['frontend']))
        runtime_versions.cache_clear()


if __name__=='__main__':unittest.main()
