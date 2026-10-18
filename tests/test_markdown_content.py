"""C operations preserve the source authority and cancellation/Undo boundaries."""
from pathlib import Path
import tempfile
import unittest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent,Qt
from PySide6.QtTest import QTest
from prompt_calculus_studio.workspace_files import MarkdownPage
from prompt_calculus_studio.markdown_source import SourceEdit
from prompt_calculus_studio.markdown_tables import tables,TableDialog,decode_cell
from prompt_calculus_studio.markdown_paste import from_html
from prompt_calculus_studio.markdown_render import render,local_target
from markdown_it import MarkdownIt


class ContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.path=Path(self.area.name)/'Note.md';self.pages=[]
    def tearDown(self):
        for page in self.pages:page.close();page.deleteLater()
        self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents();self.area.cleanup()
    def open(self,source):
        self.path.write_bytes(source.encode() if isinstance(source,str) else source)
        page=MarkdownPage(self.path);self.pages.append(page);page.resize(960,720);page.show();self.app.processEvents();return page
    def table_dialog(self,page):
        snap=page.buffer.snapshot();return TableDialog(page.md_surface,tables(snap.text)[0],snap)

    def test_table_draft_cancel_escaping_multiline_and_one_undo(self):
        raw=b'\xef\xbb\xbf'+('前文\r\n\r\n| 名稱 | 數量 | 註記 |\n| --- | ---: | :---: |\n| 中文 | 10 | **粗體** [連結](a.md) |\n| A\\|B | 2 | 一<br>二 |\n\n未知 <x z="v">保持</x>\r\n').encode()
        page=self.open(raw);before=page.buffer.snapshot().text
        dialog=self.table_dialog(page);dialog.grid.item(1,0).setText('不應保存');dialog.reject()
        self.assertEqual(page.buffer.snapshot().text,before)
        dialog=self.table_dialog(page);dialog.grid.setCurrentCell(2,0)
        dialog.grid.paste_text('"空格\n次行"\t4\t"含 | 符號"\n')
        dialog.grid.setCurrentCell(1,1);dialog.sort_rows(False);dialog.set_alignment('center');dialog.commit()
        after=page.buffer.snapshot().text;parsed=tables(after)[0]
        self.assertEqual(decode_cell(parsed.rows[1][0]),'空格\n次行')
        self.assertEqual(decode_cell(parsed.rows[1][2]),'含 | 符號')
        self.assertIn('**粗體** [連結](a.md)',after)
        self.assertTrue(page.save());saved=self.path.read_bytes()
        self.assertTrue(saved.startswith(b'\xef\xbb\xbf'+ '前文\r\n\r\n'.encode()))
        self.assertTrue(saved.endswith('未知 <x z="v">保持</x>\r\n'.encode()))
        page.undo();self.assertEqual(page.buffer.snapshot().text,before)
        page.redo();self.assertEqual(page.buffer.snapshot().text,after)

    def test_table_row_column_operations_and_stale_draft(self):
        page=self.open('| Z | A |\n| ---: | :--- |\n| a | 3 |\n| b | 1 |\n')
        dialog=self.table_dialog(page);dialog.grid.setCurrentCell(1,0);dialog.move_row(1)
        self.assertEqual(dialog.grid.item(2,0).text(),'a')
        dialog.sort_columns();self.assertEqual(dialog.grid.item(0,0).text(),'A');self.assertEqual(dialog.align,['left','right'])
        dialog.add_column();dialog.add_row();self.assertEqual((dialog.grid.rowCount(),dialog.grid.columnCount()),(4,3))
        dialog.remove_row();dialog.remove_column();self.assertEqual((dialog.grid.rowCount(),dialog.grid.columnCount()),(3,2))
        snap=page.buffer.snapshot();page.buffer.apply(snap,[SourceEdit(len(snap.text),len(snap.text),'新增')]);after=page.buffer.snapshot().text
        dialog.commit();self.assertEqual(page.buffer.snapshot().text,after);self.assertNotEqual(dialog.result(),dialog.DialogCode.Accepted)

    def test_noop_table_does_not_normalize_original_or_add_undo(self):
        source='名字 |數量\n:-----|------:\n 中文|  2\n'
        page=self.open(source);self.table_dialog(page).commit()
        self.assertEqual(page.buffer.snapshot().text,source);self.assertFalse(page.buffer.text.isUndoAvailable())

    def test_table_keyboard_cell_commit_and_tab(self):
        page=self.open('| A | B |\n| --- | --- |\n| 中 | 文 |\n')
        dialog=self.table_dialog(page);dialog.show();self.app.processEvents()
        grid=dialog.grid;grid.setCurrentCell(1,0);grid.editItem(grid.item(1,0));self.app.processEvents()
        editor=QApplication.focusWidget();editor.setPlainText('新\n行');QTest.keyClick(editor,Qt.Key.Key_Tab);self.app.processEvents()
        self.assertEqual(grid.item(1,0).text(),'新\n行');self.assertEqual(grid.currentColumn(),1);dialog.reject()

    def test_pasted_table_literal_slashes_and_code_survive_render_and_reopen(self):
        from prompt_calculus_studio.markdown_tables import encode_cell
        md=MarkdownIt('commonmark').enable('table')
        for code in (False,True):
            for count in range(4):
                with self.subTest(code=code,count=count):
                    value='X'+'\\'*count+'|Y'
                    cell='<code>'+value+'</code>' if code else value
                    source=from_html('<table><tr><th>A</th></tr><tr><td>'+cell+'</td></tr></table>')
                    table=tables(source)[0]
                    editable=('`'+value+'`') if code else value
                    self.assertEqual(decode_cell(table.rows[1][0]),editable)
                    self.assertEqual(decode_cell(encode_cell(editable)),editable)
                    inline=[t for t in md.parse(source) if t.type=='inline'][1]
                    self.assertEqual(''.join(t.content for t in inline.children),value)
                    page=self.open(source)
                    dialog=self.table_dialog(page);dialog.grid.item(1,0).setText(editable+'!');dialog.commit()
                    self.assertTrue(page.save());saved=page.encoded()
                    page.undo();self.assertEqual(page.buffer.snapshot().text,source)
                    page.redo();self.assertEqual(page.encoded(),saved)

    def test_html_paste_preserves_code_blank_lines_and_backticks(self):
        source=from_html('<p>中文 <strong>粗體</strong></p><pre><code>A\n\n\nB\n```</code></pre><p><code>a`b</code></p><script>bad()</script>')
        tokens=MarkdownIt('commonmark').parse(source)
        self.assertEqual([t.content for t in tokens if t.type=='fence'],['A\n\n\nB\n```\n'])
        code=[c.content for t in tokens for c in (t.children or []) if c.type=='code_inline']
        self.assertEqual(code,['a`b']);self.assertNotIn('bad()',source)

    def test_render_keeps_editable_sources_and_hides_only_real_comments(self):
        source='%% hidden %%\n\n`%% code %%`\n\n$$x^2$$\n\n```mermaid\nA-->B\n```\n\n```python\nprint(1)\n```\n\n文字[^n]\n\n[^n]: 腳註\n'
        projection=render(source,self.path,self.path.parent)
        self.assertNotIn('hidden',projection.body);self.assertIn('%% code %%',projection.body)
        self.assertEqual(projection.codes,['print(1)\n']);self.assertEqual(projection.diagrams,['A-->B\n'])
        self.assertEqual(projection.math,[{'source':'x^2','block':True}])
        self.assertIn('footnote-backref',projection.body);self.assertIn('編輯腳註',projection.body)

    def test_resource_resolution_rejects_external_paths_and_keeps_encoded_names(self):
        self.assertIsNone(local_target('../outside.png',self.path,self.path.parent))
        self.assertIsNone(local_target('https://example.invalid/a.png',self.path,self.path.parent))
        self.assertIsNone(local_target('.obsidian/secret.png',self.path,self.path.parent))
        self.assertEqual(local_target('a%23b.png',self.path,self.path.parent),self.path.parent/'a#b.png')

    def test_single_pane_reader_fills_outer_editor_on_first_show_and_resize(self):
        from prompt_calculus_studio.workspace_shell import WorkspaceShell
        root=self.path.parent/'documents';root.mkdir();path=root/'geometry.md'
        path.write_text('# 圖片\n\n![](missing.png)\n',encoding='utf-8')
        window=WorkspaceShell(root,self.path.parent/'settings')
        window.resize(1440,900);tab=window.open_path(path);surface=tab.widget.md_surface
        surface.set_mode('read');window.show();QTest.qWait(80)
        try:
            for width in (1440,960):
                window.resize(width,900);QTest.qWait(80)
                self.assertEqual(surface.geometry(),tab.widget.rect())
                self.assertEqual(surface.reader.rich.geometry(),surface.reader.rect())
                self.assertFalse(surface.reader.viewport().isVisible())
        finally:
            window.close();window.deleteLater();self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents()


if __name__=='__main__':unittest.main()
