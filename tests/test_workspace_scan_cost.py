"""Inventory stays cheap when failed-save artifacts sit beside documents."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import os
import stat
import unittest
from unittest.mock import patch

from prompt_calculus_studio.local_documents import index, paths
from prompt_calculus_studio.local_documents.schema import DocumentError, SUFFIXES
from prompt_calculus_studio.workspace_locations import INTERNAL_NAMES, KINDS, WorkspaceLocations


@contextmanager
def entries_context(entries):
    yield iter(entries)


class ScanCostCase(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'files'
        self.root.mkdir()
        self.locations = WorkspaceLocations(self.root, Path(self.temp.name) / 'runtime')

    def write(self, relative, data='fixture'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding='utf-8')
        return path

    def legacy_inventory(self):
        pending = [self.root]
        while pending:
            directory = pending.pop()
            for path in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
                if paths.is_link(path) or path.name.casefold() in INTERNAL_NAMES:
                    continue
                if path == self.locations.data or self.locations.data in path.parents:
                    continue
                try:
                    paths.relative_parts(path.name)
                except ValueError:
                    continue
                if path.is_dir():
                    yield path, 'folder'
                    pending.append(path)
                elif path.is_file() and path.suffix.casefold() in KINDS:
                    yield path, KINDS[path.suffix.casefold()]

    def legacy_index(self):
        suffixes = {v: k for k, v in SUFFIXES.items()} | {'.md': 'markdown'}
        pending = [self.root]
        while pending:
            for path in sorted(pending.pop().iterdir(), key=lambda p: p.name.casefold()):
                if paths.is_link(path) or path.name.casefold() in ('.pcs', '.obsidian'):
                    continue
                if path.is_dir():
                    pending.append(path)
                elif path.is_file() and path.suffix.casefold() in suffixes:
                    info = path.stat()
                    yield index.Entry(path.relative_to(self.root).as_posix(), suffixes[path.suffix.casefold()], info.st_size, info.st_mtime_ns)

    def test_supported_results_and_order_match_before_optimization(self):
        for name in ('a.md', 'B.MD', '.hidden.md', '.md', 'ignored.txt',
                     'conversation.pcs-chat', 'old.pcs-chat.recovery-123.candidate',
                     'ordinary.recovery-name.candidate.md',
                     'A folder/z.pcs-category', 'z folder/a.pcs-workflow',
                     'folder.pcs-chat.recovery-123.before/real.md',
                     '.pcs/internal.md', '.obsidian/config.md', 'assets/note.md'):
            self.write(name)
        self.assertEqual(list(self.locations.inventory(self.root)), list(self.legacy_inventory()))
        self.assertEqual(list(index.scan(self.root)), list(self.legacy_index()))
        self.assertIn(self.root / 'folder.pcs-chat.recovery-123.before/real.md',
                      [path for path, _ in self.locations.inventory(self.root)])

    def test_excluded_subtrees_are_not_entered(self):
        self.locations.data = self.root / 'runtime'
        for name in ('.pcs/a.md', '.obsidian/a.md', 'assets/a.md', 'runtime/a.md', 'visible/b.md'):
            self.write(name)
        original = os.scandir
        visited = []

        def observe(directory):
            visited.append(Path(directory))
            return original(directory)

        with patch.object(paths.os, 'scandir', side_effect=observe):
            result = list(self.locations.inventory(self.root))
        self.assertEqual(visited, [self.root, self.root / 'visible'])
        self.assertEqual([p.relative_to(self.root).as_posix() for p, _ in result], ['visible', 'visible/b.md'])

    def test_recovery_files_do_not_trigger_file_metadata_queries(self):
        queried = []
        info = SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0)

        def entry(name, directory=False):
            def record(operation):
                queried.append((name, operation))
                return info if operation == 'stat' else True
            return SimpleNamespace(name=name, path=str(self.root / name),
                                   is_dir=lambda **kwargs: directory,
                                   is_file=lambda **kwargs: record('is_file'),
                                   stat=lambda **kwargs: record('stat'))

        fixture = [entry(f'chat.pcs-chat.recovery-{n:032x}.candidate') for n in range(5765)]
        fixture += [entry('real.md'), entry('folder.pcs-chat.recovery-old.before', directory=True)]
        with patch.object(paths.os, 'scandir', return_value=entries_context(fixture)):
            result = list(paths.document_entries(self.root, KINDS))
        self.assertEqual([path.name for path, _, _ in result], ['real.md', 'folder.pcs-chat.recovery-old.before'])
        self.assertEqual(queried, [('real.md', 'is_file'), ('real.md', 'stat'),
                                  ('folder.pcs-chat.recovery-old.before', 'stat')])

    def test_reparse_entries_are_never_traversed(self):
        entries = []
        for name, mode, attributes in [('junction', stat.S_IFDIR, 0x400),
                                        ('link.md', stat.S_IFLNK, 0),
                                        ('ordinary', stat.S_IFDIR, 0)]:
            info = SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
            entries.append(SimpleNamespace(name=name, path=str(self.root / name),
                                           is_dir=lambda **kwargs: True,
                                           stat=lambda info=info, **kwargs: info))
        with patch.object(paths.os, 'scandir', return_value=entries_context(entries)):
            result = list(paths.document_entries(self.root, KINDS))
        self.assertEqual([path.name for path, _, _ in result], ['ordinary'])

    def test_supported_metadata_error_is_not_silently_ignored(self):
        def failed(**kwargs):
            raise PermissionError('fixture inaccessible')
        item = SimpleNamespace(name='real.md', path=str(self.root / 'real.md'),
                               is_dir=lambda **kwargs: False,
                               is_file=lambda **kwargs: True, stat=failed)
        with patch.object(paths.os, 'scandir', return_value=entries_context([item])):
            with self.assertRaises(PermissionError):
                list(paths.document_entries(self.root, KINDS))

    def test_checked_keeps_casefold_matching_and_existing_conflict(self):
        existing = self.write('Mixed.MD')
        self.assertEqual(paths.checked(self.root, 'mIxEd.md'), existing)
        with self.assertRaises(DocumentError) as raised:
            paths.checked(self.root, 'mixed.md', new=True)
        self.assertEqual(raised.exception.code, 'exists')
        self.assertEqual(paths.checked(self.root, 'fresh.md', new=True), self.root / 'fresh.md')

    def test_checked_rejects_casefold_ambiguity_before_following_path(self):
        entries = [SimpleNamespace(name=name, path=str(self.root / name))
                   for name in ('mixed.md', 'Mixed.MD')]
        with patch.object(paths.os, 'scandir', return_value=entries_context(entries)):
            with self.assertRaises(DocumentError) as raised:
                paths.checked(self.root, 'MIXED.md')
        self.assertEqual(raised.exception.code, 'collision')


if __name__ == '__main__':
    unittest.main()
