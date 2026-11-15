"""Real existing delivery/reducer code must ignore the extension's new kind."""
import json
import tempfile
import types
import unittest
from unittest.mock import Mock

from test_result_manifest import Fixture
from prompt_calculus_studio.comfy_event_reducer import EventReducer
from prompt_calculus_studio.comfy_event_transport import DeliveryFilter


class ExistingConsumerTests(unittest.TestCase):
    def test_manifest_notifications_never_enter_job_frame_or_document_acceptance(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = Fixture(root)
            fixture.store.ingest(fixture.packet(fixture.items(2)))
            fixture.seal()
            with fixture.service.connect() as db:
                notices = [json.loads(row[0]) for row in db.execute(
                    "SELECT body FROM event_log WHERE json_extract(body,'$.kind')='result.manifest'")]
            selected = DeliveryFilter().select(dict(owner='original-owner', events=notices))['events']
            self.assertEqual(selected, notices)
            # Exercise the real receive method. Anything after the unowned-kind
            # gate (job lookup, SQL, reconciliation or acceptance) is forbidden.
            reducer = object.__new__(EventReducer)
            reducer.identity = dict(server_instance=fixture.service.events.server_instance,
                                    epoch=fixture.service.events.epoch)
            reducer.client = types.SimpleNamespace(stopped=False)
            reducer.diagnostics = []
            reducer.job = Mock(side_effect=AssertionError('job lookup'))
            reducer.prompt_job = Mock(side_effect=AssertionError('prompt lookup'))
            reducer.accept_evidence = Mock(side_effect=AssertionError('document/frame acceptance'))
            reducer.reconcile = Mock()
            done = []
            for event in notices*3:
                reducer.receive(event, lambda: done.append(True))
            self.assertEqual(len(done), len(notices)*3)
            reducer.job.assert_not_called()
            reducer.prompt_job.assert_not_called()
            reducer.accept_evidence.assert_not_called()
            reducer.reconcile.require.assert_not_called()


if __name__ == '__main__':
    unittest.main()
