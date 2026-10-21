"""Disk recovery accepts ordinary documents without rewinding a newer runtime."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from prompt_calculus_studio.core import Storage
from prompt_calculus_studio.document_host import DesktopDocument
from prompt_calculus_studio.local_documents import category, workflow, schema
from prompt_calculus_studio.workspace_recovery import CloseRecovery, key
from prompt_calculus_studio.workspace_runtime import WorkspaceRuntime
from test_material_editor import state


class RecoveryContractTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.root = Path(self.area.name)
        self.notices = []
        self.workspace = SimpleNamespace(data=self.root/'data', show_notice=self.notices.append)
        self.workspace.data.mkdir()
        self.workspace.runtimes = WorkspaceRuntime(self.workspace)
        self.documents = []

    def tearDown(self):
        self.workspace.runtimes.close()
        for document in self.documents:
            document.refresh_timer.stop()
        self.area.cleanup()

    def make_file(self, kind):
        value = state()
        owner = value['items'][0]['module']
        value['library_categories'] = [owner]
        portable = category.from_state(value, owner) if kind == 'category' else workflow.from_state(value)
        path = self.root/('ordinary.pcs-'+kind)
        path.write_bytes(schema.encode(portable))
        return path

    def load(self, path, runtime=False):
        document = DesktopDocument(path, self.workspace.runtimes.directory)
        self.documents.append(document)
        if runtime:document.runtimes = self.workspace.runtimes
        document.prepare()
        document.baseline = copy.deepcopy(document.document['payload'])
        document.opening_settings = copy.deepcopy(document.state['settings'])
        return document

    def preserve(self, document):
        writer = CloseRecovery(self.workspace)
        location = writer.preserve(SimpleNamespace(document=document, path=document.path, title=document.title))
        self.assertIsNotNone(location)
        return writer, Path(location)

    def test_fresh_reader_accepts_none_and_mapping_without_changing_original(self):
        for kind in ('category', 'workflow'):
            for maps in (None, {'items': {'source': 'copy'}}):
                with self.subTest(kind=kind, maps=maps):
                    path = self.make_file(kind)
                    before = path.read_bytes()
                    document = self.load(path)
                    document.identity_maps = copy.deepcopy(maps)
                    document.state['items'][0]['prompt'] = 'recovered edit'
                    document.state['draft'] = ''
                    document.state['draft_base'] = 'original draft base'
                    expected = copy.deepcopy(document.state)
                    _, location = self.preserve(document)
                    cached = location.read_bytes()
                    reader = CloseRecovery(self.workspace)
                    restored = self.load(path)
                    reader.restore_document(restored)
                    self.assertEqual(restored.state, expected)
                    self.assertEqual(restored.identity_maps, maps)
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(location.read_bytes(), cached)

    def test_invalid_mapping_and_changed_source_keep_cache_and_loaded_state(self):
        path = self.make_file('category')
        document = self.load(path)
        document.state['items'][0]['prompt'] = 'recovery edit'
        writer, location = self.preserve(document)
        original = writer.cache.read(key(path))
        for change in ({'identity_maps': []}, {'sha': 'changed-source'}, {'state': {'version': 999}}):
            with self.subTest(change=change):
                writer.cache.write(key(path), {**original, **change})
                cached = location.read_bytes()
                restored = self.load(path)
                before = copy.deepcopy(restored.state)
                CloseRecovery(self.workspace).restore_document(restored)
                self.assertEqual(restored.state, before)
                self.assertEqual(location.read_bytes(), cached)

    def test_runtime_checkpoint_restores_failed_save_but_never_rewinds_newer_state(self):
        for kind in ('category', 'workflow'):
            with self.subTest(kind=kind):
                path = self.make_file(kind)
                document = self.load(path, runtime=True)
                store = Storage(document.data)
                store.save(document.state)
                self.workspace.runtimes.remember(document)
                document.host = SimpleNamespace(store=store)
                document.state['items'][0]['prompt'] = 'unsaved cache edit'
                _, location = self.preserve(document)
                store.close()
                cached = location.read_bytes()
                restored = self.load(path, runtime=True)
                CloseRecovery(self.workspace).restore_document(restored)
                self.assertEqual(restored.state['items'][0]['prompt'], 'unsaved cache edit')
                store = Storage(restored.data)
                restored.state['items'][0]['prompt'] = 'newer runtime edit'
                store.save(restored.state)
                store.close()
                newest = self.load(path, runtime=True)
                CloseRecovery(self.workspace).restore_document(newest)
                self.assertEqual(newest.state['items'][0]['prompt'], 'newer runtime edit')
                self.assertEqual(location.read_bytes(), cached)

    def test_legacy_cache_with_runtime_conflict_is_retained_without_guessing_order(self):
        path = self.make_file('category')
        document = self.load(path, runtime=True)
        store = Storage(document.data)
        store.save(document.state)
        self.workspace.runtimes.remember(document)
        document.state['items'][0]['prompt'] = 'legacy cache edit'
        writer, location = self.preserve(document)
        value = writer.cache.read(key(path))
        value.pop('runtime_base_sha256', None)
        writer.cache.write(key(path), value)
        document.state['items'][0]['prompt'] = 'newer runtime edit'
        store.save(document.state)
        store.close()
        cached = location.read_bytes()
        restored = self.load(path, runtime=True)
        CloseRecovery(self.workspace).restore_document(restored)
        self.assertEqual(restored.state['items'][0]['prompt'], 'newer runtime edit')
        self.assertEqual(location.read_bytes(), cached)
