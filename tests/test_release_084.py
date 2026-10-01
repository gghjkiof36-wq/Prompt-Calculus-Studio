import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from prompt_studio import releases


class Release084Tests(unittest.TestCase):
    def test_current_and_legacy_selection(self):
        historical = releases.RELEASES['--v0.8.4-alpha.1']
        self.assertEqual(releases.CURRENT.version, 'v0.8.5 Alpha 1')
        self.assertEqual(releases.CURRENT.flag, '--v0.8.5-alpha.1')
        self.assertIs(releases.select_release(['--v0.8.5-alpha.1', '--v085-repair4']), releases.CURRENT)
        for flag in ('--v0.8.4-alpha.1', '--v084-alpha1', '--v084-repair3', '--v0831-repair3'):
            self.assertIs(releases.select_release([flag]), historical)
        self.assertIs(releases.select_release(['--v0831-repair3', '--v083-direct']), historical)
        self.assertEqual(releases.select_release(['--v0831-chain']).flag, '--v084-chain')

    def test_old_packaged_metadata_keeps_historical_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'assets').mkdir()
            with patch.object(releases, '__file__', str(root/'releases.py')):
                self.assertIs(releases.runtime_release(), releases.CURRENT)
                for flag in ('--v0831-repair3', '--v084-alpha1', '--v0.8.4-alpha.1'):
                    (root/'assets/build-info.json').write_text(json.dumps({'release': flag}), encoding='utf-8')
                    self.assertIs(releases.runtime_release(), releases.RELEASES['--v0.8.4-alpha.1'])
                (root/'assets/build-info.json').write_text('{"release":"invalid"}', encoding='utf-8')
                with self.assertRaises(ValueError):
                    releases.runtime_release()

    def test_launchers_use_new_version_and_existing_data_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            releases.write_launchers(folder, releases.CURRENT)
            for suffix in ('.cmd', '.vbs'):
                text = (Path(folder)/(releases.CURRENT.launcher+suffix)).read_text(encoding='utf-8')
                self.assertIn('--v0.8.5-alpha', text)
                self.assertIn('PROMPT_STUDIO_DATA', text)
                self.assertNotIn('0.831', text)

    def test_multi_digit_versions_are_preserved(self):
        for version in ('0.11.1', '0.20.2'):
            release = releases.Release('--v'+version, 'v'+version, 'v'+version, 'Start-v'+version, '--v'+version)
            with patch.dict(releases.RELEASES, {release.flag: release}):
                self.assertIs(releases.select_release([release.flag]), release)
                with tempfile.TemporaryDirectory() as folder:
                    releases.write_launchers(folder, release)
                    self.assertIn('--v'+version, (Path(folder)/(release.launcher+'.cmd')).read_text(encoding='utf-8'))
