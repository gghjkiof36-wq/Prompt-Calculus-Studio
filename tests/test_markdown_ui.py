"""Document controls through real Qt input; display choices never change bytes."""
import unittest
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from prompt_calculus_studio.markdown_property_editor import PropertyEditor
import test_workspace_panes as fixture


class MarkdownUiTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    open = fixture.WorkspacePaneTests.open
    events = fixture.WorkspacePaneTests.events

    def test_inline_filename_commits_cancel_conflict_and_empty_bytes(self):
        tab = self.open('Empty.md');page = tab.widget;s = page.md_surface;title = page.file_title
        self.assertEqual(s.editor.placeholderText(),'')
        self.assertTrue(title.isVisible());self.assertEqual(page.encoded(),b'')
        QTest.mouseClick(title,Qt.MouseButton.LeftButton);self.events()
        self.assertIsNotNone(title.name);title.name.setText('A new note')
        QTest.keyClick(title.name,Qt.Key.Key_Return);self.events()
        self.assertEqual(page.path,self.root/'A new note.md');self.assertEqual(page.path.read_bytes(),b'')
        self.assertEqual(tab.title,'A new note');self.assertEqual(title.text(),'A new note')
        self.assertEqual(s.breadcrumb_name.text(),'A new note')
        self.assertFalse((self.root/'Empty.md').exists());self.assertFalse(page.dirty)
        QTest.mouseClick(title,Qt.MouseButton.LeftButton);self.events();title.name.setText('Cancelled')
        QTest.keyClick(title.name,Qt.Key.Key_Escape);self.events()
        self.assertEqual(page.path,self.root/'A new note.md');self.assertIsNone(title.name)
        QTest.mouseClick(title,Qt.MouseButton.LeftButton);self.events();title.name.setText('Notes')
        QTest.keyClick(title.name,Qt.Key.Key_Return);self.events()
        self.assertEqual(page.path,self.root/'A new note.md');self.assertIsNotNone(title.name)
        QTest.keyClick(title.name,Qt.Key.Key_Escape)
        self.assertEqual((self.root/'A new note.md').read_bytes(),b'')

    def test_outline_hierarchy_navigation_and_compact_search_order(self):
        raw = '# Chapter\ntext\n## Scene\nline\n### Detail\nline\n## After\nend'
        (self.root/'Notes.md').write_bytes(raw.encode())
        tab = self.open('Notes.md');s = tab.widget.md_surface;s.toggle_outline();self.events()
        root = s.outline.topLevelItem(0)
        self.assertEqual(s.outline.topLevelItemCount(),1);self.assertEqual(root.childCount(),2)
        self.assertEqual(root.child(0).childCount(),1)
        s.outline.navigate(root.child(1));self.assertEqual(s.editor.textCursor().position(),raw.index('## After'))
        root.setExpanded(False);s.refresh();self.assertFalse(root.isExpanded())
        s.open_find();s.query.setText('line');self.events()
        self.assertLess(s.find_bar.geometry().bottom(),s.toolbar_host.y())
        self.assertLessEqual(s.find_panel.width(),540)
        s.find_next();self.assertEqual(s.editor.textCursor().selectedText(),'line')
        self.assertIn('/2',s.match_count.text())
        s.query.setText('missing');self.assertEqual(s.match_count.text(),'0/0');self.assertFalse(s.find_forward.isEnabled())
        QTest.keyClick(s.query,Qt.Key.Key_Escape);self.events();self.assertFalse(s.find_bar.isVisible())
        self.assertEqual(tab.widget.encoded(),raw.encode())

    def test_zoom_keyboard_wheel_modes_and_reset_are_view_local(self):
        tab = self.open('Notes.md');s = tab.widget.md_surface;original = tab.widget.encoded()
        other = self.w.duplicate_view(tab,True);self.w.focus_tab(tab);self.events()
        for mode in ('write','source','read'):
            s.set_mode(mode);view = s.reader if mode == 'read' else s.editor
            QTest.keyClick(view,Qt.Key.Key_Plus,Qt.KeyboardModifier.ControlModifier);self.assertEqual(s.zoom_percent,110)
            event = QWheelEvent(QPointF(30,30),QPointF(view.mapToGlobal(QPoint(30,30))),QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.ControlModifier,Qt.ScrollPhase.NoScrollPhase,False)
            self.app.sendEvent(view.viewport(),event);self.assertEqual(s.zoom_percent,120)
            QTest.keyClick(view,Qt.Key.Key_Minus,Qt.KeyboardModifier.ControlModifier);self.assertEqual(s.zoom_percent,110)
            QTest.keyClick(view,Qt.Key.Key_0,Qt.KeyboardModifier.ControlModifier);self.assertEqual(s.zoom_percent,100)
        self.assertEqual(other.widget.md_surface.zoom_percent,100);self.assertEqual(tab.widget.encoded(),original)
        self.assertFalse(tab.widget.dirty);self.assertFalse(tab.widget.document().isUndoAvailable())

    def test_properties_alignment_draft_cancel_raw_preservation_and_undo(self):
        raw = '---\naliases: [First]\ntags: [novel/draft, rain]\nchecked: false\ncustom:\n  source: notebook\n---\nbody\n'
        (self.root/'Notes.md').write_bytes(raw.encode())
        tab = self.open('Notes.md');s = tab.widget.md_surface
        dialog = PropertyEditor(s);dialog.show();self.events()
        self.assertFalse(dialog.message.isVisible());self.assertFalse(dialog.rows[2][3].check.isChecked())
        self.assertEqual(dialog.rows[2][3].check.text(),'')
        for widgets in dialog.row_widgets:
            self.assertEqual(widgets[0].y(),widgets[1].y());self.assertEqual(widgets[1].y(),widgets[2].y())
        dialog.rows[0][3].setPlainText('Second');dialog.reject();self.assertEqual(tab.widget.encoded(),raw.encode())
        dialog = PropertyEditor(s);dialog.rows[2][3].check.setChecked(True);dialog.submit()
        self.assertIn('checked: true',tab.widget.toPlainText());self.assertIn('custom:\n  source: notebook\n',tab.widget.toPlainText())
        tab.widget.undo();self.assertEqual(tab.widget.encoded(),raw.encode())
        dialog = PropertyEditor(s);tab.widget.insertPlainText('edit');before = tab.widget.encoded();dialog.submit()
        self.assertEqual(tab.widget.encoded(),before);self.assertIn('變更',dialog.message.text());dialog.close()

    def test_display_panel_current_values_changes_without_document_edits(self):
        tab = self.open('Notes.md');s = tab.widget.md_surface;before = tab.widget.encoded()
        s.preferences_menu();self.events();panel = s.display_panel
        self.assertEqual(panel.size.value(),s.preferences['reading_size'])
        panel.size.setValue(16);self.assertAlmostEqual(s.editor.font().pointSizeF(),16)
        panel.spacing.setCurrentIndex(panel.spacing.findData(180));self.assertEqual(s.preferences['line_height'],180)
        s.set_zoom(120);self.assertEqual(panel.zoom.text(),'120%')
        QTest.keyClick(panel,Qt.Key.Key_Escape);self.events();self.assertFalse(panel.isVisible())
        self.assertEqual(tab.widget.encoded(),before);self.assertFalse(tab.widget.dirty)
        s.focus_toolbar();self.events()
        self.assertTrue(self.app.focusWidget().property('keyboardFocus'))

    def test_property_chips_type_actions_and_invalid_new_row(self):
        raw='---\ntags: [novel/draft, rain]\ncustom:\n  source: notebook\n---\nbody\n'
        (self.root/'Notes.md').write_bytes(raw.encode())
        tab=self.open('Notes.md');dialog=PropertyEditor(tab.widget.md_surface);dialog.show();self.events()
        chips=dialog.rows[0][3].tags
        chips.input.setText('車站');QTest.keyClick(chips.input,Qt.Key.Key_Return)
        chips.edit(0);chips.input.setText('小說/修訂');QTest.keyClick(chips.input,Qt.Key.Key_Return)
        chips.remove(1);self.assertEqual(chips.toPlainText(),'小說/修訂\n車站')
        dialog.add_row(focus=True);dialog.submit();QTest.qWait(60)
        _,name,kind,value=dialog.rows[-1]
        self.assertTrue(dialog.message.isVisible())
        self.assertTrue(dialog.scroll.viewport().rect().contains(name.mapTo(dialog.scroll.viewport(),name.rect().center())))
        name.setText('count');kind.open_menu();kind.menu.actions()[2].trigger();kind.menu.close()
        self.assertEqual(kind.currentData(),'number');value.single.setText('3');dialog.submit()
        self.assertIn('tags: ["小說/修訂", "車站"]',tab.widget.toPlainText())
        self.assertIn('count: 3',tab.widget.toPlainText());self.assertIn('custom:\n  source: notebook\n',tab.widget.toPlainText())
        tab.widget.undo();self.assertEqual(tab.widget.encoded(),raw.encode())

    def test_rich_reader_zoom_and_search_use_same_document_controls(self):
        raw = '---\ntags: [rain]\n---\n## Scene\n\nThe rain stopped.\n'
        (self.root/'Notes.md').write_bytes(raw.encode())
        tab = self.open('Notes.md');s = tab.widget.md_surface
        self.assertIsNone(s.reader.rich)
        s.set_mode('read');rich = s.reader.rich
        self.assertIsNotNone(rich)
        for _ in range(60):
            if rich.render_state == 'ready':break
            QTest.qWait(50)
        self.assertEqual(rich.render_state,'ready')
        for _ in range(30):
            if hasattr(rich,'content_margins'):break
            QTest.qWait(30)
        self.assertTrue(hasattr(rich,'content_margins'))
        before_margin = rich.content_margins[0]
        self.assertEqual(s.title_layout.contentsMargins().left(),before_margin)
        QTest.keyClick(rich.focusProxy() or rich,Qt.Key.Key_Plus,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(s.zoom_percent,110);self.assertAlmostEqual(rich.zoomFactor(),1.1)
        expected=[]
        rich.page().runJavaScript('document.querySelector("main").getBoundingClientRect().left',lambda left:expected.append(round(left*rich.zoomFactor())))
        for _ in range(30):
            if expected and s.title_layout.contentsMargins().left() == expected[0]:break
            QTest.qWait(30)
        self.assertTrue(expected)
        self.assertEqual(s.title_layout.contentsMargins().left(),expected[0])
        s.open_find();s.query.setText('stopped');s.find_next()
        for _ in range(30):
            if s.match_count.text() == '1/1':break
            QTest.qWait(30)
        self.assertEqual(s.match_count.text(),'1/1')
        results=[];rich.page().findTextFinished.connect(lambda result:results.append(result.numberOfMatches()))
        s.query.clear()
        for _ in range(30):
            if results:break
            QTest.qWait(30)
        self.assertEqual(results[-1:], [0]);self.assertEqual(s.match_count.text(),'')
        s.query.setText('stopped');s.find_next()
        for _ in range(30):
            if s.match_count.text() == '1/1':break
            QTest.qWait(30)
        self.assertEqual(s.match_count.text(),'1/1');results.clear()
        s.close_panel(s.find_bar)
        for _ in range(30):
            if results:break
            QTest.qWait(30)
        self.assertEqual(results[-1:], [0]);self.assertFalse(s.find_bar.isVisible())
        self.assertEqual(tab.widget.encoded(),raw.encode());self.assertFalse(tab.widget.dirty)


if __name__ == '__main__':unittest.main()
