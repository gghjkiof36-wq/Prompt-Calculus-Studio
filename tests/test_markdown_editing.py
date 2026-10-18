"""B: reachable editing actions, rendering semantics, shared data, and Undo."""
from pathlib import Path
import sys
import tempfile
import unittest

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QInputMethodEvent, QTextCursor, QTextDocument
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from prompt_calculus_studio.workspace_files import MarkdownPage
from prompt_calculus_studio.markdown_source import SourceSnapshot, edited_text, utf16_position
from prompt_calculus_studio.markdown_commands import (format_edits, clear_edits,
    move_section_edits, heading_sections)
from prompt_calculus_studio.markdown_syntax import reading_html, visible_text


class MarkdownEditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-md-b-')
        self.path = Path(self.area.name)/'Note.md'
        self.pages = []
        self.errors = []
        self.old_hook = sys.excepthook
        sys.excepthook = lambda kind,value,trace:self.errors.append(str(value))

    def tearDown(self):
        for page in self.pages:page.close();page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        sys.excepthook = self.old_hook
        self.area.cleanup()
        self.assertEqual(self.errors, [])

    def open(self, source, duplicate=None):
        if duplicate is None:self.path.write_bytes(source.encode() if isinstance(source,str) else source)
        page=MarkdownPage(self.path,duplicate.buffer if duplicate else None)
        self.pages.append(page);page.resize(960,720);page.show();self.app.processEvents()
        return page

    def select(self, page, text):
        source=page.buffer.snapshot().text;start=source.index(text)
        cursor=page.md_surface.editor.textCursor()
        cursor.setPosition(utf16_position(source,start))
        cursor.setPosition(utf16_position(source,start+len(text)),QTextCursor.MoveMode.KeepAnchor)
        page.md_surface.editor.setTextCursor(cursor)
        return start

    def render(self, page):
        page.md_surface.reader.refresh()
        return page.md_surface.reader.document()

    def test_three_modes_are_independent_and_never_rewrite_original_bytes(self):
        raw=b'\xef\xbb\xbf'+('# A 😀\r\n\n**Bold**\rtext\n').encode()
        left=self.open(raw);right=self.open(None,left)
        left.md_surface.set_mode('source');right.md_surface.set_mode('read')
        for mode in ('write','read','source','write'):left.md_surface.set_mode(mode)
        self.assertEqual(right.md_surface.mode,'read')
        self.assertEqual(left.encoded(),raw)
        self.assertFalse(left.dirty);self.assertFalse(left.document().isUndoAvailable())

    def test_reading_keyboard_ime_and_commands_do_not_mutate(self):
        page=self.open('Alpha\nBeta');s=page.md_surface;s.set_mode('read')
        QTest.keyClicks(s.reader,'XYZ');QTest.keyClick(s.reader,Qt.Key.Key_Backspace)
        commit=QInputMethodEvent();commit.setCommitString('中文');self.app.sendEvent(s.reader,commit)
        self.app.sendEvent(page,commit)
        s.query.setText('Alpha');s.replacement.setText('changed');s.replace_all()
        s.format_selection({'bold':True});s.paragraph('heading',2);page.insertPlainText('bad')
        self.assertEqual(page.encoded(),b'Alpha\nBeta');self.assertFalse(page.dirty)

    def test_ime_pending_style_is_one_undo_and_following_typing_keeps_commit(self):
        page=self.open('begin ');s=page.md_surface;page.moveCursor(QTextCursor.MoveOperation.End)
        s.format_selection({'bold':True,'color':'#123456'})
        self.app.sendEvent(s.editor,QInputMethodEvent('中文',[]))
        before=page.encoded();QTest.keyClick(s.editor,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(page.encoded(),before)
        event=QInputMethodEvent();event.setCommitString('中文');self.app.sendEvent(s.editor,event)
        formatted=page.encoded();self.assertIn('中文'.encode(),formatted)
        self.assertFalse(s.editor.textCursor().hasSelection())
        QTest.keyClick(s.editor,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(page.encoded(),b'begin ')
        QTest.keyClick(s.editor,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(page.encoded(),formatted)
        QTest.keyClicks(s.editor,'NEXT')
        self.assertIn('中文NEXT',visible_text(page.buffer.snapshot().text))

    def test_nested_format_then_clear_selection_then_save_reopen_and_shared_undo(self):
        raw=b'\xef\xbb\xbf**bold *mix* tail**\r\n[link](notes_a_b.md)\n'
        left=self.open(raw);right=self.open(None,left);s=left.md_surface
        self.select(left,'mix');s.format_selection({'family':'Microsoft JhengHei UI','size':18,'color':'#123456'})
        styled=left.encoded();self.assertIn(b'<span',styled)
        doc=self.render(left);fmt=doc.find('mix').charFormat()
        self.assertTrue(fmt.fontItalic());self.assertGreater(fmt.fontWeight(),400)
        self.assertEqual(fmt.fontPointSize(),18)
        s.clear_format();cleared=left.encoded()
        self.assertFalse(s.message)
        doc=self.render(left);fmt=doc.find('mix').charFormat()
        self.assertFalse(fmt.fontItalic());self.assertLessEqual(fmt.fontWeight(),400)
        self.assertEqual(visible_text(left.buffer.snapshot().text).splitlines()[0],'bold mix tail')
        self.assertIn(b'(notes_a_b.md)',cleared)
        self.assertTrue(left.save());self.assertEqual(self.path.read_bytes(),cleared)
        reopened=MarkdownPage(self.path);self.pages.append(reopened)
        self.assertEqual(reopened.encoded(),cleared)
        right.undo();self.assertEqual(left.encoded(),styled)
        right.undo();self.assertEqual(left.encoded(),raw)
        right.redo();right.redo();self.assertEqual(left.encoded(),cleared)

    def test_typing_can_turn_off_an_enclosing_style_and_clear_next_input(self):
        page=self.open('**abc def**');s=page.md_surface
        self.select(page,'abc');cursor=s.editor.textCursor();cursor.clearSelection();s.editor.setTextCursor(cursor)
        s.toggle_style('bold');QTest.keyClicks(s.editor,'P')
        self.assertLess(self.render(page).find('P').charFormat().fontWeight(),700)
        self.assertEqual(visible_text(page.buffer.snapshot().text),'abcP def')
        page.undo();self.assertEqual(page.encoded(),b'**abc def**')
        self.select(page,'abc');cursor=s.editor.textCursor();cursor.clearSelection();s.editor.setTextCursor(cursor)
        s.clear_format();QTest.keyClicks(s.editor,'x')
        self.assertLess(self.render(page).find('x').charFormat().fontWeight(),700)
        page.undo();self.assertEqual(page.encoded(),b'**abc def**')
        self.select(page,'abc');cursor=s.editor.textCursor();cursor.clearSelection();s.editor.setTextCursor(cursor)
        s.clear_format();event=QInputMethodEvent();event.setCommitString('中',-1,1);self.app.sendEvent(s.editor,event)
        self.assertEqual(visible_text(page.buffer.snapshot().text),'ab中 def')
        self.assertLess(self.render(page).find('中').charFormat().fontWeight(),700)
        page.undo();self.assertEqual(page.encoded(),b'**abc def**')

    def test_paragraph_indent_is_layout_not_a_code_block(self):
        page=self.open('A paragraph\n\n- task');s=page.md_surface
        s.indent_paragraph(1)
        self.assertIn(b'margin-left:2em',page.encoded())
        self.assertEqual(visible_text(page.buffer.snapshot().text),'A paragraph\n\n- task')
        self.assertGreater(s.editor.document().begin().blockFormat().leftMargin(),0)
        s.indent_paragraph(-1);self.assertEqual(page.encoded(),b'A paragraph\n\n- task')
        s.indent_paragraph(1);self.assertEqual(page.encoded().count(b'<p'),1)
        page.undo();self.assertEqual(page.encoded(),b'A paragraph\n\n- task')
        self.select(page,'task');s.indent_paragraph(1)
        self.assertTrue(page.encoded().endswith(b'    - task'))
        heading=self.open('# Heading\nbody');s=heading.md_surface;s.indent_paragraph(1)
        self.assertEqual(heading_sections(heading.buffer.snapshot().text)[0][3],'Heading')
        self.assertNotIn('# Heading',self.render(heading).toPlainText())
        s.paragraph('heading',2)
        self.assertEqual(heading_sections(heading.buffer.snapshot().text)[0][2],2)

    def test_escaped_punctuation_ordered_sequence_and_hard_break_render_without_rewrite(self):
        raw='\\*literal\\* and `\\*code\\*`  \nnext\n\n3. first\n1. second\n'
        page=self.open(raw);doc=self.render(page)
        self.assertIn('*literal* and \\*code\\*',doc.toPlainText())
        self.assertIn('3. first',doc.toPlainText());self.assertIn('4. second',doc.toPlainText())
        self.assertEqual(page.encoded(),raw.encode())

    def test_format_protects_code_math_entities_and_link_destinations(self):
        for source,target in [('> ```py\n> code\n> ```','code'),('    code','code'),('a $x+y$ b','x+y'),('A &amp; B','amp')]:
            start=source.index(target)
            with self.subTest(source=source),self.assertRaises(ValueError):
                format_edits(SourceSnapshot(source,0),start,start+len(target),{'bold':True})
        for source in ('[**Link**](notes_a_b.md)','[**URL**](https://example.invalid/a_b_c)','snake_case_name'):
            snapshot=SourceSnapshot(source,0)
            after=edited_text(snapshot,clear_edits(snapshot,0,len(source)))[0]
            self.assertEqual(after,source.replace('**',''))

    def test_unknown_html_is_literal_and_safe_rendering_never_hides_it(self):
        for source in ('<b onclick="evil()">text</b>','<span data-custom="keep">text</span>','<span style="display:none">text</span>'):
            with self.subTest(source=source):
                document=QTextDocument();document.setHtml(reading_html(source))
                self.assertEqual(document.toPlainText(),source)

    def test_toolbar_toggle_preserves_other_inline_styles(self):
        page=self.open('***mixed***');s=page.md_surface;self.select(page,'mixed')
        s.format_actions['bold'].trigger()
        fmt=self.render(page).find('mixed').charFormat()
        self.assertFalse(fmt.fontWeight()>400);self.assertTrue(fmt.fontItalic())
        page.undo();self.assertEqual(page.encoded(),b'***mixed***')

    def test_task_click_list_continuation_and_single_undo(self):
        page=self.open('# Tasks\n\n- [ ] First\n');s=page.md_surface;e=s.editor
        self.app.processEvents();page.grab()
        self.assertTrue(e.task_targets)
        target,number=e.task_targets[0]
        point=(target.center()-e.gutter.pos()).toPoint()
        QTest.mouseClick(e.gutter,Qt.MouseButton.LeftButton,pos=point)
        self.assertIn('- [x] First',page.buffer.snapshot().text)
        page.undo();self.assertIn('- [ ] First',page.buffer.snapshot().text)
        self.select(page,'First');cursor=e.textCursor();cursor.clearSelection();e.setTextCursor(cursor)
        QTest.keyClick(e,Qt.Key.Key_Return)
        self.assertIn('First\n- [ ] ',page.buffer.snapshot().text)
        page.undo();self.assertEqual(page.encoded(),b'# Tasks\n\n- [ ] First\n')

    def test_folding_is_per_view_and_revealing_source_is_lossless(self):
        source='# Title\nbody\n## Sub\n- one\n  - child\n# End\nend\n'
        left=self.open(source);right=self.open(None,left)
        left.md_surface.editor.toggle_fold(0)
        self.assertFalse(left.md_surface.editor.document().findBlockByNumber(1).isVisible())
        self.assertTrue(right.md_surface.editor.document().findBlockByNumber(1).isVisible())
        left.md_surface.editor.fold_all(False)
        self.assertTrue(left.md_surface.editor.document().findBlockByNumber(1).isVisible())
        self.assertEqual(left.encoded(),source.encode());self.assertFalse(left.dirty)

    def test_centered_writing_area_reflows_after_resize(self):
        source='# Title\n\n'+('雨停之後，街道慢慢恢復聲音。'*30)+'\n'
        page=self.open(source);surface=page.md_surface;editor=surface.editor
        for width in (2000,960,720,1800):
            page.resize(width,720);self.app.processEvents()
            self.assertEqual(editor.horizontalScrollBar().maximum(),0)
            self.assertGreater(editor.document().findBlockByNumber(2).layout().lineCount(),1)
        surface.set_preference('reading_width',520);self.app.processEvents()
        self.assertEqual(editor.horizontalScrollBar().maximum(),0)
        surface.set_preference('wrap',False);self.app.processEvents()
        self.assertGreater(editor.horizontalScrollBar().maximum(),0)
        surface.set_preference('wrap',True);self.app.processEvents()
        self.assertEqual(editor.horizontalScrollBar().maximum(),0)
        self.assertEqual(page.encoded(),source.encode());self.assertFalse(page.dirty)

    def test_paragraph_alignment_changes_real_layout_and_roundtrips(self):
        page=self.open('Paragraph');s=page.md_surface
        page.moveCursor(QTextCursor.MoveOperation.End)
        before=s.editor.cursorRect().x()
        s.paragraph('align','right');self.app.processEvents()
        self.assertGreater(s.editor.cursorRect().x(),before+100)
        self.assertIn(b'text-align:right',page.encoded())
        self.assertTrue(page.save());page.undo();self.assertEqual(page.encoded(),b'Paragraph')

    def test_replace_all_one_undo_and_reading_find_does_not_touch_bytes(self):
        page=self.open('😀 Same\r\nsame\nSAME');s=page.md_surface
        s.query.setText('same');s.replacement.setText('new');s.replace_all()
        self.assertEqual(page.buffer.snapshot().text,'😀 new\nnew\nnew')
        page.undo();self.assertEqual(page.encoded(),'😀 Same\r\nsame\nSAME'.encode())
        s.set_mode('read');s.find_next();self.assertEqual(s.reader.textCursor().selectedText(),'Same')
        self.assertFalse(page.dirty)

    def test_final_chapter_move_preserves_heading_boundary_and_undo(self):
        raw=b'# A\r\nalpha\n# B\r\nbeta'
        page=self.open(raw);snapshot=page.buffer.snapshot();sections=heading_sections(snapshot.text)
        page.md_surface.apply(move_section_edits(snapshot,*sections[1][:2],0))
        self.assertEqual([item[3] for item in heading_sections(page.buffer.snapshot().text)],['B','A'])
        self.assertTrue(page.save());page.undo();self.assertEqual(page.encoded(),raw)


if __name__ == '__main__':unittest.main()
