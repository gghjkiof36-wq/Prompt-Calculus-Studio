"""User-facing paste, popup lifetimes and native popup surface regressions."""
import unittest
from PySide6.QtCore import Qt, QMimeData, QUrl, QPoint
from PySide6.QtGui import QTextCursor, QImage, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.widgets import RoundMenu
from prompt_calculus_studio.markdown_source import utf16_position
import test_workspace_panes as fixture


class MarkdownPopupClipboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.WorkspacePaneTests.setUpClass.__func__(cls)
        if cls.app.platformName()=='offscreen':
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    setUp=fixture.WorkspacePaneTests.setUp
    def tearDown(self):
        # Release Python-owned test MIME objects before QApplication teardown.
        # The product only reads the clipboard during plain paste.
        self.app.clipboard().clear()
        fixture.WorkspacePaneTests.tearDown(self)
    open=fixture.WorkspacePaneTests.open
    events=fixture.WorkspacePaneTests.events

    def select(self,s,start,end):
        text=s.page.buffer.snapshot().text
        cursor=s.editor.textCursor();cursor.setPosition(utf16_position(text,start))
        cursor.setPosition(utf16_position(text,end),QTextCursor.MoveMode.KeepAnchor)
        s.editor.setTextCursor(cursor)

    def paste(self,s):
        QTest.keyClick(s.editor,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.ShiftModifier)
        self.events()

    def test_plain_paste_keeps_literal_unicode_replaces_selection_and_one_shared_undo(self):
        raw='before SELECT after'
        (self.root/'Notes.md').write_bytes(raw.encode())
        tab=self.open('Notes.md');s=tab.widget.md_surface
        other=self.w.duplicate_view(tab,True);self.w.focus_tab(tab);self.events()
        for mode in ('write','source'):
            s.set_mode(mode);self.select(s,7,13)
            mime=QMimeData();mime.setText('中文😀\r\n**字面原文**')
            mime.setHtml('<p><b>DIFFERENT RICH TEXT</b></p>');self.app.clipboard().setMimeData(mime)
            self.paste(s)
            expected='before 中文😀\n**字面原文** after'
            self.assertEqual(s.page.buffer.snapshot().text,expected)
            self.assertEqual(other.widget.buffer.snapshot().text,expected)
            s.page.undo();self.events();self.assertEqual(s.page.buffer.snapshot().text,raw)
            s.page.redo();self.events();self.assertEqual(s.page.buffer.snapshot().text,expected)
            s.page.undo();self.events()
        self.paste(s);expected=s.page.buffer.snapshot().text
        self.assertTrue(self.w.save_tab(tab));self.assertEqual(tab.path.read_text(encoding='utf-8'),expected)

    def test_plain_paste_ignores_media_and_cannot_modify_read_or_ime_content(self):
        s=self.open('Notes.md').widget.md_surface;raw=s.page.buffer.snapshot().text
        mime=QMimeData();mime.setText('literal path');mime.setUrls([QUrl.fromLocalFile(str(self.root/'image.png'))])
        image=QImage(2,2,QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.red);mime.setImageData(image)
        self.app.clipboard().setMimeData(mime);self.select(s,0,len(raw));self.paste(s)
        self.assertEqual(s.page.buffer.snapshot().text,'literal path')
        self.assertFalse((self.root/'assets').exists())
        s.page.undo();self.events()
        s.page.buffer.composing.add(99)
        try:self.paste(s);self.assertEqual(s.page.buffer.snapshot().text,raw)
        finally:s.page.buffer.composing.discard(99)
        s.set_mode('read');s.editor.paste_plain();self.assertEqual(s.page.buffer.snapshot().text,raw)
        s.set_mode('write');s.editor.setReadOnly(True)
        self.paste(s);self.assertEqual(s.page.buffer.snapshot().text,raw)
        s.editor.setReadOnly(False);s.open_find();before=s.page.buffer.snapshot().text
        QTest.keyClick(s.query,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(s.page.buffer.snapshot().text,before)

    def test_plain_paste_updates_multiple_cursors_as_one_edit(self):
        (self.root/'Notes.md').write_text('one two one',encoding='utf-8')
        s=self.open('Notes.md').widget.md_surface;self.select(s,0,3)
        s.advanced.next_occurrence();self.assertEqual(len(s.advanced.cursors),1)
        mime=QMimeData();mime.setText('原文😀');mime.setHtml('<b>formatted</b>');self.app.clipboard().setMimeData(mime)
        self.paste(s);self.assertEqual(s.page.buffer.snapshot().text,'原文😀 two 原文😀')
        s.page.undo();self.events();self.assertEqual(s.page.buffer.snapshot().text,'one two one')

    def assert_clear_corners(self,widget):
        image=widget.grab().toImage()
        for x,y in ((0,0),(image.width()-1,0),(0,image.height()-1),(image.width()-1,image.height()-1)):
            self.assertEqual(image.pixelColor(x,y).alpha(),0,(widget.objectName(),x,y))

    def test_menu_surfaces_reopen_and_toolbar_actions_survive_close(self):
        s=self.open('Notes.md').widget.md_surface
        for button in (s.view_tools,s.mode_box):
            for _ in range(2):
                button.click();self.events();menu=QApplication.activePopupWidget()
                self.assertIsInstance(menu,RoundMenu);self.assert_clear_corners(menu)
                self.assertLessEqual(menu.geometry().right(),button.mapToGlobal(button.rect().bottomRight()).x()+1)
                # The closing press is consumed, not replayed into its trigger.
                QTest.mouseClick(menu,Qt.MouseButton.LeftButton,pos=QPoint(-5,-5));self.events()
                self.assertIsNone(QApplication.activePopupWidget())
        for action in s.toolbar.actions():
            menu=action.menu()
            if menu is None:continue
            for _ in range(2):
                menu.open_for(s.toolbar);self.events();self.assert_clear_corners(menu)
                menu.close();self.events();self.assertTrue(menu.actions())
        selector=self.w.notebook.selector
        for _ in range(2):
            selector.menu().open_for(selector);self.events();selector.menu().close();self.events()
        self.assertTrue(selector.menu().actions())

    def test_link_completion_can_be_dismissed_without_editing_the_note(self):
        from prompt_calculus_studio.markdown_relations import LinkDialog
        (self.root/'Target.md').write_text('# Target',encoding='utf-8')
        s=self.open('Notes.md').widget.md_surface;original=s.page.encoded()
        dialog=LinkDialog(s);dialog.show();self.events()
        try:
            dialog.target.setText('Tar');completion=dialog.target.completer()
            completion.setCompletionPrefix('Tar');completion.complete();self.events()
            popup=completion.popup();self.assertTrue(popup.isVisible())
            self.assertEqual(completion.currentCompletion(),'Target');self.assert_clear_corners(popup)
            QTest.keyClick(popup,Qt.Key.Key_Escape);self.events()
            self.assertFalse(popup.isVisible());self.assertTrue(dialog.isVisible())
            self.assertEqual(s.page.encoded(),original)
        finally:dialog.reject();dialog.deleteLater();self.events()

    def test_display_dropdown_rows_and_panels_use_clean_edges(self):
        s=self.open('Notes.md').widget.md_surface;s.preferences_menu();self.events();panel=s.display_panel
        self.assert_clear_corners(panel)
        for combo in (panel.width_control,panel.spacing,panel.tabs,panel.family):
            previous=combo.currentIndex();combo.showPopup();self.events()
            view=combo.view();self.assert_clear_corners(view.window())
            row=view.visualRect(combo.model().index(previous,0)).height()
            self.assertLessEqual(row,max(28,view.fontMetrics().height()+12))
            self.assertGreaterEqual(row,28)
            self.assertEqual(combo.currentIndex(),previous)
            QTest.keyClick(view,Qt.Key.Key_Escape);self.events()
            self.assertEqual(combo.currentIndex(),previous)
        panel.close();self.events()
        s.open_commands();self.events();self.assert_clear_corners(s.command_box);s.command_box.close()
        for effect in (Qt.UIEffect.UI_AnimateMenu,Qt.UIEffect.UI_FadeMenu,Qt.UIEffect.UI_AnimateCombo,
                       Qt.UIEffect.UI_AnimateTooltip,Qt.UIEffect.UI_FadeTooltip):
            self.assertFalse(self.app.isEffectEnabled(effect))


if __name__=='__main__':unittest.main()
