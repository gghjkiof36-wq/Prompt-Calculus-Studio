"""Display-only concurrency: quotas, identity fences and cooperative teardown."""
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QCoreApplication
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication, QWidget
from _qt_wait import wait_until, wait_ms
from prompt_calculus_studio.preview_requests import DecodeKey, Identity, Limits, stamp
from prompt_calculus_studio.preview_loader import PreviewLoader, PreviewAdapter, decode, close_window

APP = QApplication.instance() or QApplication([])


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.root = Path(self.area.name)
        self.path = self.root/'a.png'
        self.image = QImage(48,32,QImage.Format.Format_ARGB32)
        self.image.fill(QColor('red'));self.image.save(str(self.path))
        self.loaders=[];self.gates=[];self.widgets=[]

    def tearDown(self):
        for gate in self.gates:gate.set()
        for loader in self.loaders:
            self.assertTrue(wait_until(lambda:not loader.shutdown(),3000))
            self.assertEqual(loader.pool.activeThreadCount(),0)
            self.assertFalse(loader.runnables)
            self.assertEqual(loader.queue.reserved_bytes,0)
            self.assertEqual(loader.queue.reserved_count,0)
            self.assertEqual(loader.queue.decoding_bytes,0)
        from shiboken6 import isValid
        for widget in self.widgets:
            if isValid(widget):widget.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.area.cleanup()

    def loader(self,**kwargs):
        loader=PreviewLoader(**kwargs);self.loaders.append(loader);return loader

    def key(self,path=None,size=48):
        path=path or self.path
        return DecodeKey((1,2,'workspace'),str(path),stamp(path),'digest',size,size)

    def identity(self,key=None,consumer=1,serial=1,card=0):
        key=key or self.key()
        return Identity(key.scope,consumer,serial,card,key)

    def gate_reader(self):
        gate=threading.Event();self.gates.append(gate)
        entered=threading.Event();threads=[]
        def reader(key):
            threads.append(threading.get_ident());entered.set()
            if not gate.wait(3):raise TimeoutError()
            return decode(key)
        return gate,entered,threads,reader

    def test_deduplicate_and_size_key_and_byte_lru(self):
        gate,entered,threads,reader=self.gate_reader()
        loader=self.loader(reader=reader,limits=Limits(cache_bytes=48*32*4))
        values=[]
        for consumer in (1,2):
            self.assertTrue(loader.request(self.identity(consumer=consumer),'source',lambda *v:values.append(v)))
        self.assertTrue(wait_until(entered.is_set));gate.set()
        self.assertTrue(wait_until(lambda:len(values)==2))
        self.assertEqual(loader.counts['decodes'],1)
        self.assertNotIn(threading.get_ident(),threads)
        loader.request(self.identity(consumer=3),'source',lambda *v:values.append(v))
        self.assertEqual(len(values),3);self.assertEqual(loader.counts['decodes'],1)
        loader.request(self.identity(self.key(size=24)),'source',lambda *v:values.append(v))
        self.assertTrue(wait_until(lambda:len(values)==4))
        self.assertEqual(loader.counts['decodes'],2)
        self.assertLessEqual(loader.cache_bytes,loader.limits.cache_bytes)

    def test_pending_count_bytes_and_cancel_never_started(self):
        gate,entered,threads,reader=self.gate_reader()
        cost=self.key().reservation
        loader=self.loader(reader=reader,limits=Limits(workers=1,pending_count=2,pending_bytes=cost))
        got=[]
        loader.request(self.identity(),'result',lambda *v:got.append(v))
        self.assertTrue(wait_until(entered.is_set))
        second=self.identity(self.key(size=47),consumer=2)
        self.assertTrue(loader.request(second,'result',lambda *v:got.append(v)))
        self.assertFalse(loader.request(self.identity(self.key(size=46),consumer=3),'result',lambda *v:got.append(v)))
        loader.cancel(2);self.assertEqual(loader.pending_bytes,0)
        gate.set();self.assertTrue(wait_until(lambda:len(got)==1))
        self.assertEqual(loader.counts['decodes'],1)

    def test_capacity_retries_reuse_stamp_until_request_is_cancelled(self):
        gate,entered,threads,reader=self.gate_reader()
        loader=self.loader(reader=reader,limits=Limits(workers=1,pending_count=1))
        loader.request(self.identity(),'result',lambda *_:None)
        self.assertTrue(wait_until(entered.is_set))
        loader.request(self.identity(self.key(size=47),consumer=2),'result',lambda *_:None)
        widget=QWidget();self.widgets.append(widget)
        store=SimpleNamespace(directory=self.root)
        window=SimpleNamespace(store=store,state={},canvas_edit_context=None,is_closed=lambda:False,_preview_loader=loader)
        adapter=PreviewAdapter(widget,window,store)
        with patch('prompt_calculus_studio.preview_loader.stamp',wraps=stamp) as observed:
            adapter.load([(0,self.path,'',48,48,'fit')])
            for _ in range(10):loader.capacity.emit()
            self.assertEqual(observed.call_count,1)
            self.assertEqual(len(adapter.waiting),1)
            adapter.cancel()
            self.assertIsNone(adapter.prepared)
        gate.set()

    def test_decoding_memory_admission_limits_short_jobs_without_blocking_threads(self):
        gate,entered,threads,reader=self.gate_reader()
        loader=self.loader(reader=reader,limits=Limits(workers=4,decoding_bytes=64*1024*1024))
        got=[]
        for n in range(3):
            key=DecodeKey((1,2,'workspace'),str(self.path),stamp(self.path),str(n),48,48,raw_bytes=40*1024*1024)
            loader.request(self.identity(key,card=n),'source',lambda *v:got.append(v))
        self.assertTrue(wait_until(entered.is_set))
        self.assertEqual(len(loader.running),1)
        self.assertEqual(len(loader.pending),2)
        self.assertEqual(loader.queue.decoding_bytes,40*1024*1024)
        gate.set();self.assertTrue(wait_until(lambda:len(got)==3))
        self.assertEqual(loader.counts['decodes'],3)
        self.assertLessEqual(loader.counts['peak_decoding_bytes'],loader.limits.decoding_bytes)
        self.assertEqual(loader.queue.decoding_bytes,0)

    def test_completions_bounded_when_gui_is_not_consuming(self):
        loader=self.loader(limits=Limits(workers=2,completed_count=2))
        for n in range(20):loader.request(self.identity(self.key(size=20+n),card=n),'result',lambda *_:None)
        self.assertTrue(wait_until(lambda:not loader.jobs,3000))
        self.assertLessEqual(loader.counts['peak_completed'],2)
        self.assertLessEqual(loader.counts['peak_completed_bytes'],loader.limits.completed_bytes)

    def test_count_limit_and_visible_requests_overtake_pending_hidden_cards(self):
        gate,entered,threads,reader=self.gate_reader();order=[]
        def ordered(key):order.append(key.width);return reader(key)
        loader=self.loader(reader=ordered,limits=Limits(workers=1,pending_count=2))
        got=[]
        loader.request(self.identity(),'result',lambda *v:got.append(v))
        self.assertTrue(wait_until(entered.is_set))
        self.assertTrue(loader.request(self.identity(self.key(size=47),card=1),'result',lambda *v:got.append(v),False))
        self.assertTrue(loader.request(self.identity(self.key(size=46),card=2),'result',lambda *v:got.append(v),True))
        self.assertFalse(loader.request(self.identity(self.key(size=45),card=3),'result',lambda *_:None))
        gate.set();self.assertTrue(wait_until(lambda:len(got)==3))
        self.assertEqual(order,[48,46,47])

    def test_completed_bytes_backpressure_while_gui_does_not_dispatch(self):
        first=threading.Event();calls=[]
        def large(key):
            image=QImage(2048,2048,QImage.Format.Format_RGBA64);image.fill(QColor('red'))
            calls.append(key.width)
            first.set()
            return image,'ready'
        loader=self.loader(reader=large,limits=Limits(workers=1,completed_bytes=32*1024*1024,cache_bytes=0))
        got=[]
        for n in range(2):
            key=DecodeKey((1,2,'workspace'),str(self.path),stamp(self.path),str(n),2048,2048)
            loader.request(self.identity(key,card=n),'source',lambda *v:got.append(v))
        # Starve GUI dispatch. Capacity is reserved BEFORE the first read;
        # the second remains GUI-pending, never blocking a pool thread.
        self.assertTrue(first.wait(2))
        self.assertEqual(len(calls),1)
        self.assertEqual(loader.queue.reserved_count,1)
        self.assertEqual(loader.queue.reserved_bytes,32*1024*1024)
        self.assertTrue(wait_until(lambda:len(got)==2))
        self.assertEqual(loader.counts['peak_completed_bytes'],32*1024*1024)

    def test_short_jobs_are_owned_until_native_pool_returns(self):
        loader=self.loader(limits=Limits(workers=2,completed_count=2))
        got=[]
        for n in range(40):
            loader.request(self.identity(self.key(size=9+n),card=n),'result',lambda *v:got.append(v))
        self.assertTrue(wait_until(lambda:len(got)==40,3000))
        self.assertTrue(wait_until(lambda:loader.pool.activeThreadCount()==0))
        loader.pump()
        self.assertFalse(loader.runnables)
        self.assertFalse(loader.jobs)
        self.assertEqual(loader.queue.reserved_count,0)

    def adapter(self,loader):
        widget=QWidget();self.widgets.append(widget)
        store=SimpleNamespace(directory=self.root)
        window=SimpleNamespace(store=store,state={'workspace':'one'},canvas_edit_context=object(),
                               is_closed=lambda:False,_preview_loader=loader)
        adapter=PreviewAdapter(widget,window,store,'source')
        return widget,window,adapter

    def test_a_b_a_stale_and_delete_and_epoch(self):
        gate,entered,threads,reader=self.gate_reader()
        loader=self.loader(reader=reader)
        widget,window,adapter=self.adapter(loader)
        blue=self.root/'b.png';image=QImage(self.image);image.fill(QColor('blue'));image.save(str(blue))
        got=[];adapter.ready.connect(lambda _,image,status:got.append(image.pixelColor(0,0)))
        spec=lambda p:[(0,p,'',48,48,'fit')]
        adapter.load(spec(self.path));self.assertTrue(wait_until(entered.is_set))
        adapter.load(spec(blue));adapter.load(spec(self.path));gate.set()
        self.assertTrue(wait_until(lambda:len(got)==1));self.assertEqual(got,[QColor('red')])
        loader.cache.clear();loader.cache_bytes=0
        adapter.load(spec(blue));window.canvas_edit_context=object()
        self.assertTrue(wait_until(lambda:not loader.jobs));self.assertEqual(got,[QColor('red')])
        adapter.load(spec(self.path));adapter.cancel();widget.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.assertTrue(wait_until(lambda:not loader.jobs))

    def test_hide_cancels_pending_and_show_resumes(self):
        gate,entered,threads,reader=self.gate_reader()
        loader=self.loader(reader=reader,limits=Limits(workers=1))
        widget,window,adapter=self.adapter(loader)
        got=[];adapter.ready.connect(lambda *v:got.append(v))
        widget.show();adapter.load([(i,self.path,'',20+i,20+i,'fit') for i in range(8)])
        self.assertTrue(wait_until(entered.is_set));widget.hide()
        self.assertFalse(loader.pending);gate.set()
        self.assertTrue(wait_until(lambda:not loader.jobs));self.assertFalse(got)
        widget.show();self.assertTrue(wait_until(lambda:len(got)==8))

    def test_close_keeps_running_read_until_done_without_gui_join(self):
        gate,entered,threads,reader=self.gate_reader()
        loader=self.loader(reader=reader)
        widget,window,adapter=self.adapter(loader)
        got=[];adapter.ready.connect(lambda *v:got.append(v))
        adapter.load([(0,self.path,'',48,48,'fit')]);self.assertTrue(wait_until(entered.is_set))
        self.assertTrue(close_window(window));self.assertTrue(loader.closed)
        self.assertFalse(loader.request(self.identity(),'source',lambda *_:None))
        gate.set();self.assertTrue(wait_until(lambda:not close_window(window)))
        self.assertFalse(got);self.assertFalse(loader.cache);self.assertFalse(loader.pending)

    def test_hidden_owner_change_does_not_resume_the_old_specs(self):
        gate,entered,threads,reader=self.gate_reader()
        loader=self.loader(reader=reader)
        widget,window,adapter=self.adapter(loader)
        got=[];adapter.ready.connect(lambda *v:got.append(v))
        widget.show();adapter.load([(0,self.path,'',48,48,'fit')])
        self.assertTrue(wait_until(entered.is_set));widget.hide()
        window.canvas_edit_context=object()
        gate.set();self.assertTrue(wait_until(lambda:not loader.jobs))
        widget.show()
        self.assertFalse(adapter.specs)
        self.assertFalse(adapter.waiting)
        self.assertFalse(got)

    def test_worker_failure_does_not_leak_slot(self):
        def fail(key):raise OSError('private name must not escape')
        loader=self.loader(reader=fail);got=[]
        loader.request(self.identity(),'result',lambda *v:got.append(v))
        self.assertTrue(wait_until(lambda:bool(got)))
        self.assertEqual(got[0][-1],'failed');self.assertFalse(loader.running)
        loader.reader=decode
        loader.request(self.identity(),'result',lambda *v:got.append(v))
        self.assertTrue(wait_until(lambda:len(got)==2));self.assertEqual(got[-1][-1],'ready')

    def test_missing_corrupt_and_replacement_during_read(self):
        for path in (self.root/'missing',self.root/'bad.png'):
            if path.name=='bad.png':path.write_bytes(b'broken')
            image,status=decode(self.key(path));self.assertTrue(image.isNull())
        from prompt_calculus_studio import preview_loader
        real=preview_loader.QImageReader
        key=self.key()
        class Reader:
            def __init__(self,*args):self.reader=real(*args)
            def __getattr__(self,name):return getattr(self.reader,name)
            def read(self):
                image=self.reader.read();self.reader=None
                self_path=Path(key.path);self_path.write_bytes(b'replaced')
                return image
        with patch.object(preview_loader,'QImageReader',Reader):
            image,status=decode(key)
        self.assertTrue(image.isNull());self.assertEqual(status,'changed')

    def test_recorder_rejects_content_and_nonfinite_numbers(self):
        from prompt_calculus_studio.performance_recorder import StallRecorder
        got=[];recorder=StallRecorder();recorder.writer=SimpleNamespace(submit=got.append)
        recorder.image('decode',consumer='PRIVATE',path='PRIVATE',request='PRIVATE',bytes=-2,
                       pixels=10**1000,queue_ms=float('nan'),duration_ms=1.2)
        self.assertNotIn('PRIVATE',str(got));self.assertEqual(got[0]['bytes'],0)
        self.assertEqual(got[0]['pixels'],2**53-1);self.assertNotIn('queue_ms',got[0])
