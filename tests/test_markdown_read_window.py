"""Cold rich-reader activation must not recreate the visible workspace window."""
import unittest
from PySide6.QtCore import QObject, QEvent, QThreadPool
from PySide6.QtTest import QTest
import test_workspace_panes as fixture


class ReadingWindowTests(unittest.TestCase):
    setUpClass=classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp=fixture.WorkspacePaneTests.setUp
    open=fixture.WorkspacePaneTests.open
    events=fixture.WorkspacePaneTests.events

    def tearDown(self):
        self.w.notebook.stop();QThreadPool.globalInstance().waitForDone(5000)
        fixture.WorkspacePaneTests.tearDown(self)

    def test_first_read_and_return_keep_shell_handle_geometry_and_visibility(self):
        (self.root/'Notes.md').write_text('# 表格\n\n| A | B |\n|---|---|\n| 中文 | 值 |\n',encoding='utf-8')
        s=self.open('Notes.md').widget.md_surface
        class Watch(QObject):
            def __init__(self,window):
                super().__init__(window);self.events=[];window.installEventFilter(self)
            def eventFilter(self,obj,event):
                if event.type() in (QEvent.Type.Hide,QEvent.Type.WinIdChange):self.events.append(event.type())
                return False
        watch=Watch(self.w);handle=int(self.w.winId());geometry=self.w.geometry()
        for mode in ('read','write','read'):
            s.set_mode(mode);QTest.qWait(150)
            self.assertEqual(int(self.w.winId()),handle)
            self.assertEqual(self.w.geometry(),geometry)
            self.assertTrue(self.w.isVisible())
        self.assertEqual(watch.events,[])
        self.assertTrue(s.reader.advanced)
        for _ in range(100):
            if s.reader.rich.render_state=='ready':break
            QTest.qWait(30)
        self.assertEqual(s.reader.rich.render_state,'ready')
        self.assertIs(s.reader.rich.nativeParentWidget(),s.reader)
        self.assertFalse(s.page.dirty)
