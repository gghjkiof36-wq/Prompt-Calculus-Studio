"""Opt-in event capture, bounded diagnostics and content exclusion."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QTimer
from prompt_calculus_studio.performance_recorder import (
    JsonlWriter, add_arguments, create_application, start_recording)
from prompt_calculus_studio.performance_callbacks import wrap_callback


class StallRecorderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=create_application([],enabled=True)
        cls.recorder=cls.app.stall_recorder

    @classmethod
    def tearDownClass(cls):cls.recorder.close()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)
        start_recording(self.app,self.data)

    def tearDown(self):
        self.recorder.writer.close();self.recorder.writer=None

    def records(self):
        self.recorder.writer.close()
        return [json.loads(line) for file in (self.data/'logs').glob('*.jsonl') for line in file.read_text().splitlines()]

    def test_threshold_and_zero_timer_attribution(self):
        for delay in (.005,.060,.080):
            loop=QEventLoop()
            def callback():time.sleep(delay);loop.quit()
            QTimer.singleShot(0,wrap_callback(self.recorder,'stage pump',callback))
            loop.exec()
        rows=self.records()
        self.assertEqual(len(rows),2,rows)
        self.assertEqual([r['feature'] for r in rows],['stage pump']*2)
        self.assertTrue(all(r['duration_ms']>=50 for r in rows))
        self.assertTrue(all(r['event_type'] in ('Timer','ZeroTimerEvent','MetaCall','Callback') for r in rows),rows)

    def test_zero_timer_event_with_nested_timer_is_not_duplicated(self):
        recorder=self.recorder
        class Receiver(QObject):
            def event(self,event):
                if event.type()==QEvent.Type.ZeroTimerEvent:
                    QCoreApplication.sendEvent(self,QEvent(QEvent.Type.User));return True
                if event.type()==QEvent.Type.User:
                    wrap_callback(recorder,'stage pump',lambda:time.sleep(.060))();return True
                return super().event(event)
        receiver=Receiver()
        QCoreApplication.sendEvent(receiver,QEvent(QEvent.Type.ZeroTimerEvent))
        rows=self.records();self.assertEqual(len(rows),1,rows)
        self.assertEqual(rows[0]['event_type'],'ZeroTimerEvent')
        self.assertEqual(rows[0]['feature'],'stage pump')

    def test_nested_delivery_and_callback_are_one_stall(self):
        recorder=self.recorder
        class Receiver(QObject):
            def event(self,event):
                if event.type()==QEvent.Type.User:
                    wrap_callback(recorder,'history projection',lambda:time.sleep(.060))()
                    return True
                return super().event(event)
        receiver=Receiver()
        outer=wrap_callback(recorder,'canvas refresh',lambda:QCoreApplication.sendEvent(receiver,QEvent(QEvent.Type.User)))
        outer()
        rows=self.records()
        self.assertEqual(len(rows),1,rows)
        self.assertEqual(rows[0]['feature'],'canvas refresh')

    def test_document_prompt_object_name_and_exception_are_never_serialized(self):
        canary='PCS_PRIVATE_CANARY_7df863f1'
        class Receiver(QObject):
            def event(self,event):
                if event.type()==QEvent.Type.User:
                    time.sleep(.060);return True
                return super().event(event)
        receiver=Receiver();receiver.setObjectName(canary)
        receiver.state={'uses':{'root':{'prompt':canary}},'document_path':canary,'prompt':canary}
        QCoreApplication.sendEvent(receiver,QEvent(QEvent.Type.User))
        rows=self.records();self.assertEqual(len(rows),1)
        self.assertNotIn(canary,json.dumps(rows))
        self.assertEqual(set(rows[0]),{'schema','time','duration_ms','event_type','feature','object_class'})
        self.assertEqual(rows[0]['object_class'],'unregistered')
        with self.assertRaises(ValueError):wrap_callback(self.recorder,canary,lambda:None)

    def test_rotation_bounds_and_shutdown_drain(self):
        writer=JsonlWriter(self.data/'rotation',max_bytes=600,backups=2,capacity=100)
        record=dict(schema=1,time='2026-11-12T00:00:00Z',duration_ms=60.,event_type='Timer',feature='stage pump',object_class='QTimer')
        for _ in range(30):writer.submit(record)
        writer.close();writer.close()
        files=list(writer.path.parent.glob('*.jsonl'))
        self.assertEqual(len(files),3)
        self.assertFalse(writer.thread.is_alive());self.assertFalse(writer.failed)
        for file in files:
            self.assertLessEqual(file.stat().st_size,600)
            for line in file.read_text().splitlines():self.assertEqual(json.loads(line)['schema'],1)

    def test_overflow_is_bounded_and_reported(self):
        gate=threading.Event();entered=threading.Event();original=JsonlWriter._write
        def slow(writer,record):entered.set();gate.wait(2);original(writer,record)
        with patch.object(JsonlWriter,'_write',slow):
            writer=JsonlWriter(self.data/'overflow',capacity=2)
            record=dict(schema=1,time='2026-11-12T00:00:00Z',duration_ms=60.,event_type='Timer',feature='stage pump',object_class='QTimer')
            writer.submit(record);self.assertTrue(entered.wait(1))
            for _ in range(20):writer.submit(record)
            self.assertEqual(writer.queue.qsize(),2);self.assertEqual(writer.dropped,18)
            gate.set();writer.close()
        rows=[json.loads(line) for line in writer.path.read_text().splitlines()]
        self.assertEqual(sum(r.get('dropped_count',0) for r in rows),18)

    def test_io_failure_does_not_escape_to_gui(self):
        with patch.object(JsonlWriter,'_write',side_effect=OSError('private path')):
            writer=JsonlWriter(self.data/'failure')
            writer.submit({'schema':1});writer.close()
        self.assertTrue(writer.failed);self.assertFalse(writer.path.exists())

    def test_disabled_factory_is_stock_and_creates_no_files(self):
        # Separate process: QApplication is a singleton, so never compare two
        # modes by toggling a Python notify override in the same application.
        code='''import sys,json
sys.path[:0]=json.loads(sys.argv[1])
from pathlib import Path
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.performance_recorder import create_application,start_recording,stop_recording
app=create_application([])
assert type(app) is QApplication
start_recording(app,sys.argv[2]);app.processEvents();stop_recording(app)
assert not Path(sys.argv[2]).exists()
assert 'prompt_calculus_studio.performance_callbacks' not in sys.modules
'''
        result=subprocess.run([sys.executable,'-I','-B','-c',code,json.dumps(sys.path),str(self.data/'off')],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_threshold_parser_rejects_nonfinite_and_nonpositive(self):
        parser=argparse.ArgumentParser();add_arguments(parser)
        self.assertEqual(parser.parse_args(['--record-stalls']).record_stalls_threshold,50.)
        self.assertEqual(parser.parse_args(['--record-stalls-threshold','12.5']).record_stalls_threshold,12.5)
        for value in ('nan','inf','-1','0'):
            with self.assertRaises(SystemExit):parser.parse_args(['--record-stalls-threshold',value])
