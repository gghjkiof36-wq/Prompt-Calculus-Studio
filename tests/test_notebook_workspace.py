import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt,QThreadPool,QPoint
from PySide6.QtGui import QTextCursor,QInputMethodEvent
from PySide6.QtTest import QTest
from prompt_calculus_studio.markdown_source import SourceEdit,utf16_position as qt_position
from prompt_calculus_studio.notebook_composer import Composition
from prompt_calculus_studio.notebook_dialogs import RecoveryDialog,WorkspacesDialog
import test_workspace_panes as fixture


class NotebookWorkspaceTests(unittest.TestCase):
    setUpClass=classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp=fixture.WorkspacePaneTests.setUp
    open=fixture.WorkspacePaneTests.open
    events=fixture.WorkspacePaneTests.events
    def tearDown(self):
        self.w.notebook.stop();QThreadPool.globalInstance().waitForDone(3000)
        for dialog in list(self.w.notebook.dialogs):dialog.close()
        fixture.WorkspacePaneTests.tearDown(self)
    def note(self,text,name='Notes.md'):
        (self.root/name).write_bytes(text.encode());return self.open(name).widget.md_surface
    def wait_index(self):
        for _ in range(100):
            QTest.qWait(20)
            if self.w.notebook.job is None:return
        self.fail('index timed out')
    def test_sidebar_query_bookmarks_and_commands(self):
        s=self.note('---\ntags: [小說/初稿]\n---\n# 雨夜\n地圖與線索')
        m=self.w.notebook;m.show('search');m.panel.query.setText('tag:小說 地圖');m.refresh();self.wait_index()
        self.assertEqual(m.panel.results.topLevelItemCount(),1)
        self.assertFalse(self.w.tree.isVisible());m.bookmark_search();m.show('bookmarks');self.wait_index()
        self.assertEqual(m.panel.results.topLevelItemCount(),1)
        m.show('files');self.assertTrue(self.w.tree.isVisible());s.open_commands()
        names=[name for name,_ in s.commands]
        self.assertIn('匯出 PDF…',names);self.assertIn('合併目前筆記…',names)
    def test_template_daily_unique_and_cancel(self):
        s=self.note('正文')
        a=self.w.notebook.operations();a.create('範本/日誌','---\ntype: daily\n---\n{{date}}')
        self.w.notebook.store.put('notebook:'+str(self.root),{'daily_template':'範本/日誌.md'})
        first=a.daily();again=a.daily();self.assertEqual(first,again)
        self.assertIn('type: daily',first.read_text(encoding='utf-8'))
        self.assertNotEqual(a.unique(),a.unique())
    def test_merge_rewrites_links_retains_source_and_undo(self):
        raw=b'\xef\xbb\xbf# Origin\r\n\r\nBody\r\n';(self.root/'Notes.md').write_bytes(raw)
        target=self.root/'Target.md';target.write_text('# Target\n',encoding='utf-8')
        ref=self.root/'Ref.md';ref.write_text('[[Notes#Origin]]',encoding='utf-8')
        s=self.open('Notes.md').widget.md_surface;a=self.w.notebook.operations()
        plan=Composition(a,s,target);plan.commit()
        self.assertFalse((self.root/'Notes.md').exists());self.assertIn('Body',target.read_text())
        self.assertEqual(ref.read_text(),'[[Target#Origin]]');self.assertTrue((plan.journal/'merged-source.md').exists())
        plan.undo();self.assertEqual((self.root/'Notes.md').read_bytes(),raw);self.assertEqual(ref.read_text(),'[[Notes#Origin]]')
    def test_extract_refuses_stale_and_rolls_back_on_failure(self):
        s=self.note('# A\n第一段\n\n# B\n第二段\n');a=self.w.notebook.operations();target=self.root/'Extract.md'
        text=s.page.buffer.snapshot().text;start=text.index('# B');cursor=s.editor.textCursor()
        cursor.setPosition(qt_position(text,start));cursor.movePosition(QTextCursor.MoveOperation.End,QTextCursor.MoveMode.KeepAnchor);s.editor.setTextCursor(cursor)
        plan=Composition(a,s,target,True);s.apply([SourceEdit(0,0,'改動')])
        with self.assertRaises(ValueError):plan.commit()
        self.assertFalse(target.exists());s.page.undo()
        text=s.page.buffer.snapshot().text;cursor=s.editor.textCursor();cursor.setPosition(qt_position(text,start));cursor.movePosition(QTextCursor.MoveOperation.End,QTextCursor.MoveMode.KeepAnchor);s.editor.setTextCursor(cursor)
        plan=Composition(a,s,target,True)
        with patch('prompt_calculus_studio.notebook_composer.checked_write',side_effect=OSError('denied')):
            with self.assertRaises(OSError):plan.commit()
        self.assertFalse(target.exists());self.assertIn('第二段',s.page.buffer.snapshot().text)
        plan=Composition(a,s,target,True);plan.commit();self.assertIn('[[Extract]]',(self.root/'Notes.md').read_text(encoding='utf-8'))
        self.assertIn('第二段',target.read_text(encoding='utf-8'));plan.undo();self.assertFalse(target.exists())
    def test_recovery_is_undoable_and_stale_guarded(self):
        s=self.note('原稿');m=self.w.notebook;m.store.capture(s.page.path,s.page.encoded(),'原稿',True)
        s.apply([SourceEdit(0,2,'修改')]);self.w.save_current();dialog=RecoveryDialog(m,s)
        dialog.list.setCurrentRow(dialog.list.count()-1);dialog.restore()
        self.assertEqual(s.page.buffer.snapshot().text,'原稿');s.page.undo();self.assertEqual(s.page.buffer.snapshot().text,'修改')
        dialog.close()
    def test_multi_cursor_single_undo_unicode_and_ime(self):
        s=self.note('雨😀 one\none\none');e=s.editor
        cursor=e.textCursor();cursor.setPosition(4);cursor.movePosition(QTextCursor.MoveOperation.EndOfWord,QTextCursor.MoveMode.KeepAnchor);e.setTextCursor(cursor)
        s.advanced.next_occurrence();s.advanced.next_occurrence();self.assertEqual(len(s.advanced.cursors),2)
        QTest.keyClicks(e,'X');self.assertEqual(s.page.buffer.snapshot().text,'雨😀 X\nX\nX')
        s.page.undo();self.assertEqual(s.page.buffer.snapshot().text,'雨😀 one\none\none')
        event=QInputMethodEvent('中文',[]);self.app.sendEvent(e,event);self.assertFalse(s.advanced.cursors)
        done=QInputMethodEvent();done.setCommitString('中');self.app.sendEvent(e,done)
    def test_vim_and_slash_are_optional(self):
        s=self.note('one two\nnext');s.preferences['vim']=True;s.advanced.configure();e=s.editor
        e.moveCursor(QTextCursor.MoveOperation.Start);QTest.keyClicks(e,'dw');self.assertEqual(s.page.buffer.snapshot().text,'two\nnext')
        QTest.keyClicks(e,'u');self.assertEqual(s.page.buffer.snapshot().text,'one two\nnext')
        QTest.keyClicks(e,'iZ');self.assertTrue(s.page.buffer.snapshot().text.startswith('Z'))
        QTest.keyClick(e,Qt.Key.Key_Escape);self.assertEqual(s.advanced.vim_mode,'normal')
    def test_named_workspace_preserves_content_and_missing_files(self):
        s=self.note('內容');m=self.w.notebook;dialog=WorkspacesDialog(m);dialog.name.setText('寫作');dialog.run_save()
        self.w.new_tab();dialog.load();self.assertEqual(m.surface().page.buffer.snapshot().text,'內容')
        dialog.close()

    def test_composer_buffer_failure_and_undo_failure_are_atomic(self):
        s=self.note('# Original\nDraft');a=self.w.notebook.operations()
        target=self.root/'Target.md';target.write_bytes(b'# Target\r\n')
        source=s.page.path
        t=self.w.open_path(target,retained=True,new_tab=True).widget.md_surface
        self.w.notebook.open_note(source);s=self.w.notebook.surface()
        plan=Composition(a,s,target)
        with patch.object(t.page.buffer,'apply',side_effect=ValueError('injected')):
            with self.assertRaises(ValueError):plan.commit()
        self.assertEqual(target.read_bytes(),b'# Target\r\n')
        self.assertTrue(s.page.path.exists());self.assertEqual(t.page.buffer.snapshot().text,'# Target\n')
        s.apply([SourceEdit(len(s.page.buffer.snapshot().text),len(s.page.buffer.snapshot().text),' unsaved')])
        expected=s.page.encoded();source=s.page.path;plan=Composition(a,s,target);plan.commit()
        after=target.read_bytes();real=plan.restore_file;calls=[]
        def fail_second(change):
            calls.append(change)
            if len(calls)==2:raise OSError('injected undo failure')
            return real(change)
        with patch.object(plan,'restore_file',side_effect=fail_second):
            with self.assertRaises(OSError):plan.undo()
        self.assertEqual(target.read_bytes(),after);self.assertFalse(source.exists())
        plan.undo();restored=self.w.tab_for_path(source).widget
        self.assertEqual(restored.encoded(),expected);self.assertTrue(restored.dirty)
        self.assertEqual(source.read_bytes(),b'# Original\nDraft')

    def test_composer_prepend_keeps_yaml_and_cancel_has_no_changes(self):
        s=self.note('---\ntype: scene\n---\n\nA');a=self.w.notebook.operations()
        target=self.root/'Target.md';raw=b'---\nstatus: draft\n---\n\nB';target.write_bytes(raw)
        plan=Composition(a,s,target,prepend=True)
        self.assertEqual(target.read_bytes(),raw);self.assertTrue(s.page.path.exists())
        value=next(c['after'] for c in plan.changes if c['path']==target)
        self.assertTrue(value.startswith(b'---\n'));self.assertIn(b'type: scene',value)
        self.assertLess(value.find(b'A'),value.find(b'B'))

    def test_spelling_custom_dictionary_shortcuts_and_snapshot_undo(self):
        from prompt_calculus_studio.markdown_shortcuts import apply_shortcuts
        from PySide6.QtWidgets import QMenu
        s=self.note('helo world');s.preferences['spellcheck']=True;s.advanced.configure();s.advanced.check_spelling()
        self.assertIn('helo',[word for _,_,word in s.advanced.misspellings])
        menu=QMenu();s.advanced.spelling_menu(menu,1)
        next(action for action in menu.actions() if action.text()=='hello').trigger()
        self.assertEqual(s.page.buffer.snapshot().text,'hello world')
        s.page.undo();self.assertEqual(s.page.buffer.snapshot().text,'helo world')
        next(action for action in menu.actions() if action.text()=='加入字典').trigger()
        self.assertNotIn('helo',[word for _,_,word in s.advanced.misspellings])
        self.assertIn('helo',self.w.notebook.store.get('spelling:en',[]))
        other=self.note('helo again','Other.md');other.preferences['spellcheck']=True
        other.advanced.configure();other.advanced.check_spelling()
        self.assertNotIn('helo',[word for _,_,word in other.advanced.misspellings])
        self.w.notebook.open_note(s.page.path)
        self.w.notebook.store.put('markdown-shortcuts',{'Ctrl+F':'Ctrl+Alt+F'})
        apply_shortcuts(s);s.find_bar.hide();s.editor.setFocus()
        QTest.keyClick(s.editor,Qt.Key.Key_F,Qt.KeyboardModifier.ControlModifier)
        self.assertFalse(s.find_bar.isVisible())
        before=s.page.buffer.snapshot();s.apply([SourceEdit(0,4,'hello')]);self.assertNotEqual(s.page.buffer.snapshot().text,before.text)
        s.page.undo();self.assertEqual(s.page.buffer.snapshot().text,before.text)
        s.page.redo();self.assertEqual(s.page.buffer.snapshot().text,'hello world')

    def test_incremental_line_metadata_matches_full_parser(self):
        from prompt_calculus_studio.markdown_syntax import line_info
        from prompt_calculus_studio.markdown_view import SourceView
        initial='---\ntype: scene\n---\n\n## A\nbody\n\n1. item\n2. next\n\n```\nraw\n```\n'
        for replacement in ('words','## title','```','$$','> quote','    code','- item','---','<p>para</p>'):
            text=initial.replace('body',replacement);actual=SourceView.line_metadata(text,line_info(initial));expected=line_info(text)
            simplify=lambda rows:[(r['text'],r['start'],r['end'],r['protected'],r['display_marker'],r['heading'].groups() if r['heading'] else None) for r in rows]
            self.assertEqual(simplify(actual),simplify(expected),replacement)

    def test_sidebar_refresh_retains_collapsed_selection_and_scroll(self):
        s=self.note('---\ntags: [people/main, people/other, place/city]\n---\nText')
        m=self.w.notebook;m.show('tags');m.refresh();self.wait_index();p=m.panel
        parent=p.results.topLevelItem(0);parent.setExpanded(False);p.results.setCurrentItem(parent)
        p.populate(p.records);self.assertIs(p.results.topLevelItem(0),parent)
        new=self.root/'Other.md';new.write_text('---\ntags: [people/main]\n---\nX',encoding='utf-8')
        m.refresh();self.wait_index()
        selected=p.results.currentItem();self.assertIsNotNone(selected);self.assertEqual(selected.text(0),'people  2')
        self.assertFalse(selected.isExpanded())

    def test_workspace_overwrite_delete_undo_and_disabled_actions(self):
        self.note('Content');d=WorkspacesDialog(self.w.notebook)
        self.assertFalse(d.save_button.isEnabled());self.assertFalse(d.load_button.isEnabled());self.assertFalse(d.delete_button.isEnabled())
        d.name.setText('Writing');d.run_save();before=dict(d.values)
        self.w.new_tab();d.run_save();self.assertNotEqual(d.values,before)
        d.undo();self.assertEqual(d.values,before);d.remove();self.assertFalse(d.values)
        self.assertFalse(d.load_button.isEnabled());d.undo();self.assertEqual(d.values,before);d.close()

    def test_sidebar_repeated_backlinks_keep_first_occurrence_selected(self):
        self.note('Current');ref=self.root/'Ref.md';ref.write_text('[[Notes]]\n\n[[Notes]]',encoding='utf-8')
        m=self.w.notebook;m.show('links');m.refresh();self.wait_index();p=m.panel
        group=next(p.results.topLevelItem(i) for i in range(p.results.topLevelItemCount()) if p.results.topLevelItem(i).childCount()==2)
        p.results.setCurrentItem(group.child(0));first=group.text(0)
        ref.write_text('Prefix\n[[Notes]]\n\n[[Notes]]',encoding='utf-8');m.refresh();self.wait_index()
        group=next(p.results.topLevelItem(i) for i in range(p.results.topLevelItemCount()) if p.results.topLevelItem(i).text(0)==first)
        self.assertIs(p.results.currentItem(),group.child(0))
        self.assertEqual(group.childCount(),2)

    def test_diff_disabled_for_plain_text_and_light_multi_cursor_ink(self):
        s=self.note('- list\n- list');d=RecoveryDialog(self.w.notebook,s)
        d.difference.setChecked(False);self.assertFalse(d.highlighter.enabled)
        self.assertEqual(d.preview.toPlainText(),s.page.buffer.snapshot().text);d.close()
        self.w.settings['visual_palette']='paper';self.w.style_shell();self.events()
        s.editor.moveCursor(QTextCursor.MoveOperation.Start);s.advanced.add_vertical(1)
        extra=s.advanced.extras()[0];self.assertEqual(extra.format.foreground().color().name(),'#292b29')
        self.assertEqual(extra.format.background().color().name(),'#ead79a')

    def test_rectangle_selection_and_single_undo(self):
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QEvent,QPointF
        s=self.note('abcd\nabcd\nabcd');e=s.editor;mods=Qt.KeyboardModifier.AltModifier|Qt.KeyboardModifier.ShiftModifier
        def point(position):
            cursor=QTextCursor(e.document());cursor.setPosition(position);return QPointF(e.cursorRect(cursor).center())
        press=QMouseEvent(QEvent.Type.MouseButtonPress,point(1),point(1),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,mods)
        move=QMouseEvent(QEvent.Type.MouseMove,point(13),point(13),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,mods)
        self.assertTrue(s.advanced.mouse_press(press));self.assertTrue(s.advanced.mouse_move(move))
        self.assertEqual(len(s.advanced.cursors),2);s.advanced.multi_insert('X')
        self.assertEqual(s.page.buffer.snapshot().text,'aXd\naXd\naXd');s.page.undo()
        self.assertEqual(s.page.buffer.snapshot().text,'abcd\nabcd\nabcd')

    def test_fold_ranges_handle_nested_lists_and_heading_levels(self):
        s=self.note('# A\nbody\n## B\nbody\n# C\n- one\n  - two\n\n    - three\n- four\nend')
        ends=[s.editor.fold_end(n) for n in (0,2,4,5,6,8,9)]
        self.assertEqual(ends,[4,4,11,9,9,9,10])
