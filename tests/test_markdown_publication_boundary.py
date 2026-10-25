"""Exercise the real Windows directory boundary around Markdown publication."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prompt_calculus_studio.workspace_file_io import (
    create_markdown, TargetNameCollision, PublishedMarkdownError,
)


class MarkdownPublicationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pcs-md-publication-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = self.root/'notes'
        self.parent.mkdir()
        self.target = self.parent/'Notes.md'

    @unittest.skipUnless(os.name == 'nt', 'Windows directory pin')
    def test_directory_cannot_be_replaced_before_first_payload_write(self):
        original = Path.open
        attempted = []
        def intercept(path, *args, **kwargs):
            if path.name.startswith('.pcs-new-'):
                try:
                    self.parent.rename(self.root/'moved-notes')
                except OSError:
                    attempted.append('blocked')
                else:
                    attempted.append('replaced')
                    self.parent.mkdir()
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', intercept):
            create_markdown(self.target, b'private synthetic draft')
        self.assertEqual(attempted, ['blocked'])
        self.assertFalse((self.root/'moved-notes').exists())
        self.assertEqual(self.target.read_bytes(), b'private synthetic draft')
        self.assertEqual([p.name for p in self.parent.iterdir()], ['Notes.md'])

    @unittest.skipUnless(os.name == 'nt', 'Windows directory pin')
    def test_parent_remains_fixed_until_publication_and_cleanup_end(self):
        link, unlink = os.link, Path.unlink
        attempted = []
        def blocked(stage):
            with self.assertRaises(OSError):
                self.parent.rename(self.root/('moved-'+stage))
            attempted.append(stage)
        def publish(a, b):
            blocked('publish')
            return link(a, b)
        def cleanup(path, *args, **kwargs):
            if path.name.startswith('.pcs-new-'):
                blocked('cleanup')
            return unlink(path, *args, **kwargs)
        with patch('os.link', side_effect=publish), patch.object(Path, 'unlink', cleanup):
            create_markdown(self.target, b'preserved')
        self.assertEqual(attempted, ['publish', 'cleanup'])
        self.parent.rename(self.root/'released')
        self.assertEqual((self.root/'released/Notes.md').read_bytes(), b'preserved')

    def test_exclusive_collision_keeps_competing_file_and_cleans_temporary(self):
        def publish(a, b):
            b.write_bytes(b'other writer')
            raise FileExistsError(str(b))
        with patch('os.link', side_effect=publish), self.assertRaises(TargetNameCollision):
            create_markdown(self.target, b'new draft')
        self.assertEqual(self.target.read_bytes(), b'other writer')
        self.assertEqual([p.name for p in self.parent.iterdir()], ['Notes.md'])

    def test_cleanup_error_after_publication_is_not_a_name_collision(self):
        original = Path.unlink
        def cleanup(path, *args, **kwargs):
            if path.name.startswith('.pcs-new-'):
                raise PermissionError('synthetic cleanup denial')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', cleanup), self.assertRaises(PublishedMarkdownError):
            create_markdown(self.target, b'complete draft')
        self.assertEqual(self.target.read_bytes(), b'complete draft')


if __name__ == '__main__':
    unittest.main()
