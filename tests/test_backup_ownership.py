"""R22-S1: backup cleanup must only delete the file object it owns."""
import hashlib
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from prompt_calculus_studio import backup
from prompt_calculus_studio.local_documents import windows


NATIVE_REPLACE = windows.replace


def zip_bytes(payload):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(zipfile.ZipInfo('previous.txt'), payload)
    return stream.getvalue()


@unittest.skipUnless(os.name == 'nt', 'Windows handle-bound backup cleanup')
class BackupOwnershipTests(unittest.TestCase):
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
            connection.execute('INSERT INTO sample VALUES (?)', ('R22 synthetic snapshot',))
            connection.commit()
        finally:
            connection.close()
        self.snapshot_bytes = self.snapshot.read_bytes()
        self.cancel = threading.Event()

    def tearDown(self):
        self.tmp.cleanup()

    def run_backup(self):
        return backup.archive_data(self.data, self.snapshot, self.destination, self.cancel)

    def old_backup(self):
        raw = zip_bytes(b'healthy original backup')
        self.destination.write_bytes(raw)
        return raw

    def assert_bytes(self, path, expected):
        actual = path.read_bytes()
        self.assertEqual(hashlib.sha256(actual).digest(), hashlib.sha256(expected).digest())
        self.assertEqual(actual, expected)

    def assert_archive(self, path):
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.namelist(), ['data/studio.sqlite3'])
            self.assertEqual(archive.infolist()[0].compress_type, zipfile.ZIP_STORED)
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read('data/studio.sqlite3'), self.snapshot_bytes)
        self.assert_bytes(self.snapshot, self.snapshot_bytes)

    def assert_stream_bytes(self, stream, expected):
        position = stream.tell()
        try:
            stream.seek(0)
            actual = stream.read()
        finally:
            stream.seek(position)
        self.assertEqual(hashlib.sha256(actual).digest(), hashlib.sha256(expected).digest())
        self.assertEqual(actual, expected)

    def assert_held_archive(self, stream):
        position = stream.tell()
        try:
            stream.seek(0)
            self.assert_archive(stream)
        finally:
            stream.seek(position)

    def test_new_backup_preserves_uncreated_same_prefix_files(self):
        self.destination = self.output / '備份-😀.zip'
        native_write = backup._write_archive
        foreign = {}
        observed = []

        def write(directory, snapshot, stream, cancel):
            partials = list(self.output.glob('.pcs-backup-*.partial'))
            self.assertEqual(len(partials), 1)
            partial = partials[0]
            observed.append(partial)
            # Discover the live name exactly as another directory reader can;
            # neither UUID generation nor the product's identity is patched.
            for suffix in ('.before', '.external'):
                path = partial.with_suffix(suffix)
                raw = ('foreign same-prefix ' + suffix).encode('ascii')
                with path.open('xb') as writer:
                    writer.write(raw)
                foreign[path] = raw
            return native_write(directory, snapshot, stream, cancel)

        with patch.object(backup, '_write_archive', side_effect=write):
            result = self.run_backup()
        self.assertEqual(len(observed), 1)
        self.assertEqual(result, str(self.destination))
        self.assertEqual(result.recovery_paths, ())
        self.assert_archive(self.destination)
        self.assertFalse(observed[0].exists())
        self.assertEqual(set(self.output.glob('.pcs-backup-*')), set(foreign))
        for path, raw in foreign.items():
            self.assert_bytes(path, raw)

    def test_new_backup_preserves_reused_partial_name_after_rename(self):
        native_rename = backup._rename_owned
        foreign = b'foreign object reusing the published temporary name'
        reused = []

        def rename(stream, destination):
            partials = list(self.output.glob('.pcs-backup-*.partial'))
            self.assertEqual(len(partials), 1)
            temporary = partials[0]
            before = os.fstat(stream.fileno())
            native_rename(stream, destination)
            after = os.fstat(stream.fileno())
            self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
            self.assert_held_archive(stream)
            with temporary.open('xb') as writer:
                writer.write(foreign)
            reused.append(temporary)

        with patch.object(backup, '_rename_owned', side_effect=rename) as publication:
            result = self.run_backup()
        self.assertEqual(publication.call_count, 1)
        self.assertEqual(result, str(self.destination))
        self.assertEqual(result.recovery_paths, ())
        self.assert_archive(self.destination)
        self.assertEqual(len(reused), 1)
        self.assert_bytes(reused[0], foreign)
        self.assertEqual(list(self.output.glob('.pcs-backup-*')), reused)

    def test_cancel_cleanup_keeps_partial_handle_until_owned_delete(self):
        import msvcrt

        old = self.old_backup()
        native_write = backup._write_archive
        native_mark = backup._mark_delete
        foreign = b'foreign writer at cancelled temporary name'
        candidate = self.output / 'foreign-input.bin'
        candidate.write_bytes(foreign)
        displaced = self.output / 'unexpected-displaced.bin'
        held = []
        deleted = []

        def write(directory, snapshot, stream, cancel):
            partials = list(self.output.glob('.pcs-backup-*.partial'))
            self.assertEqual(len(partials), 1)
            info = os.fstat(stream.fileno())
            held.append((partials[0], stream, (info.st_dev, info.st_ino)))
            native_write(directory, snapshot, stream, cancel)
            cancel.set()

        def mark(handle):
            self.assertEqual(len(held), 1)
            path, stream, identity = held[0]
            self.assertEqual(handle, msvcrt.get_osfhandle(stream.fileno()))
            info = os.fstat(stream.fileno())
            self.assertEqual((info.st_dev, info.st_ino), identity)
            with self.assertRaises(OSError):
                NATIVE_REPLACE(path, candidate, displaced)
            self.assertFalse(displaced.exists())
            self.assert_bytes(candidate, foreign)
            info = os.fstat(stream.fileno())
            self.assertEqual((info.st_dev, info.st_ino), identity)
            deleted.append(path)
            return native_mark(handle)

        with patch.object(backup, '_write_archive', side_effect=write):
            with patch.object(backup, '_mark_delete', side_effect=mark) as cleanup:
                with self.assertRaisesRegex(ValueError, '已取消備份'):
                    self.run_backup()
        self.assertEqual(cleanup.call_count, 1)
        self.assertEqual(len(deleted), 1)
        self.assertFalse(deleted[0].exists())
        self.assert_bytes(candidate, foreign)
        self.assert_bytes(self.destination, old)
        self.assert_bytes(self.snapshot, self.snapshot_bytes)
        self.assertEqual(list(self.output.glob('.pcs-backup-*')), [])

    def test_success_cleanup_keeps_before_handle_until_owned_delete(self):
        import msvcrt

        old = self.old_backup()
        native_create = backup._create_private
        native_mark = backup._mark_delete
        foreign = b'foreign writer at held original-copy name'
        candidate = self.output / 'foreign-input.bin'
        candidate.write_bytes(foreign)
        displaced = self.output / 'unexpected-displaced.bin'
        held = []
        denied = []

        def create(path, source):
            stream = native_create(path, source)
            if path.suffix == '.before':
                info = os.fstat(stream.fileno())
                held.append((path, stream, (info.st_dev, info.st_ino)))
            return stream

        def mark(handle):
            # Both recovery files are deleted through their continuously held
            # streams; the original source is covered separately below.
            if held and not denied:
                path, stream, identity = held[0]
                if handle == msvcrt.get_osfhandle(stream.fileno()):
                    self.assert_stream_bytes(stream, old)
                    with self.assertRaises(OSError):
                        NATIVE_REPLACE(path, candidate, displaced)
                    self.assertFalse(displaced.exists())
                    self.assert_bytes(candidate, foreign)
                    info = os.fstat(stream.fileno())
                    self.assertEqual((info.st_dev, info.st_ino), identity)
                    self.assert_stream_bytes(stream, old)
                    denied.append(path)
            return native_mark(handle)

        with patch.object(backup, '_create_private', side_effect=create):
            with patch.object(backup, '_mark_delete', side_effect=mark):
                result = self.run_backup()
        self.assertEqual(len(held), 1)
        self.assertEqual(len(denied), 1)
        self.assertFalse(denied[0].exists())
        self.assertEqual(result, str(self.destination))
        self.assertEqual(result.recovery_paths, ())
        self.assert_archive(self.destination)
        self.assert_bytes(candidate, foreign)
        self.assertEqual(list(self.output.glob('.pcs-backup-*')), [])

    def test_published_source_and_candidate_reject_native_replacement(self):
        old = self.old_backup()
        native_publish = backup._publish
        denied = []
        foreign = {}
        originals = []

        def publish(source, stream, destination, displaced):
            self.assertIsNotNone(source)
            source_info = os.fstat(source.fileno())
            candidate_info = os.fstat(stream.fileno())
            native_publish(source, stream, destination, displaced)
            originals.append(displaced)
            # Both names remain backed by the original held objects after the
            # two real exclusive renames. Competing writers cannot replace
            # either the displaced source or the newly published candidate.
            for label, path in (('source', displaced), ('candidate', destination)):
                candidate = self.output / ('foreign-' + label + '.bin')
                evidence = candidate.with_suffix('.displaced')
                raw = ('foreign writer at published ' + label).encode('ascii')
                candidate.write_bytes(raw)
                foreign[candidate] = raw
                with self.assertRaises(OSError):
                    NATIVE_REPLACE(path, candidate, evidence)
                self.assertFalse(evidence.exists())
                self.assert_bytes(candidate, raw)
                self.assert_stream_bytes(source, old)
                self.assert_held_archive(stream)
                after_source = os.fstat(source.fileno())
                after_candidate = os.fstat(stream.fileno())
                self.assertEqual((after_source.st_dev, after_source.st_ino),
                                 (source_info.st_dev, source_info.st_ino))
                self.assertEqual((after_candidate.st_dev, after_candidate.st_ino),
                                 (candidate_info.st_dev, candidate_info.st_ino))
                denied.append(label)

        with patch.object(backup, '_publish', side_effect=publish) as publication:
            result = self.run_backup()
        self.assertEqual(publication.call_count, 1)
        self.assertEqual(denied, ['source', 'candidate'])
        self.assertEqual(result, str(self.destination))
        self.assertEqual(result.recovery_paths, ())
        self.assert_archive(self.destination)
        self.assertEqual(len(originals), 1)
        self.assertFalse(originals[0].exists())
        self.assertEqual(list(self.output.glob('.pcs-backup-*')), [])
        for path, raw in foreign.items():
            self.assert_bytes(path, raw)

    def test_source_cleanup_uses_original_held_handle_and_blocks_replacement(self):
        import msvcrt

        old = self.old_backup()
        native_create = backup._create_private
        native_delete = backup._delete_stream
        held = []
        denied = []
        foreign = {}

        def create(path, source):
            if path.suffix == '.partial':
                self.assertIsNotNone(source)
                info = os.fstat(source.fileno())
                held.append((source, source.fileno(), msvcrt.get_osfhandle(source.fileno()),
                             (info.st_dev, info.st_ino)))
            return native_create(path, source)

        def delete(stream):
            self.assertEqual(len(held), 1)
            source, descriptor, handle, identity = held[0]
            if stream is not source:
                return native_delete(stream)  # The continuously held .before.
            self.assertEqual(stream.fileno(), descriptor)
            self.assertEqual(msvcrt.get_osfhandle(stream.fileno()), handle)
            info = os.fstat(stream.fileno())
            self.assertEqual((info.st_dev, info.st_ino), identity)
            paths = list(self.output.glob('.pcs-backup-*.external'))
            self.assertEqual(len(paths), 1)
            path = paths[0]
            candidate = self.output / ('foreign-' + path.suffix[1:] + '.bin')
            displaced = candidate.with_suffix('.displaced')
            raw = ('foreign native writer at ' + path.suffix).encode('ascii')
            candidate.write_bytes(raw)
            foreign[candidate] = raw
            # This is the exact source fd held before publication. A compatible
            # reader can inspect its new name, but a native writer cannot move
            # that name away before the same held object is deleted.
            self.assert_stream_bytes(stream, old)
            with windows.reader(path) as inspection:
                self.assertEqual(inspection.read(), old)
            with self.assertRaises(OSError):
                NATIVE_REPLACE(path, candidate, displaced)
            self.assertFalse(displaced.exists())
            self.assert_bytes(candidate, raw)
            self.assert_stream_bytes(source, old)
            info = os.fstat(source.fileno())
            self.assertEqual((info.st_dev, info.st_ino), identity)
            denied.append(path)
            return native_delete(source)

        with patch.object(backup, '_create_private', side_effect=create):
            with patch.object(backup, '_delete_stream', side_effect=delete) as cleanup:
                result = self.run_backup()
        self.assertEqual(cleanup.call_count, 2)
        self.assertEqual(result, str(self.destination))
        self.assertEqual(result.recovery_paths, ())
        self.assertEqual([path.suffix for path in denied], ['.external'])
        self.assertEqual(list(self.output.glob('.pcs-backup-*')), [])
        self.assertTrue(all(not path.exists() for path in denied))
        self.assert_archive(self.destination)
        for path, raw in foreign.items():
            self.assert_bytes(path, raw)

    def test_creation_conversion_failures_preserve_foreign_names_and_report_cleanup(self):
        import msvcrt

        native_open = msvcrt.open_osfhandle
        native_fdopen = backup.os.fdopen
        native_mark = backup._mark_delete
        cases = (('open_osfhandle', False), ('fdopen', False), ('open_osfhandle', True))
        for stage, deny_cleanup in cases:
            with self.subTest(stage=stage, cleanup_denied=deny_cleanup):
                self.output = self.root / ('conversion-' + stage + ('-denied' if deny_cleanup else ''))
                self.output.mkdir()
                self.destination = self.output / 'backup.zip'
                old = self.old_backup()
                conversion_error = OSError('injected ' + stage + ' conversion failure')
                cleanup_error = PermissionError('injected handle cleanup denial')
                observed = []
                foreign = {}
                marked = []
                source_opened = []
                source_wrapped = []

                def observe(handle):
                    partials = list(self.output.glob('.pcs-backup-*.partial'))
                    self.assertEqual(len(partials), 1)
                    partial = partials[0]
                    observed.append((partial, handle))
                    for suffix in ('.before', '.external'):
                        path = partial.with_suffix(suffix)
                        raw = ('foreign during conversion ' + suffix).encode('ascii')
                        with path.open('xb') as writer:
                            writer.write(raw)
                        foreign[path] = raw

                def open_handle(handle, flags):
                    if flags & os.O_RDWR:
                        if stage == 'open_osfhandle':
                            observe(handle)
                            raise conversion_error
                    else:
                        source_opened.append(handle)
                    return native_open(handle, flags)

                def fdopen(descriptor, mode='r', *args, **kwargs):
                    if mode == 'w+b' and stage == 'fdopen':
                        observe(msvcrt.get_osfhandle(descriptor))
                        raise conversion_error
                    if mode == 'rb':
                        source_wrapped.append(descriptor)
                    return native_fdopen(descriptor, mode, *args, **kwargs)

                def mark(handle):
                    marked.append(handle)
                    if deny_cleanup:
                        raise cleanup_error
                    return native_mark(handle)

                # Both wrappers delegate the source reader's O_RDONLY/rb
                # path; the injected failure only targets private creation.
                with patch.object(msvcrt, 'open_osfhandle', side_effect=open_handle):
                    with patch.object(backup.os, 'fdopen', side_effect=fdopen):
                        with patch.object(backup, '_mark_delete', side_effect=mark):
                            with self.assertRaises(OSError) as caught:
                                self.run_backup()
                self.assertEqual(len(source_opened), 1)
                self.assertEqual(len(source_wrapped), 1)
                self.assertEqual(len(observed), 1)
                partial, handle = observed[0]
                self.assertEqual(marked, [handle])
                self.assert_bytes(self.destination, old)
                self.assert_bytes(self.snapshot, self.snapshot_bytes)
                for path, raw in foreign.items():
                    self.assert_bytes(path, raw)
                if deny_cleanup:
                    self.assert_bytes(partial, b'')
                    self.assertIn(str(partial), str(caught.exception))
                    self.assertIn('未發布', str(caught.exception))
                    self.assertIs(caught.exception.__cause__, cleanup_error)
                    self.assertIs(cleanup_error.__context__, conversion_error)
                    self.assertEqual(set(self.output.glob('.pcs-backup-*')), set(foreign) | {partial})
                else:
                    self.assertIs(caught.exception, conversion_error)
                    self.assertIn('conversion failure', str(caught.exception))
                    self.assertFalse(partial.exists())
                    self.assertEqual(set(self.output.glob('.pcs-backup-*')), set(foreign))
