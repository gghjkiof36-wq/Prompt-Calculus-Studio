"""Changing the program folder keeps the original files, settings and drafts."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prompt_calculus_studio import workspace_profile as profile


class WorkspaceProfileTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-profile-')
        self.base = Path(self.area.name)
        self.old, self.new = self.base/'old', self.base/'new'
        (self.old/'files').mkdir(parents=True)
        (self.old/'data').mkdir()
        self.new.mkdir()
        (self.old/'files/筆記.md').write_bytes(b'# Keep\r\n$E=mc^2$\r\n')
        (self.old/'data/draft.json').write_bytes(b'{"text":"unsaved"}')

    def tearDown(self):
        self.area.cleanup()

    def snapshot(self):
        return {p.relative_to(self.old).as_posix(): p.read_bytes() for p in self.old.rglob('*') if p.is_file()}

    def test_reopening_new_installation_reuses_exact_old_locations_and_bytes(self):
        before = self.snapshot()
        root, data = profile.from_installation(self.old)
        profile.remember(self.new, root, data)
        self.assertEqual(profile.locations(self.new), (self.old/'files', self.old/'data'))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.new/'files').exists())
        # A subsequent update can select the previous program folder too.
        following = self.base/'following'
        following.mkdir()
        profile.remember(following, *profile.from_installation(self.new))
        self.assertEqual(profile.locations(following), (root, data))
        self.assertEqual(self.snapshot(), before)

    def test_explicit_launcher_paths_keep_precedence(self):
        profile.remember(self.new, self.old/'files', self.old/'data')
        root, data = self.base/'explicit-files', self.base/'explicit-data'
        self.assertEqual(profile.locations(self.new, root=root, data=data, env_data='wrong'), (root, data))
        self.assertEqual(profile.locations(self.new, env_data=data), (self.new/'files', data))

    def test_missing_or_invalid_profile_does_not_create_a_blank_replacement(self):
        profile.remember(self.new, self.old/'files', self.old/'data')
        (self.old/'files').rename(self.old/'moved-files')
        with self.assertRaises(ValueError):
            profile.locations(self.new)
        self.assertFalse((self.new/'files').exists())
        (self.new/profile.PROFILE).write_text('{invalid', encoding='utf-8')
        with self.assertRaises(ValueError):
            profile.locations(self.new)
        with self.assertRaises(ValueError):
            profile.validate(self.old/'data', self.old/'data')

    def test_failed_profile_write_preserves_previous_selection_and_originals(self):
        profile.remember(self.new, self.old/'files', self.old/'data')
        before, original = self.snapshot(), (self.new/profile.PROFILE).read_bytes()
        with patch.object(os, 'replace', side_effect=OSError('injected write failure')):
            with self.assertRaises(OSError):
                profile.remember(self.new, self.old/'files', self.old/'data')
        self.assertEqual((self.new/profile.PROFILE).read_bytes(), original)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.new.glob('.pcs-workspace-*')), [])

    def test_folder_picker_cancel_does_not_change_profile(self):
        from PySide6.QtWidgets import QApplication, QFileDialog
        app = QApplication.instance() or QApplication([])
        before = self.snapshot()
        with patch.object(QFileDialog, 'getExistingDirectory', return_value=''):
            self.assertIsNone(profile.choose(self.new))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.new/profile.PROFILE).exists())


if __name__ == '__main__':
    unittest.main()
