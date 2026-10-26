"""Material type changes keep one native envelope and readable card spacing."""
import unittest
import copy
from PySide6.QtCore import Qt, QObject, QEvent, QRect, QPoint
from PySide6.QtTest import QTest
import test_prompt_dialog_regressions as fixture
from prompt_calculus_studio import prompt_groups as groups, materials
from prompt_calculus_studio.prompt_group_editor import DATA


class ResizeLog(QObject):
    def __init__(self,widget):
        super().__init__(widget);self.events=[];widget.installEventFilter(self)
    def eventFilter(self,widget,event):
        if event.type() in (QEvent.Type.Resize,QEvent.Type.Move):self.events.append(event.type())
        return False


class MaterialLayoutTests(unittest.TestCase):
    setUpClass=classmethod(fixture.PromptDialogTests.setUpClass.__func__)
    setUp=fixture.PromptDialogTests.setUp
    tearDown=fixture.PromptDialogTests.tearDown

    def test_management_and_combination_preview_are_bottom_left(self):
        self.w.resize(1600,1100);dialog=self.page.new_material();pages=dialog.pages
        for kind in ('text','combination','flow','text','combination'):
            pages.type_picker.setCurrentIndex(pages.type_picker.findData(kind));QTest.qWait(30)
            control=pages.details_toggle if kind=='text' else pages.group_details
            self.assertTrue(control.isVisible());self.assertEqual(pages.name_row.indexOf(control),-1)
            corner=control.mapTo(pages,control.rect().bottomRight())
            self.assertLessEqual(abs(control.mapTo(pages,QPoint()).x()-dialog.name.mapTo(pages,QPoint()).x()),2)
            self.assertGreater(corner.y(),pages.stack.geometry().bottom())
            if kind=='combination':
                preview=pages.group_editor.preview_toggle
                self.assertGreater(preview.mapTo(pages,QPoint()).x(),corner.x())
                self.assertEqual(preview.mapTo(pages,QPoint()).y(),control.mapTo(pages,QPoint()).y())
            control.click();QTest.qWait(260);self.assertTrue(dialog.panels.opened)
            pages.close_group_details(animate=False)
        dialog.reject()

    def test_flow_pin_selects_start_without_opening_and_survives_reorder_save(self):
        self.w.resize(1600,1100);dialog=self.page.new_material();dialog.name.setText('Pinned flow')
        dialog.pages.type_picker.setCurrentIndex(dialog.pages.type_picker.findData('flow'));ed=dialog.pages.group_editor
        first=ed.sequence['groups'][0];second=groups.new_group('Second','second prompt')
        ed.sequence['groups'].append(second);ed.refresh_overview();QTest.qWait(30)
        listing=ed.listing;target=listing.regions(listing.visualItemRect(listing.item(1)))['open'].center()
        QTest.mouseClick(listing.viewport(),Qt.MouseButton.LeftButton,pos=target)
        self.assertEqual(ed.sequence['start'],second['id']);self.assertEqual(ed.stack.currentIndex(),0)
        self.assertTrue(listing.item(1).data(DATA)['is_start']);self.assertFalse(listing.item(0).data(DATA)['is_start'])
        QTest.mouseDClick(listing.viewport(),Qt.MouseButton.LeftButton,pos=target)
        self.assertEqual(ed.stack.currentIndex(),0)
        before=[first['id'],second['id']];ed.reorder(ed.sequence['groups'],before,list(reversed(before)))
        self.assertEqual(ed.sequence['start'],second['id'])
        listing.setCurrentRow(1);QTest.keyClick(listing,Qt.Key.Key_P)
        self.assertEqual(ed.sequence['start'],first['id'])
        QTest.keyClick(listing,Qt.Key.Key_Return);self.assertEqual(ed.current_group['id'],first['id']);ed.back()
        ed.set_start(second['id']);ident=dialog.form.base['id'];self.assertTrue(dialog.save(),dialog.error.text());QTest.qWait(20)
        saved=copy.deepcopy(materials.item(self.w.state,ident));self.assertEqual(saved['composition']['sequence']['start'],second['id'])
        dialog=self.page.edit_material(ident);ed=dialog.pages.group_editor
        self.assertEqual(ed.sequence['start'],second['id']);ed.set_start(first['id']);dialog.reject()
        self.assertEqual(materials.item(self.w.state,ident),saved)
        self.w.canvas.undo();self.assertFalse(any(item['id']==ident for item in self.w.state['items']))
        self.w.canvas.redo();self.assertEqual(materials.item(self.w.state,ident),saved)

    def test_combination_dedupe_below_excludes_right_aligned_and_persists(self):
        self.w.resize(1600,1100);dialog=self.page.new_material();dialog.name.setText('Dedupe placement')
        pages=dialog.pages;pages.type_picker.setCurrentIndex(pages.type_picker.findData('combination'));ed=pages.group_editor
        ed.input_tabs.setCurrentIndex(1);ed.direct_text.setPlainText('sea, sea, sky')
        pages.show_group_details();pages.details.setCurrentIndex(1);QTest.qWait(280)
        check=ed.deduplicate;excludes=dialog.form.excludes
        self.assertTrue(dialog.panels.side.isAncestorOf(check));self.assertTrue(check.isVisible())
        self.assertGreater(check.mapTo(pages.details,QPoint()).y(),excludes.mapTo(pages.details,excludes.rect().bottomLeft()).y())
        self.assertLessEqual(abs(check.mapTo(pages.details,check.rect().topRight()).x()-excludes.mapTo(pages.details,excludes.rect().topRight()).x()),2)
        QTest.mouseClick(check,Qt.MouseButton.LeftButton);self.assertTrue(ed.current_group['deduplicate'])
        pages.type_picker.setCurrentIndex(pages.type_picker.findData('flow'));self.assertTrue(check.isHidden())
        pages.type_picker.setCurrentIndex(pages.type_picker.findData('combination'));self.assertTrue(check.isChecked())
        ident=dialog.form.base['id'];self.assertTrue(dialog.save(),dialog.error.text());QTest.qWait(20)
        self.assertTrue(materials.item(self.w.state,ident)['composition']['combination']['deduplicate'])
        dialog=self.page.edit_material(ident);self.assertTrue(dialog.pages.group_editor.deduplicate.isChecked());dialog.reject()

    def test_type_switch_keeps_native_window_fixed_and_retains_each_draft(self):
        self.w.resize(1600,1100);dialog=self.page.new_material();QTest.qWait(30)
        picker=dialog.pages.type_picker;dialog.prompt.setPlainText('text draft')
        native=QRect(dialog.geometry());events=ResizeLog(dialog)
        for _ in range(3):
            for kind in ('combination','flow','text'):
                picker.setCurrentIndex(picker.findData(kind));QTest.qWait(20)
                self.assertEqual(dialog.geometry(),native)
                self.assertTrue(dialog.updatesEnabled())
                self.assertTrue(dialog.panels.main.isVisible())
                if kind!='text':
                    editor=dialog.pages.group_editor
                    self.assertGreaterEqual(dialog.panels.main.height(),680)
                    if kind=='combination':
                        editor.input_tabs.setCurrentIndex(1);editor.direct_text.setPlainText('group draft')
                else:self.assertEqual(dialog.prompt.toPlainText(),'text draft')
        picker.setCurrentIndex(picker.findData('combination'))
        self.assertEqual(dialog.pages.group_editor.direct_text.toPlainText(),'group draft')
        self.assertEqual(events.events,[],'No native backing-store resize while selecting a material type')
        dialog.reject()

    def test_right_card_tabs_have_space_before_content_for_every_type(self):
        self.w.resize(1600,1100);dialog=self.page.new_material();picker=dialog.pages.type_picker
        for kind in ('text','combination','flow'):
            picker.setCurrentIndex(picker.findData(kind));QTest.qWait(20)
            if kind=='text':dialog.pages.details_toggle.setChecked(True)
            else:dialog.pages.show_group_details()
            QTest.qWait(260)
            for index in range(3):
                tabs=dialog.pages.details;tabs.setCurrentIndex(index);QTest.qWait(20)
                pane=tabs.currentWidget()
                self.assertEqual(pane.layout().contentsMargins().top(),24)
                first=pane.layout().itemAt(0).widget()
                gap=first.mapTo(tabs,QPoint()).y()-tabs.tabBar().geometry().bottom()-1
                self.assertGreaterEqual(gap,20,(kind,index,gap))
            self.assertEqual(dialog.panels.side.height(),dialog.panels.main.height())
            dialog.pages.close_group_details(animate=False)
        dialog.reject()

    def test_taller_group_clamps_to_short_window_with_save_reachable(self):
        self.w.resize(800,650);dialog=self.page.new_material()
        for kind in ('combination','flow'):
            dialog.pages.type_picker.setCurrentIndex(dialog.pages.type_picker.findData(kind));QTest.qWait(20)
            self.assertTrue(dialog.panels.rect().contains(dialog.panels.main.geometry()))
            button=dialog.save_button
            self.assertTrue(button.isVisible())
            self.assertTrue(dialog.panels.main.rect().contains(button.mapTo(dialog.panels.main,button.rect().center())))
        dialog.reject()

    def test_variant_toolbar_removes_repeated_labels_and_keeps_actions_together(self):
        self.w.resize(1600,1100);dialog=self.page.new_material();dialog.prompt.setPlainText('first variant')
        dialog.pages.add_variants.click();QTest.qWait(30)
        pages=dialog.pages;editor=pages.variant_editor
        self.assertTrue(pages.prompt_label.isHidden())
        self.assertEqual(pages.name_row.indexOf(editor.choose),2)
        self.assertTrue(editor.choose.accessibleName())
        self.assertTrue(editor.mode.accessibleName())
        mode=editor.mode.mapTo(editor,QPoint());add=pages.add_variants.mapTo(editor,QPoint())
        self.assertLessEqual(abs(mode.y()-add.y()),4)
        self.assertLess(add.y(),editor.stack.y())
        editor.stack.currentWidget().setPlainText('second variant')
        pages.details_toggle.setChecked(True);QTest.qWait(260)
        gap=editor.listing.mapTo(pages.details,QPoint()).y()-pages.details.tabBar().geometry().bottom()-1
        self.assertGreaterEqual(gap,20)
        pages.type_picker.setCurrentIndex(pages.type_picker.findData('flow'))
        pages.type_picker.setCurrentIndex(pages.type_picker.findData('text'));QTest.qWait(20)
        self.assertEqual(editor.stack.currentWidget().toPlainText(),'second variant')
        self.assertTrue(pages.add_variants.isVisible())
        editor.choose.setCurrentIndex(0)
        self.assertEqual(editor.stack.currentWidget().toPlainText(),'first variant')
        dialog.reject()
