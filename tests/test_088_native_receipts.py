"""Lost/late browser acknowledgements never override exact backend evidence."""
import copy
import unittest
from unittest.mock import patch
import test_native_queue as fixture


class NativeReceiptTests(unittest.TestCase):
    setUp=fixture.NativeQueueTests.setUp
    prepared=fixture.NativeQueueTests.prepared

    def submitted(self):
        payload=self.prepared();self.service.prepare_prompt(payload);self.queue.sessions.clear()
        return payload,(0,payload['prompt_id'],payload['prompt'],payload['extra_data'])

    def test_closed_browser_ack_recovers_exact_queued_or_completed_job(self):
        payload,entry=self.submitted();original=copy.deepcopy(self.queue.read('operation-1')['payload'])
        self.assertEqual(self.queue.reconcile('operation-1',[entry])['state'],'queued')
        self.assertEqual(self.queue.status('operation-1')['payload'],original)
        self.assertFalse(self.queue.read('operation-1')['submit_inflight'])
        # The same ID remains the same accepted request with no browser present.
        self.assertEqual(self.queue.start(self.request)['prompt_id'],payload['prompt_id'])
        self.assertFalse(self.queue.sessions)
        self.assertEqual(self.queue.reconcile('operation-1',[entry])['state'],'queued')

    def test_id_only_wrong_graph_and_foreign_marker_cannot_confirm_submission(self):
        payload,entry=self.submitted()
        for mutation in ('id-only','graph','marker','identity','missing-marker','invalid-extra'):
            changed=copy.deepcopy(entry)
            if mutation=='id-only':observations=[payload['prompt_id']]
            else:
                if mutation=='graph':changed[2]['5']['inputs']['width']=256
                if mutation=='marker':changed[3]['extra_pnginfo']['prompt_studio']['generation']['native_operation']='other-operation'
                if mutation=='identity':changed[3]['extra_pnginfo']['prompt_studio']['generation']['frontend_id']='other-graph'
                if mutation=='missing-marker':changed[3].clear()
                if mutation=='invalid-extra':changed[3]['extra_pnginfo']=None
                observations=[changed]
            self.assertEqual(self.queue.reconcile('operation-1',observations)['state'],'submitted',mutation)
        self.assertEqual(self.queue.reconcile('operation-1',[entry])['state'],'queued')

    def test_late_browser_failure_keeps_confirmed_backend_job(self):
        payload,entry=self.submitted();self.queue.reconcile('operation-1',[entry])
        self.queue.sessions['tab']=dict(self.live,seen=0)
        result=self.queue.reply(dict(self.live,id='operation-1',error='page closed after transport',uncertain=True))
        self.assertEqual(result['state'],'queued');self.assertEqual(result['prompt_id'],payload['prompt_id'])
        self.assertEqual(self.queue.read('operation-1')['frontend_error'],'page closed after transport')
        self.assertEqual(self.queue.start(self.request)['prompt_id'],payload['prompt_id'])

    def test_missing_backend_evidence_remains_unknown_without_resubmission(self):
        payload,entry=self.submitted()
        operation=self.queue.read('operation-1');operation['created']-=40;self.queue.save(operation)
        self.assertEqual(self.queue.reconcile('operation-1',[])['state'],'unconfirmed')
        self.assertEqual(self.queue.start(self.request)['state'],'unconfirmed')
        self.assertEqual(self.queue.read('operation-1')['prompt_id'],payload['prompt_id'])
        self.assertEqual(self.queue.reconcile('operation-1',[entry])['state'],'queued')

    def test_undispatched_request_cannot_use_a_stored_graph_without_browser(self):
        self.queue.sessions.clear()
        with self.assertRaisesRegex(ValueError,'同一份'):
            self.queue.start(self.request)
        with self.assertRaisesRegex(ValueError,'找不到'):
            self.queue.read(self.request['id'])
