"""D links/properties/file operations through shared Qt editor lifetimes."""
import unittest
from unittest.mock import patch
from PySide6.QtTest import QTest
from prompt_calculus_studio.markdown_relations import LinkDialog,open_link
from prompt_calculus_studio.markdown_property_editor import PropertyEditor
from prompt_calculus_studio.markdown_relocation import checked_write
import test_workspace_panes as fixture


class RelationWorkspaceTests(unittest.TestCase):
    setUpClass=classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp=fixture.WorkspacePaneTests.setUp
    tearDown=fixture.WorkspacePaneTests.tearDown
    open=fixture.WorkspacePaneTests.open
    events=fixture.WorkspacePaneTests.events
    def test_rename_updates_incoming_dirty_shared_views_and_undo(self):
        raw=b'\xef\xbb\xbf# Reader\r\n[[Notes|Alias]] [link](Notes.md#Notes)\r\n'
        (self.root/'Incoming.md').write_bytes(raw)
        source=self.open('Notes.md');reader=self.open('Incoming.md',retained=True,new_tab=True)
        other=self.w.duplicate_view(reader,True)
        reader.widget.insertPlainText('Unfinished ');draft=reader.widget.encoded()
        context=self.w.commands.context('tab',tab=source)
        target=self.w.commands.files.run('rename',context,name='Renamed')
        self.assertEqual(target,self.root/'Renamed.md')
        self.assertFalse((self.root/'Notes.md').exists())
        self.assertEqual(source.path,target)
        self.assertIn(b'[[Renamed|Alias]]',reader.path.read_bytes())
        self.assertTrue(reader.path.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'Unfinished',reader.path.read_bytes())
        self.assertIn('Unfinished',other.widget.toPlainText());self.assertIs(reader.widget.buffer,other.widget.buffer)
        other.widget.undo();self.assertEqual(reader.widget.encoded(),draft)
        reader.widget.redo();self.assertIn('[[Renamed|Alias]]',other.widget.toPlainText())
        self.assertTrue(self.w.save_tab(reader));self.assertIn(b'Unfinished',reader.path.read_bytes())
    def test_move_updates_relative_images_and_external_references(self):
        (self.root/'assets').mkdir();(self.root/'assets/image.png').write_bytes(b'synthetic')
        original=b'\xef\xbb\xbf# Notes\r\n![scene](assets/image.png)\r\n[[Empty]]\r\n'
        (self.root/'Notes.md').write_bytes(original)
        (self.root/'Incoming.md').write_text('[[Notes]] [x](Notes.md)\n',encoding='utf-8')
        source=self.open('Notes.md');other=self.w.duplicate_view(source,True)
        target=self.w.commands.files.run('move',self.w.commands.context('tab',tab=source),directory=self.root/'Folder')
        self.assertEqual(target,self.root/'Folder/Notes.md')
        self.assertEqual(other.path,target);self.assertEqual(source.widget.path,target)
        moved=target.read_bytes();self.assertIn(b'../assets/image.png',moved);self.assertIn(b'\r\n',moved)
        incoming=(self.root/'Incoming.md').read_text(encoding='utf-8');self.assertIn('[[Folder/Notes]]',incoming)
        self.assertTrue(self.w.save_tab(other));self.assertFalse((self.root/'Notes.md').exists())
    def test_failed_second_reference_write_restores_disk_and_views(self):
        for name in ('A','B'):(self.root/(name+'.md')).write_text('[[Notes]]\n',encoding='utf-8')
        source=self.open('Notes.md');reader=self.open('A.md',retained=True,new_tab=True)
        original={p:p.read_bytes() for p in self.root.glob('*.md')};calls=[]
        def failing(path,before,after):
            calls.append(path)
            if len(calls)==2:raise OSError('synthetic write failure')
            return checked_write(path,before,after)
        with patch('prompt_calculus_studio.markdown_relocation.checked_write',failing):
            result=self.w.commands.files.run('rename',self.w.commands.context('tab',tab=source),name='Renamed')
        self.assertIsNone(result);self.assertFalse((self.root/'Renamed.md').exists())
        self.assertEqual({p:p.read_bytes() for p in self.root.glob('*.md')},original)
        self.assertEqual(source.path,self.root/'Notes.md');self.assertEqual(reader.widget.toPlainText(),'[[Notes]]\n')
        self.assertFalse(self.w.autosave.paused)
    def test_save_as_rewrites_outgoing_only_and_keeps_original(self):
        (self.root/'Notes.md').write_text('[Empty](Empty.md)\n',encoding='utf-8')
        (self.root/'Incoming.md').write_text('[[Notes]]\n',encoding='utf-8')
        tab=self.open('Notes.md');other=self.w.duplicate_view(tab,True)
        target=self.w.commands.files.run('save_as',self.w.commands.context('tab',tab=tab),name='Copy',directory=self.root/'Folder')
        self.assertEqual(target,self.root/'Folder/Copy.md');self.assertIn('../Empty.md',target.read_text(encoding='utf-8'))
        self.assertEqual((self.root/'Notes.md').read_text(encoding='utf-8'),'[Empty](Empty.md)\n')
        self.assertEqual((self.root/'Incoming.md').read_text(encoding='utf-8'),'[[Notes]]\n')
        self.assertEqual(other.path,target);self.assertFalse(tab.widget.dirty)
    def test_link_and_property_drafts_cancel_stale_guard_and_single_undo(self):
        tab=self.open('Notes.md');surface=tab.widget.md_surface;original=tab.widget.encoded()
        dialog=LinkDialog(surface);dialog.target.setText('Empty');dialog.reject();self.assertEqual(tab.widget.encoded(),original)
        dialog=LinkDialog(surface);dialog.target.setText('Empty');dialog.label.setText('next');dialog.submit()
        self.assertIn('[[Empty|next]]',tab.widget.toPlainText());tab.widget.undo();self.assertEqual(tab.widget.encoded(),original)
        properties=PropertyEditor(surface);properties.add_row();_,name,kind,value=properties.rows[-1]
        name.setText('tags');kind.setCurrentIndex(kind.findData('tags'));value.setPlainText('novel/test\nscene');properties.submit()
        self.assertIn('tags: ["novel/test", "scene"]',tab.widget.toPlainText());tab.widget.undo();self.assertEqual(tab.widget.encoded(),original)
        dialog=LinkDialog(surface);dialog.target.setText('Empty');tab.widget.insertPlainText('changed');before=tab.widget.encoded();dialog.submit()
        self.assertEqual(tab.widget.encoded(),before);self.assertIn('變更',dialog.error.text())
        for item in surface.findChildren(LinkDialog)+surface.findChildren(PropertyEditor):item.close()
    def test_explicit_link_open_finds_heading_without_touching_source(self):
        (self.root/'Target.md').write_text('# Start\n\n## Scene\ntext\n',encoding='utf-8')
        tab=self.open('Notes.md');before=tab.widget.encoded()
        open_link(tab.widget.md_surface,'Target#Scene',wiki=True);self.events()
        target=self.w.tab_for_path(self.root/'Target.md');self.assertIsNotNone(target)
        self.assertEqual(target.widget.textCursor().position(),9);self.assertEqual(tab.widget.encoded(),before)

if __name__=='__main__':unittest.main()
