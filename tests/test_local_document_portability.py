import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
from unittest.mock import patch

from local_document_fixtures import DocumentsCase, p0
from prompt_calculus_studio import composition_image, core
from prompt_calculus_studio.local_documents import assets, index, io, schema, paths, workflow


class PortabilityTests(DocumentsCase):
    def test_full_category_and_workflow_move_without_original_root_db_or_index(self):
        category = self.portable(self.category())
        workflow_doc = self.portable(self.workflow())
        for name, doc in [('場景.pcs-category', category), ('街景.pcs-workflow', workflow_doc)]:
            self.assertEqual(io.create(self.destination, name, doc).status, 'saved')
        (self.destination / '筆記.md').write_bytes(b'\xef\xbb\xbf' + '# 中文\r\n'.encode('utf-8'))
        (self.destination / '空白.md').write_bytes(b'')
        snapshot = index.rebuild(self.destination)
        cache = index.write_cache(self.destination, snapshot)
        cache.unlink()
        moved = self.root / 'another root'
        self.destination.rename(moved)
        assert self.source.resolve().is_relative_to(self.root.resolve()) and self.source.name == 'synthetic source'
        shutil.rmtree(self.source)  # only this test's explicitly created synthetic source
        with patch.object(core, 'Storage', side_effect=AssertionError('Original DB cannot be opened')):
            for name, expected in [('場景.pcs-category', category), ('街景.pcs-workflow', workflow_doc)]:
                loaded = io.load(moved, name)
                self.assertEqual(loaded.document, expected)
                self.assertEqual(loaded.issues, ())
                for reference, _, _ in assets.references(loaded.document):
                    self.assertEqual(assets.resolve(loaded.document, moved, reference).read_bytes(), p0.png())
            rebuilt = index.rebuild(moved)
        self.assertEqual(len(rebuilt['entries']), 4)
        self.assertEqual(rebuilt['errors'], [])
        self.assertEqual((moved / '空白.md').read_bytes(), b'')
        self.assertEqual((moved / '筆記.md').read_bytes(), b'\xef\xbb\xbf' + '# 中文\r\n'.encode('utf-8'))

    def test_generation_canvas_layer_and_preview_resolve_via_manifest(self):
        raw = p0.png()
        relative = 'originals/generation/synthetic.png'
        file = self.source / relative
        file.parent.mkdir(parents=True)
        file.write_bytes(raw)
        source = dict(relative=relative, sha256=hashlib.sha256(raw).hexdigest(), width=1, height=1, name='synthetic.png')
        self.state['generation']['source'] = copy.deepcopy(source)
        self.state['canvas_functions']['images']['__source_synthetic'] = dict(source=copy.deepcopy(source), attached=False)
        canvas = self.state['multi_output']['canvases'][self.ids['canvas_a']]
        canvas['image'] = composition_image.document(64, 64)
        canvas['image']['layers'].append(composition_image.layer('image', source=copy.deepcopy(source)))
        document = self.workflow()
        portable = self.portable(document)
        self.assertEqual(portable['payload'], document['payload'])
        self.assertEqual(assets.inspect(portable, self.destination), [])
        self.assertEqual(assets.resolve(portable, self.destination, relative).read_bytes(), raw)
        # Shared validators still require the legacy logical path.
        core.validate_state(workflow.to_state(portable))

    def test_missing_modified_or_manifest_unlisted_asset_is_explicit(self):
        doc = self.category()
        self.assertTrue(assets.inspect(doc, self.source))
        portable = self.portable(doc)
        asset = self.destination / portable['assets'][0]['path']
        asset.write_bytes(b'changed')
        self.assertTrue(any(i['code'] == 'asset-hash' for i in assets.inspect(portable, self.destination)))
        asset.unlink()
        self.assertTrue(assets.inspect(portable, self.destination))
        self.assertEqual(io.create(self.destination, 'invalid.pcs-category', portable).status, 'failed')
        self.assertFalse((self.destination / 'invalid.pcs-category').exists())

    def test_attachment_budget_or_flush_failure_publishes_no_partial_asset(self):
        document = self.category()
        with patch.object(assets, 'MAX_TOTAL_BYTES', 1), self.assertRaises(schema.DocumentError):
            self.portable(document)
        self.assertEqual(list(self.destination.iterdir()), [])
        with patch.object(assets.os, 'fsync', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.portable(document)
        self.assertEqual([p for p in self.destination.rglob('*') if p.is_file()], [])

    def test_absolute_and_escaping_resources_are_not_opened(self):
        for value in ('../outside.png', 'C:/PRIVATE_SENTINEL/picture.png', '/outside.png', 'x\\y.png'):
            doc = self.category()
            doc['payload']['items'][0]['preview'] = value
            with self.subTest(value=value), self.assertRaises(schema.DocumentError), \
                    patch.object(assets, 'read_bytes', side_effect=AssertionError('must not open')):
                assets.prepare(doc, self.source, self.destination)

    def test_unknown_absolute_graph_resource_reports_exact_location(self):
        document = self.workflow()
        document['payload']['state']['generation']['profiles'][0]['graph']['5']['inputs']['filename'] = 'C:/PRIVATE_SENTINEL/image.png'
        issues = assets.unsupported_paths(document)
        self.assertEqual(len(issues), 1)
        self.assertIn('/graph/5/inputs/filename', issues[0]['location'])
        with self.assertRaises(schema.DocumentError):
            self.portable(document)

    def test_windows_name_and_case_collision_boundaries(self):
        for name in ('../bad.pcs-category', 'CON.pcs-category', 'a./x', 'a /x', 'LPT1.txt',
                     'AUX', 'COM¹.txt', 'a:b', 'x\\y', '/absolute', '.obsidian/a', 'folder//file', 'a\x00b'):
            with self.subTest(name=name), self.assertRaises(schema.DocumentError):
                paths.checked(self.destination, name)
        (self.destination / 'CASE.pcs-category').write_bytes(b'existing')
        result = io.create(self.destination, 'case.pcs-category', self.portable(self.category()))
        self.assertEqual(result.status, 'conflict')
        self.assertEqual((self.destination / 'CASE.pcs-category').read_bytes(), b'existing')

    def test_index_inventory_is_lazy_and_obsidian_untouched(self):
        marker = self.destination / '.obsidian'
        marker.mkdir()
        (marker / 'settings.md').write_text('do not read', encoding='utf-8')
        hidden = (marker / 'settings.md').read_bytes()
        with self.assertRaises(schema.DocumentError):
            paths.checked(marker, 'new.md')
        (self.destination / 'bad.pcs-category').write_bytes(b'not-json')
        with patch.object(index, 'load', side_effect=AssertionError('lazy inventory must not parse')):
            entries = list(index.scan(self.destination))
        self.assertEqual([e.relative for e in entries], ['bad.pcs-category'])
        self.assertEqual(index.rebuild(self.destination)['errors'][0]['code'], 'invalid')
        self.assertEqual((marker / 'settings.md').read_bytes(), hidden)

    def test_index_collision_is_reported_without_overwriting_files(self):
        result = self.saved()
        original = self.destination / '分類.pcs-category'
        (self.destination / 'copy.pcs-category').write_bytes(original.read_bytes())
        snapshot = index.rebuild(self.destination)
        self.assertEqual(len(snapshot['errors']), 1)
        self.assertEqual(snapshot['errors'][0]['code'], 'duplicate-id')
        cache = index.write_cache(self.destination, snapshot)
        self.assertNotIn('payload', cache.read_text('utf-8'))
        self.assertEqual(io.load(self.destination, original.name).document, result.document)

    def test_thousand_materials_still_one_index_entry(self):
        document = self.category()
        template = copy.deepcopy(document['payload']['items'][0])
        template.pop('composition')
        template.pop('preview', None)
        document['payload']['items'] = [dict(template, id=f'synthetic-{n}', name=f'Material {n}') for n in range(1000)]
        self.assertEqual(io.create(self.destination, 'many.pcs-category', document).status, 'saved')
        rebuilt = index.rebuild(self.destination)
        self.assertEqual(len(rebuilt['entries']), 1)
        self.assertEqual(rebuilt['errors'], [])
        self.assertEqual(len(io.load(self.destination, 'many.pcs-category').document['payload']['items']), 1000)

    def test_symlink_or_junction_escape_and_cycle_not_followed(self):
        link = self.destination / 'loop'
        if os.name == 'nt':
            import subprocess
            # Junction creation only; cleanup removes this exact junction, not its target.
            process = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.destination)], capture_output=True)
            self.assertEqual(process.returncode, 0, process.stdout)
            self.addCleanup(lambda: os.rmdir(link) if os.path.lexists(link) else None)
        else:
            link.symlink_to(self.destination, target_is_directory=True)
        with self.assertRaises(schema.DocumentError):
            paths.checked(self.destination, 'loop/file.pcs-category')
        self.assertEqual(list(index.scan(self.destination)), [])
