"""Orphan recovery discovery and the existing Home entry, using isolated data."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.draft_cache import DraftCache
from prompt_calculus_studio.workspace_recovery import key
from prompt_calculus_studio import workspace_recovery_inventory as inventory
from prompt_calculus_studio.workspace_home import WorkspaceHome
from prompt_calculus_studio.workspace_shell import WorkspaceShell
import test_workspace_panes as fixture


class RecoveryInventoryTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-recovery-inventory-')
        self.root = Path(self.area.name)
        self.files = self.root / 'files'
        self.files.mkdir()
        cache = DraftCache(self.root / 'data')
        cache.roots = (self.root / 'primary', self.root / 'fallback')
        for directory in cache.roots:
            directory.mkdir()
        self.recovery = SimpleNamespace(cache=cache, memory={})

    def tearDown(self):
        self.area.cleanup()

    def value(self, original, **changes):
        result = dict(path=str(original), kind='md', sha=hashlib.sha256(b'original').hexdigest(),
                      raw=base64.b64encode(b'').decode('ascii'))
        result.update(changes)
        return result

    def record(self, original, *, directory=0, value=None, record_key=None, filename=None):
        cache = self.recovery.cache
        record_key = key(original) if record_key is None else record_key
        target = cache.roots[directory] / (filename or cache.name(record_key))
        target.write_text(json.dumps(dict(key=record_key, value=value if value is not None else self.value(original))),
                          encoding='utf-8')
        return target

    def test_disk_only_fresh_discovery_deduplicates_and_never_changes_originals(self):
        original = self.files / '遺失.md'
        first = self.record(original)
        second = self.record(original, directory=1)
        self.recovery.memory[key(self.files / '記憶體.md')] = self.value(self.files / '記憶體.md')
        before = {p: p.read_bytes() for p in (first, second)}
        self.assertEqual(inventory.missing_records(self.recovery), [original])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        original.write_bytes(b'new external file')
        self.assertEqual(inventory.missing_records(self.recovery), [])
        self.assertEqual(original.read_bytes(), b'new external file')
        original.unlink()
        self.recovery.cache.clear(key(original))
        self.assertEqual(inventory.missing_records(self.recovery), [])

    def test_fallback_newer_metadata_takes_priority_without_resurrecting_stale_record(self):
        original = self.files / 'Note.md'
        old = self.record(original)
        new = self.record(original, directory=1, value=self.value(original, sha='invalid'))
        os.utime(old, ns=(1_000_000_000, 1_000_000_000))
        os.utime(new, ns=(2_000_000_000, 2_000_000_000))
        self.assertEqual(inventory.missing_records(self.recovery), [])
        new.unlink()
        self.assertEqual(inventory.missing_records(self.recovery), [original])

    def test_missing_parent_and_all_supported_document_kinds_are_listed(self):
        originals = [self.files / 'missing folder' / 'Empty.md', self.files / 'List.pcs-category',
                     self.files / 'Flow.pcs-workflow']
        for original, kind in zip(originals, ('md', 'category', 'workflow')):
            self.record(original, value=self.value(original, kind=kind))
        self.assertEqual(set(inventory.missing_records(self.recovery)), set(originals))
        self.assertFalse((self.files / 'missing folder').exists())

    def test_former_parent_replaced_by_file_does_not_hide_recoverable_draft(self):
        original = self.files / 'old folder' / 'Notes.md'
        record = self.record(original)
        original.parent.write_bytes(b'keep replacement file')
        self.assertEqual(inventory.missing_records(self.recovery), [original])
        self.assertEqual(original.parent.read_bytes(), b'keep replacement file')
        self.assertTrue(record.is_file())

    @unittest.skipUnless(os.name == 'nt', 'Windows disconnected drive contract')
    def test_offline_drive_does_not_hide_recoverable_draft(self):
        original = Path('Z:/disconnected-source/Notes.md')
        self.record(original)
        missing = {original, *original.parents}
        real_lstat = Path.lstat
        def offline(path):
            if path in missing:
                raise FileNotFoundError('disconnected drive')
            return real_lstat(path)
        with patch.object(Path, 'lstat', offline):
            self.assertEqual(inventory.missing_records(self.recovery), [original])

    def test_metadata_key_filename_kind_and_path_validation_exclude_unrelated_records(self):
        valid = self.files / 'valid.md'
        self.record(valid)
        cases = [
            ('relative.md', dict(path='relative.md')),
            ('wrong-kind.md', dict(kind='workflow')),
            ('no-hash.md', dict(sha='')),
            ('unknown.txt', dict(kind=None)),
            ('different-path.md', dict(path=str(self.files / 'other.md'))),
            ('traversal.md', dict(path=str(self.files / '..' / 'other.md'))),
        ]
        for name, changes in cases:
            original = self.files / name
            self.record(original, value=self.value(original, **changes))
        self.record(self.files / 'wrong-filename.md', filename='f'*64 + '.json')
        self.record(self.files / 'layout.md', record_key='layout')
        self.record(self.files / 'owner.md', record_key='owner:123')
        (self.recovery.cache.roots[0] / ('e'*64 + '.json')).write_text('{broken', encoding='utf-8')
        # A lookalike record under a content folder is never scanned.
        elsewhere = self.files / self.recovery.cache.name(key(self.files / 'not-cache.md'))
        elsewhere.write_text(json.dumps(dict(key=key(self.files / 'not-cache.md'),
                                             value=self.value(self.files / 'not-cache.md'))), encoding='utf-8')
        self.assertEqual(inventory.missing_records(self.recovery), [valid])

    def test_symlink_cache_file_and_root_are_not_read(self):
        original = self.files / 'Note.md'
        record = self.record(original)
        real_is_link = inventory.paths.is_link
        for blocked in (record, record.parent):
            with self.subTest(blocked=blocked), \
                    patch.object(inventory.paths, 'is_link', side_effect=lambda path: path == blocked or real_is_link(path)), \
                    patch.object(inventory.paths, 'read_bytes', wraps=inventory.paths.read_bytes) as read:
                self.assertEqual(inventory.missing_records(self.recovery), [])
                read.assert_not_called()
        self.assertTrue(record.is_file())

    def test_scan_budgets_cover_rejected_records_without_reading_large_files(self):
        original = self.files / 'Note.md'
        self.record(original)
        with patch.object(inventory, 'MAX_SCAN_BYTES', 16), \
                patch.object(inventory.paths, 'read_bytes', wraps=inventory.paths.read_bytes) as read:
            self.assertEqual(inventory.missing_records(self.recovery), [])
            read.assert_not_called()
        self.record(self.files / 'Second.md')
        self.record(self.files / 'Third.md', directory=1)
        with patch.object(inventory, 'MAX_ENTRIES', 1), \
                patch.object(inventory.paths, 'read_bytes', wraps=inventory.paths.read_bytes) as read:
            self.assertEqual(len(inventory.missing_records(self.recovery)), 1)
            self.assertEqual(read.call_count, 1)

    def test_read_failure_and_deep_json_keep_cache_and_do_not_break_home_discovery(self):
        original = self.files / 'Note.md'
        record = self.record(original)
        with patch.object(inventory.paths, 'read_bytes', side_effect=OSError('unreadable')):
            self.assertEqual(inventory.missing_records(self.recovery), [])
        record.write_text('[' * 2000 + '0' + ']' * 2000, encoding='utf-8')
        self.assertEqual(inventory.missing_records(self.recovery), [])
        self.assertTrue(record.is_file())


class RecoveryHomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    events = fixture.WorkspacePaneTests.events

    def orphan(self, name='遺失筆記.md'):
        original = self.root / name
        value = dict(path=str(original), kind='md', sha=hashlib.sha256(b'old').hexdigest(),
                     raw=base64.b64encode('保留草稿'.encode()).decode())
        self.w.close_recovery.cache.write(key(original), value)
        return original

    def test_home_empty_default_is_unchanged_and_all_recovery_rows_remain_accessible(self):
        home = self.w.pages.currentWidget()
        self.assertIsInstance(home, WorkspaceHome)
        self.assertTrue(home.recovery_listing.isHidden())
        self.assertTrue(home.recovery_heading.isHidden())
        paths = [self.orphan(str(i) + '.md') for i in range(6)]
        home.set_recoveries([*paths, paths[0]])
        self.assertEqual(home.recovery_listing.count(), 6)
        self.assertLess(home.recovery_listing.height(), 6*40)
        self.assertFalse(home.recovery_heading.isHidden())
        self.assertEqual(home.recovery_listing.item(0).text(), '0')
        self.assertIn(str(paths[0]), home.recovery_listing.item(0).toolTip())
        home.set_recoveries([])
        self.assertTrue(home.recovery_listing.isHidden())
        self.assertTrue(home.recovery_heading.isHidden())

    def test_click_and_keyboard_use_the_home_pane_even_when_another_pane_is_active(self):
        original = self.orphan()
        right = self.w.ensure_split(empty=True)
        home_tab = self.w.new_tab(pane=right)
        self.w.new_tab(pane=self.w.panes[0])
        home = home_tab.widget
        self.events()
        with patch.object(self.w.layout_store, 'open_recovery', create=True) as recover:
            home.recovery_listing.itemClicked.emit(home.recovery_listing.item(0))
            recover.assert_called_once_with(str(original), pane=right)
            recover.reset_mock()
            home.recovery_listing.setCurrentRow(0)
            home.recovery_listing.setFocus()
            QTest.keyClick(home.recovery_listing, Qt.Key.Key_Return)
            self.events()
            recover.assert_called_once_with(str(original), pane=right)
        self.assertFalse(original.exists())
        self.assertIsNotNone(self.w.close_recovery.cache.read(key(original)))

    def test_restart_finds_orphan_without_layout_slot_or_recent_path_and_does_not_add_tabs(self):
        original = self.orphan()
        self.assertTrue(self.w.close())
        data = self.w.data
        self.w.deleteLater(); self.events()
        self.w = WorkspaceShell(self.root, data)
        self.w.report_error = self.errors.append
        self.w.show(); self.events()
        self.assertEqual(len(self.w.tabs_by_key), 1)
        home = self.w.pages.currentWidget()
        self.assertIsInstance(home, WorkspaceHome)
        self.assertNotIn(str(original), self.w.recent_files)
        self.assertEqual(home.recovery_listing.count(), 1)
        self.assertEqual(home.recovery_listing.item(0).data(Qt.ItemDataRole.UserRole), str(original))
        self.assertFalse(original.exists())
        self.assertEqual(self.w.close_recovery.memory, {})
        self.assertIsNotNone(self.w.close_recovery.cache.read(key(original)))


if __name__ == '__main__':
    unittest.main()
