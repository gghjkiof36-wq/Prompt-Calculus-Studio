"""Typed icon cache pixels and ownership, with synthetic PNG/JPEG/WebP only."""
import hashlib
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PySide6.QtCore import QEvent,QSize
from PySide6.QtGui import QColor,QIcon,QImage,QImageReader,QPainter,QPixmap
from PySide6.QtWidgets import QApplication,QListWidget,QListWidgetItem
from prompt_calculus_studio.queue_icons import QueueIconCache,read_icon

APP=QApplication.instance() or QApplication([])


def pixels(image):
    image=image.convertToFormat(QImage.Format.Format_RGBA8888)
    return image.size(),image.devicePixelRatio(),hashlib.sha256(image.constBits()).hexdigest()


class QueueIconTests(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.root=Path(self.area.name)
        self.window=SimpleNamespace(store=SimpleNamespace(directory=self.root),state={'workspace':'original'},
                                    canvas_edit_context=object(),is_closed=lambda:False)
        self.listing=QListWidget();self.page=0
        self.view=QueueIconCache(self.listing,self.window,lambda:self.page)

    def tearDown(self):
        self.listing.deleteLater();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.area.cleanup()

    def source(self,fmt='png',width=1024,height=768,alpha=False):
        path=self.root/f'{width}-{height}-{alpha}.{fmt}'
        image=QImage(width,height,QImage.Format.Format_ARGB32);image.fill(QColor(15,45,190,100 if alpha else 255))
        p=QPainter(image)
        for index in range(20):p.fillRect(index*width//23,index*height//24,width//3,height//4,QColor(index*11,210-index*9,70,180 if alpha else 255))
        p.end();self.assertTrue(image.save(str(path)))
        if fmt=='jpg':
            content=path.read_bytes()
            exif=b'Exif\0\0II'+struct.pack('<HIH',42,8,1)+struct.pack('<HHI',274,3,1)+struct.pack('<H',6)+b'\0\0'+b'\0'*4
            path.write_bytes(content[:2]+b'\xff\xe1'+struct.pack('>H',len(exif)+2)+exif+content[2:])
        return path

    def rows(self,count=16,width=1024,height=768):
        original=self.source(width=width,height=height);rows=[]
        for n in range(count):
            path=self.root/f'item-{n}.png';path.write_bytes(original.read_bytes())
            target=QListWidgetItem(str(n));self.listing.addItem(target)
            rows.append((str(n),target,path,dict(sha256=str(n),width=width,height=height)))
        return rows

    def test_first_and_cached_icons_match_old_pixels_at_each_dpr(self):
        for fmt in ('png','jpg','webp'):
            for width,height in ((87,43),(256,192),(1024,768)):
                for alpha in (False,True) if fmt!='jpg' else (False,):
                    path=self.source(fmt,width,height,alpha)
                    reader=QImageReader(str(path));reader.setScaledSize(QSize(52,52));expected=QIcon(QPixmap.fromImage(reader.read()))
                    for dpr in (1.,1.5,2.):
                        with self.subTest(format=fmt,size=(width,height),alpha=alpha,dpr=dpr):
                            target=QListWidgetItem();self.listing.addItem(target)
                            row=('pixel',target,path,dict(width=width,height=height))
                            self.view.load([row]);first=pixels(target.icon().pixmap(QSize(52,52),dpr).toImage())
                            self.view.load([row]);cached=pixels(target.icon().pixmap(QSize(52,52),dpr).toImage())
                            self.assertEqual(first,pixels(expected.pixmap(QSize(52,52),dpr).toImage()))
                            self.assertEqual(cached,first)

    def test_status_reuses_each_entry_and_stamp_replacement_reads_only_one(self):
        rows=self.rows()
        with patch('prompt_calculus_studio.queue_icons.read_icon',wraps=read_icon) as reader:
            self.view.load(rows);self.assertEqual(reader.call_count,16)
            for row in rows:row[1].setText('status changed')
            self.view.load(rows);self.assertEqual(reader.call_count,16)
            image=QImage(1024,768,QImage.Format.Format_RGB32);image.fill(QColor('green'));image.save(str(rows[0][2]))
            self.view.load(rows);self.assertEqual(reader.call_count,17)
        self.assertEqual(rows[0][1].icon().pixmap(52,52).toImage().pixelColor(0,0),QColor('green'))

    def test_owner_page_and_dpr_changes_invalidate_cache(self):
        rows=self.rows(2);self.view.load(rows)
        with patch('prompt_calculus_studio.queue_icons.read_icon',wraps=read_icon) as reader:
            self.page=1;self.view.load(rows);self.assertEqual(reader.call_count,2)
            self.window.canvas_edit_context=object();self.view.load(rows);self.assertEqual(reader.call_count,4)
            with patch.object(self.listing,'devicePixelRatioF',return_value=2.):
                self.view.load(rows);self.assertEqual(reader.call_count,6)

    def test_removed_rows_and_missing_images_do_not_keep_stale_icons(self):
        rows=self.rows(2);self.view.load(rows)
        self.listing.takeItem(0);self.view.load([rows[1]])
        self.assertEqual(set(self.view.cache),{'1'})
        self.view.load([('1',rows[1][1],None,None)])
        self.assertTrue(rows[1][1].icon().isNull());self.assertFalse(self.view.cache)

    def test_cheap_images_keep_original_reader_without_stamp_overhead(self):
        rows=self.rows(8,256,192)
        with patch('prompt_calculus_studio.queue_icons.stamp',side_effect=AssertionError('cheap stamp')):
            self.view.load(rows)
        self.assertFalse(self.view.cache)
        self.assertTrue(all(not row[1].icon().isNull() for row in rows))

    def test_closed_window_never_reads(self):
        rows=self.rows(1);self.view.load(rows);self.window.is_closed=lambda:True
        with patch('prompt_calculus_studio.queue_icons.read_icon',side_effect=AssertionError('late read')):
            self.view.load(rows)
        self.assertFalse(self.view.cache)
