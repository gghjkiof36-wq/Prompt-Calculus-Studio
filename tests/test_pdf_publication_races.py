"""The final PDF bytes must not silently replace a concurrent destination."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QObject, QSaveFile

from prompt_calculus_studio.markdown_pdf import PdfExport
from prompt_calculus_studio.local_documents import windows
from test_markdown_save_races import external_replace, NATIVE_REPLACE


@unittest.skipUnless(os.name == 'nt', 'Windows recoverable publication')
class PdfPublicationTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-pdf-publish-')
        self.addCleanup(self.area.cleanup)
        self.path = Path(self.area.name)/'export.pdf'
        self.before = b'%PDF-1.4\nbefore'+b'x'*600
        self.raw = b'%PDF-1.4\ncandidate'+b'y'*600
        self.external = b'%PDF-1.4\nexternal'+b'z'*600

    def job(self, existing=True):
        if existing:self.path.write_bytes(self.before)
        job = PdfExport.__new__(PdfExport);QObject.__init__(job)
        job.path = self.path;job.before = self.before if existing else None;job.complete = False
        self.results = [];job.finish = lambda ok,message:self.results.append((ok,message))
        return job

    def test_existing_and_new_destinations_preserve_exact_pdf_bytes(self):
        job = self.job();job.write(self.raw)
        self.assertTrue(self.results[0][0], self.results)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.path.unlink()
        job = self.job(False);job.write(self.raw)
        self.assertTrue(self.results[0][0], self.results)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_existing_destination_race_reports_failure_and_retains_actual_external(self):
        job = self.job()
        def race(destination, temporary, backup):
            external_replace(destination, self.external)
            NATIVE_REPLACE(destination, temporary, backup)
        with patch.object(windows, 'replace', side_effect=race):job.write(self.raw)
        self.assertFalse(self.results[0][0], self.results)
        copies = [p.read_bytes() for p in self.path.parent.glob('*.recovery-*')]
        self.assertIn(self.external, copies)
        self.assertIn(self.raw, copies)

    def test_new_destination_appears_at_final_rename_is_not_overwritten(self):
        job = self.job(False)
        rename = os.rename
        def race(source, destination):
            destination.write_bytes(self.external)
            rename(source, destination)
        with patch('prompt_calculus_studio.file_publication.os.rename', side_effect=race):job.write(self.raw)
        self.assertFalse(self.results[0][0], self.results)
        self.assertEqual(self.path.read_bytes(), self.external)
        self.assertIn(self.raw, [p.read_bytes() for p in self.path.parent.glob('*.recovery-*')])

    def test_post_publication_race_leaves_newer_external_in_place(self):
        job = self.job()
        def race(destination, temporary, backup):
            NATIVE_REPLACE(destination, temporary, backup)
            external_replace(destination, self.external)
        with patch.object(windows, 'replace', side_effect=race):job.write(self.raw)
        self.assertFalse(self.results[0][0], self.results)
        self.assertEqual(self.path.read_bytes(), self.external)

    def test_failed_staging_and_cancel_leave_destination_unchanged(self):
        job = self.job()
        with patch.object(QSaveFile, 'commit', return_value=False):job.write(self.raw)
        self.assertFalse(self.results[0][0])
        self.assertEqual(self.path.read_bytes(), self.before)
        job.complete = True;self.results.clear();job.write(self.raw)
        self.assertFalse(self.results)
        self.assertEqual(self.path.read_bytes(), self.before)
