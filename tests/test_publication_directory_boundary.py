"""089-006: real Windows directory races, all fixtures under a private temp root."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSaveFile
from shiboken6 import isValid
from prompt_calculus_studio import file_publication as publication
from prompt_calculus_studio.local_documents import directory_pin, windows


BASE = b'SYNTHETIC_EXPECTED_NOTE\n'
LOCAL = b'SYNTHETIC_PRIVATE_CANDIDATE_BODY\n'
OUTSIDE = b'SYNTHETIC_OUTSIDE_VICTIM\n'


def set_junction(directory, destination):
    """Set a mount-point tag on this test's existing directory, not a rename.

    Used to distinguish an empty-directory hole from the ordinary rename
    race. No data is written through the resulting junction.
    """
    api = windows._api()
    handle = api.CreateFileW(windows._path(directory), 0x40000000, 0x7,
                             None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        substitute = ('\\??\\' + str(destination.absolute())).encode('utf-16-le')
        display = str(destination.absolute()).encode('utf-16-le')
        names = substitute + b'\0\0' + display + b'\0\0'
        payload = struct.pack('<HHHH', 0, len(substitute), len(substitute) + 2, len(display)) + names
        data = struct.pack('<IHH', 0xa0000003, len(payload), 0) + payload
        buffer = ctypes.create_string_buffer(data)
        returned = wintypes.DWORD()
        api.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        api.DeviceIoControl.restype = wintypes.BOOL
        if not api.DeviceIoControl(handle, 0x900a4, buffer, len(data), None, 0,
                                   ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        api.CloseHandle(handle)


@unittest.skipUnless(os.name == 'nt', 'Windows directory publication boundary')
class DirectoryPublicationTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-directory-boundary-')
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.parent = self.root/'document-root'/'章節'
        self.parent.mkdir(parents=True)
        self.path = self.parent/'note.md'
        self.path.write_bytes(BASE)
        self.outside = self.root/'outside-root'
        self.outside.mkdir()
        (self.outside/self.path.name).write_bytes(OUTSIDE)

    def assert_outside_unchanged(self):
        self.assertEqual(list(self.outside.iterdir()), [self.outside/self.path.name])
        self.assertEqual((self.outside/self.path.name).read_bytes(), OUTSIDE)

    def test_original_late_commit_rename_is_denied_and_candidate_stays_inside(self):
        parent = self.parent
        class SwappingWriter(QSaveFile):
            def commit(writer):
                ok = super().commit()
                if ok:parent.rename(parent.with_name('parked'))
                return ok
        with self.assertRaises(publication.PublicationError) as caught:
            publication.checked_replace(self.path, BASE, LOCAL, SwappingWriter)
        self.assertEqual(self.path.read_bytes(), BASE)
        self.assertFalse(caught.exception.result.uncertain)
        self.assertEqual(len(caught.exception.result.recovery_paths), 1)
        candidate = Path(caught.exception.result.recovery_paths[0])
        self.assertEqual(candidate.parent, self.parent)
        self.assertEqual(candidate.read_bytes(), LOCAL)
        self.assert_outside_unchanged()
        # The failed save must not leave a directory lock or guard behind.
        self.assertFalse(list(self.parent.glob('*.guard')))
        self.parent.rename(self.parent.with_name('released'))

    def test_ancestor_rename_denied_at_commit_and_normal_save_finishes(self):
        owner = self
        denied = []
        class Writer(QSaveFile):
            def commit(writer):
                ok = super().commit()
                if ok:
                    for directory in (owner.parent, owner.parent.parent):
                        with owner.assertRaises(PermissionError):
                            directory.rename(directory.with_name(directory.name + '-moved'))
                        denied.append(directory)
                return ok
        self.assertEqual(publication.checked_replace(self.path, BASE, LOCAL, Writer).status, 'saved')
        self.assertEqual(len(denied), 2)
        self.assertEqual(self.path.read_bytes(), LOCAL)
        self.assertEqual(list(self.parent.iterdir()), [self.path])
        self.assert_outside_unchanged()

    def test_pin_starts_before_candidate_open_and_covers_cleanup(self):
        owner, native_unlink = self, Path.unlink
        phases = []
        def cannot_move(phase):
            with owner.assertRaises(PermissionError):
                owner.parent.rename(owner.parent.with_name('moved'))
            phases.append(phase)
        class Writer(QSaveFile):
            def open(writer, mode):
                cannot_move('open')
                return super().open(mode)
            def write(writer, raw):
                cannot_move('write')
                return super().write(raw)
            def commit(writer):
                cannot_move('commit')
                return super().commit()
        def cleanup(path, *args, **kwargs):
            if '.recovery-' in path.name:cannot_move('cleanup')
            return native_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', cleanup):
            publication.checked_replace(self.path, BASE, LOCAL, Writer)
        self.assertTrue({'open', 'write', 'commit', 'cleanup'} <= set(phases))
        self.assert_outside_unchanged()

    def test_native_replace_and_error_diagnostics_still_have_pinned_parent(self):
        def refused(*args):
            with self.assertRaises(PermissionError):self.parent.rename(self.parent.with_name('moved'))
            raise OSError('synthetic native failure')
        real_exists = Path.exists
        seen = []
        def exists(path):
            if '.recovery-' in path.name:
                with self.assertRaises(PermissionError):self.parent.rename(self.parent.with_name('moved'))
                seen.append(path)
            return real_exists(path)
        with patch.object(windows, 'replace', side_effect=refused), patch.object(Path, 'exists', exists):
            with self.assertRaises(publication.PublicationError) as caught:
                publication.checked_replace(self.path, BASE, LOCAL, QSaveFile)
        self.assertTrue(seen)
        self.assertTrue(caught.exception.result.uncertain)
        self.assertIn(LOCAL, [Path(p).read_bytes() for p in caught.exception.result.recovery_paths])
        self.assertEqual(self.path.read_bytes(), BASE)
        self.assert_outside_unchanged()

    def test_empty_parent_cannot_be_converted_to_junction_in_place(self):
        self.path.unlink()
        with directory_pin.pinned_parent(self.path):
            guards = list(self.parent.iterdir())
            self.assertEqual(len(guards), 1)
            self.assertEqual(guards[0].stat().st_size, 0)
            with self.assertRaises(PermissionError):guards[0].unlink()
            with self.assertRaises(OSError) as denied:set_junction(self.parent, self.outside)
            self.assertEqual(denied.exception.winerror, 145)  # ERROR_DIR_NOT_EMPTY
        self.assertEqual(list(self.parent.iterdir()), [])
        # Positive control: the same operation works when the guard is gone.
        set_junction(self.parent, self.outside)
        try:
            self.assertTrue(self.parent.is_junction())
        finally:
            os.rmdir(self.parent)  # This fixture's junction only, never its target.
        self.assert_outside_unchanged()

    def test_existing_junction_rejected_before_any_writer_is_created(self):
        link = self.root/'link'
        created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.outside)],
                                 capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(created.returncode, 0, created.stderr)
        try:
            def writer(*args):self.fail('payload staging must not start')
            with self.assertRaises(publication.PublicationError) as caught:
                publication.checked_replace(link/self.path.name, OUTSIDE, LOCAL, writer)
            self.assertEqual(caught.exception.result.recovery_paths, ())
            self.assertFalse(caught.exception.result.uncertain)
            self.assert_outside_unchanged()
        finally:
            os.rmdir(link)

    def test_junction_introduced_during_chain_acquisition_is_rejected(self):
        real_open = directory_pin._relative
        parked = self.parent.with_name('parked')
        switched = []
        def race(parent, name, **kwargs):
            if name == self.parent.name and not switched:
                self.parent.rename(parked)
                self.parent.mkdir()
                set_junction(self.parent, self.outside)
                switched.append(True)
            return real_open(parent, name, **kwargs)
        try:
            with patch.object(directory_pin, '_relative', side_effect=race):
                with self.assertRaises(publication.PublicationError):
                    publication.checked_replace(self.path, BASE, LOCAL, QSaveFile)
            self.assertEqual(switched, [True])
            self.assertEqual((parked/self.path.name).read_bytes(), BASE)
            self.assert_outside_unchanged()
        finally:
            if switched:os.rmdir(self.parent)

    def test_empty_parent_changed_during_guard_setup_rejected_before_payload(self):
        self.path.unlink()
        real_open = directory_pin._relative
        switched = []
        def race(parent, name, **kwargs):
            if kwargs.get('guard'):
                set_junction(self.parent, self.outside)
                switched.append(True)
            handle = real_open(parent, name, **kwargs)
            try:
                self.assert_outside_unchanged()
            except BaseException:
                windows._api().CloseHandle(handle)
                raise
            return handle
        try:
            with patch.object(directory_pin, '_relative', side_effect=race):
                with self.assertRaises(publication.PublicationError) as caught:
                    publication.checked_replace(self.path, None, LOCAL, QSaveFile)
            self.assertEqual(switched, [True])
            self.assertEqual(caught.exception.result.recovery_paths, ())
            self.assertFalse(caught.exception.result.uncertain)
            self.assert_outside_unchanged()
        finally:
            if switched:os.rmdir(self.parent)

    def test_guard_creation_failure_releases_ancestors_without_staging(self):
        real_open = directory_pin._relative
        def fail(parent, name, **kwargs):
            if kwargs.get('guard'):raise PermissionError('synthetic read-only parent')
            return real_open(parent, name, **kwargs)
        with patch.object(directory_pin, '_relative', side_effect=fail):
            with self.assertRaises(publication.PublicationError) as caught:
                publication.checked_replace(self.path, BASE, LOCAL, QSaveFile)
        self.assertEqual(caught.exception.result.recovery_paths, ())
        self.assertEqual(list(self.parent.iterdir()), [self.path])
        self.parent.rename(self.parent.with_name('released'))
        self.assert_outside_unchanged()

    def test_preexisting_delete_handle_refuses_save_before_staging(self):
        api = windows._api()
        handle = api.CreateFileW(windows._path(self.parent), 0x10000, 0x7,
                                 None, 3, 0x02200000, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            with self.assertRaises(publication.PublicationError) as caught:
                publication.checked_replace(self.path, BASE, LOCAL, QSaveFile)
            self.assertEqual(caught.exception.result.recovery_paths, ())
            self.assertEqual(list(self.parent.iterdir()), [self.path])
            self.assertEqual(self.path.read_bytes(), BASE)
            self.assert_outside_unchanged()
        finally:
            api.CloseHandle(handle)

    def test_partial_staging_writer_is_destroyed_while_parent_still_pinned(self):
        instances, denied = [], []
        owner = self
        def destroyed():
            try:owner.parent.rename(owner.parent.with_name('moved'))
            except PermissionError:denied.append(True)
        class Writer(QSaveFile):
            def __init__(writer, name):
                super().__init__(name)
                writer.destroyed.connect(destroyed)
                instances.append(writer)  # Keep a Python ref beyond the exception.
            def write(writer, raw):
                super().write(raw[:3])
                raise OSError('synthetic failure after partial staging')
        with self.assertRaises(publication.PublicationError):
            publication.checked_replace(self.path, BASE, LOCAL, Writer)
        self.assertEqual(denied, [True])
        self.assertFalse(isValid(instances[0]))
        self.assertEqual(list(self.parent.iterdir()), [self.path])
        self.assertEqual(self.path.read_bytes(), BASE)
        self.assert_outside_unchanged()

    def test_new_document_in_empty_unicode_parent_and_repeated_save(self):
        self.path.unlink()
        self.assertEqual(publication.checked_replace(self.path, None, BASE, QSaveFile).status, 'saved')
        for before, after in [(BASE, LOCAL), (LOCAL, b''), (b'', BASE)]:
            self.assertEqual(publication.checked_replace(self.path, before, after, QSaveFile).status, 'saved')
            self.assertEqual(self.path.read_bytes(), after)
            self.assertEqual(list(self.parent.iterdir()), [self.path])
        self.assert_outside_unchanged()

    def test_overlapping_saves_do_not_block_child_io_or_release_each_others_pin(self):
        with directory_pin.pinned_parent(self.path):
            self.assertEqual(publication.checked_replace(self.path, BASE, LOCAL, QSaveFile).status, 'saved')
            with self.assertRaises(PermissionError):self.parent.rename(self.parent.with_name('moved'))
            self.assertEqual(len(list(self.parent.glob('*.guard'))), 1)
        self.assertEqual(list(self.parent.iterdir()), [self.path])
        self.assert_outside_unchanged()
