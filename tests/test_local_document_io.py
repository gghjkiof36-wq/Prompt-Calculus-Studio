import copy
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from local_document_fixtures import DocumentsCase, ROOT
from prompt_calculus_studio.local_documents import io, schema, windows


@unittest.skipUnless(os.name == 'nt', 'Windows native replacement contract')
class SaveTests(DocumentsCase):
    def edit(self, result, text='candidate'):
        document = copy.deepcopy(result.document)
        document['payload']['items'][0]['notes'] = text
        return document

    def test_real_replace_new_revision_and_new_receipt(self):
        saved = self.saved()
        candidate = self.edit(saved)
        before = copy.deepcopy(candidate)
        result = io.save(self.destination, '分類.pcs-category', candidate, saved.receipt)
        self.assertEqual(result.status, 'saved', result)
        self.assertFalse(result.dirty)
        self.assertEqual(result.document['revision']['parent'], saved.receipt.revision)
        self.assertNotEqual(result.receipt.revision, saved.receipt.revision)
        self.assertEqual(candidate, before)
        loaded = io.load(self.destination, '分類.pcs-category')
        self.assertEqual(loaded.document, result.document)
        self.assertTrue(any(Path(p).suffix == '.external' for p in result.recovery_paths))

    def test_external_modification_before_save_keeps_original_path_and_dirty(self):
        saved = self.saved()
        path = self.destination / '分類.pcs-category'
        external = self.edit(saved, 'external')
        raw = schema.encode(external)
        path.write_bytes(raw)
        candidate = self.edit(saved)
        result = io.save(self.destination, path.name, candidate, saved.receipt)
        self.assertEqual(result.status, 'conflict', result)
        self.assertTrue(result.dirty)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(candidate['payload']['items'][0]['notes'], 'candidate')

    def test_revision_or_path_mismatch_does_not_write(self):
        saved = self.saved()
        candidate = self.edit(saved)
        candidate['revision']['id'] = '74c43193-418a-4dfe-bb0c-3d9134045a5d'
        before = list(self.destination.iterdir())
        result = io.save(self.destination, '分類.pcs-category', candidate, saved.receipt)
        self.assertEqual(result.status, 'conflict')
        self.assertEqual(list(self.destination.iterdir()), before)

    def test_write_flush_and_pre_replace_failures_leave_exact_original(self):
        saved = self.saved()
        path = self.destination / '分類.pcs-category'
        raw = path.read_bytes()
        candidate = self.edit(saved)
        for phase in ('temporary-written', 'temporary-flushed', 'before-replace'):
            def fail_at(current):
                if current == phase:
                    raise OSError('injected ' + phase)
            with self.subTest(phase=phase), patch.object(io, '_checkpoint', side_effect=fail_at):
                result = io.save(self.destination, path.name, candidate, saved.receipt)
            self.assertEqual(result.status, 'failed', result)
            self.assertTrue(result.dirty)
            self.assertEqual(path.read_bytes(), raw)
        with patch.object(io.os, 'fsync', side_effect=OSError('simulated full disk')):
            result = io.save(self.destination, path.name, candidate, saved.receipt)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(path.read_bytes(), raw)

    def test_native_replace_failure_keeps_original_and_candidate_recoverable(self):
        saved = self.saved()
        path = self.destination / '分類.pcs-category'
        raw = path.read_bytes()
        with patch.object(windows, 'replace', side_effect=PermissionError('locked')):
            result = io.save(self.destination, path.name, self.edit(saved), saved.receipt)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(path.read_bytes(), raw)
        candidate = next(Path(p) for p in result.recovery_paths if p.endswith('.candidate'))
        self.assertEqual(schema.decode(candidate.read_bytes())['payload']['items'][0]['notes'], 'candidate')

    def test_native_open_writer_blocks_without_modifying_original(self):
        saved = self.saved()
        path = self.destination / '分類.pcs-category'
        raw = path.read_bytes()
        with path.open('ab'):
            result = io.save(self.destination, path.name, self.edit(saved), saved.receipt)
        self.assertEqual(result.status, 'failed', result)
        self.assertEqual(path.read_bytes(), raw)

    def test_replace_time_race_preserves_external_and_candidate_reports_conflict(self):
        saved = self.saved()
        path = self.destination / '分類.pcs-category'
        external = schema.encode(self.edit(saved, 'external at replacement'))
        external_path = self.destination / 'external.tmp'
        external_path.write_bytes(external)
        def race(phase):
            if phase == 'before-replace':
                # MoveFileEx replacement is denied by an open Windows reader;
                # another editor using ReplaceFile can replace the pinned name.
                windows.replace(path, external_path, self.destination / 'competitor.previous')
        candidate = self.edit(saved)
        with patch.object(io, '_checkpoint', side_effect=race):
            result = io.save(self.destination, path.name, candidate, saved.receipt)
        self.assertEqual(result.status, 'conflict', result)
        self.assertTrue(result.dirty)
        recovered = {Path(p).suffix: Path(p).read_bytes() for p in result.recovery_paths}
        self.assertEqual(recovered['.external'], external)
        self.assertEqual(schema.decode(recovered['.candidate'])['payload']['items'][0]['notes'], 'candidate')
        self.assertEqual(candidate['revision'], saved.document['revision'])
        self.assertTrue(io.recovery_files(self.destination, path.name))

    def test_post_replace_failure_is_pending_recovery_not_success(self):
        saved = self.saved()
        def fail(phase):
            if phase == 'after-replace':
                raise OSError('injected after replacement')
        with patch.object(io, '_checkpoint', side_effect=fail):
            result = io.save(self.destination, '分類.pcs-category', self.edit(saved), saved.receipt)
        self.assertEqual(result.status, 'conflict')
        recovered = {Path(p).suffix: Path(p).read_bytes() for p in result.recovery_paths}
        self.assertEqual(schema.decode(recovered['.external']), saved.document)
        self.assertEqual(schema.decode(recovered['.candidate'])['payload']['items'][0]['notes'], 'candidate')

    def test_partial_native_rename_error_preserves_both_versions_without_retry(self):
        saved = self.saved()
        def partial(destination, temporary, backup):
            os.replace(destination, backup)
            raise OSError(1177, 'simulated partial ReplaceFile failure')
        with patch.object(windows, 'replace', side_effect=partial) as replacement:
            result = io.save(self.destination, '分類.pcs-category', self.edit(saved), saved.receipt)
        self.assertEqual(replacement.call_count, 1)
        self.assertEqual(result.status, 'conflict')
        recovered = {Path(p).suffix: Path(p).read_bytes() for p in result.recovery_paths}
        self.assertEqual(schema.decode(recovered['.external']), saved.document)
        self.assertEqual(schema.decode(recovered['.candidate'])['payload']['items'][0]['notes'], 'candidate')

    def test_process_exit_before_and_after_replace_keeps_recoverable_bytes(self):
        saved = self.saved()
        script = '''
import os, sys
from prompt_calculus_studio.local_documents import io
loaded = io.load(sys.argv[1], sys.argv[2])
candidate = loaded.document
candidate['payload']['items'][0]['notes'] = 'crash candidate'
def stop(phase):
    if phase == sys.argv[3]: os._exit(83)
io._checkpoint = stop
io.save(sys.argv[1], sys.argv[2], candidate, loaded.receipt)
'''
        for phase in ('before-replace', 'after-replace'):
            name = phase + '.pcs-category'
            result = io.create(self.destination, name, saved.document)
            self.assertEqual(result.status, 'saved')
            process = subprocess.run([sys.executable, '-B', '-c', script, str(self.destination), name, phase],
                                     cwd=ROOT, capture_output=True, timeout=30)
            self.assertEqual(process.returncode, 83, process.stderr)
            current = io.load(self.destination, name).document
            self.assertEqual(current['payload']['items'][0]['notes'],
                             saved.document['payload']['items'][0]['notes'] if phase == 'before-replace' else 'crash candidate')
            files = io.recovery_files(self.destination, name)
            candidates = [schema.decode(p.read_bytes()) for p in files if p.suffix == '.candidate']
            self.assertTrue(any(d['payload']['items'][0]['notes'] == 'crash candidate' for d in candidates))
            before = [schema.decode(p.read_bytes()) for p in files if p.suffix == '.before']
            self.assertIn(saved.document, before)


class CreateTests(DocumentsCase):
    def test_create_is_exclusive_and_invalid_or_cancelled_preparation_writes_nothing(self):
        saved = self.saved()
        path = self.destination / '分類.pcs-category'
        raw = path.read_bytes()
        result = io.create(self.destination, path.name, saved.document)
        self.assertEqual(result.status, 'conflict')
        self.assertEqual(path.read_bytes(), raw)
        document = self.category()
        document['file_schema_version'] = 99
        before = set(self.destination.iterdir())
        result = io.create(self.destination, 'invalid.pcs-category', document)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(set(self.destination.iterdir()), before)

    def test_create_race_does_not_overwrite_competing_file(self):
        document = self.portable(self.category())
        path = self.destination / 'race.pcs-category'
        def race(phase):
            if phase == 'before-create':
                path.write_bytes(b'external winner')
        with patch.object(io, '_checkpoint', side_effect=race):
            result = io.create(self.destination, path.name, document)
        self.assertEqual(result.status, 'conflict')
        self.assertEqual(path.read_bytes(), b'external winner')
        self.assertTrue(result.dirty)

    def test_read_rejects_wrong_extension_and_create_missing_parent(self):
        result = io.create(self.destination, 'wrong.pcs-workflow', self.portable(self.category()))
        self.assertEqual(result.status, 'failed')
        result = io.create(self.destination, 'missing/file.pcs-category', self.category())
        self.assertEqual(result.status, 'failed')
        self.assertFalse((self.destination / 'missing').exists())
