"""Controlled late callbacks with synthetic credentials, never a live service."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtGui import QImage
import test_civitai_repairs as fixtures
from test_civitai import version, parent


class TokenEpochTests(unittest.TestCase):
    setUp = fixtures.DeferredUiTests.setUp
    tearDown = fixtures.DeferredUiTests.tearDown
    finish = fixtures.DeferredUiTests.finish
    select_preview = fixtures.DeferredUiTests.select_preview

    def response(self):
        return dict(items=[dict(parent(), type='LORA', modelVersions=[version()])], metadata={})

    def test_changed_token_rejects_old_search_and_keeps_new_job(self):
        self.page.token.setText('synthetic-token-A'); self.page.search()
        _, done, failed, job = self.pending.pop(0)
        self.page.token.setText('synthetic-token-B')
        # A completion may already be queued when cancellation is requested.
        self.page.browse_jobs.active = None
        self.page.search()
        newer = self.page.search_job
        before = (copy.deepcopy(self.page.results), copy.deepcopy(self.page.search_cache), self.page.counter.text())
        done(self.response()); failed('stale synthetic failure')
        self.assertEqual((self.page.results, self.page.search_cache, self.page.counter.text()), before)
        self.assertIs(self.page.search_job, newer)
        self.finish(self.response())
        self.assertEqual(len(self.page.results), 1)
        self.assertTrue(self.page.search_cache)

    def test_clear_token_rejects_search_and_connection_failures(self):
        for operation in (self.page.search, self.page.test_connection):
            self.page.token.setText('synthetic-token-A'); operation()
            _, done, failed, job = self.pending.pop(0)
            with patch('prompt_calculus_studio.civitai_ui.clear_token'):
                self.page.clear_token()
            before = (self.page.connection.text(), self.page.counter.text(), copy.deepcopy(self.page.search_cache))
            failed('stale synthetic failure')
            with patch('prompt_calculus_studio.civitai_ui.save_token') as saved:
                done(dict(authenticated=True, **self.response()))
                saved.assert_not_called()
            self.assertEqual((self.page.connection.text(), self.page.counter.text(), self.page.search_cache), before)
            self.page.browse_jobs.active = None

    def test_queued_old_search_is_discarded_before_dispatch(self):
        self.page.token.setText('synthetic-token-A'); self.page.test_connection()
        self.page.search()
        self.assertIsNotNone(self.page.pending_search)
        self.page.token.setText('synthetic-token-B')
        with patch('prompt_calculus_studio.civitai_ui.save_token') as saved:
            self.finish(dict(authenticated=True)); saved.assert_not_called()
        self.assertIsNone(self.page.pending_search)
        self.assertEqual(len(self.started), 1)

    def test_token_change_rejects_detail_and_preview_callbacks(self):
        self.page.token.setText('synthetic-token-A')
        self.page.current = parent()
        self.page.version.blockSignals(True)
        self.page.version.clear(); self.page.version.addItem('Version', version())
        self.page.version.blockSignals(False); self.page.refresh_version()
        _, done, failed, job = self.pending.pop(0)
        self.page.token.setText('synthetic-token-B')
        before = (copy.deepcopy(self.page.source), copy.deepcopy(self.page.version.currentData()),
                  self.page.detail_state.text(), len(self.image_requests))
        done(dict(version(), name='OLD_AUTH_MARKER')); failed('stale detail failure')
        self.assertEqual((self.page.source, self.page.version.currentData(), self.page.detail_state.text(), len(self.image_requests)), before)
        self.page.browse_jobs.active = None
        self.select_preview('synthetic')
        callback = self.image_requests[-1]
        self.page.token.setText('synthetic-token-C')
        before_text = self.page.preview.text()
        callback[2](QImage(16, 16, QImage.Format.Format_RGB32)); callback[3]('stale image failure')
        self.assertTrue(self.page.preview.image.isNull())
        self.assertEqual(self.page.preview.text(), before_text)

    def test_unchanged_token_allows_failure_and_connection_save(self):
        self.page.token.setText('synthetic-token-A'); self.page.search()
        self.finish(error='current synthetic failure')
        self.assertEqual(self.page.counter.text(), 'current synthetic failure')
        self.page.test_connection()
        with patch('prompt_calculus_studio.civitai_ui.save_token') as saved:
            self.finish(dict(authenticated=True))
            saved.assert_called_once_with(self.w.store.directory, 'synthetic-token-A')
