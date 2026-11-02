"""Gallery viewer order spans pages and copy import stays with its original album."""
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
import test_workspace_shell as fixture


class GalleryViewerTests(unittest.TestCase):
    setUpClass=classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    setUp=fixture.WorkspaceShellTests.setUp
    tearDown=fixture.WorkspaceShellTests.tearDown
    events=fixture.WorkspaceShellTests.events

    def prepare_gallery(self):
        host=self.w.ensure_utility();catalog=host.catalog
        catalog.put('album',dict(id='a',name='圖片'))
        image=QImage(96,144,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.darkGreen)
        self.source=self.root/'source.png';image.save(str(self.source))
        for i in range(62):
            catalog.put('image',dict(id=f'i{i:03}',name=f'圖-{i:03}',path=str(self.source),thumb='',owned=False,metadata={'raw':{}},manual=None),'a')
        catalog.put('album',dict(id='b',name='其他'))
        self.w.open_route('gallery');self.events()
        gallery=host.gallery.load();gallery.album='a';gallery.page=0;gallery.refresh();self.events()
        return gallery,catalog

    def test_double_click_opens_internal_full_album_and_keeps_page_selection(self):
        gallery,catalog=self.prepare_gallery();item=gallery.images.item(59)
        gallery.images.scrollToItem(item);gallery.images.setCurrentItem(item);self.events()
        with patch('prompt_calculus_studio.image_viewer.show_image') as show,patch('prompt_calculus_studio.pages.open_file',side_effect=AssertionError('external viewer')):
            # Same native double-click signal, without opening a blocking modal.
            gallery.images.itemDoubleClicked.emit(item)
        options=show.call_args.kwargs
        self.assertEqual(len(options['records']),62);self.assertEqual(options['selected_id'],'i059')
        self.assertTrue(all('metadata' not in r for r in options['records']))
        options['on_select']('i060');self.assertEqual(gallery.page,1);self.assertEqual(gallery.record['id'],'i060')
        options['on_select']('i059');self.assertEqual(gallery.page,0);self.assertEqual(gallery.record['id'],'i059')
        gallery.search.setText('圖-060');gallery.search_changed()
        options['on_select']('i058');self.assertEqual(gallery.search.text(),'圖-060')
        with patch('prompt_calculus_studio.image_viewer.show_image') as show:gallery.view_image(gallery.images.item(0))
        self.assertEqual([r['id'] for r in show.call_args.kwargs['records']],['i060'])

    def test_saved_copy_keeps_owner_and_navigation_after_sorted_insertion(self):
        gallery,catalog=self.prepare_gallery();before=self.source.read_bytes()
        with patch('prompt_calculus_studio.image_viewer.show_image') as show:gallery.view_image(gallery.images.item(59))
        options=show.call_args.kwargs
        destination=self.root/'000-copy.png';image=QImage(96,144,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.darkBlue);image.save(str(destination))
        options['on_save'](destination)
        self.assertEqual(catalog.count('image','a'),63);self.assertEqual(self.source.read_bytes(),before)
        options['on_select']('i059')
        self.assertEqual(gallery.page,1);self.assertEqual(gallery.record['id'],'i059')
        gallery.album='b';gallery.refresh()
        destination=self.root/'other-copy.png';image.save(str(destination));options['on_save'](destination)
        self.assertEqual(catalog.count('image','a'),64);self.assertEqual(catalog.count('image','b'),0)
        self.assertEqual(gallery.album,'b');self.assertEqual(gallery.images.count(),0)


if __name__=='__main__':unittest.main()
