"""Decode each first-page image once and invalidate only changed sources."""
import os
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from prompt_calculus_studio.preview_decode import decode_image
import test_workspace_shell as fixture


class GalleryRefreshCostTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    setUp = fixture.WorkspaceShellTests.setUp
    tearDown = fixture.WorkspaceShellTests.tearDown
    events = fixture.WorkspaceShellTests.events

    def test_first_page_revisit_replacement_and_page_bound(self):
        host=self.w.ensure_utility()
        host.catalog.put('album',dict(id='a',name='Images'))
        image=QImage(96,144,QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.darkGreen)
        files=[]
        for i in range(61):
            path=self.root/f'image-{i:03}.png'
            image.save(str(path));files.append(path)
            host.catalog.put('image',dict(id=f'i{i:03}',name=path.name,path=str(path),
                             thumb='',owned=False,metadata={'raw':{}},manual=None),'a')
        with patch('prompt_calculus_studio.preview_decode.decode_image',wraps=decode_image) as decode:
            def thumbnails():return sum(call.args[1]==384 for call in decode.call_args_list)
            self.w.open_route('gallery');self.events()
            self.assertEqual(thumbnails(),60)
            gallery=host.gallery.load()
            gallery.images.setCurrentRow(4)
            selected=gallery.record['id']
            decode.reset_mock()
            self.w.open_route('home');self.w.open_route('gallery');self.events()
            self.assertEqual(decode.call_count,0)
            self.assertEqual(gallery.record['id'],selected)
            self.assertEqual(gallery.images.currentRow(),4)
            before=files[4].stat()
            image.fill(Qt.GlobalColor.darkBlue);image.save(str(files[4]))
            os.utime(files[4],ns=(before.st_atime_ns,before.st_mtime_ns+1_000_000_000))
            gallery.refresh()
            self.assertEqual(thumbnails(),1)
            self.assertEqual(decode.call_count,2)  # changed thumbnail and selected detail
            self.assertEqual(gallery.record['id'],selected)
            decode.reset_mock()
            gallery.turn(1)
            self.assertEqual(thumbnails(),1)
            self.assertEqual(set(gallery._page_icons),{'i060'})
            files[60].unlink()
            gallery.refresh()
            self.assertTrue(gallery.images.item(0).icon().isNull())
            image.save(str(files[60]))
            gallery.refresh()
            self.assertFalse(gallery.images.item(0).icon().isNull())
            gallery.search.setText('does-not-exist')
            gallery.search_changed()
            self.assertEqual(gallery._page_icons,{})
