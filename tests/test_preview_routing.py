"""The small-image call sites stay synchronous; mixed slots retain identity."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor,QImage
from PySide6.QtWidgets import QApplication
from _qt_wait import wait_until
from prompt_calculus_studio.preview_requests import use_worker,worker_batch,WORKER_ENCODED_BYTES,WORKER_BMP_BYTES,WORKER_BATCH_MIN
from prompt_calculus_studio import preview_loader
from prompt_calculus_studio.image_preview import SourcePreview,load_sources
from prompt_calculus_studio.canvas_results import CanvasResults

APP=QApplication.instance() or QApplication([])


class RoutingTests(unittest.TestCase):
    def setUp(self):
        # Identity tests deliberately exercise a worker even with one source;
        # the separate batch tests below restore the production threshold.
        route=patch('prompt_calculus_studio.preview_requests.WORKER_BATCH_MIN',1)
        route.start();self.addCleanup(route.stop)
        self.area=tempfile.TemporaryDirectory();self.root=Path(self.area.name)
        self.small=self.root/'small.png';self.large=self.root/'large.bmp'
        for path,width,height,color in ((self.small,96,64,'blue'),(self.large,1024,768,'red')):
            image=QImage(width,height,QImage.Format.Format_RGB32);image.fill(QColor(color));image.save(str(path))
        self.store=SimpleNamespace(directory=self.root)
        self.items=[];self.widgets=[];self.gate=threading.Event();self.entered=threading.Event()
        self.window=SimpleNamespace(store=self.store,state={'workspace':'one','multi_output':{'version':7,
            'connections':[dict(source='source',destination='preview',kind='image')]}},
            is_closed=lambda:False,canvas_edit_context=object(),comfy=SimpleNamespace(images=SimpleNamespace(
                collection=lambda _:dict(items=self.items),source=lambda _:None)))

    def tearDown(self):
        self.gate.set()
        loader=getattr(self.window,'_preview_loader',None)
        if loader:self.assertTrue(wait_until(lambda:not loader.shutdown(),3000))
        for widget in self.widgets:widget.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.area.cleanup()

    def held(self):
        def read(key):
            self.entered.set()
            if not self.gate.wait(3):raise TimeoutError()
            return preview_loader.decode(key)
        self.window._preview_loader=preview_loader.PreviewLoader(reader=read)

    def source(self):
        widget=SourcePreview(self.store,self.window);self.widgets.append(widget);return widget

    def test_encoded_thresholds_do_not_open_header(self):
        for name,limit in (('sample.png',WORKER_ENCODED_BYTES),('sample.BMP',WORKER_BMP_BYTES)):
            self.assertFalse(use_worker(name,limit))
            self.assertTrue(use_worker(name,limit+1))
        self.assertFalse(use_worker(self.root/'absent'))

    def test_small_source_stays_at_original_sync_call_site(self):
        widget=self.source()
        with patch.object(preview_loader.PreviewLoader,'request',side_effect=AssertionError('small queued')):
            widget.load_path(self.small)
        self.assertEqual(widget.image.pixelColor(0,0),QColor('blue'))
        self.assertIsNone(widget.adapter)
        self.assertFalse(hasattr(self.window,'_preview_loader'))

    def test_mixed_result_slots_are_not_completion_order(self):
        self.held()
        card=CanvasResults(self.window,'preview');self.widgets.append(card)
        for n,path in enumerate((self.small,self.large,self.small)):
            self.items.append(dict(relative=path.name,name=str(n),sha256=str(n),reference={'prompt_id':'p','index':n}))
        card.refresh()
        self.assertEqual([p.isNull() for p in card.preview.pictures],[False,True,False])
        self.assertTrue(wait_until(self.entered.is_set));self.gate.set()
        self.assertTrue(wait_until(lambda:not card.preview.pending))
        self.assertEqual([p.toImage().pixelColor(0,0) for p in card.preview.pictures],
                         [QColor('blue'),QColor('red'),QColor('blue')])
        self.assertEqual([r['name'] for r in card.input_records],['0','1','2'])

    def test_source_large_small_large_and_hide_cannot_resurrect_old_spec(self):
        self.held();widget=self.source();widget.show()
        widget.load_path(self.large);self.assertTrue(wait_until(self.entered.is_set))
        widget.load_path(self.small)
        widget.hide();widget.show();self.gate.set()
        loader=self.window._preview_loader
        self.assertTrue(wait_until(lambda:not loader.jobs))
        self.assertEqual(widget.image.pixelColor(0,0),QColor('blue'))
        widget.load_path(self.large)
        self.assertTrue(wait_until(lambda:widget.image.pixelColor(0,0)==QColor('red')))

    def test_same_source_replacement_keeps_pixels_until_ready(self):
        self.held();widget=self.source();widget.load_path(self.small)
        image=QImage(1024,768,QImage.Format.Format_RGB32);image.fill(QColor('red'))
        self.assertTrue(image.save(str(self.small)))
        self.assertTrue(use_worker(self.small))
        widget.load_path(self.small);self.assertTrue(wait_until(self.entered.is_set))
        self.assertEqual(widget.image.pixelColor(0,0),QColor('blue'))
        self.gate.set()
        self.assertTrue(wait_until(lambda:widget.image.pixelColor(0,0)==QColor('red')))

    def test_sync_result_then_other_background_source_does_not_keep_wrong_pixels(self):
        self.held();card=CanvasResults(self.window,'preview');self.widgets.append(card)
        def select(path,token):
            self.items[:]=[dict(relative=path.name,name=token,sha256=token,reference={'prompt_id':token,'index':0})]
            card.refresh()
        select(self.large,'a');self.assertTrue(wait_until(self.entered.is_set));self.gate.set()
        self.assertTrue(wait_until(lambda:not card.preview.pending))
        select(self.small,'b')
        self.assertEqual(card.preview.pictures[0].toImage().pixelColor(0,0),QColor('blue'))
        self.gate.clear();self.entered.clear()
        select(self.large,'a-new-receipt')
        self.assertTrue(card.preview.pictures[0].isNull())
        self.gate.set();self.assertTrue(wait_until(lambda:not card.preview.pending))
        self.assertEqual(card.preview.pictures[0].toImage().pixelColor(0,0),QColor('red'))

    def test_source_cancel_or_close_before_deferred_adapter_creates_no_worker(self):
        widget=self.source();widget.load_path(self.large);widget.load_path(self.small)
        APP.processEvents()
        self.assertIsNone(widget.adapter)
        widget.load_path(self.large);self.window.is_closed=lambda:True
        APP.processEvents()
        self.assertIsNone(widget.adapter)
        self.assertIsNone(widget.pending_path)

    def test_source_request_reuses_the_callers_file_stamp(self):
        self.held();widget=self.source()
        with patch.object(preview_loader,'stamp',wraps=preview_loader.stamp) as observed:
            widget.load_path(self.large,dict(width=1024,height=768))
            self.assertTrue(wait_until(self.entered.is_set))
            self.assertEqual(observed.call_count,0)
        self.gate.set();self.assertTrue(wait_until(lambda:not widget.image.isNull()))

    def test_cold_source_defers_stat_and_rejects_a_file_removed_before_dispatch(self):
        widget=self.source()
        with patch.object(Path,'stat',side_effect=AssertionError('bulk source stat')):
            widget.load_path(self.large,dict(width=1024,height=768))
        self.assertTrue(widget.image.isNull())
        self.assertIsNone(widget.adapter)
        self.large.unlink()
        APP.processEvents()
        self.assertTrue(widget.image.isNull())
        self.assertIsNone(widget.source_key)
        self.assertIsNone(widget.adapter)

    def test_cold_source_replacement_is_stamped_at_dispatch(self):
        widget=self.source()
        widget.load_path(self.large,dict(width=1024,height=768))
        replacement=QImage(1024,768,QImage.Format.Format_RGB32);replacement.fill(QColor('green'))
        self.assertTrue(replacement.save(str(self.large)))
        self.assertTrue(wait_until(lambda:not widget.image.isNull()))
        self.assertEqual(widget.image.pixelColor(0,0),QColor('green'))

    def test_source_owner_change_before_deferred_dispatch_cannot_admit_old_path(self):
        widget=self.source()
        widget.load_path(self.large,dict(width=1024,height=768))
        self.window.canvas_edit_context=object()
        self.window.state['workspace']='different workspace'
        APP.processEvents()
        self.assertIsNone(widget.adapter)
        self.assertIsNone(widget.pending_path)
        self.assertIsNone(widget.source_key)
        self.assertTrue(widget.image.isNull())

    def test_source_same_path_in_a_new_owner_does_not_reuse_a_stale_pending_request(self):
        self.held();widget=self.source()
        widget.load_path(self.large,dict(width=1024,height=768))
        self.assertTrue(wait_until(self.entered.is_set))
        self.window.canvas_edit_context=object()
        widget.load_path(self.large,dict(width=1024,height=768))
        self.gate.set()
        self.assertTrue(wait_until(lambda:not widget.image.isNull()))
        self.assertEqual(widget.image.pixelColor(0,0),QColor('red'))

    def test_result_paths_match_repair_notification_paths_on_windows(self):
        path=self.root/'nested'/'asset.png';path.parent.mkdir();path.write_bytes(self.small.read_bytes())
        self.items[:]=[dict(relative='nested/asset.png',name='source',sha256='image',
            width=96,height=64,reference={'prompt_id':'p','index':0})]
        card=CanvasResults(self.window,'preview');self.widgets.append(card)
        self.assertEqual(card.input_records[0]['path'],str(path))
        with patch.object(card,'refresh') as refreshed:
            card.result_repaired('nested/asset.png')
            refreshed.assert_called_once_with()

    def test_retained_result_card_reloads_same_signature_in_a_new_owner(self):
        self.held()
        self.items[:]=[dict(relative=self.large.name,name='source',sha256='image',
            width=1024,height=768,reference={'prompt_id':'p','index':0})]
        card=CanvasResults(self.window,'preview');self.widgets.append(card)
        self.assertTrue(wait_until(self.entered.is_set))
        previous=card.adapter.serial
        self.window.canvas_edit_context=object();self.window.state['workspace']='new workspace'
        card.refresh()
        self.assertGreater(card.adapter.serial,previous)
        self.gate.set();self.assertTrue(wait_until(lambda:not card.preview.pending))
        self.assertEqual(card.preview.pictures[0].toImage().pixelColor(0,0),QColor('red'))

    def batch_items(self,count):
        return [dict(relative=self.large.name,name=str(n),sha256=str(n),width=1024,height=768,
                     reference={'attempt':'accepted','index':n}) for n in range(count)]

    def test_production_threshold_both_sides_and_mixed_metadata_without_io(self):
        with patch('prompt_calculus_studio.preview_requests.WORKER_BATCH_MIN',WORKER_BATCH_MIN):
            for count in (0,1,WORKER_BATCH_MIN-1,WORKER_BATCH_MIN,WORKER_BATCH_MIN+1):
                with patch('prompt_calculus_studio.preview_requests.file_stat',side_effect=AssertionError('extra stat')):
                    selected=worker_batch(['large.bmp']*count,[dict(width=1024,height=768)]*count)
                self.assertEqual(selected,set(range(count)) if count>=WORKER_BATCH_MIN else set())
            sources=[dict(width=96,height=64)]+[dict(width=1024,height=768)]*WORKER_BATCH_MIN
            self.assertEqual(worker_batch(['fixture.bmp']*len(sources),sources),set(range(1,len(sources))))

    def test_below_production_threshold_result_does_not_enter_scheduler(self):
        with patch('prompt_calculus_studio.preview_requests.WORKER_BATCH_MIN',WORKER_BATCH_MIN):
            self.items[:]=self.batch_items(WORKER_BATCH_MIN-1)
            with patch.object(preview_loader.PreviewLoader,'request',side_effect=AssertionError('legacy queued')):
                card=CanvasResults(self.window,'preview');self.widgets.append(card)
            self.assertIsNone(card.adapter)
            self.assertTrue(all(not p.isNull() for p in card.preview.pictures))

    def test_production_mixed_source_batch_cancel_before_dispatch(self):
        with patch('prompt_calculus_studio.preview_requests.WORKER_BATCH_MIN',WORKER_BATCH_MIN):
            cards=[self.source() for _ in range(WORKER_BATCH_MIN+1)]
            requests=[(c,self.large,dict(width=1024,height=768)) for c in cards[:-1]]
            requests.append((cards[-1],self.small,dict(width=96,height=64)))
            load_sources(requests)
            self.assertTrue(all(c.image.isNull() for c in cards[:-1]))
            self.assertEqual(cards[-1].image.pixelColor(0,0),QColor('blue'))
            for card in cards[:-1]:card.load_path(self.small)
            APP.processEvents()
            self.assertFalse(hasattr(self.window,'_preview_loader'))
            self.assertTrue(all(c.image.pixelColor(0,0)==QColor('blue') for c in cards))

    def test_live_db_fallback_does_not_disable_reopened_result_worker(self):
        with patch('prompt_calculus_studio.preview_requests.WORKER_BATCH_MIN',WORKER_BATCH_MIN):
            self.items[:]=self.batch_items(WORKER_BATCH_MIN)
            fetch=SimpleNamespace(pending={'accepted':{}},preview=lambda _:None,awaiting=lambda _:False,
                                  repaired=SimpleNamespace(connect=lambda *_:None))
            self.window.comfy.events=SimpleNamespace(results=fetch)
            card=CanvasResults(self.window,'preview');self.widgets.append(card)
            self.assertIsNone(card.adapter)
            self.assertTrue(all(not p.isNull() for p in card.preview.pictures))
            fetch.pending.clear();self.held()
            reopened=CanvasResults(self.window,'preview');self.widgets.append(reopened)
            self.assertTrue(wait_until(self.entered.is_set))
            self.assertTrue(all(p.isNull() for p in reopened.preview.pictures))
            self.gate.set();self.assertTrue(wait_until(lambda:not reopened.preview.pending))
            self.assertTrue(all(p.toImage().pixelColor(0,0)==QColor('red') for p in reopened.preview.pictures))

    def test_warm_source_batch_checks_replacement_without_classification(self):
        requests=[(self.source(),self.large,dict(width=1024,height=768)) for _ in range(WORKER_BATCH_MIN)]
        load_sources(requests)
        self.assertTrue(wait_until(lambda:all(not widget.image.isNull() for widget,_,_ in requests)))
        with patch('prompt_calculus_studio.image_preview.worker_batch',side_effect=AssertionError('warm classified')):
            load_sources(requests)
        replacement=QImage(1024,768,QImage.Format.Format_RGB32);replacement.fill(QColor('green'))
        self.assertTrue(replacement.save(str(self.large)))
        load_sources(requests)
        self.assertTrue(wait_until(lambda:all(widget.image.pixelColor(0,0)==QColor('green') for widget,_,_ in requests)))

    def test_v7_empty_source_batch_keeps_icon_without_legacy_placeholder(self):
        from prompt_calculus_studio.source_panel import Preview
        from prompt_calculus_studio.image_preview import source_batch
        widget=Preview(self.store,self.window);self.widgets.append(widget)
        with source_batch():widget.load_path(None)
        self.assertEqual(widget.text(),'')
