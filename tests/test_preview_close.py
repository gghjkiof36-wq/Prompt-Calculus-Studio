"""Short preview jobs participate in the real Window transaction close barrier."""
import threading
from unittest.mock import patch

from PySide6.QtGui import QImage, QPixmap, QColor
from _qt_wait import wait_until
from _qt_cleanup import close_window
import test_close_barrier as fixtures
from prompt_calculus_studio import preview_loader
from prompt_calculus_studio.image_preview import SourcePreview,load_sources
from prompt_calculus_studio.preview_requests import WORKER_BATCH_MIN
from prompt_calculus_studio.canvas_results import CanvasResults


class PreviewCloseTests(fixtures.CloseBarrierTests):
    def held_loader(self):
        gate=threading.Event();entered=threading.Event()
        self.addCleanup(gate.set)
        def read(key):
            entered.set()
            if not gate.wait(3):raise TimeoutError('close test did not release reader')
            return preview_loader.decode(key)
        loader=preview_loader.PreviewLoader(reader=read)
        self.w._preview_loader=loader
        return loader,gate,entered

    def assert_closes(self,loader,gate,entered):
        self.assertTrue(wait_until(entered.is_set))
        self.assertFalse(self.w.close())
        self.assertTrue(self.w.closing)
        self.assertTrue(loader.closed)
        self.assertFalse(getattr(self.w,'_close_completed',False))
        self.assertEqual(self.w.store.db.execute('SELECT 1').fetchone(),(1,))
        gate.set();close_window(self.w)
        self.assertFalse(loader.shutdown())
        self.assertEqual(loader.pool.activeThreadCount(),0)
        self.assertFalse(loader.jobs)
        self.assertFalse(loader.runnables)

    def test_source_card_close_while_reading(self):
        loader,gate,entered=self.held_loader()
        image=QImage(1024,768,QImage.Format.Format_RGB32);image.fill(QColor('red'))
        path=self.root/'large.bmp';self.assertTrue(image.save(str(path)))
        cards=[SourcePreview(self.w.store,self.w) for _ in range(WORKER_BATCH_MIN)]
        for card in cards:card.setParent(self.w)
        load_sources([(card,path,dict(width=1024,height=768)) for card in cards])
        self.assertTrue(all(card.image.isNull() for card in cards))
        self.assert_closes(loader,gate,entered)
        self.assertTrue(all(card.image.isNull() for card in cards))

    def test_result_card_close_while_reading(self):
        loader,gate,entered=self.held_loader()
        card=CanvasResults(self.w);card.setParent(self.w)
        card.preview.set_pictures([QPixmap()])
        card.schedule_pictures([(0,self.source,'',1536,1536,'fit')],
                               [dict(path=str(self.source),name='fixture')])
        self.assert_closes(loader,gate,entered)
        self.assertTrue(card.preview.pictures[0].isNull())

    def test_close_cancels_never_started_and_keeps_accepted_write(self):
        loader,gate,entered=self.held_loader()
        card=CanvasResults(self.w);card.setParent(self.w)
        card.preview.set_pictures([QPixmap() for _ in range(8)])
        card.schedule_pictures([(i,self.source,str(i),128,128,'fit') for i in range(8)],
                               [dict(path=str(self.source),name='fixture') for _ in range(8)])
        self.assertTrue(wait_until(entered.is_set));self.assertTrue(loader.pending)
        receipts=[];held=[]
        from prompt_calculus_studio.job_outcomes import JobOutcome
        with patch.object(self.w.jobs.pool,'start',side_effect=held.append):
            self.assertTrue(self.w.jobs.start('accepted writer',lambda cancel:JobOutcome.completed('receipt'),receipts.append))
            outcome=held[0].function(held[0].cancel)
            self.assertFalse(self.w.close());self.assertFalse(loader.pending)
            held[0].signals.finished.emit(outcome.value,outcome.message)
            self.assertEqual(receipts,['receipt'])
        gate.set();close_window(self.w)
        self.assertFalse(loader.shutdown());self.assertLessEqual(loader.counts['decodes'],loader.limits.workers)
