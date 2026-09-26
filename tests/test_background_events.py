import copy
import importlib
import unittest
from test_comfy_integration import Service

BackgroundEvents=importlib.import_module('integration_test.background_events').BackgroundEvents


class EventTests(unittest.TestCase):
    def setUp(self):
        self.sent=[]
        self.events=BackgroundEvents(lambda *args:self.sent.append(copy.deepcopy(args)))
        self.events.register('prompt','background-client')

    def event(self,event,**data):
        self.events.observe(event,dict(prompt_id='prompt',**data),'background-client')

    def test_true_snapshot_watermark_then_live_and_native_sender_preserved(self):
        self.event('execution_start',timestamp=100)
        self.event('progress_state',nodes={'1':{'value':1,'max':28}})
        self.event('progress_state',nodes={'1':{'value':6,'max':28}})
        self.events.attach('prompt','new-browser','subscription')
        snapshot=self.sent[-1]
        self.assertEqual(snapshot[0],'pcs_background_snapshot')
        self.assertEqual(snapshot[1]['watermark'],3)
        self.assertEqual([e['seq'] for e in snapshot[1]['events']],[1,3])
        self.event('executed',node='8',output={'images':['a','b']})
        self.assertEqual(self.sent[-2][0],'pcs_background_event')
        self.assertEqual(self.sent[-2][1]['seq'],4)
        self.assertEqual(self.sent[-2][1]['data'],self.sent[-1][1])
        self.assertEqual(self.sent[-1][2],'background-client')

    def test_wrong_prompt_or_client_cannot_pollute_snapshot(self):
        self.events.observe('execution_start',{'prompt_id':'other'},'background-client')
        self.events.observe('execution_start',{'prompt_id':'prompt'},'other-client')
        self.events.attach('prompt','new','s')
        self.assertEqual(self.sent[-1][1]['events'],[])

    def test_completed_before_attach_does_not_revive_start_or_terminal(self):
        self.event('execution_start')
        self.event('execution_success')
        count=len(self.sent)
        self.assertTrue(self.events.attach('prompt','new','s')['finished'])
        self.assertEqual(len(self.sent),count)

    def test_duplicate_subscribe_does_not_replay_and_detach_stops_late_delivery(self):
        self.event('execution_start')
        self.events.attach('prompt','new','s')
        count=len(self.sent)
        self.events.attach('prompt','new','s')
        self.assertEqual(len(self.sent),count)
        self.events.detach('new')
        self.event('progress',value=5,max=28)
        self.assertEqual(len(self.sent),count+1)

    def test_subscribe_cannot_steal_background_socket(self):
        with self.assertRaises(ValueError):self.events.attach('prompt','background-client','s')

    def test_payloads_retained_without_aliasing_or_fabricated_progress(self):
        data={'prompt_id':'prompt','nodes':{'1':{'value':2,'max':28}}}
        self.events.observe('progress_state',data,'background-client')
        data['nodes']['1']['value']=99
        self.events.attach('prompt','new','s')
        self.assertEqual(self.sent[-1][1]['events'][0]['data']['nodes']['1']['value'],2)

if __name__=='__main__':unittest.main()
