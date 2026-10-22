"""R21: streamed backups preserve originals and expose uncertain publication."""
import hashlib
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch
import zipfile

from prompt_calculus_studio import backup
from prompt_calculus_studio.local_documents import windows


NATIVE_REPLACE = windows.replace
NATIVE_RENAME = os.rename
ZIP_WRITE = zipfile.ZipFile.write


def zip_bytes(payload):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(zipfile.ZipInfo('previous.txt'), payload)
    return stream.getvalue()


@unittest.skipUnless(os.name == 'nt', 'Windows directory pin and native replacement')
class BackupAtomicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.output = self.root / 'backups'
        self.output.mkdir()
        self.destination = self.output / 'backup.zip'
        self.snapshot = self.root / 'snapshot.sqlite3'
        connection = sqlite3.connect(self.snapshot)
        try:
            connection.execute('CREATE TABLE sample (value TEXT NOT NULL)')
            connection.execute('INSERT INTO sample VALUES (?)', ('R21 synthetic snapshot',))
            connection.commit()
        finally:
            connection.close()
        self.snapshot_bytes = self.snapshot.read_bytes()
        self.cancel = threading.Event()

    def tearDown(self):
        self.tmp.cleanup()

    def run_backup(self, destination=None, snapshot=None):
        return backup.archive_data(self.data, snapshot or self.snapshot,
                                   destination or self.destination, self.cancel)

    def old_backup(self, destination=None, payload=b'previous healthy backup'):
        destination = destination or self.destination
        raw = zip_bytes(payload)
        destination.write_bytes(raw)
        return raw

    def assert_bytes(self, path, expected):
        actual = path.read_bytes()
        self.assertEqual(hashlib.sha256(actual).digest(), hashlib.sha256(expected).digest())
        self.assertEqual(actual, expected)

    def assert_archive(self, path, additional=None):
        expected = {'data/studio.sqlite3': self.snapshot_bytes}
        expected.update(additional or {})
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(set(archive.namelist()), set(expected))
            self.assertEqual(len(archive.infolist()), len(expected))
            self.assertIsNone(archive.testzip())
            for info in archive.infolist():
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(archive.read(info.filename), expected[info.filename])
        self.assert_bytes(self.snapshot, self.snapshot_bytes)

    def assert_no_recovery(self):
        self.assertEqual(list(self.root.rglob('.pcs-backup-*')), [])

    def retained(self, error, suffixes):
        files = list(self.output.glob('.pcs-backup-*'))
        self.assertEqual(len(files), len(suffixes))
        self.assertEqual({path.suffix for path in files}, set(suffixes))
        self.assertEqual(len({path.stem for path in files}), 1)
        message = str(error)
        self.assertIn('備份發布未能確認完成', message)
        self.assertIn('保留的恢復檔', message)
        for path in files:
            self.assertIn(str(path), message)
        return {path.suffix: path for path in files}

    def foreign_replace(self, destination, raw):
        candidate = destination.with_name('other-writer-' + uuid.uuid4().hex + '.zip')
        candidate.write_bytes(raw)
        previous = candidate.with_suffix('.previous')
        NATIVE_REPLACE(destination, candidate, previous)
        return previous

    def test_new_backup_keeps_stored_format_and_original_inclusion_scope(self):
        additional = {
            'data/thumbnails/tiny.jpg': b'synthetic thumbnail',
            'data/originals/nested/image.png': b'synthetic owned original',
            'data/civitai/downloads/receipt.json': b'{"id": 21}',
        }
        for name, raw in additional.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        excluded = {
            self.data / 'credentials.json': b'fake credential, not a real token',
            self.data / 'models' / 'model.safetensors': b'not a model',
            self.data / 'civitai' / 'credentials.json': b'fake private setting',
            self.root / 'external-original.png': b'external original',
        }
        for path, raw in excluded.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        result = self.run_backup()
        self.assertIsInstance(result, str)
        self.assertEqual(result, str(self.destination))
        self.assertEqual(result.recovery_paths, ())
        self.assert_archive(self.destination, additional)
        for name, raw in additional.items():
            self.assert_bytes(self.root / name, raw)
        for path, raw in excluded.items():
            self.assert_bytes(path, raw)
        self.assert_no_recovery()

    def test_existing_backup_uses_real_replace_and_blocks_in_place_write(self):
        old = self.old_backup()
        denied = []

        def replace(destination, temporary, displaced):
            try:
                destination.write_bytes(b'in-place writer must not truncate the original')
            except PermissionError:
                denied.append(True)
            self.assert_bytes(destination, old)
            NATIVE_REPLACE(destination, temporary, displaced)

        with patch.object(windows, 'replace', side_effect=replace) as publication:
            result = self.run_backup()
        self.assertEqual(publication.call_count, 1)
        self.assertEqual(denied, [True])
        self.assertEqual(result.recovery_paths, ())
        self.assert_archive(self.destination)
        self.assert_no_recovery()

    def test_cancel_before_start_preserves_existing_or_absent_destination(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                destination = self.output / ('existing.zip' if existing else 'new.zip')
                old = self.old_backup(destination) if existing else None
                self.cancel.set()
                with self.assertRaisesRegex(ValueError, '已取消備份'):
                    self.run_backup(destination)
                if existing:
                    self.assert_bytes(destination, old)
                else:
                    self.assertFalse(destination.exists())
                self.assert_no_recovery()

    def test_cancel_after_snapshot_covers_database_only_backup(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.cancel.clear()
                destination = self.output / ('existing.zip' if existing else 'new.zip')
                old = self.old_backup(destination) if existing else None
                written = []

                def write(archive, filename, arcname=None, *args, **kwargs):
                    result = ZIP_WRITE(archive, filename, arcname, *args, **kwargs)
                    written.append(arcname)
                    self.cancel.set()
                    return result

                with patch.object(zipfile.ZipFile, 'write', new=write):
                    with self.assertRaisesRegex(ValueError, '已取消備份'):
                        self.run_backup(destination)
                self.assertEqual(written, ['data/studio.sqlite3'])
                if existing:
                    self.assert_bytes(destination, old)
                else:
                    self.assertFalse(destination.exists())
                self.assert_no_recovery()

    def test_cancel_after_last_attachment_preserves_old_backup(self):
        old = self.old_backup()
        attachment = self.data / 'originals' / 'only.bin'
        attachment.parent.mkdir()
        attachment.write_bytes(b'last attachment exact bytes')
        written = []

        def write(archive, filename, arcname=None, *args, **kwargs):
            result = ZIP_WRITE(archive, filename, arcname, *args, **kwargs)
            written.append(arcname)
            if arcname == 'data/originals/only.bin':
                self.cancel.set()
            return result

        with patch.object(zipfile.ZipFile, 'write', new=write):
            with self.assertRaisesRegex(ValueError, '已取消備份'):
                self.run_backup()
        self.assertEqual(written, ['data/studio.sqlite3', 'data/originals/only.bin'])
        self.assert_bytes(self.destination, old)
        self.assert_bytes(attachment, b'last attachment exact bytes')
        self.assert_no_recovery()

    def test_missing_snapshot_preserves_old_backup_and_explicit_retry_succeeds(self):
        old = self.old_backup()
        with self.assertRaises(FileNotFoundError):
            self.run_backup(snapshot=self.root / 'missing.sqlite3')
        self.assert_bytes(self.destination, old)
        self.assert_no_recovery()
        self.run_backup()
        self.assert_archive(self.destination)
        self.assert_no_recovery()

    def test_fsync_failures_before_publication_preserve_old_backup(self):
        native_fsync = os.fsync
        for failure_call in (1, 2):
            with self.subTest(stage='partial' if failure_call == 1 else 'before'):
                destination = self.output / ('fsync-%s.zip' % failure_call)
                old = self.old_backup(destination)
                calls = []

                def fsync(descriptor):
                    calls.append(descriptor)
                    if len(calls) == failure_call:
                        raise OSError('injected fsync failure')
                    return native_fsync(descriptor)

                with patch.object(backup.os, 'fsync', side_effect=fsync):
                    with patch.object(windows, 'replace', wraps=NATIVE_REPLACE) as publication:
                        with self.assertRaisesRegex(OSError, 'injected fsync failure'):
                            self.run_backup(destination)
                self.assertEqual(len(calls), failure_call)
                self.assertEqual(publication.call_count, 0)
                self.assert_bytes(destination, old)
                self.assert_no_recovery()

    def test_cancel_during_before_copy_removes_only_unpublished_files(self):
        old = self.old_backup(payload=b'p' * (1024 * 1024 + 17))
        unrelated = self.output / 'unrelated.txt'
        unrelated.write_bytes(b'keep unrelated file')
        native_digest = backup._digest
        copied = []
        cancel = self.cancel

        def digest(stream, event=None, copy_to=None):
            if copy_to is None:
                return native_digest(stream, event)

            class CancelAfterWrite:
                def write(self, chunk):
                    result = copy_to.write(chunk)
                    copied.append(len(chunk))
                    cancel.set()
                    return result

            return native_digest(stream, event, CancelAfterWrite())

        with patch.object(backup, '_digest', side_effect=digest):
            with self.assertRaisesRegex(ValueError, '已取消備份'):
                self.run_backup()
        self.assertEqual(copied, [1024 * 1024])
        self.assertLess(sum(copied), len(old))
        self.assert_bytes(self.destination, old)
        self.assert_bytes(unrelated, b'keep unrelated file')
        self.assert_no_recovery()

    def test_native_call_failure_before_replace_retains_candidate_and_before(self):
        old = self.old_backup()
        with patch.object(windows, 'replace', side_effect=OSError('native call denied')) as publication:
            with self.assertRaises(OSError) as caught:
                self.run_backup()
        self.assertEqual(publication.call_count, 1)
        files = self.retained(caught.exception, {'.partial', '.before'})
        self.assertIn('native call denied', str(caught.exception))
        self.assert_bytes(self.destination, old)
        self.assert_bytes(files['.before'], old)
        self.assert_archive(files['.partial'])

    def test_native_call_error_after_real_replace_preserves_recovery(self):
        old = self.old_backup()

        def replace(destination, temporary, displaced):
            NATIVE_REPLACE(destination, temporary, displaced)
            raise OSError('injected error after native replacement')

        with patch.object(windows, 'replace', side_effect=replace) as publication:
            with self.assertRaises(OSError) as caught:
                self.run_backup()
        self.assertEqual(publication.call_count, 1)
        files = self.retained(caught.exception, {'.before', '.external'})
        self.assert_bytes(files['.before'], old)
        self.assert_bytes(files['.external'], old)
        self.assert_archive(self.destination)

    def test_native_partial_move_model_preserves_all_available_versions(self):
        old = self.old_backup()

        def partial(destination, temporary, displaced):
            # Controlled model of a failed native call after moving the old
            # destination away. It is not a claim to induce a real OS fault.
            stand_in = self.output / 'native-partial-stand-in.zip'
            stand_in.write_bytes(zip_bytes(b'temporary native stand-in'))
            NATIVE_REPLACE(destination, stand_in, displaced)
            destination.unlink()
            raise OSError('injected partial native failure')

        with patch.object(windows, 'replace', side_effect=partial) as publication:
            with self.assertRaises(OSError) as caught:
                self.run_backup()
        self.assertEqual(publication.call_count, 1)
        self.assertFalse(self.destination.exists())
        files = self.retained(caught.exception, {'.partial', '.before', '.external'})
        self.assert_bytes(files['.before'], old)
        self.assert_bytes(files['.external'], old)
        self.assert_archive(files['.partial'])

    def test_new_destination_race_does_not_overwrite_competitor(self):
        competitor = zip_bytes(b'external new destination')

        def rename(temporary, destination):
            destination.write_bytes(competitor)
            return NATIVE_RENAME(temporary, destination)

        with patch.object(backup.os, 'rename', side_effect=rename) as publication:
            with self.assertRaises(OSError) as caught:
                self.run_backup()
        self.assertEqual(publication.call_count, 1)
        self.assert_bytes(self.destination, competitor)
        files = self.retained(caught.exception, {'.partial'})
        self.assert_archive(files['.partial'])

    def test_actual_displaced_and_postpublication_races_never_roll_back(self):
        for timing in ('before', 'after'):
            with self.subTest(timing=timing):
                # Separate directories keep both subtests' recovery evidence.
                self.output = self.root / ('race-' + timing)
                self.output.mkdir()
                self.destination = self.output / 'backup.zip'
                old = self.old_backup()
                external = zip_bytes(('external ' + timing).encode('ascii'))
                foreign_previous = []

                def replace(destination, temporary, displaced):
                    if timing == 'before':
                        foreign_previous.append(self.foreign_replace(destination, external))
                    NATIVE_REPLACE(destination, temporary, displaced)
                    if timing == 'after':
                        foreign_previous.append(self.foreign_replace(destination, external))

                with patch.object(windows, 'replace', side_effect=replace) as publication:
                    with self.assertRaises(OSError) as caught:
                        self.run_backup()
                self.assertEqual(publication.call_count, 1)
                files = self.retained(caught.exception, {'.before', '.external'})
                self.assert_bytes(files['.before'], old)
                if timing == 'before':
                    self.assertIn('替換時發現外部修改', str(caught.exception))
                    self.assert_bytes(files['.external'], external)
                    self.assert_bytes(foreign_previous[0], old)
                    self.assert_archive(self.destination)
                else:
                    self.assertIn('發布後備份檔再次被修改', str(caught.exception))
                    self.assert_bytes(files['.external'], old)
                    self.assert_bytes(self.destination, external)
                    self.assert_archive(foreign_previous[0])

    def test_destination_inside_data_is_rejected_without_writes(self):
        destination = self.data / 'inside.zip'
        old = self.old_backup(destination)
        with self.assertRaisesRegex(ValueError, 'data 資料夾外'):
            self.run_backup(destination)
        self.assert_bytes(destination, old)
        self.assert_no_recovery()

    def test_successful_publication_reports_recovery_cleanup_failure(self):
        old = self.old_backup()
        native_unlink = Path.unlink
        denied = []

        def unlink(path, *args, **kwargs):
            if path.name.startswith('.pcs-backup-') and path.suffix == '.before':
                denied.append(path)
                raise PermissionError('injected recovery cleanup denial')
            return native_unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', new=unlink):
            result = self.run_backup()
        self.assertEqual(result, str(self.destination))
        self.assertEqual(len(denied), 1)
        self.assertEqual(result.recovery_paths, (str(denied[0]),))
        self.assertEqual(list(self.output.glob('.pcs-backup-*')), denied)
        self.assert_bytes(denied[0], old)
        self.assert_archive(self.destination)
