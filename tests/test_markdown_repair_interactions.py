"""IME coordinates, preedit presentation and source-preserving image actions."""
import json
import time
import os
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtGui import QInputMethodEvent, QTextCharFormat, QColor, QTextCursor, QImage
from PySide6.QtWidgets import QPlainTextEdit, QDialogButtonBox
from PySide6.QtTest import QTest
import test_markdown_editing as fixture
from prompt_calculus_studio.markdown_image_size import image_at, size_edit
from prompt_calculus_studio.markdown_source import SourceSnapshot, edited_text, SourceEdit
from prompt_calculus_studio.markdown_render import render
from prompt_calculus_studio.markdown_syntax import reading_html


class MarkdownRepairTests(fixture.unittest.TestCase):
    setUpClass=classmethod(fixture.MarkdownEditingTests.setUpClass.__func__)
    setUp=fixture.MarkdownEditingTests.setUp
    tearDown=fixture.MarkdownEditingTests.tearDown
    open=fixture.MarkdownEditingTests.open

    def test_ime_rectangle_follows_centered_viewport_zoom_scroll_and_preedit(self):
        page=self.open('中文😀\n'*80);s=page.md_surface;e=s.editor
        for width,zoom in ((1800,70),(1000,100),(700,140)):
            page.resize(width,700);s.set_zoom(zoom);self.app.processEvents()
            e.moveCursor(QTextCursor.MoveOperation.End);e.ensureCursorVisible();e.setFocus()
            self.app.sendEvent(e,QInputMethodEvent('選字',[]))
            rect=e.inputMethodQuery(Qt.InputMethodQuery.ImCursorRectangle)
            expected=e.cursorRect().translated(e.viewport().pos())
            self.assertLess(abs(rect.x()-expected.x()),1)
            self.assertLess(abs(rect.y()-expected.y()),1)
            self.assertGreater(rect.x(),e.viewport().x())
            self.app.sendEvent(e,QInputMethodEvent())
        self.assertEqual(page.buffer.snapshot().text,'中文😀\n'*80)

    def test_preedit_theme_preserves_cursor_commit_replacement_and_undo(self):
        page=self.open('原文');e=page.md_surface.editor;e.moveCursor(QTextCursor.MoveOperation.End)
        fmt=QTextCharFormat();fmt.setBackground(QColor('white'));fmt.setForeground(QColor('black'))
        fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DashUnderline)
        attrs=[QInputMethodEvent.Attribute(QInputMethodEvent.AttributeType.TextFormat,0,2,fmt),
               QInputMethodEvent.Attribute(QInputMethodEvent.AttributeType.Cursor,1,1,None)]
        observed=[];base=QPlainTextEdit.inputMethodEvent
        def receive(editor,event):
            observed.extend(event.attributes());return base(editor,event)
        with patch.object(QPlainTextEdit,'inputMethodEvent',receive):
            self.app.sendEvent(e,QInputMethodEvent('中文',attrs))
        themed=observed[0].value.toCharFormat()
        self.assertEqual(themed.background().color().name(),e.tokens()['selected'])
        self.assertEqual(themed.foreground().color().name(),e.tokens()['text'])
        self.assertEqual(themed.underlineStyle(),fmt.underlineStyle())
        self.assertEqual(observed[1].start,1)
        self.assertEqual(page.buffer.snapshot().text,'原文')
        event=QInputMethodEvent();event.setCommitString('字',-1,1);self.app.sendEvent(e,event)
        self.assertEqual(page.buffer.snapshot().text,'原字')
        page.undo();self.assertEqual(page.buffer.snapshot().text,'原文')
        page.redo();self.assertEqual(page.buffer.snapshot().text,'原字')
        self.assertFalse(page.buffer.composing)

    def test_image_size_keeps_path_title_unicode_and_other_content(self):
        cases=[('![照片](assets/a.png "title")','![照片|320](assets/a.png "title")'),
               ('![[assets/a.png|照片]]','![[assets/a.png|照片|320]]'),
               ('![照\\]片|120x80](<assets/a.png> "title")','![照\\]片|320](<assets/a.png> "title")'),
               ('![](assets/a.png|600)','![|320](assets/a.png)')]
        for source,expected in cases:
            with self.subTest(source=source):
                raw='😀前文\n'+source+'\n後文'
                link=image_at(raw,raw.index('assets'))
                result,_=edited_text(SourceSnapshot(raw,0),[size_edit(raw,link,320)])
                self.assertEqual(result,'😀前文\n'+expected+'\n後文')
        self.assertIsNone(image_at('`![](a.png)`',5))
        self.assertIsNone(image_at('![[Note.md]]',5))

    def test_image_dialog_cancel_stale_apply_save_reopen_and_history(self):
        raw='前文\n\n![](photo.png)\n\n後文'
        image=QImage(800,400,QImage.Format.Format_RGB32);image.fill(QColor('#8899aa'))
        target=self.path.parent/'photo.png';image.save(str(target));original=target.read_bytes()
        page=self.open(raw);s=page.md_surface;s.navigate(raw.index('photo'))
        action=next(a for a in s.format_actions['insert'].menu().actions() if a.text()=='調整圖片尺寸…')
        action.trigger();dialog=s.image_size_dialog;self.assertTrue(dialog.isVisible())
        dialog.width_field.setValue(320);dialog.reject()
        self.assertEqual(page.buffer.snapshot().text,raw)
        s.resize_image();dialog=s.image_size_dialog;dialog.width_field.setValue(320)
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Apply),Qt.MouseButton.LeftButton)
        updated=raw.replace('![]','![|320]');self.assertEqual(page.buffer.snapshot().text,updated)
        page.undo();self.assertEqual(page.buffer.snapshot().text,raw)
        page.redo();self.assertEqual(page.buffer.snapshot().text,updated)
        self.assertEqual(target.read_bytes(),original)
        # Stale dialog must not write to a later revision.
        s.navigate(updated.index('photo'));s.resize_image();dialog=s.image_size_dialog
        s.apply([SourceEdit(0,0,'new ')]);dialog.width_field.setValue(640);dialog.submit()
        self.assertIn('new ',page.buffer.snapshot().text);self.assertIn('|320',page.buffer.snapshot().text)
        self.assertFalse(dialog.message.isHidden());dialog.reject()
        # The persisted source is sufficient to reconstruct display size.
        self.assertTrue(page.save());saved=self.path.read_bytes();page.close()
        reopened=self.open(saved)
        self.assertEqual(reopened.buffer.snapshot().text,page.buffer.snapshot().text)
        result=render(reopened.buffer.snapshot().text,self.path,self.path.parent)
        self.assertEqual(result.attachments[0]['width'],320)

    def test_unicode_line_breaks_and_image_lines_keep_text_below_images(self):
        media=lambda entry,index:'<img src="example.png">'
        source='前文\u2028![](a.png)\u2028\u2028後文\u2028![](b.png)'
        result=render(source,self.path,self.path.parent,media_renderer=media)
        self.assertRegex(result.body,r'前文<br\s*/?>\s*<img')
        self.assertRegex(result.body,r'<img[^>]*><br\s*/?>\s*<br\s*/?>\s*後文')
        self.assertIn('<br/>',reading_html('前文\u2028後文'))
        result=render('前文\n![](a.png)\n後文',self.path,self.path.parent,media_renderer=media)
        self.assertRegex(result.body,r'<img[^>]*><br\s*/?>\s*後文')
        inline=render('前 ![](a.png) 後',self.path,self.path.parent,media_renderer=media)
        self.assertNotIn('<br',inline.body)
        code=render('`a\u2028b`',self.path,self.path.parent)
        self.assertNotIn('<br',code.body)

    def test_insert_attachment_separates_prose_with_single_undo(self):
        from prompt_calculus_studio.markdown_attachments import insert
        page=self.open('前文後文');s=page.md_surface;s.navigate(2)
        image=QImage(20,10,QImage.Format.Format_RGB32);image.fill(QColor('red'))
        self.assertTrue(insert(s,image=image));text=page.buffer.snapshot().text
        self.assertRegex(text,r'^前文\n\n!\[\]\(assets/[^)]+\)\n\n後文$')
        page.undo();self.assertEqual(page.buffer.snapshot().text,'前文後文')
        page.redo();self.assertEqual(page.buffer.snapshot().text,text)

    def test_reader_image_geometry_and_outline_at_small_and_large_zoom(self):
        image=QImage(800,400,QImage.Format.Format_RGB32);image.fill(QColor('#465d69'))
        image.save(str(self.path.parent/'photo.png'))
        raw='# 圖片排版\n\n前段文字\u2028![圖片|320](photo.png)\u2028\u2028後段文字\n\n## 下一節\n\n內容'
        page=self.open(raw);s=page.md_surface;page.resize(1800,800)
        s.set_mode('read');s.toggle_outline()
        def wait(condition):
            until=time.monotonic()+12
            while not condition() and time.monotonic()<until:QTest.qWait(20)
            self.assertTrue(condition())
        wait(lambda:s.reader.rich is not None and s.reader.rich.render_state=='ready')
        rich=s.reader.rich
        def js(code):
            values=[];rich.page().runJavaScript('JSON.stringify('+code+')',values.append)
            wait(lambda:bool(values));return json.loads(values[0])
        wait(lambda:js('document.querySelector("main img")?.naturalWidth>0'))
        for zoom in (70,140):
            s.set_zoom(zoom);QTest.qWait(250)
            state=js('''(()=>{const img=document.querySelector('main img');const r=img.getBoundingClientRect();
                const walk=document.createTreeWalker(document.querySelector('main'),NodeFilter.SHOW_TEXT);
                let node;while(node=walk.nextNode())if(node.textContent.includes('後段文字')){
                    const range=document.createRange();range.selectNodeContents(node);
                    return {width:r.width,height:r.height,bottom:r.bottom,textTop:range.getBoundingClientRect().top};}
                return null;})()''')
            self.assertAlmostEqual(state['width'],320,delta=1)
            self.assertAlmostEqual(state['height'],160,delta=1)
            self.assertGreaterEqual(state['textTop'],state['bottom'])
            self.assertEqual(s.outline_panel.x(),0)
            output=os.environ.get('PCS_MD_REPAIR_SCREENSHOTS')
            if output:
                Path(output).mkdir(parents=True,exist_ok=True)
                page.grab().save(str(Path(output)/f'reader-{zoom}.png'))
        self.assertEqual(page.encoded(),raw.encode())
