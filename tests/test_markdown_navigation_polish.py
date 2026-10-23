"""Reading navigation, live layout and motion preserve document ownership."""
import json
import unittest
from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from prompt_calculus_studio.markdown_relations import open_link
import test_workspace_panes as fixture


class ReadingNavigationTests(unittest.TestCase):
    setUpClass=classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp=fixture.WorkspacePaneTests.setUp
    tearDown=fixture.WorkspacePaneTests.tearDown
    open=fixture.WorkspacePaneTests.open
    events=fixture.WorkspacePaneTests.events

    def wait(self,predicate):
        for _ in range(250):
            self.app.processEvents()
            if predicate():return
            QTest.qWait(10)
        self.fail('Reading operation did not finish')

    def js(self,rich,script):
        result=[];rich.page().runJavaScript(script,result.append)
        self.wait(lambda:bool(result))
        return result[0]

    def reading(self):
        (self.root/'Notes.md').write_text('# Chapter\n\nBody [link](Empty.md).\n\n| A | B |\n| --- | --- |\n| C | D |\n',encoding='utf-8')
        tab=self.open('Notes.md',retained=True);s=tab.widget.md_surface
        s.set_mode('read');self.wait(lambda:s.reader.rich.render_state=='ready')
        return tab,s

    def test_links_reuse_dirty_tabs_across_panes_and_keep_anchor_navigation(self):
        (self.root/'Empty.md').write_text('# Start\n\n## Target\ntext\n',encoding='utf-8')
        source=self.open('Notes.md',retained=True)
        target=self.open('Empty.md',retained=True,new_tab=True)
        target.widget.moveCursor(target.widget.textCursor().MoveOperation.End)
        target.widget.insertPlainText('unsaved')
        dirty=target.widget.encoded()
        original_cursor=target.widget.textCursor().position()
        self.w.move_view(target)
        self.w.focus_tab(source);source.widget.md_surface.set_mode('read')
        count=len(self.w.tabs_by_key)
        for _ in range(2):
            opened=open_link(source.widget.md_surface,'Empty.md#Target');self.events()
            self.assertIs(opened,target);self.assertEqual(len(self.w.tabs_by_key),count)
            self.assertEqual(target.widget.encoded(),dirty);self.assertTrue(target.widget.dirty)
            self.assertEqual(target.widget.textCursor().position(),original_cursor)
            self.assertEqual(target.widget.md_surface.mode,'read')
        self.w.focus_tab(source)
        workflow=self.open('Street.pcs-workflow',retained=False,new_tab=True)
        self.assertFalse(workflow.retained)
        self.w.focus_tab(source);count=len(self.w.tabs_by_key)
        self.assertIs(open_link(source.widget.md_surface,'Street.pcs-workflow'),workflow)
        self.assertEqual(len(self.w.tabs_by_key),count)
        self.assertTrue(workflow.retained)

    def test_title_tracks_body_after_sidebar_outline_resize_and_zoom(self):
        tab,s=self.reading();before=tab.widget.encoded()
        for width,sidebar,outline,zoom in ((1680,True,False,100),(1680,False,True,100),
                                          (1100,False,True,125),(1400,True,False,100)):
            self.w.resize(width,920);self.w.sidebar_preference=sidebar;self.w.sync_sidebar()
            s.set_zoom(zoom)
            if s.outline_layout.requested!=outline:s.toggle_outline()
            QTest.qWait(220);self.events()
            rich=s.reader.rich
            bounds=json.loads(self.js(rich,'JSON.stringify([document.querySelector("main").getBoundingClientRect().left,innerWidth])'))
            expected=rich.mapTo(s,QPoint()).x()+round(bounds[0]*rich.zoomFactor())
            actual=tab.widget.file_title.mapTo(s,QPoint()).x()
            self.assertLessEqual(abs(actual-expected),2,(width,sidebar,outline,zoom,actual,expected))
        self.assertEqual(tab.widget.encoded(),before)

    def test_outline_motion_can_reverse_and_reduced_motion_is_immediate(self):
        tab=self.open('Notes.md');s=tab.widget.md_surface;layout=s.outline_layout
        self.w.resize(900,800);self.events()
        self.w.settings['reduce_motion']=False;s.toggle_outline()
        self.assertLess(layout.offset,0);layout.motion.setCurrentTime(70)
        mid=layout.offset;s.toggle_outline()
        self.assertEqual(layout.offset,mid)
        self.assertGreater(s.content_pane.layout().contentsMargins().left(),0)
        layout.motion.setCurrentTime(layout.motion.duration())
        self.assertTrue(s.outline_panel.isHidden())
        self.assertEqual(s.content_pane.layout().contentsMargins().left(),0)
        self.w.settings['reduce_motion']=True;s.toggle_outline()
        self.assertTrue(s.outline_panel.isVisible());self.assertEqual(layout.offset,0)
        s.toggle_outline();self.assertTrue(s.outline_panel.isHidden())

    def test_plain_rich_note_does_not_load_optional_engines_or_hidden_document(self):
        _,s=self.reading();rich=s.reader.rich
        self.assertEqual(rich.presented_version,rich.version)
        self.assertTrue(s.reader.document().isEmpty())
        self.assertEqual(self.js(rich,'typeof mermaid + "/" + typeof katex'),'undefined/undefined')
        self.assertEqual(self.js(rich,'document.querySelectorAll("table").length'),1)
        self.assertNotIn('編輯表格',self.js(rich,'document.querySelector("main").innerText'))


if __name__=='__main__':unittest.main()
