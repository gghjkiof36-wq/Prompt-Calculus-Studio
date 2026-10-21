"""Synthetic Windows junction races; every destination stays inside one fixture."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PySide6.QtGui import QImage

import test_publication_directory_boundary as junctions
import test_workspace_recovery_handoff as helper
from local_document_fixtures import p0
from prompt_calculus_studio.local_documents import assets, category, directory_pin, io
from prompt_calculus_studio import markdown_attachments, workspace_files


@unittest.skipUnless(os.name == 'nt', 'Windows directory boundary')
class WriterBoundaryTests(unittest.TestCase):
    def test_pin_failure_does_not_create_payload_or_parents(self):
        with tempfile.TemporaryDirectory(prefix='pcs-pin-failure-') as directory:
            base = Path(directory)
            source, root = base / 'source', base / 'root'
            source.mkdir(); root.mkdir()
            sample = helper.portable_fixture(source, 'Source')
            document = io.load(source, sample.name).document
            with patch.object(directory_pin, 'pinned_parent', side_effect=OSError('synthetic pin failure')):
                result = io.create(root, 'File.pcs-workflow', document)
                self.assertEqual(result.status, 'failed')
                self.assertEqual(result.recovery_paths, ())
                with self.assertRaises(OSError):workspace_files.create_file(root, 'markdown', 'Empty')
                with self.assertRaises(OSError):
                    with directory_pin.pinned_target(root, 'assets/new/file.png', create_parents=True):pass
            self.assertEqual(list(root.iterdir()), [])

    def test_existing_nested_junction_is_not_used_to_create_asset_parents(self):
        with tempfile.TemporaryDirectory(prefix='pcs-nested-boundary-') as directory:
            base = Path(directory).absolute()
            source, root, outside = (base / name for name in ('source', 'root', 'synthetic-outside'))
            for folder in (source, root, outside):folder.mkdir()
            (source / 'assets').mkdir(); (source / 'assets/p0-swatch.png').write_bytes(p0.png())
            state, ids = p0.fixture(); document = category.from_state(state, ids['category_a'])
            link = root / 'assets'; link.mkdir()
            self.assertTrue(link.is_relative_to(base) and outside.is_relative_to(base))
            junctions.set_junction(link, outside)
            try:
                with self.assertRaises((ValueError, OSError)):assets.prepare(document, source, root)
                self.assertEqual(list(outside.iterdir()), [])
            finally:link.rmdir()

    def test_empty_directory_cannot_be_retagged_while_writing(self):
        with tempfile.TemporaryDirectory(prefix='pcs-empty-boundary-') as directory:
            base = Path(directory).absolute()
            root, outside = base / 'root', base / 'synthetic-outside'
            root.mkdir(); outside.mkdir()
            original_open = Path.open
            attempted = []
            def opening(path, mode='r', *args, **kwargs):
                if mode == 'xb' and path.parent == root:
                    self.assertTrue(root.is_relative_to(base) and outside.is_relative_to(base))
                    with self.assertRaises(OSError) as denied:junctions.set_junction(root, outside)
                    attempted.append(getattr(denied.exception, 'winerror', None))
                return original_open(path, mode, *args, **kwargs)
            with patch.object(Path, 'open', opening):
                result = workspace_files.create_file(root, 'markdown', 'Empty')
            self.assertEqual(len(attempted), 1)
            self.assertEqual(result.read_bytes(), b'')
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse(root.is_junction())

    def test_attachment_cleanup_stays_pinned_on_publication_failure(self):
        with tempfile.TemporaryDirectory(prefix='pcs-cleanup-boundary-') as directory:
            base = Path(directory).absolute(); root = base / 'root'; root.mkdir()
            surface = SimpleNamespace(page=SimpleNamespace(path=root / 'note.md',
                file_title=SimpleNamespace(workspace=SimpleNamespace(root=root))))
            image = QImage(16, 16, QImage.Format.Format_RGB32); image.fill(0xff406080)
            original_unlink = Path.unlink
            denied = []
            def unlink(path, *args, **kwargs):
                if path.name.startswith('.md-prepare-'):
                    parent = path.parent.absolute(); parked = parent.with_name('parked')
                    self.assertTrue(parent.is_relative_to(base) and parked.is_relative_to(base))
                    with self.assertRaises(PermissionError):parent.rename(parked)
                    denied.append(True)
                return original_unlink(path, *args, **kwargs)
            with patch('os.link', side_effect=OSError('synthetic publication failure')), patch.object(Path, 'unlink', unlink):
                with self.assertRaises(OSError):markdown_attachments.prepare(surface, image=image)
            self.assertEqual(denied, [True])
            self.assertEqual(list((root / 'assets').iterdir()), [])

    def test_parent_swap_before_first_write_never_escapes(self):
        observations = []
        for family in ('create', 'save', 'portable-assets', 'markdown-attachment', 'markdown-create'):
            with self.subTest(family=family), tempfile.TemporaryDirectory(prefix='pcs-private-race-') as directory:
                base = Path(directory).absolute()
                root, source, outside = (base / name for name in ('document-root', 'source', 'synthetic-outside'))
                for folder in (root, source, outside):folder.mkdir()
                marker = outside / 'sentinel.txt'; marker.write_bytes(b'SYNTHETIC_UNCHANGED')
                sample = helper.portable_fixture(source, 'Source')
                document = io.load(source, sample.name).document
                if family == 'save':
                    created = io.create(root, 'Document.pcs-workflow', document)
                    self.assertEqual(created.status, 'saved')
                if family == 'portable-assets':
                    (source / 'assets').mkdir()
                    (source / 'assets/p0-swatch.png').write_bytes(p0.png())
                    state, ids = p0.fixture()
                    document = category.from_state(state, ids['category_a'])
                picture = QImage(16, 16, QImage.Format.Format_RGB32); picture.fill(0xff406080)
                surface = SimpleNamespace(page=SimpleNamespace(path=root / 'note.md',
                    file_title=SimpleNamespace(workspace=SimpleNamespace(root=root))))
                original_document = copy.deepcopy(document)
                source_before = sample.read_bytes()
                original_open = Path.open
                attack = dict(attempted=False, denied=False, moved=False, junction=False)
                parked = parent = None
                def opening(path, mode='r', *args, **kwargs):
                    nonlocal parked, parent
                    if mode == 'xb' and not attack['attempted'] and path.absolute().is_relative_to(root):
                        attack['attempted'] = True
                        parent = path.parent.absolute(); parked = parent.with_name(parent.name + '-parked')
                        # Both rename endpoints and the fake outside target are
                        # explicitly confined before any directory operation.
                        for target in (parent, parked, outside):
                            self.assertTrue(target.is_relative_to(base))
                            self.assertNotEqual(target, base)
                        try:
                            parent.rename(parked)
                        except PermissionError:
                            attack['denied'] = True
                        else:
                            attack['moved'] = True
                            parent.mkdir()
                            junctions.set_junction(parent, outside)
                            attack['junction'] = True
                    return original_open(path, mode, *args, **kwargs)
                result, error = None, None
                try:
                    with patch.object(Path, 'open', opening):
                        try:
                            if family == 'create':result = io.create(root, 'Document.pcs-workflow', document)
                            elif family == 'save':result = io.save(root, 'Document.pcs-workflow', document, created.receipt)
                            elif family == 'portable-assets':result = assets.prepare(document, source, root)
                            elif family == 'markdown-attachment':result = markdown_attachments.prepare(surface, image=picture)
                            else:result = workspace_files.create_file(root, 'markdown', 'Empty')
                        except (ValueError, OSError) as exc:error = str(exc)
                    outside_files = sorted(p.name for p in outside.iterdir())
                    observations.append(dict(family=family, attack=attack.copy(), error=error,
                        status=getattr(result, 'status', 'returned' if result is not None else None),
                        outside_files=outside_files,
                        outside_hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in outside.iterdir() if p.is_file()}))
                    if os.environ.get('PCS_REVIEW_OUT'):
                        (Path(os.environ['PCS_REVIEW_OUT']) / 'directory-races.json').write_text(
                            json.dumps(observations, ensure_ascii=False, indent=2), encoding='utf-8')
                    self.assertTrue(attack['attempted'])
                    self.assertEqual(outside_files, ['sentinel.txt'])
                    self.assertEqual(marker.read_bytes(), b'SYNTHETIC_UNCHANGED')
                    self.assertEqual(sample.read_bytes(), source_before)
                    self.assertEqual(document, original_document)
                    self.assertIsNone(error)
                    if family in ('create', 'save'):self.assertEqual(result.status, 'saved', result)
                    self.assertTrue(attack['denied'])
                finally:
                    # rmdir removes only this test's junction, never its target.
                    if attack['moved']:
                        self.assertTrue(parent.absolute().is_relative_to(base))
                        self.assertTrue(parked.absolute().is_relative_to(base))
                        parent.rmdir()
                        parked.rename(parent)
