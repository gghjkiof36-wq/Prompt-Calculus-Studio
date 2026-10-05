"""Header connection checks use a mock transport and preserve active work."""
import copy
import os
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.window import Window

APP=QApplication.instance() or QApplication([])


class NavigationConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.calls=[]
        self.environment=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_DATA':self.temp.name})
        self.environment.start()
        def request(client,route,data=None,done=None,failed=None,**kwargs):
            self.calls.append(dict(route=route,data=data,done=done,failed=failed))
        self.transport=patch('prompt_calculus_studio.comfy_client.ComfyClient.request',autospec=True,side_effect=request)
        self.transport.start();self.w=Window(self.temp.name)
        self.assertEqual(self.w.state['multi_output']['version'],7)
        self.w.state['settings'].update(online=False,material='solid')
        self.w.apply_theme();self.w.resize(1440,900);self.w.show();APP.processEvents()
        self.client=self.w.comfy;self.nav=self.w.studio_navigation
        self.w.display_recovery.stop();self.client.timer.stop()
        self.client.last_results=time.monotonic();self.client.register_library=False
        self.observers=[]
        for owner,name in ((self.client.generation,'observe'),(self.client.input_flow,'observe'),
                           (self.client.queue,'observe'),(self.client.images,'poll'),(self.client,'check_run')):
            item=patch.object(owner,name);item.start();self.observers.append(item)

    def tearDown(self):
        self.client.generation.batch=None;self.client.run_id=''
        self.w.close();APP.processEvents()
        for item in self.observers:item.stop()
        self.transport.stop();self.environment.stop();self.temp.cleanup()

    def click(self):
        QTest.mouseClick(self.nav.connection,Qt.MouseButton.LeftButton)
        self.client.timer.stop()

    def test_extension_install_entry_is_available_before_connection_and_binding(self):
        self.w.settings('workflows')
        # Opening the catalog already starts its independent read-only lookup.
        # The installer entry must work without waiting for that response.
        prior=list(self.calls)
        self.w.settings_page.workflows.extension_button.click()
        self.assertEqual(self.w.settings_page.manager.view,'extension')
        self.assertFalse(self.client.enabled)
        self.assertEqual(self.calls,prior)

    def test_extension_identity_is_filtered_and_cleared_on_connection_loss(self):
        self.client.enabled=True;self.client.token='fixture-token';self.client.poll()
        self.calls[-1]['done'](dict(ready=False,running=0,pending=0,capabilities=[],
            extension={'root':['invalid'],'version':'fixture','git_head':'a'*40,'extra':'ignored'}))
        self.assertEqual(self.client.extension_info,{'version':'fixture','git_head':'a'*40})
        self.client.poll();self.calls[-1]['failed']('synthetic disconnect')
        self.assertEqual(self.client.extension_info,{})

    def finish_status(self):
        self.assertEqual(self.calls[-1]['route'],'desktop/status')
        self.calls[-1]['done'](dict(ready=True,lease='lease',target='fixture',running=1,pending=0,
                                  capabilities=['native_queue_v1','native_bindings_v1','stage_execution_v1'],
                                  snapshot_versions=[self.w.state['version']]))

    def test_header_connects_saved_endpoint_and_debounces_clicks(self):
        before_page=self.w.tabs.currentWidget();self.client.url='http://127.0.0.1:8199'
        self.w.state['settings']['comfy_url']=self.client.url
        self.click()
        self.assertEqual(self.client.url,'http://127.0.0.1:8199')
        self.assertIs(self.w.tabs.currentWidget(),before_page)
        self.assertTrue(self.client.enabled);self.assertTrue(self.client.connection_checking)
        self.assertIn('連線中',self.nav.brand.text());self.assertFalse(self.nav.connection.isEnabled())
        self.assertNotIn('8199',self.nav.brand.text())
        for _ in range(4):self.nav.connection.click();self.assertFalse(self.client.recheck_connection())
        self.assertEqual([call['route'] for call in self.calls],['config'])
        self.calls[-1]['done']({'token':'fixture-token'});self.finish_status()
        self.assertTrue(self.client.connected);self.assertFalse(self.client.connection_checking)
        self.assertTrue(self.nav.connection.isEnabled());self.assertIn('已連線',self.nav.brand.text())
        self.assertFalse(self.client.recheck_connection())
        self.assertEqual([call['route'] for call in self.calls if call['route'] in ('config','desktop/status')],['config','desktop/status'])

    def test_connected_active_generation_is_polled_without_reset_or_release(self):
        client=self.client;client.enabled=True;client.connected=True;client.token='fixture-token'
        client.run_id='active-run';client.lease='lease';batch={'fixture':'active'};client.generation.batch=batch
        epoch=client.epoch
        with patch.object(client,'reset_connection') as reset,patch.object(client.generation,'disconnected') as disconnected:
            self.click();self.finish_status()
            reset.assert_not_called();disconnected.assert_not_called()
        self.assertEqual(client.epoch,epoch);self.assertEqual(client.run_id,'active-run')
        self.assertIs(client.generation.batch,batch);self.assertEqual(client.lease,'lease')
        self.assertEqual([call['route'] for call in self.calls if call['route'] in ('config','desktop/status')],['desktop/status'])
        self.assertTrue(all(call['data'] is None for call in self.calls))

    def test_click_joins_periodic_check_and_failure_restores_button(self):
        self.client.enabled=True;self.client.token='fixture-token';self.client.poll()
        self.click();self.assertEqual(len(self.calls),1);self.assertTrue(self.client.connection_checking)
        self.calls[-1]['failed']('fixture unavailable')
        self.assertFalse(self.client.connection_checking);self.assertTrue(self.nav.connection.isEnabled())
        self.assertIn('未連線',self.nav.brand.text())

    def test_changed_saved_address_never_interrupts_existing_work(self):
        self.w.state['settings']['comfy_url']='http://127.0.0.1:8199'
        original=self.client.url;self.client.run_id='active-run'
        with patch.object(self.w,'notice') as notice,patch.object(self.client,'reset_connection') as reset:
            self.click();notice.assert_called_once();reset.assert_not_called()
        self.assertEqual(self.client.url,original);self.assertEqual(self.client.run_id,'active-run')
        self.assertFalse(self.calls);self.assertTrue(self.nav.connection.isEnabled())

    def test_changed_saved_address_connects_when_idle_and_keyboard_works(self):
        self.w.state['settings']['comfy_url']='http://127.0.0.1:8199'
        self.nav.connection.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(self.nav.connection,Qt.Key.Key_Space);self.client.timer.stop()
        self.assertEqual(self.client.url,'http://127.0.0.1:8199')
        self.assertTrue(self.client.connection_checking);self.assertEqual(self.calls[0]['route'],'config')

    def test_failure_can_retry_saved_endpoint_without_changing_bindings_or_drafts(self):
        before=copy.deepcopy(self.w.state['multi_output']);epoch=self.client.epoch
        self.click();self.calls[-1]['failed']('fixture connection refused')
        self.assertFalse(self.client.connected);self.assertTrue(self.nav.connection.isEnabled())
        self.assertFalse(self.client.recheck_connection())
        # Advance only the debounce clock; the transport remains a fixture.
        with patch('prompt_calculus_studio.comfy_client.time.monotonic',return_value=time.monotonic()+1):
            self.click()
        self.assertEqual([call['route'] for call in self.calls],['config','config'])
        self.calls[-1]['done']({'token':'retry-token'});self.finish_status()
        self.assertTrue(self.client.connected);self.assertFalse(self.client.connection_checking)
        self.assertTrue(self.nav.connection.isEnabled());self.assertEqual(self.client.epoch,epoch)
        self.assertEqual(self.w.state['multi_output'],before)


if __name__=='__main__':unittest.main()
