"""Real CivitAI page tasks cross the adapter boundary; no network is used."""
import threading
import unittest
from unittest.mock import patch

from prompt_calculus_studio.civitai import CivitAIError
from prompt_calculus_studio.civitai_adapter import CivitAIBrowseAdapter
from prompt_calculus_studio.jobs import Job
from prompt_calculus_studio.service_adapters import ServiceRequest, ServiceResult
import test_civitai_repairs as fixture
from test_civitai import parent, version


class ServiceAdapterUITests(unittest.TestCase):
    setUp = fixture.DeferredUiTests.setUp
    tearDown = fixture.DeferredUiTests.tearDown

    def deliver(self):
        task, done, failed, original = self.pending.pop(0)
        job = Job(task)
        job.cancel = original.cancel
        received = []
        job.signals.finished.connect(lambda result, error: received.append((result, error)))
        job.run()
        self.assertEqual(len(received), 1)
        original.worker.active = None
        result, error = received[0]
        if error:
            self.assertIsNotNone(failed)
            failed(error)
        else:
            done(result)
        return result, error

    def test_connection_search_and_version_use_adapter_and_keep_provider_shapes(self):
        seen = []
        execute = CivitAIBrowseAdapter.execute
        def capture(adapter, request, cancel):
            seen.append(request)
            return execute(adapter, request, cancel)
        self.page.token.setText('synthetic-secret')
        with patch.object(CivitAIBrowseAdapter, 'execute', capture), \
             patch('prompt_calculus_studio.civitai_ui.CivitAIClient') as client, \
             patch('prompt_calculus_studio.civitai_ui.save_token') as save, \
             patch.object(self.page, 'queue_preview'):
            client.return_value.test_connection.return_value = {'authenticated': True}
            self.page.test_connection()
            result, error = self.deliver()
            self.assertEqual(result, {'authenticated': True})
            self.assertEqual(error, '')
            save.assert_called_once_with(self.w.store.directory, 'synthetic-secret')
            client.return_value.search_models.return_value = {'items': [], 'metadata': {}}
            self.page.query.setText('frozen query')
            self.page.search()
            self.deliver()
            self.assertIsNone(self.page.search_job)
            self.assertEqual(self.page.counter.text(), '沒有相符結果')
            self.page.current = parent()
            self.page.version.blockSignals(True)
            self.page.version.clear()
            self.page.version.addItem('Version', version())
            self.page.version.blockSignals(False)
            client.return_value.version.return_value = version()
            self.page.refresh_version()
            self.deliver()
            self.assertTrue(self.page.version.currentData()['_details_loaded'])
            self.assertEqual([r.operation for r in seen], ['connection', 'search', 'version'])
            self.assertEqual(seen[1].input['query'], 'frozen query')
            self.assertEqual(seen[2].input, {'version_id': 22})
            self.assertEqual(len({r.request_id for r in seen}), 3)
            self.assertTrue(all(r.scope['owner_id'] == seen[0].scope['owner_id'] for r in seen))
            self.assertNotIn('synthetic-secret', repr(seen))
            self.assertNotIn('synthetic-secret', str([r.scope for r in seen]))

    def test_provider_failure_and_wrong_request_go_through_failed_and_clear_search(self):
        self.page.token.setText('synthetic-secret')
        with patch('prompt_calculus_studio.civitai_ui.CivitAIClient') as client:
            client.return_value.search_models.side_effect = CivitAIError('連線失敗。', kind='network')
            self.page.search()
            _, error = self.deliver()
            self.assertEqual(error, '連線失敗。')
            self.assertIsNone(self.page.search_job)
            self.assertEqual(self.page.counter.text(), error)
        def mismatched(adapter, request, cancel):
            other = ServiceRequest(request.service, request.operation, scope=request.scope, input=request.input)
            return ServiceResult.success(other, {'items': [], 'metadata': {}})
        with patch.object(CivitAIBrowseAdapter, 'execute', mismatched):
            self.page.search()
            _, error = self.deliver()
            self.assertIn('回覆與目前要求不符', error)
            self.assertIsNone(self.page.search_job)
            self.assertEqual(self.page.counter.text(), error)
            self.assertFalse(self.page.search_cache)

    def test_token_cancelled_task_never_creates_client_or_saves_credentials(self):
        self.page.token.setText('synthetic-secret')
        self.page.test_connection()
        self.page.token.setText('changed-secret')
        with patch('prompt_calculus_studio.civitai_ui.CivitAIClient') as client, \
             patch('prompt_calculus_studio.civitai_ui.save_token') as save:
            _, error = self.deliver()
            self.assertIn('已取消', error)
            client.assert_not_called()
            save.assert_not_called()
            self.assertEqual(self.page.connection.text(), 'Token 已變更，請重新測試連線。')


if __name__ == '__main__':
    unittest.main()
