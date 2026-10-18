"""Responsive outline and render-only search under real Qt input."""
import json
import unittest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
import test_workspace_panes as fixture


class OutlineSearchTests(unittest.TestCase):
    setUpClass=classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp=fixture.WorkspacePaneTests.setUp
    tearDown=fixture.WorkspacePaneTests.tearDown
    open=fixture.WorkspacePaneTests.open
    events=fixture.WorkspacePaneTests.events

    def js(self,rich,script):
        result=[];rich.page().runJavaScript(script,result.append)
        for _ in range(100):
            if result:return result[0]
            QTest.qWait(20)
        self.fail('WebEngine callback did not complete')

    def test_outline_reserves_space_in_narrow_and_split_views_until_closed(self):
        raw=''.join('#'*level+' '+('第幾章與長標題測試😀'*3)+'\n\n正文。\n\n' for level in range(1,7))
        (self.root/'Notes.md').write_bytes(raw.encode());tab=self.open('Notes.md');s=tab.widget.md_surface
        self.w.resize(2200,960);self.events();before=s.title_host.mapTo(s,s.page.file_title.pos()).x()
        s.toggle_outline();QTest.qWait(80)
        self.assertEqual(s.outline_layout.placement,'gutter')
        self.assertEqual(s.title_host.mapTo(s,s.page.file_title.pos()).x(),before)
        self.assertGreater(s.outline_panel.x(),0)
        self.assertEqual(len(s.outline.items),6)
        self.assertGreater(s.outline.visualItemRect(s.outline.items[-1]).height(),s.outline.fontMetrics().lineSpacing())
        self.assertTrue(s.outline.visualItemRect(s.outline.items[-1]).bottom()<s.outline.viewport().height())
        height=s.outline_panel.card.height();s.outline.items[0].setExpanded(False);self.events()
        self.assertLess(s.outline_panel.card.height(),height)
        self.w.resize(960,960);self.w.sidebar_preference=True;self.w.sync_sidebar();QTest.qWait(80)
        self.assertEqual(s.outline_layout.placement,'column');self.assertTrue(s.outline_panel.isVisible())
        width=s.editor.width();self.assertGreater(width,250)
        self.assertLess(s.outline_panel.geometry().right(),s.stack.geometry().left()+1)
        QTest.mouseClick(s.outline_button,Qt.MouseButton.LeftButton);self.events()
        self.assertFalse(s.outline_panel.isVisible());self.assertGreater(s.editor.width(),width)
        QTest.mouseClick(s.outline_button,Qt.MouseButton.LeftButton);self.events();self.assertEqual(s.editor.width(),width)
        QTest.mouseClick(s.editor.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(s.editor.viewport().width()-20,40))
        self.assertTrue(s.outline_panel.isVisible())
        self.w.sidebar_preference=False;self.w.sync_sidebar();self.events()
        self.assertEqual(s.outline_layout.placement,'column')
        other=self.w.duplicate_view(tab,True);self.events()
        self.assertEqual(s.outline_layout.placement,'column');self.assertTrue(s.outline_panel.isVisible())
        self.assertLess(s.outline_panel.geometry().right(),s.stack.geometry().left()+1)
        self.assertFalse(other.widget.md_surface.outline_panel.isVisible())
        QTest.keyClick(other.widget.md_surface.editor,Qt.Key.Key_Escape);self.events()
        self.assertTrue(s.outline_panel.isVisible())
        s.outline.navigate(s.outline.items[-1]);self.assertTrue(s.outline_panel.isVisible())
        self.assertEqual(tab.widget.encoded(),raw.encode());self.assertFalse(tab.widget.dirty)

    def test_native_search_highlights_all_utf16_matches_and_clears_without_edits(self):
        raw='😀 地圖，地圖。\n\n地圖。'
        (self.root/'Notes.md').write_bytes(raw.encode());tab=self.open('Notes.md');s=tab.widget.md_surface
        before=tab.widget.encoded();s.open_find(True);s.query.setText('地圖');s.find_next();self.events()
        selections=s.editor.extraSelections();self.assertEqual(len(selections),3)
        self.assertTrue(all(item.cursor.selectedText()=='地圖' for item in selections))
        self.assertTrue(s.editor.property('searchHit'))
        colors={item.format.background().color().name() for item in selections}
        self.assertEqual(colors,{'#e7d65b','#fff16a'})
        s.find_next(-1);self.assertIn('3/3',s.match_count.text())
        s.replacement.setText('信件');s.replace_one();self.assertEqual(s.page.buffer.snapshot().text,raw.rsplit('地圖',1)[0]+'信件。')
        s.page.undo();self.events();self.assertEqual(tab.widget.encoded(),before)
        s.set_mode('read');self.events();s.find_next()
        self.assertEqual(len(s.reader.extraSelections()),3)
        s.close_panel(s.find_bar);self.assertEqual(s.editor.extraSelections(),[]);self.assertFalse(s.editor.property('searchHit'))
        self.assertEqual(tab.widget.encoded(),before)

    def test_search_enter_and_command_keys_do_not_edit_the_document(self):
        raw='Map and map.\n\nAnother map.'
        (self.root/'Notes.md').write_bytes(raw.encode());tab=self.open('Notes.md');s=tab.widget.md_surface
        s.editor.setFocus();QTest.keyClick(s.editor,Qt.Key.Key_F,Qt.KeyboardModifier.ControlModifier);self.events()
        self.assertTrue(s.query.hasFocus())
        QTest.keyClicks(s.query,'map');self.events()
        for expected in ('1/3','2/3','3/3','1/3'):
            QTest.keyClick(s.query,Qt.Key.Key_Return);self.events()
            self.assertEqual(tab.widget.encoded(),raw.encode())
            self.assertEqual(s.match_count.text(),expected)
            self.assertTrue(s.query.hasFocus())
        QTest.keyClick(s.query,Qt.Key.Key_Escape);self.events()
        self.assertFalse(s.find_bar.isVisible());self.assertTrue(s.editor.hasFocus())
        s.open_commands();s.command_query.setText('不存在的命令');self.events()
        QTest.keyClick(s.command_query,Qt.Key.Key_Return);self.events()
        self.assertEqual(tab.widget.encoded(),raw.encode());self.assertFalse(tab.widget.dirty)

    def test_commands_are_bounded_and_leave_the_document_in_place(self):
        tab=self.open('Notes.md');s=tab.widget.md_surface;before=tab.widget.encoded()
        origin=s.content_pane.geometry();s.open_commands();self.events()
        self.assertEqual(s.content_pane.geometry(),origin)
        self.assertTrue(s.command_box.isWindow());self.assertLessEqual(s.command_box.width(),520)
        first=s.command_list.currentRow();QTest.keyClick(s.command_query,Qt.Key.Key_Down)
        self.assertEqual(s.command_list.currentRow(),first+1)
        QTest.keyClick(s.command_query,Qt.Key.Key_Up);self.assertEqual(s.command_list.currentRow(),first)
        s.command_query.setText('不存在的命令');self.events()
        self.assertEqual(s.content_pane.geometry(),origin)
        QTest.keyClick(s.command_query,Qt.Key.Key_Escape);self.events()
        self.assertFalse(s.command_box.isVisible());self.assertEqual(tab.widget.encoded(),before)
        self.w.resize(700,700);self.events();s.open_commands();self.events()
        self.assertLessEqual(s.command_box.width(),s.width())

    def test_rich_find_crosses_inline_formatting_and_preserves_dom_selection(self):
        raw='---\ntype: note\n---\n😀 地**圖**。地圖。\n\n地圖\n\nMap map [literal].'
        (self.root/'Notes.md').write_bytes(raw.encode());tab=self.open('Notes.md');s=tab.widget.md_surface;s.set_mode('read');rich=s.reader.rich
        for _ in range(100):
            if rich.render_state=='ready':break
            QTest.qWait(30)
        original=self.js(rich,'document.querySelector("main").innerHTML')
        self.js(rich,"(()=>{const r=new Range();r.selectNodeContents(document.querySelector('p'));const s=getSelection();s.removeAllRanges();s.addRange(r);return s.toString()})()")
        selection=rich.selectedText();s.open_find();s.query.setText('地圖');s.find_next(-1);QTest.qWait(60)
        self.assertEqual(s.match_count.text(),'3/3')
        ranges=json.loads(self.js(rich,"JSON.stringify([...CSS.highlights.get('find-matches')].map(r=>r.toString()))"))
        self.assertEqual(ranges,['地圖']*3)
        self.assertEqual(self.js(rich,'document.querySelector("main").innerHTML'),original)
        self.assertEqual(rich.selectedText(),selection)
        s.query.setText('map');s.case_sensitive.setChecked(True);s.find_next();QTest.qWait(60)
        self.assertEqual(s.match_count.text(),'1/1')
        s.case_sensitive.setChecked(False);s.find_next();QTest.qWait(60);self.assertEqual(s.match_count.text(),'1/2')
        s.query.setText('[literal]');s.find_next();QTest.qWait(60);self.assertEqual(s.match_count.text(),'1/1')
        s.close_panel(s.find_bar);self.assertFalse(self.js(rich,"CSS.highlights.has('find-matches')"))
        self.assertEqual(tab.widget.encoded(),raw.encode());self.assertFalse(tab.widget.dirty)


if __name__=='__main__':unittest.main()
