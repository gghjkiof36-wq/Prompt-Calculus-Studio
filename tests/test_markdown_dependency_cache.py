"""Published dependencies remain fresh without rendering hidden readers."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QEvent, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from prompt_calculus_studio.workspace_shell import WorkspaceShell


class DependencyCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.area=tempfile.TemporaryDirectory(prefix='pcs-dependency-cache-')
        self.root=Path(self.area.name)/'files';self.root.mkdir()
        self.parent=self.root/'Parent.md';self.parent.write_bytes(b'![[Child]]\n')
        self.child=self.root/'Child.md';self.child.write_bytes(b'OLD\n')
        self.other=self.root/'Other.md';self.other.write_bytes(b'Other\n')
        self.window=WorkspaceShell(self.root,Path(self.area.name)/'data')
        self.window.resize(1100,800);self.window.show()
        self.tab=self.window.open_path(self.parent,retained=True,new_tab=True)
        self.surface=self.tab.widget.md_surface
        self.surface.set_mode('read')
        self.wait(lambda:self.ready('OLD'))
        self.reader=self.surface.reader;self.rich=self.reader.rich
        self.snapshot=self.surface.page.buffer.snapshot()

    def wait(self,condition):
        deadline=time.monotonic()+8
        while not condition() and time.monotonic()<deadline:QTest.qWait(10)
        self.assertTrue(condition())

    def ready(self,text):
        rich=self.surface.reader.rich
        return (rich is not None and rich.render_state=='ready'
                and rich.presented_version==rich.version and text in rich.projection.body)

    def tearDown(self):
        self.assertTrue(self.window.close())
        self.window.deleteLater()
        for _ in range(3):
            self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents()
        self.assertFalse(isValid(self.window))
        self.assertTrue(QThreadPool.globalInstance().waitForDone(2000))
        self.area.cleanup()

    def assert_source_preserved(self):
        self.assertIs(self.surface.page.buffer.snapshot(),self.snapshot)
        self.assertFalse(self.surface.page.dirty)
        self.assertEqual(self.parent.read_bytes(),b'![[Child]]\n')

    def test_hidden_dependency_update_rebuilds_once_and_unchanged_mode_reuses(self):
        version=self.rich.version;projection=self.rich.projection
        self.surface.set_mode('write')
        self.child.write_bytes(b'NEW\n')
        self.wait(lambda:self.reader._render_key is None and not self.rich.relations.timer.isActive())
        self.assertEqual(self.rich.version,version);self.assertIs(self.rich.projection,projection)
        self.surface.set_mode('read');self.wait(lambda:self.ready('NEW'))
        QTest.qWait(30)
        self.assertEqual(self.rich.version,version+1)
        projection=self.rich.projection;key=self.reader._render_key
        self.surface.set_mode('write');self.surface.set_mode('read');QTest.qWait(30)
        self.assertIs(self.rich.projection,projection);self.assertEqual(self.reader._render_key,key)
        self.assertEqual(self.rich.version,version+1);self.assert_source_preserved()

    def test_inactive_read_tab_returns_fresh_and_rapid_show_hide_does_not_render(self):
        other=self.window.open_path(self.other,retained=True,new_tab=True)
        self.assertFalse(self.reader.isVisible());self.assertEqual(self.surface.mode,'read')
        version=self.rich.version
        self.child.write_bytes(b'NEW\n')
        self.wait(lambda:self.reader._render_key is None and not self.rich.relations.timer.isActive())
        self.window.tab_strip.bar.setCurrentIndex(self.window.index_of(self.tab.key))
        self.window.tab_strip.bar.setCurrentIndex(self.window.index_of(other.key))
        QTest.qWait(30)
        self.assertFalse(self.reader.isVisible());self.assertIsNone(self.reader._render_key)
        self.assertEqual(self.rich.version,version)
        self.window.tab_strip.bar.setCurrentIndex(self.window.index_of(self.tab.key))
        self.wait(lambda:self.ready('NEW'));QTest.qWait(30)
        self.assertEqual(self.rich.version,version+1);self.assert_source_preserved()

    def test_late_hidden_read_projection_does_not_restore_invalid_key(self):
        from prompt_calculus_studio.markdown_render import Projection
        self.window.open_path(self.other,retained=True,new_tab=True)
        self.child.write_bytes(b'NEW\n')
        self.wait(lambda:self.reader._render_key is None and not self.rich.relations.timer.isActive())
        version=self.rich.version
        # A worker already in flight may complete while a read tab is hidden.
        self.rich.projection_ready(version,(Projection(body='<p>OLD late</p>'),'unused'),'')
        self.wait(lambda:self.rich.hydration is None)
        self.assertIsNone(self.reader._render_key)
        self.window.tab_strip.bar.setCurrentIndex(self.window.index_of(self.tab.key))
        self.wait(lambda:self.ready('NEW'))
        projection=self.rich.projection
        self.rich.projection_ready(version,(Projection(body='<p>STALE</p>'),'unused'),'')
        self.rich.bridge.presented(version);self.rich.bridge.finished('ready',version)
        self.assertIs(self.rich.projection,projection)
        self.assertEqual(self.rich.presented_version,self.rich.version)
        self.assert_source_preserved()

    def test_note_preview_without_cache_api_keeps_visible_refresh(self):
        from prompt_calculus_studio.notebook_dialogs import NotePreview
        preview=NotePreview(None,workspace=self.window,path=self.parent)
        try:
            self.assertFalse(hasattr(preview,'_invalidate_cache'))
            with patch.object(preview,'refresh') as refresh:
                preview.show();QTest.qWait(10)
                preview.rich.relations.refresh();refresh.assert_called_once_with()
                preview.hide();preview.rich.relations.refresh();refresh.assert_called_once_with()
        finally:
            preview.close();preview.deleteLater()
            for _ in range(3):
                self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents()


if __name__=='__main__':unittest.main()
