"""Real small ZIP failures retain per-conversation publication results."""
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
from zipfile import ZIP_STORED, ZipFile

from test_conversation_import import sample
from prompt_calculus_studio.conversation_archive import load_archive
from prompt_calculus_studio.conversation_import import Destination, RECEIPT, import_selected


TITLES = ('First complete', 'Second attachment', 'Third unprocessed')


def write_crc_archive(path, *, damaged=True, attachment_index=1):
    records = [sample('crc-'+str(i), title) for i, title in enumerate(TITLES)]
    records[attachment_index]['mapping']['a']['message']['content']['parts'].append(
        dict(content_type='image_asset_pointer', asset_pointer='file-service://file-tiny.png'))
    with ZipFile(path, 'w', compression=ZIP_STORED) as archive:
        archive.writestr('conversations.json', json.dumps(records))
        archive.writestr('file-tiny.png', b'synthetic tiny attachment')
        info = archive.getinfo('file-tiny.png')
        header_offset = info.header_offset
    if damaged:
        raw = bytearray(path.read_bytes())
        name_size, extra_size = struct.unpack_from('<HH', raw, header_offset+26)
        raw[header_offset+30+name_size+extra_size] ^= 1
        path.write_bytes(raw)  # Fixed before load_archive, never changed mid-import.
    return path.read_bytes()


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


class ConversationCrcTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root/'output'
        self.destination.mkdir()
        self.path = self.root/'export.zip'

    def run_import(self, archive):
        return import_selected(archive, (0, 1, 2), Destination.choose(self.destination), threading.Event())

    def test_valid_attachment_completes_all_conversations(self):
        original = write_crc_archive(self.path, damaged=False)
        result = self.run_import(load_archive(self.path))
        self.assertEqual(len(result.completed), 3)
        self.assertFalse(result.failed or result.unprocessed or result.cancelled)
        self.assertTrue(all((path.parent/RECEIPT).is_file() for path in result.completed))
        self.assertEqual(self.path.read_bytes(), original)

    def test_late_crc_preserves_completed_and_retry_does_not_overwrite(self):
        original = write_crc_archive(self.path)
        archive = load_archive(self.path)
        result = self.run_import(archive)
        self.assertEqual(len(result.completed), 1)
        self.assertEqual(result.skipped, [])
        self.assertFalse(result.cancelled)
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.failed[0][:2], (TITLES[1], ''))
        self.assertIn('CRC', result.failed[0][2])
        self.assertEqual(result.unprocessed, [TITLES[2]])
        complete = result.completed[0]
        self.assertEqual(result.titles[str(complete)], TITLES[0])
        self.assertEqual(json.loads((complete.parent/RECEIPT).read_text(encoding='utf-8'))['state'], 'complete')
        self.assertEqual(list(self.destination.iterdir()), [complete.parent])
        complete.write_text('user edit preserved', encoding='utf-8')
        before_retry = snapshot(self.destination)
        retry = self.run_import(archive)  # Explicit second request, never an automatic retry.
        self.assertEqual(retry.completed, [])
        self.assertEqual(retry.skipped, [complete])
        self.assertEqual(retry.failed, result.failed)
        self.assertEqual(retry.unprocessed, [TITLES[2]])
        self.assertEqual(snapshot(self.destination), before_retry)
        self.assertEqual(self.path.read_bytes(), original)

    def test_first_crc_returns_failure_and_all_later_titles(self):
        original = write_crc_archive(self.path, attachment_index=0)
        result = self.run_import(load_archive(self.path))
        self.assertEqual(result.completed, [])
        self.assertEqual(result.failed[0][:2], (TITLES[0], ''))
        self.assertEqual(result.unprocessed, list(TITLES[1:]))
        self.assertFalse(result.cancelled)
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertEqual(self.path.read_bytes(), original)

    def test_unexpected_program_error_is_not_converted_to_partial_success(self):
        write_crc_archive(self.path, damaged=False)
        archive = load_archive(self.path)
        with patch('prompt_calculus_studio.conversation_import.attachment_plan', side_effect=TypeError('program error')):
            with self.assertRaisesRegex(TypeError, 'program error'):
                self.run_import(archive)
        self.assertEqual(list(self.destination.iterdir()), [])
