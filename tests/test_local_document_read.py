"""Small document reads stay small without weakening size/race checks."""
import io
from pathlib import Path
import stat
import tempfile
import tracemalloc
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prompt_calculus_studio.local_documents import paths, schema


class DocumentReadTests(unittest.TestCase):
    def test_small_file_has_bounded_peak_memory(self):
        with tempfile.TemporaryDirectory() as area:
            file = Path(area)/'small.pcs-category'
            content = b'x' * 4096
            file.write_bytes(content)
            tracemalloc.start()
            try:
                raw, info = paths.read_bytes(file)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertEqual(raw, content)
            self.assertEqual(info.st_size, len(content))
            self.assertLess(peak, 1024 * 1024)

    def test_empty_exact_limit_and_oversize_files(self):
        with tempfile.TemporaryDirectory() as area:
            file = Path(area)/'sample'
            for size in (0, 16, 17):
                with self.subTest(size=size):
                    file.write_bytes(b'x' * size)
                    if size <= 16:
                        self.assertEqual(paths.read_bytes(file, limit=16)[0], b'x' * size)
                    else:
                        with self.assertRaises(schema.DocumentError) as error:
                            paths.read_bytes(file, limit=16)
                        self.assertEqual(error.exception.code, 'limit')

    def test_growth_truncation_and_timestamp_changes_are_conflicts(self):
        # Model a writer racing after the initial fstat, including a restored
        # timestamp/size. Never accept a partial read as a complete document.
        initial = SimpleNamespace(st_mode=stat.S_IFREG, st_size=4, st_mtime_ns=10)
        for content, after in (
                (b'abcde', initial),
                (b'abc', initial),
                (b'abcd', SimpleNamespace(st_size=4, st_mtime_ns=11)),
                (b'abcde', SimpleNamespace(st_size=10, st_mtime_ns=11))):
            with self.subTest(content=content, after=after):
                stream = io.BytesIO(content)
                stream.fileno = lambda: 123
                with patch('builtins.open', return_value=stream), \
                        patch.object(paths.os, 'fstat', side_effect=[initial, after]):
                    with self.assertRaises(schema.DocumentError) as error:
                        paths.read_bytes('unused', limit=16)
                    self.assertEqual(error.exception.code, 'conflict')
