"""The shared adapter preserves the old list/detail/helper pixels and selection."""
import hashlib
from pathlib import Path
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QSize
from PySide6.QtGui import QColor,QImage,QPainter,QPixmap
from PySide6.QtWidgets import QApplication,QWidget
from _qt_wait import wait_until
from prompt_calculus_studio.canvas_results import CanvasResults
from prompt_calculus_studio.preview_loader import PreviewAdapter
from prompt_calculus_studio import preview_loader
from prompt_calculus_studio.preview_requests import DecodeKey,stamp
from prompt_calculus_studio.image_preview import SourcePreview
from prompt_calculus_studio.widgets import thumb_icon

APP=QApplication.instance() or QApplication([])


def pixels(image):
    image=image.convertToFormat(QImage.Format.Format_RGBA8888)
    return image.width(),image.height(),hashlib.sha256(image.constBits()).hexdigest()


class LegacyFormatsTests(unittest.TestCase):
    def setUp(self):
        # Force both adapter transforms for pixel/owner equivalence. Production
        # threshold routing is covered independently in test_preview_routing.
        route=patch('prompt_calculus_studio.preview_requests.WORKER_BATCH_MIN',1)
        route.start();self.addCleanup(route.stop)
        self.area=tempfile.TemporaryDirectory();self.root=Path(self.area.name);self.widgets=[]
        self.store=SimpleNamespace(directory=self.root);self.collection=None;self.chosen=None
        self.window=SimpleNamespace(store=self.store,state={'workspace':'fixture','multi_output':{
            'version':3,'connections':[dict(source='input',destination='preview',kind='image')]},
            'canvas_functions':{'images':{'input':{}}}},is_closed=lambda:False,canvas_edit_context=object())
        self.window.canvas=SimpleNamespace(functions=SimpleNamespace(current_source=lambda _:self.chosen),image_previews={})
        self.window.comfy=SimpleNamespace(images=SimpleNamespace(collection=lambda _:self.collection))

    def tearDown(self):
        loader=getattr(self.window,'_preview_loader',None)
        if loader:self.assertTrue(wait_until(lambda:not loader.shutdown(),3000))
        for widget in self.widgets:widget.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.area.cleanup()

    def sources(self,fmt,count):
        items=[]
        for n in range(count):
            path=self.root/f'{n}.{fmt}'
            image=QImage(512,384,QImage.Format.Format_ARGB32);image.fill(QColor(30+n*80,70,140,110))
            painter=QPainter(image)
            painter.fillRect(43,51,177,203,QColor(230,20+n*30,50,220));painter.end()
            self.assertTrue(image.save(str(path)))
            if fmt=='jpg':
                content=path.read_bytes()
                exif=b'Exif\0\0II'+struct.pack('<HIH',42,8,1)+struct.pack('<HHI',274,3,1)+struct.pack('<H',6)+b'\0\0'+b'\0'*4
                path.write_bytes(content[:2]+b'\xff\xe1'+struct.pack('>H',len(exif)+2)+exif+content[2:])
            items.append(dict(relative=path.name,name=f'image {n}',width=512,height=384,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),reference={'image':str(n)}))
        self.collection=dict(collection=f'{fmt}-{count}',items=items);self.chosen=items[-1]
        return items

    def panel(self):
        panel=CanvasResults(self.window,'preview');self.widgets.append(panel);return panel

    def snapshot(self,panel):
        images=panel.preview.pictures or [panel.preview.picture]
        return dict(images=[pixels(p.toImage()) for p in images],selected=panel.images.currentRow(),
            icons=[pixels(panel.images.item(n).icon().pixmap(QSize(72,56)).toImage()) for n in range(panel.images.count())],
            paths=[r['path'] for r in panel.input_records])

    def test_legacy_list_batch_and_detail_match_the_original_sync_branches(self):
        for fmt in ('png','jpg','webp','bmp'):
            for count in (1,2):
                with self.subTest(format=fmt,count=count):
                    self.sources(fmt,count)
                    with patch('prompt_calculus_studio.canvas_results.worker_batch',return_value=set()):old=self.panel()
                    expected=self.snapshot(old)
                    candidate=self.panel()
                    self.assertTrue(wait_until(lambda:not candidate.preview.pending and
                        all(not candidate.images.item(n).icon().isNull() for n in range(count)),3000))
                    self.assertEqual(self.snapshot(candidate),expected)

    def test_shared_thumbnail_adapter_matches_the_unchanged_helper(self):
        widget=QWidget();self.widgets.append(widget)
        adapter=PreviewAdapter(widget,self.window,self.store,'legacy');received=[]
        adapter.ready.connect(lambda slot,image,status:received.append((image,status)))
        for fmt in ('png','jpg','webp','bmp'):
            with self.subTest(format=fmt):
                row=self.sources(fmt,1)[0];expected=thumb_icon(self.store,row['relative'],72).pixmap(QSize(72,72))
                received.clear()
                adapter.load([(0,self.root/row['relative'],'',72,72,'legacy-thumb')])
                self.assertTrue(wait_until(lambda:bool(received)))
                self.assertEqual(received[0][1],'ready')
                actual=QPixmap.fromImage(received[0][0])
                self.assertEqual(pixels(actual.toImage()),pixels(expected.toImage()))

    def test_retained_legacy_list_and_detail_reload_in_a_new_owner(self):
        self.sources('png',1);gate=threading.Event();entered=threading.Event()
        def read(key):
            entered.set()
            if not gate.wait(3):raise TimeoutError()
            return preview_loader.decode(key)
        self.window._preview_loader=preview_loader.PreviewLoader(reader=read)
        try:
            panel=self.panel();self.assertTrue(wait_until(entered.is_set))
            previous=[a.serial for a in (panel.adapter,panel.thumb_adapter)]
            self.window.canvas_edit_context=object();self.window.state['workspace']='new workspace'
            panel.refresh()
            self.assertTrue(all(a.serial>n for a,n in zip((panel.adapter,panel.thumb_adapter),previous)))
            gate.set()
            self.assertTrue(wait_until(lambda:not panel.preview.pending and not panel.images.item(0).icon().isNull()))
            self.assertFalse(panel.preview.picture.isNull())
            self.assertEqual(panel.images.currentRow(),0)
        finally:gate.set()

    def test_corrupt_and_missing_files_preserve_result_slots_and_source_placeholders(self):
        self.window.state['multi_output']['version']=7
        self.window.comfy.images.source=lambda _:None
        for fmt in ('png','jpg','webp','bmp'):
            with self.subTest(format=fmt):
                rows=self.sources(fmt,3)
                (self.root/rows[1]['relative']).write_bytes(b'broken')
                (self.root/rows[2]['relative']).unlink()
                with patch('prompt_calculus_studio.canvas_results.worker_batch',return_value=set()):old=self.panel()
                expected=pixels(old.preview.pictures[0].toImage())
                candidate=self.panel()
                self.assertTrue(wait_until(lambda:not candidate.preview.pending))
                self.assertEqual([p.isNull() for p in candidate.preview.pictures],[False,True,True])
                self.assertEqual(pixels(candidate.preview.pictures[0].toImage()),expected)
                self.assertEqual([r['path'] for r in candidate.input_records],
                                 [str(self.root/r['relative']) for r in rows])
                cards=[]
                for row in rows:
                    card=SourcePreview(self.store,self.window);self.widgets.append(card);cards.append(card)
                    card.load_path(self.root/row['relative'],row)
                self.assertTrue(wait_until(lambda:all(c.pending_path is None for c in cards) and
                                           not self.window._preview_loader.jobs))
                self.assertEqual([c.image.isNull() for c in cards],[False,True,True])
                self.assertEqual([c.text() for c in cards[1:]],['圖片無法讀取']*2)

    def test_each_format_replaced_after_read_is_rejected(self):
        real=preview_loader.QImageReader
        for fmt in ('png','jpg','webp','bmp'):
            with self.subTest(format=fmt):
                row=self.sources(fmt,1)[0];path=self.root/row['relative']
                key=DecodeKey((1,2,'fixture'),str(path),stamp(path),'',128,128)
                class Reader:
                    def __init__(self,*args):self.reader=real(*args)
                    def __getattr__(self,name):return getattr(self.reader,name)
                    def read(self):
                        image=self.reader.read();self.reader=None
                        path.write_bytes(b'replaced after read')
                        return image
                with patch.object(preview_loader,'QImageReader',Reader):image,status=preview_loader.decode(key)
                self.assertTrue(image.isNull());self.assertEqual(status,'changed')

    def test_live_receipt_size_reuse_preserves_scaled_pixels_and_exif(self):
        from prompt_calculus_studio import canvas_results
        self.window.state['multi_output']['version']=7
        self.window.comfy.images.source=lambda _:None
        pending={}
        self.window.comfy.events=SimpleNamespace(results=SimpleNamespace(pending=pending,
            preview=lambda _:None,awaiting=lambda _:False,repaired=SimpleNamespace(connect=lambda *_:None)))
        real=canvas_results.QImageReader
        for fmt in ('png','jpg','webp','bmp'):
            with self.subTest(format=fmt):
                rows=self.sources(fmt,2)
                for row in rows:row['reference']['attempt']='accepted'
                with patch.object(canvas_results,'worker_batch',return_value=set()):old=self.panel()
                expected=[pixels(p.toImage()) for p in old.preview.pictures]
                pending['accepted']={}
                class Reader:
                    def __init__(self,*args):self.reader=real(*args)
                    def __getattr__(self,name):return getattr(self.reader,name)
                    def size(self):raise AssertionError('accepted receipt reopened header on GUI')
                with patch.object(canvas_results,'QImageReader',Reader):candidate=self.panel()
                self.assertEqual([pixels(p.toImage()) for p in candidate.preview.pictures],expected)
                pending.clear()
