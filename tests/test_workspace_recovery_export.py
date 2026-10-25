"""Disk-only recovery creates independent files, without opening Qt or jobs."""
import base64
import copy
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from prompt_calculus_studio import materials
from prompt_calculus_studio.draft_cache import DraftCache
from prompt_calculus_studio.local_documents import assets, category, identity, io, schema, workflow
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.workspace_locations import WorkspaceLocations
from prompt_calculus_studio.workspace_recovery import CloseRecovery, key
from prompt_calculus_studio.workspace_recovery_export import RecoveryPublishedError
from test_material_editor import state


class RecoveryExportTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-recovery-export-')
        self.addCleanup(self.area.cleanup)
        self.base = Path(self.area.name)
        self.root, self.data = self.base/'files', self.base/'data'
        self.root.mkdir(); self.data.mkdir()
        self.runtime_root = self.data/'documents'
        self.runtime_root.mkdir()
        self.workspace = SimpleNamespace(data=self.data, show_notice=lambda _: None,
            locations=WorkspaceLocations(self.root, self.data),
            runtimes=SimpleNamespace(directory=self.runtime_root))
        self.recovery = CloseRecovery(self.workspace)

    def markdown(self, original, raw):
        value = dict(kind='md', path=str(original), sha=hashlib.sha256(b'original').hexdigest(),
                     raw=base64.b64encode(raw).decode('ascii'))
        self.recovery.cache.write(key(original), value)
        return value

    def native(self, kind, mapped=False):
        value = state()
        item = value['items'][0]
        item['preview'] = 'assets/test.png'
        canvas = next(iter(value['multi_output']['canvases']))
        materials.add_to_canvas(value, item['id'], canvas)
        source = self.base/'source-assets'
        (source/'assets').mkdir(parents=True, exist_ok=True)
        (source/'assets/test.png').write_bytes(b'synthetic immutable thumbnail')
        runtime = self.runtime_root/('runtime-'+kind)
        runtime.mkdir()
        original = self.root/('Lost'+schema.SUFFIXES[kind])
        initial = (category.from_state(value, item['module']) if kind == 'category'
                   else workflow.from_state(value))
        initial['extensions']['test.keep'] = {'value': 'retained'}
        initial = assets.prepare(initial, source, runtime)
        editable = prepare_state((category if kind == 'category' else workflow).to_state(initial), multi=True)
        if kind == 'category':
            editable['items'][0]['prompt'] = 'cached edit, not the original'
        else:
            materials.canvas_node(editable, next(iter(editable['uses'])))['prompt'] = 'cached edit, not the original'
        editable['draft'] = ''; editable['draft_base'] = 'empty manual base'
        duplicate = identity.duplicate(initial) if mapped else None
        record = dict(kind=kind, path=str(original), sha=hashlib.sha256(schema.encode(initial)).hexdigest(),
                      state=editable, baseline=copy.deepcopy(initial['payload']),
                      runtime_document=initial, document=duplicate.document if duplicate else copy.deepcopy(initial),
                      identity_maps=duplicate.id_maps if duplicate else None,
                      opening_settings=copy.deepcopy(editable['settings']), data=str(runtime))
        self.recovery.cache.write(key(original), record)
        return original, record, runtime

    def test_markdown_uses_fresh_disk_bytes_numbers_names_and_keeps_cache(self):
        original = self.root/'Lost.md'
        raw = b'\xef\xbb\xbf# Exact\r\n\r\n' + '公式 $x^2$\r\n'.encode()
        disk = self.markdown(original, raw)
        self.recovery.memory[key(original)] = dict(disk, raw=base64.b64encode(b'stale').decode())
        (self.root/'Recovered.md').write_bytes(b'competitor')
        (self.root/'recovered(1).MD').write_bytes(b'competitor two')
        target = self.recovery.recover_as(original, 'Recovered', str(self.root))
        self.assertEqual(target, self.root/'Recovered(2).md')
        self.assertEqual(target.read_bytes(), raw)
        self.assertFalse(original.exists())
        self.assertEqual((self.root/'Recovered.md').read_bytes(), b'competitor')
        self.assertEqual(DraftCache(self.data).read(key(original)), disk)
        self.assertEqual(self.recovery.verify_recovered(original, target), hashlib.sha256(raw).hexdigest())

    def test_original_path_never_recreated_even_as_a_numbered_candidate(self):
        original = self.root/'Recovered(1).md'
        self.markdown(original, b'cached')
        with self.assertRaisesRegex(ValueError, '不同名稱'):
            self.recovery.recover_as(original, 'recovered(1).MD', self.root)
        with self.assertRaisesRegex(ValueError, '不同名稱'):
            self.recovery.recover_as(self.root/'nested'/'..'/original.name, original.name, self.root)
        (self.root/'Recovered.md').write_bytes(b'existing')
        target = self.recovery.recover_as(original, 'Recovered', self.root)
        self.assertEqual(target, self.root/'Recovered(2).md')
        self.assertFalse(original.exists())
        original.write_bytes(b'returned original')
        self.recovery.recover_as(original, 'Another', self.root)
        self.assertEqual(original.read_bytes(), b'returned original')

    def test_missing_parent_plain_markdown_can_recover_elsewhere(self):
        original = self.base/'deleted-folder'/'Lost.md'
        raw = b'# Plain\r\n'
        self.markdown(original, raw)
        target = self.recovery.recover_as(original, 'Recovered', self.root)
        self.assertEqual(target.read_bytes(), raw)
        self.assertFalse(original.parent.exists())

    def test_invalid_disk_record_remains_visible_and_is_never_replaced(self):
        original = self.root/'Lost.md'
        self.assertFalse(self.recovery.has_record(original))
        path = self.recovery.cache.roots[0]/self.recovery.cache.name(key(original))
        path.parent.mkdir(parents=True)
        path.write_bytes(b'{broken json')
        self.assertTrue(self.recovery.has_record(original))
        with self.assertRaisesRegex(ValueError, '無法讀取'):
            self.recovery.recover_as(original, 'Recovered', self.root)
        self.assertEqual(path.read_bytes(), b'{broken json')
        good = self.markdown(original, b'cached')
        for bad in (dict(good, path=str(self.root/'Different.md')),
                    dict(good, raw='not base64'), dict(good, kind='workflow')):
            self.recovery.cache.write(key(original), bad)
            with self.assertRaises(ValueError):
                self.recovery.recover_as(original, 'Recovered', self.root)
            self.assertEqual(self.recovery.cache.read(key(original)), bad)
        self.assertFalse((self.root/'Recovered.md').exists())

    def test_native_category_workflow_and_mapped_copies_keep_assets_and_independent_identity(self):
        for kind in ('category', 'workflow'):
            with self.subTest(kind=kind):
                original, record, runtime = self.native(kind, mapped=True)
                before = {str(p): p.read_bytes() for p in runtime.rglob('*') if p.is_file()}
                target = self.recovery.recover_as(original, 'Recovered', self.root)
                loaded = io.load(target.parent, target.name)
                document = loaded.document
                self.assertEqual(loaded.issues, ())
                self.assertNotEqual(document['document_id'], record['document']['document_id'])
                self.assertEqual(document['extensions']['test.keep'], {'value': 'retained'})
                self.assertEqual(document['extensions']['pcs.duplicate_source']['document_id'],
                                 record['document']['document_id'])
                payload = document['payload']['state'] if kind == 'workflow' else document['payload']
                if kind == 'category':
                    self.assertEqual(payload['items'][0]['prompt'], 'cached edit, not the original')
                    self.assertNotEqual(payload['items'][0]['id'], record['state']['items'][0]['id'])
                if kind == 'workflow':
                    use = next(iter(payload['uses']))
                    self.assertEqual(materials.canvas_node(payload, use)['prompt'], 'cached edit, not the original')
                    self.assertNotIn(use, record['state']['uses'])
                    self.assertEqual(payload['draft'], '')
                    self.assertEqual(payload['draft_base'], 'empty manual base')
                self.assertEqual(before, {str(p): p.read_bytes() for p in runtime.rglob('*') if p.is_file()})
                self.assertFalse(original.exists())
                self.assertEqual(DraftCache(self.data).read(key(original)), record)
                self.assertEqual(self.recovery.verify_recovered(original, target), loaded.receipt.sha256)
        self.assertFalse((self.data/'execution-index.sqlite3').exists())

    def test_legacy_native_cache_without_document_envelope_is_supported(self):
        original, record, _ = self.native('category')
        record.pop('document')
        self.recovery.cache.write(key(original), record)
        target = self.recovery.recover_as(original, 'Legacy', self.root)
        self.assertEqual(io.load(target.parent, target.name).document['payload']['items'][0]['prompt'],
                         'cached edit, not the original')

    def test_native_missing_or_unsafe_assets_keep_cache_and_do_not_publish(self):
        original, record, runtime = self.native('category')
        for source in (str(self.base/'outside-runtime'), str(runtime)):
            bad = dict(record, data=source)
            if source == str(runtime):
                (runtime/record['runtime_document']['assets'][0]['path']).unlink()
            self.recovery.cache.write(key(original), bad)
            with self.assertRaises(ValueError):
                self.recovery.recover_as(original, 'Recovered', self.root)
            self.assertEqual(DraftCache(self.data).read(key(original)), bad)
            self.assertFalse((self.root/'Recovered.pcs-category').exists())

    def test_exclusive_race_keeps_competitor_and_tries_next_name(self):
        original = self.root/'Lost.md'
        self.markdown(original, b'recovery')
        actual_link = os.link
        def race(source, destination):
            if Path(destination).name == 'Recovered.md':
                Path(destination).write_bytes(b'race winner')
            return actual_link(source, destination)
        with patch('prompt_calculus_studio.workspace_file_io.os.link', side_effect=race):
            target = self.recovery.recover_as(original, 'Recovered', self.root)
        self.assertEqual(target.name, 'Recovered(1).md')
        self.assertEqual((self.root/'Recovered.md').read_bytes(), b'race winner')

    def test_native_post_publication_failure_reports_path_and_never_retries(self):
        original, record, _ = self.native('category')
        def fail(phase):
            if phase == 'after-create':
                raise FileExistsError('post-publication failure')
        with patch.object(io, '_checkpoint', side_effect=fail), self.assertRaises(RecoveryPublishedError) as caught:
            self.recovery.recover_as(original, 'Recovered', self.root)
        self.assertEqual(caught.exception.published_path, self.root/'Recovered.pcs-category')
        self.assertTrue(caught.exception.published_path.is_file())
        self.assertFalse((self.root/'Recovered(1).pcs-category').exists())
        self.assertEqual(DraftCache(self.data).read(key(original)), record)
        self.assertEqual(self.recovery.verify_recovered(original, caught.exception.published_path),
                         caught.exception.expected_sha)

    def test_verification_rejects_external_replacement_and_newer_recovery_record(self):
        original = self.root/'Lost.md'
        first = self.markdown(original, b'cached recovery')
        target = self.recovery.recover_as(original, 'Recovered', self.root)
        target.write_bytes(b'valid but unrelated markdown')
        with self.assertRaisesRegex(ValueError, '替換或修改'):
            self.recovery.verify_recovered(original, target)
        self.assertEqual(DraftCache(self.data).read(key(original)), first)
        target.write_bytes(b'cached recovery')
        latest = self.markdown(original, b'newer draft from another writer')
        with self.assertRaisesRegex(ValueError, '副本已變更'):
            self.recovery.verify_recovered(original, target)
        self.assertEqual(DraftCache(self.data).read(key(original)), latest)

    def test_native_deleted_after_publication_retains_receipt_without_repeating_create(self):
        original, record, _ = self.native('category')
        target = self.root/'Recovered.pcs-category'
        def disappear(phase):
            if phase == 'after-create':
                target.unlink()
        with patch.object(io, '_checkpoint', side_effect=disappear), \
             patch.object(io, 'create', wraps=io.create) as create, \
             self.assertRaises(RecoveryPublishedError) as caught:
            self.recovery.recover_as(original, 'Recovered', self.root)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(caught.exception.published_path, target)
        self.assertTrue(caught.exception.expected_sha)
        self.assertFalse(target.exists())
        self.assertFalse((self.root/'Recovered(1).pcs-category').exists())
        self.assertEqual(DraftCache(self.data).read(key(original)), record)
        with self.assertRaises((OSError, ValueError)):
            self.recovery.verify_recovered(original, target)

    def test_markdown_readback_failure_reports_published_path_and_retains_cache(self):
        from prompt_calculus_studio.local_documents import paths
        original = self.root/'Lost.md'
        record = self.markdown(original, b'cached recovery')
        target = self.root/'Recovered.md'
        actual_read = paths.read_bytes
        def read(path, *args):
            if path == target:
                target.write_bytes(b'externally replaced')
            return actual_read(path, *args)
        with patch.object(paths, 'read_bytes', side_effect=read), self.assertRaises(RecoveryPublishedError) as caught:
            self.recovery.recover_as(original, 'Recovered', self.root)
        self.assertEqual(caught.exception.published_path, target)
        self.assertEqual(caught.exception.expected_sha, hashlib.sha256(b'cached recovery').hexdigest())
        with self.assertRaises(ValueError):
            self.recovery.verify_recovered(original, target)
        self.assertEqual(DraftCache(self.data).read(key(original)), record)

    def test_markdown_local_image_reference_tracks_new_folder_without_rewriting_text(self):
        original = self.root/'Lost.md'
        (self.root/'picture.png').write_bytes(b'local image bytes')
        folder = self.root/'New folder'
        folder.mkdir()
        raw = b'\xef\xbb\xbf![local](picture.png)\r\n\r\n$\\int_0^1 x^2 dx$\r\n'
        self.markdown(original, raw)
        target = self.recovery.recover_as(original, 'Recovered', folder)
        self.assertEqual(target.read_bytes(), raw.replace(b'(picture.png)', b'(../picture.png)'))
        self.assertEqual((self.root/'picture.png').read_bytes(), b'local image bytes')


if __name__ == '__main__':
    unittest.main()
