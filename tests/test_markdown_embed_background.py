"""Actual embed handoff, complete read evidence and first-publication races."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QThreadPool, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from prompt_calculus_studio.markdown_read_receipt import (
    EmbedHandoff, ReceiptNotes, handoff_embed, verify_receipt)
from prompt_calculus_studio.markdown_links import Notes
from prompt_calculus_studio.markdown_render import render
from prompt_calculus_studio.markdown_source import SourceEdit
from prompt_calculus_studio.workspace_shell import WorkspaceShell


def isolated_area():
    base=Path(os.environ.get('PROMPT_STUDIO_DATA',tempfile.gettempdir()))
    base.mkdir(parents=True,exist_ok=True)
    return tempfile.TemporaryDirectory(prefix='hp001-',dir=base)


class ReadReceiptTests(unittest.TestCase):
    def setUp(self):
        self.area=isolated_area();self.root=Path(self.area.name)
        self.parent=self.root/'Parent.md';self.parent.write_bytes(b'![[Child]]\n')
        self.child=self.root/'Child.md';self.child.write_bytes(b'OLD\n')

    def tearDown(self):self.area.cleanup()

    def projection(self,text=None,overrides=None):
        notes=ReceiptNotes(self.root,overrides or {})
        value=render(text or self.parent.read_text(encoding='utf-8'),self.parent,self.root,notes=notes)
        return value,notes.receipt()

    def test_same_stat_different_bytes_and_normalized_text_are_not_content_proof(self):
        _,receipt=self.projection();stamp=self.child.stat()
        self.child.write_bytes(b'NEW\n');os.utime(self.child,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        self.assertEqual(self.child.stat().st_size,stamp.st_size)
        self.assertEqual(self.child.stat().st_mtime_ns,stamp.st_mtime_ns)
        self.assertFalse(verify_receipt(receipt))
        self.child.write_bytes(b'NEW\r')
        _,receipt=self.projection()
        self.child.write_bytes(b'NEW\n')
        self.assertFalse(verify_receipt(receipt),'Digest must cover raw consumed bytes, not normalized text')

    def test_unselected_alias_content_and_names_are_recorded(self):
        self.child.write_text('---\naliases: [Link]\n---\nSELECTED\n',encoding='utf-8')
        other=self.root/'Other.md';other.write_text('---\naliases: [Else]\n---\nOTHER\n',encoding='utf-8')
        projection,receipt=self.projection('![[Link]]')
        self.assertIn('SELECTED',projection.body)
        self.assertNotIn(other,projection.dependencies)
        self.assertIn(other,receipt.read_paths)
        self.assertTrue(verify_receipt(receipt))
        other.write_text('---\naliases: [Link]\n---\nOTHER\n',encoding='utf-8')
        self.assertFalse(verify_receipt(receipt))

    def test_alias_name_add_remove_rename_and_empty_directory_change(self):
        self.child.write_text('---\naliases: [Link]\n---\nSELECTED\n',encoding='utf-8')
        _,receipt=self.projection('![[Link]]')
        new=self.root/'Link.md';new.write_bytes(b'NEW')
        self.assertFalse(verify_receipt(receipt));new.unlink()
        self.assertTrue(verify_receipt(receipt))
        moved=self.root/'Renamed.md';self.child.rename(moved)
        self.assertFalse(verify_receipt(receipt));moved.rename(self.child)
        (self.root/'Empty').mkdir()
        self.assertFalse(verify_receipt(receipt),'New directories must be discovered before adding new name candidates')

    def test_missing_direct_target_probe_detects_later_creation(self):
        _,receipt=self.projection('![[nested/Missing]]')
        (self.root/'nested').mkdir();(self.root/'nested/Missing.md').write_bytes(b'APPEARED')
        self.assertFalse(verify_receipt(receipt))
        self.assertNotIn(self.root.parent,receipt.watch_paths())

    def test_rejected_traversal_never_adds_outside_watches(self):
        _,receipt=self.projection('![[../outside]]')
        watched=receipt.watch_paths()
        self.assertTrue(all(path.is_relative_to(self.root) and '..' not in path.parts for path in watched))
        self.assertNotIn(self.root.parent,watched)

    def test_nested_dotdot_probe_watches_normalized_inside_target(self):
        (self.root/'nested').mkdir()
        projection,receipt=self.projection('![[nested/../Child]]')
        self.assertIn('OLD',projection.body)
        watched=receipt.watch_paths()
        self.assertIn(self.child,watched)
        self.assertTrue(all(path.is_relative_to(self.root) and '..' not in path.parts for path in watched))

    def test_rejected_link_probe_watches_only_its_safe_parent(self):
        from prompt_calculus_studio.markdown_read_receipt import ReadReceipt
        parent=self.root/'nested';parent.mkdir();link=parent/'linked'
        receipt=ReadReceipt(self.root,(),(('nested/linked/Child.md',self.root,None),),None,())
        with patch('prompt_calculus_studio.markdown_read_receipt.paths.is_link',side_effect=lambda path:path==link):
            self.assertEqual(receipt.watch_paths(),{parent})

    def test_root_or_ancestor_replaced_by_link_refuses_watch_paths(self):
        from prompt_calculus_studio.local_documents import paths
        _,receipt=self.projection();original=paths.is_link
        for replaced in (self.root,self.root.parent):
            with self.subTest(replaced=replaced),patch.object(paths,'is_link',side_effect=lambda path:path==replaced or original(path)):
                with self.assertRaises(ValueError):receipt.watch_paths()

    def test_previously_read_files_and_directories_stop_at_new_link(self):
        from prompt_calculus_studio.local_documents import paths
        from prompt_calculus_studio.markdown_read_receipt import ReadReceipt
        parent=self.root/'nested';link=parent/'replaced';(link/'deep').mkdir(parents=True)
        child=link/'deep/Child.md';child.write_bytes(b'OLD')
        receipt=ReadReceipt(self.root,((child,('bytes',3,'unused')),),(),None,(parent,link,link/'deep'))
        original=paths.is_link
        with patch.object(paths,'is_link',side_effect=lambda path:path==link or original(path)):
            self.assertEqual(receipt.watch_paths(),{parent})

    def test_full_projection_matches_normal_renderer_with_anchor_code_and_cycles(self):
        self.child.write_text('# One\nkeep\n\n## Two\n```python\nx=1\n```\n\n![[Parent]]\n',encoding='utf-8')
        text='![[Child#Two]]\n\n[[Child|ordinary]]\n'
        actual,receipt=self.projection(text)
        expected=render(text,self.parent,self.root)
        self.assertEqual(actual,expected)
        self.assertTrue(verify_receipt(receipt))

    def test_real_token_handoff_precedes_child_io_and_ignores_literals_and_links(self):
        with patch.object(Notes,'resolve',side_effect=AssertionError('Child IO before handoff')):
            for source in ('![[Child]]','==![[Child]]==','![note](Child.md)'):
                with self.subTest(source=source),self.assertRaises(EmbedHandoff):
                    render(source,self.parent,self.root,before_embed=handoff_embed)
            for source in ('[[Child]]','[Child](Child.md)','`![[Child]]`','```text\n![[Child]]\n```'):
                render(source,self.parent,self.root,before_embed=handoff_embed)

    def test_buffer_receipt_does_not_claim_disk_is_the_buffer_version(self):
        value,receipt=self.projection(overrides={self.child:'UNSAVED'})
        self.assertIn('UNSAVED',value.body)
        self.assertEqual(dict(receipt.reads)[self.child],('buffer',))
        self.child.write_bytes(b'EXTERNAL')
        self.assertTrue(verify_receipt(receipt),'The GUI must validate the captured buffer owner/snapshot separately')


class EmbeddedBackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.area=isolated_area();self.root=Path(self.area.name)/'files';self.root.mkdir()
        self.parent=self.root/'Parent.md';self.parent.write_bytes(b'![[Child]]\n')
        self.child=self.root/'Child.md';self.child.write_bytes(b'OLD\n')
        self.errors=[];self.old_hook=sys.excepthook
        sys.excepthook=lambda kind,value,trace:self.errors.append(str(value))
        self.window=WorkspaceShell(self.root,Path(self.area.name)/'data')
        self.window.autosave.delay_ms=60000;self.window.report_error=self.errors.append
        self.window.resize(1100,800);self.window.show()
        self.tab=None;self.surface=None
        self.releases=[]

    def tearDown(self):
        for release in self.releases:release.set()
        self.assertTrue(QThreadPool.globalInstance().waitForDone(5000))
        for pane in list(self.window.panes):
            while pane.strip.bar.count():
                self.assertTrue(self.window.close_tab(0,decision='discard',make_home=False,pane=pane))
        self.assertTrue(self.window.close());self.window.deleteLater()
        for _ in range(3):
            self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents()
        sys.excepthook=self.old_hook
        self.area.cleanup()
        self.assertEqual(self.errors,[])

    def wait(self,condition):
        deadline=time.monotonic()+10
        while not condition() and time.monotonic()<deadline:QTest.qWait(10)
        self.assertTrue(condition(),self.errors)

    def open_parent(self):
        self.tab=self.window.open_path(self.parent,retained=True,new_tab=True)
        self.surface=self.tab.widget.md_surface
        return self.surface

    def ready(self,text):
        rich=self.surface.reader.rich
        return bool(rich and rich.projection and rich.render_state=='ready'
            and rich.presented_version==rich.version and text in rich.projection.body)

    def edit(self,tab,text):
        snapshot=tab.widget.buffer.snapshot()
        tab.widget.buffer.apply(snapshot,[SourceEdit(0,len(snapshot.text),text)])
        tab.widget.md_surface.refresh()

    def hold_first_read(self):
        entered=threading.Event();release=threading.Event();self.releases.append(release)
        original=ReceiptNotes._read;calls=[]
        def read(notes,path):
            value=original(notes,path)
            if path==self.child:
                calls.append(threading.get_ident())
                if len(calls)==1:entered.set();release.wait(5)
            return value
        return patch.object(ReceiptNotes,'_read',read),entered,release,calls

    def publish_observer(self,bodies):
        from prompt_calculus_studio.markdown_rich_view import RichView
        original=RichView.publish
        def publish(view,projection,root):
            original(view,projection,root)
            if view.surface.page.path==self.parent and view.projection is projection:bodies.append(projection.body)
        return patch.object(RichView,'publish',publish)

    def test_short_parent_large_child_reads_only_on_worker_and_publishes_complete_content(self):
        self.child.write_bytes(b'CHILD_START\n'+b'word '*39000+b'\nCHILD_END')
        surface=self.open_parent();source=surface.page.buffer.snapshot()
        controller,entered,release,calls=self.hold_first_read()
        verified=[]
        def verify(receipt):
            verified.append(threading.get_ident());return verify_receipt(receipt)
        with controller,patch('prompt_calculus_studio.markdown_read_receipt.verify_receipt',side_effect=verify):
            surface.set_mode('read')
            self.assertTrue(entered.wait(2));self.assertIsNone(surface.reader.rich.projection)
            self.assertNotIn(threading.get_ident(),calls)
            release.set();self.wait(lambda:self.ready('CHILD_END'))
        self.assertIn('CHILD_START',surface.reader.rich.projection.body)
        self.assertTrue(verified);self.assertNotIn(threading.get_ident(),verified)
        self.assertIs(surface.page.buffer.snapshot(),source)
        self.assertFalse(surface.page.dirty);self.assertEqual(self.parent.read_bytes(),b'![[Child]]\n')

    def test_dependency_free_projection_skips_empty_verify_job_and_finishes(self):
        from prompt_calculus_studio.markdown_read_projection import RichReadingProjection
        self.parent.write_text('**DENSE_START**\n\n'+'word '*27000+'\n\nDENSE_END',encoding='utf-8')
        surface=self.open_parent();original=RichReadingProjection.verify
        with patch.object(RichReadingProjection,'verify',autospec=True,side_effect=original) as verify:
            surface.set_mode('read');self.wait(lambda:self.ready('DENSE_END'))
            verify.assert_not_called()
        self.assertIn('DENSE_START',surface.reader.rich.projection.body)

    def test_root_replaced_before_watch_binding_fails_without_retry(self):
        from prompt_calculus_studio.local_documents import paths
        from prompt_calculus_studio.markdown_rich_view import RichView
        surface=self.open_parent();original_ready=RichView.projection_ready;original_link=paths.is_link
        def ready(view,*args):
            with patch.object(paths,'is_link',side_effect=lambda path:path==self.root or original_link(path)):
                original_ready(view,*args)
        with patch.object(RichView,'projection_ready',ready):
            surface.set_mode('read');rich=surface.reader.rich
            with patch.object(rich.relations,'bind',wraps=rich.relations.bind) as bind:
                self.wait(lambda:rich.render_state=='error')
                bind.assert_not_called()
                self.assertIsNone(rich.projection)
                self.assertFalse(rich.relations.timer.isActive())
                self.assertFalse(rich.input_timer.isActive())

    def test_first_unpublished_open_buffer_update_rejects_old_snapshot(self):
        child_tab=self.window.open_path(self.child,retained=True,new_tab=True)
        self.edit(child_tab,'DRAFT_OLD\n')
        surface=self.open_parent();controller,entered,release,_=self.hold_first_read();bodies=[]
        with controller,self.publish_observer(bodies):
            surface.set_mode('read');self.assertTrue(entered.wait(2))
            self.edit(child_tab,'DRAFT_NEW\n')
            release.set();self.wait(lambda:self.ready('DRAFT_NEW'))
        self.assertFalse(any('DRAFT_OLD' in body for body in bodies))
        self.assertEqual(self.child.read_bytes(),b'OLD\n');self.assertFalse(surface.page.dirty)
        child_tab.widget.undo();self.assertEqual(child_tab.widget.buffer.snapshot().text,'DRAFT_OLD\n')

    def test_first_disk_change_before_watch_same_stat_cannot_publish_old(self):
        surface=self.open_parent();controller,entered,release,_=self.hold_first_read();bodies=[]
        with controller,self.publish_observer(bodies):
            surface.set_mode('read');self.assertTrue(entered.wait(2))
            self.assertNotIn(str(self.child),surface.reader.rich.relations.files())
            stamp=self.child.stat();self.child.write_bytes(b'NEW\n')
            os.utime(self.child,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            release.set();self.wait(lambda:self.ready('NEW'))
        self.assertFalse(any('OLD' in body for body in bodies))

    def test_opening_child_after_dispatch_changes_the_consumed_source_owner(self):
        surface=self.open_parent();controller,entered,release,_=self.hold_first_read();bodies=[]
        with controller,self.publish_observer(bodies):
            surface.set_mode('read');self.assertTrue(entered.wait(2))
            child_tab=self.window.open_path(self.child,retained=True,new_tab=True)
            self.edit(child_tab,'OPEN_NEW\n')
            self.window.focus_tab(self.tab)
            release.set();self.wait(lambda:self.ready('OPEN_NEW'))
        self.assertFalse(any('OLD' in body for body in bodies))

    def test_unselected_open_alias_candidate_invalidates_published_projection(self):
        self.parent.write_bytes(b'![[Link]]\n')
        self.child.write_text('---\naliases: [Link]\n---\nSELECTED\n',encoding='utf-8')
        other=self.root/'Other.md';other.write_text('---\naliases: [Else]\n---\nOTHER\n',encoding='utf-8')
        candidate=self.window.open_path(other,retained=True,new_tab=True)
        surface=self.open_parent();surface.set_mode('read');self.wait(lambda:self.ready('SELECTED'))
        self.assertNotIn(other,surface.reader.rich.projection.dependencies)
        self.edit(candidate,'---\naliases: [Link]\n---\nOTHER\n')
        self.wait(lambda:self.ready('同名筆記'))
        self.assertNotIn('SELECTED',surface.reader.rich.projection.body)

    def png(self):
        # A small synthetic image; no private data or external service.
        from PySide6.QtGui import QImage,QColor
        image=QImage(2,2,QImage.Format.Format_RGB32);image.fill(QColor('blue'))
        path=self.root/'pixel.png';self.assertTrue(image.save(str(path)));return path

    def test_child_change_between_hydration_slices_never_publishes_old_media_projection(self):
        from prompt_calculus_studio.markdown_rich_view import RichView
        self.png()
        text='OLD_MEDIA\n\n'+('\n\n![image](pixel.png)'*4)
        self.child.write_text(text,encoding='utf-8')
        child_tab=self.window.open_path(self.child,retained=True,new_tab=True)
        surface=self.open_parent();original=RichView.resource;changed=[];bodies=[]
        def resource(view,entry,index):
            value=original(view,entry,index)
            if view.surface is surface and view.read_request and view.read_request['stage']=='hydrate' and not changed:
                changed.append(index)
                QTimer.singleShot(0,lambda:self.edit(child_tab,text.replace('OLD_MEDIA','NEW_MEDIA')))
                time.sleep(.008)  # Force the existing 6 ms budget to yield one slice.
            return value
        with patch.object(RichView,'resource',resource),self.publish_observer(bodies):
            surface.set_mode('read');self.wait(lambda:self.ready('NEW_MEDIA'))
        self.assertEqual(changed,[0])
        self.assertFalse(any('OLD_MEDIA' in body for body in bodies))
        self.assertEqual(surface.reader.rich.projection.body.count('<img '),4)
        self.assertNotIn('pcs-resource:',surface.reader.rich.projection.body)

    def test_media_prefix_handoff_clears_only_partial_resource_indexes(self):
        from prompt_calculus_studio.markdown_rich_view import RichView
        self.png();self.parent.write_bytes(b'![prefix](pixel.png)\n\n![[Child]]\n')
        surface=self.open_parent();original=RichView.start_projection;seen=[]
        def start(view,text,root):
            seen.append((set(view.gate.allowed),dict(view.image_sources)))
            return original(view,text,root)
        with patch.object(RichView,'start_projection',start):
            surface.set_mode('read');self.wait(lambda:self.ready('OLD'))
        self.assertTrue(seen);self.assertEqual(seen[0],(set(),{}))
        self.assertEqual(surface.reader.rich.projection.body.count('<img '),1)

    def test_mode_switch_while_first_job_is_pending_can_return_without_permanent_loading(self):
        surface=self.open_parent();controller,entered,release,_=self.hold_first_read()
        with controller:
            surface.set_mode('read');self.assertTrue(entered.wait(2))
            surface.set_mode('write');release.set()
            self.wait(lambda:surface.reader.rich.projection_worker.job is None)
            self.assertIsNone(surface.reader.rich.projection)
            self.assertIsNone(surface.reader._render_key)
            surface.set_mode('read');self.wait(lambda:self.ready('OLD'))

    def test_detached_page_cannot_publish_before_deferred_qobject_deletion(self):
        surface=self.open_parent();controller,entered,release,_=self.hold_first_read();bodies=[]
        with controller,self.publish_observer(bodies):
            surface.set_mode('read');self.assertTrue(entered.wait(2))
            rich=surface.reader.rich
            self.window._detach_view(self.tab)
            release.set()
            self.wait(lambda:rich.projection_worker.job is None)
            self.assertIsNone(rich.projection);self.assertEqual(bodies,[])
            # Complete the same normal close cleanup after testing the detach gap.
            self.tab.widget.deleteLater()
            self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            self.assertFalse(isValid(surface))


if __name__=='__main__':unittest.main()
