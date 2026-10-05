"""Offscreen queue controls and durable runner, never a live backend."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
import test_work_queue as core_tests
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.queue_panel import QueueDialog
from prompt_calculus_studio.image_iteration_panel import ImageIterationDialog
from prompt_calculus_studio.image_bindings import import_source
from test_comfy_integration import png

APP=QApplication.instance() or QApplication([])


class QueueUiTests(unittest.TestCase):
    def setUp(self):
        fixture=core_tests.QueueTests()
        fixture.setUp();self.fixture=fixture;self.addCleanup(fixture.doCleanups)
        self.work=fixture.capture()
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        with patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1'}):self.window=Window(self.directory.name)
        self.addCleanup(self.window.close)
        self.window.comfy.timer.stop()
        self.runner=self.window.comfy.queue
        self.client=self.window.comfy
        self.client.connected=True;self.client.queue_supported=True
        self.calls=[]
        def request(route,data=None,done=None,failed=None,**kwargs):
            self.calls.append(dict(route=route,data=data,done=done,failed=failed,**kwargs))
        self.client.request=request

    def test_start_pause_lost_response_and_reopen_preserve_queue_without_resubmission(self):
        first=self.runner.store.add(self.work,self.work['capture'])
        self.runner.store.add(self.work,self.work['capture'])
        original=copy.deepcopy(self.window.state)
        self.runner.start();self.runner.start()
        sends=[c for c in self.calls if c['route']=='workflow/queue/submit']
        self.assertEqual(len(sends),1)
        self.runner.pause()
        sends[0]['failed']('response lost')
        self.assertEqual(self.runner.store.read(first['id'])['state'],'unconfirmed')
        self.runner.start();self.runner.observe()
        self.assertEqual(len([c for c in self.calls if c['route']=='workflow/queue/submit']),1)
        self.assertEqual(self.window.state,original)
        from prompt_calculus_studio.queue_runner import QueueRunner
        reopened=QueueRunner(self.client)
        self.assertFalse(reopened.dispatching)
        self.assertEqual(reopened.store.read(first['id'])['state'],'unconfirmed')

    def test_empty_queue_stops_and_later_add_does_not_autostart(self):
        self.runner.start();self.assertFalse(self.runner.dispatching)
        self.runner.store.add(self.work,self.work['capture']);self.runner.observe()
        self.assertEqual(self.calls,[])
        dialog=QueueDialog(self.window);self.addCleanup(dialog.close)
        self.assertEqual(dialog.items.count(),1)
        dialog.items.setCurrentRow(0);dialog.edit('remove')
        self.assertEqual(dialog.items.count(),0)

    def test_legacy_queue_dialog_is_not_an_active_canvas_entry(self):
        self.window.show();self.window.enter_canvas();APP.processEvents()
        self.client.run_id='already-running'
        self.window.run_controls.set_execution_only(True);self.window.run_controls.refresh()
        self.assertFalse(hasattr(self.window,'canvas_queue'))
        self.assertEqual(self.window.run_controls.run_button.text(),'執行')
        self.window.canvas.add_flow_node('schedulers')
        self.assertEqual(len(self.window.canvas.data()['schedulers']),1)

    def test_preview_does_not_change_explicit_single_selection_or_source_bytes(self):
        paths=[]
        for index in range(3):
            path=Path(self.directory.name)/f'{index}.png';png(path,{});paths.append(path)
        dialog=ImageIterationDialog(self.window);self.addCleanup(dialog.close)
        dialog.add_paths(paths)
        self.assertEqual(dialog.scope.currentData(),'all')
        dialog.scope.setCurrentIndex(1);dialog.single.setCurrentIndex(2)
        before=copy.deepcopy(dialog.sources)
        dialog.turn(1);dialog.turn(1)
        self.assertEqual(dialog.single.currentData(),2)
        self.assertEqual(dialog.sources,before)
        paths[0].write_bytes(b'changed original')
        self.assertTrue((self.window.store.directory/dialog.sources[0]['relative']).is_file())


if __name__=='__main__':unittest.main()
