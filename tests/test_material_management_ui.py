"""Management-card entry points, draft ownership and short-window geometry."""
import copy
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QScreen
from PySide6.QtTest import QTest

import test_prompt_group_ui as prior
from prompt_calculus_studio import materials, prompt_groups as groups
from prompt_calculus_studio.prompt_group_editor import GroupEditor


class MaterialManagementUITests(unittest.TestCase):
    setUpClass=classmethod(prior.GroupUITests.setUpClass.__func__)

    def setUp(self):
        self.screen=patch.object(QScreen,'availableGeometry',return_value=QRect(0,0,1920,1080))
        self.screen.start();prior.GroupUITests.setUp(self)

    def tearDown(self):
        try:prior.GroupUITests.tearDown(self)
        finally:self.screen.stop()

    def settle(self):
        QTest.qWait(250)

    def click(self,widget):
        QTest.mouseClick(widget,Qt.MouseButton.LeftButton);self.settle()

    def variants(self):
        dialog=self.page.new_material();dialog.name.setText('表情變體測試');dialog.prompt.setPlainText('smile')
        self.click(dialog.pages.add_variants)
        editor=dialog.pages.variant_editor;editor.stack.currentWidget().setPlainText('laughing')
        self.click(dialog.pages.add_variants);editor.stack.currentWidget().setPlainText('serious expression')
        editor.choose.setCurrentIndex(0);self.app.processEvents()
        return dialog,editor

    def combination(self):
        dialog=self.page.new_material();dialog.name.setText('管理與預覽測試')
        picker=dialog.pages.type_picker;picker.setCurrentIndex(picker.findData('combination'))
        editor=dialog.pages.group_editor;editor.input_tabs.setCurrentIndex(1);editor.direct_text.setPlainText('garden, daylight')
        self.settle();return dialog,editor

    def test_variant_header_and_single_management_entry_expose_double_click_details(self):
        dialog,editor=self.variants();pages=dialog.pages
        self.assertEqual(pages.variant_label.text(),'變體')
        self.assertGreater(pages.name_row.indexOf(editor.choose),pages.name_row.indexOf(dialog.name_label))
        self.assertTrue(editor.choose.isVisible());self.assertFalse(pages.add_variants.icon().isNull())
        self.assertEqual(pages.add_variants.text(),'新增變體');self.assertTrue(pages.add_variants.isVisible())
        self.assertEqual(pages.details_toggle.text(),'管理');self.assertFalse(pages.group_details.isVisible())
        self.assertFalse(dialog.panels.opened)
        self.click(pages.details_toggle)
        self.assertTrue(dialog.panels.opened);self.assertEqual(dialog.panels.heading.text(),'管理')
        self.assertIs(dialog.panels.side_pages.currentWidget(),pages.details_container)
        self.assertEqual(pages.details.tabText(pages.details.indexOf(editor.management)),'變體')
        self.assertIs(pages.details.currentWidget(),editor.management)
        item=editor.listing.item(1);point=editor.listing.visualItemRect(item).center()
        QTest.mouseClick(editor.listing.viewport(),Qt.MouseButton.LeftButton,pos=point)
        self.assertFalse(editor.name.isVisible());self.assertFalse(editor.repeat.isVisible())
        QTest.mouseDClick(editor.listing.viewport(),Qt.MouseButton.LeftButton,pos=point);self.app.processEvents()
        self.assertTrue(editor.name.isVisible());self.assertTrue(editor.repeat.isVisible())
        self.assertEqual(editor.choose.currentIndex(),1)
        self.assertTrue(dialog.panels.side.isAncestorOf(editor.name))
        self.click(editor.back_button);self.assertTrue(editor.details.isHidden());dialog.reject()

    def test_typical_windows_keep_outer_form_still_and_scroll_only_long_prompt(self):
        for width,height in ((1700,980),(980,740)):
            self.w.resize(width,height);self.app.processEvents();dialog,editor=self.variants()
            self.assertEqual(dialog.form_scroll.verticalScrollBar().maximum(),0,(width,height))
            self.click(dialog.pages.details_toggle);editor.open_details();self.app.processEvents()
            self.assertEqual(dialog.form_scroll.verticalScrollBar().maximum(),0,(width,height,'management'))
            prompt=editor.stack.currentWidget();prompt.setPlainText('\n'.join(f'long prompt line {i}' for i in range(200)))
            self.app.processEvents()
            self.assertGreater(prompt.verticalScrollBar().maximum(),0)
            self.assertEqual(dialog.form_scroll.verticalScrollBar().maximum(),0,(width,height,'long Prompt'))
            self.assertTrue(dialog.save_button.isVisible());dialog.reject()

    def test_variant_management_save_reopen_cancel_undo_redo_preserves_native_drafts(self):
        dialog,editor=self.variants();self.click(dialog.pages.details_toggle)
        editor.choose.setCurrentIndex(1);editor.open_details();editor.name.selectAll()
        QTest.keyClicks(editor.name,'Laughter');editor.repeat.setValue(2);self.click(editor.back_button)
        QTest.mouseClick(editor.listing.viewport(),Qt.MouseButton.LeftButton,pos=editor.listing.row_regions(editor.listing.item(1))['pin'].center())
        editor.mode.setCurrentIndex(editor.mode.findData('decrement'))
        ident=dialog.form.base['id'];self.click(dialog.save_button)
        self.assertIsNone(self.page.active_dialog)
        saved=copy.deepcopy(materials.item(self.w.state,ident));variant=saved['composition']['variants']
        self.assertEqual(variant['mode'],'decrement');self.assertEqual(variant['groups'][1]['name'],'Laughter')
        self.assertEqual(variant['groups'][1]['repeat'],2);self.assertEqual(variant['start'],variant['groups'][1]['id'])
        dialog=self.page.edit_material(ident);editor=dialog.pages.variant_editor
        self.settle();self.assertTrue(dialog.error.isHidden())
        editor.set_start(editor.sequence['groups'][0]['id']);self.app.processEvents()
        self.assertFalse(dialog.error.isHidden())
        self.assertEqual(dialog.form_scroll.verticalScrollBar().maximum(),0)
        self.assertFalse(dialog.form_scroll.verticalScrollBar().isVisible())
        editor.choose.setCurrentIndex(1);document=editor.stack.currentWidget();document.insertPlainText('bright, ')
        editor.choose.setCurrentIndex(0);editor.choose.setCurrentIndex(1);document.undo()
        self.assertEqual(document.toPlainText(),'laughing');document.redo();self.assertEqual(document.toPlainText(),'bright, laughing')
        self.click(dialog.cancel_button);self.assertEqual(materials.item(self.w.state,ident),saved)
        self.w.canvas.undo();self.assertFalse(any(item['id']==ident for item in self.w.state['items']))
        self.w.canvas.redo();self.assertEqual(materials.item(self.w.state,ident),saved)

    def assert_combination_placement(self,dialog,editor):
        pages=dialog.pages
        self.assertGreaterEqual(pages.name_row.indexOf(editor.preview_toggle),0)
        self.assertEqual(editor.preview_row.indexOf(pages.group_details),0)
        self.assertEqual(editor.preview_row.indexOf(editor.preview_toggle),-1)
        self.assertTrue(editor.preview_toggle.isVisible());self.assertTrue(pages.group_details.isVisible())

    def combination_geometry(self,dialog):
        editor=dialog.pages.group_editor
        report=dict(dialog=dialog.height(),main=dialog.panels.main.height(),
                    viewport=dialog.form_scroll.viewport().height(),
                    scroll=dialog.form_scroll.verticalScrollBar().maximum())
        for name,widget in (('page',dialog.pages),('group',editor),('input',editor.input_pages),('modules',editor.modules)):
            report[name]=dict(height=widget.height(),minimum=widget.minimumHeight(),
                             hint=widget.sizeHint().height(),minimum_hint=widget.minimumSizeHint().height())
        return report

    def test_two_module_combination_picker_save_reopen_never_clips_into_outer_scroll(self):
        for width,height in ((1700,980),(980,740)):
            self.w.resize(width,height);self.app.processEvents()
            dialog=self.page.new_material();dialog.name.setText(f'Two modules {width}')
            picker=dialog.pages.type_picker;picker.setCurrentIndex(picker.findData('combination'))
            editor=dialog.pages.group_editor;editor.input_tabs.setCurrentIndex(0)
            for ident in (self.ids[0],self.ids[2]):
                editor.open_picker();self.app.processEvents()
                item=next(editor.candidates.item(i) for i in range(editor.candidates.count())
                          if editor.candidates.item(i).data(Qt.ItemDataRole.UserRole)==ident)
                QTest.mouseClick(editor.candidates.viewport(),Qt.MouseButton.LeftButton,
                                 pos=editor.candidates.visualItemRect(item).center())
                self.settle();self.assertEqual(editor.stack.currentIndex(),1)
            ident=dialog.form.base['id']
            for existing in (False,True):
                self.settle()
                for state in ('closed','preview','management'):
                    if state=='preview':self.click(editor.preview_toggle)
                    elif state=='management':self.click(dialog.pages.group_details)
                    with self.subTest(width=width,existing=existing,state=state):
                        report=self.combination_geometry(dialog)
                        self.assertEqual(report['scroll'],0,report)
                        self.assertEqual(editor.modules.count(),2)
                        last=editor.modules.visualItemRect(editor.modules.item(1))
                        self.assertTrue(editor.modules.viewport().rect().contains(last),report)
                        control=dialog.pages.group_details
                        area=dialog.form_scroll.viewport()
                        bottom=control.mapTo(area,control.rect().bottomRight())
                        self.assertTrue(area.rect().contains(bottom),report)
                if not existing:
                    self.click(dialog.save_button);self.assertIsNone(self.page.active_dialog)
                    dialog=self.page.edit_material(ident);editor=dialog.pages.group_editor
                else:dialog.reject()

    def test_new_and_existing_combination_preview_management_toggle_consistently(self):
        dialog,editor=self.combination();ident=dialog.form.base['id']
        for existing in (False,True):
            self.settle()
            self.assert_combination_placement(dialog,editor)
            self.assertEqual(dialog.form_scroll.verticalScrollBar().maximum(),0,self.combination_geometry(dialog))
            self.click(editor.preview_toggle)
            self.assertTrue(dialog.panels.opened);self.assertTrue(editor.preview_toggle.isChecked())
            self.assertFalse(dialog.pages.group_details.isChecked())
            self.assertIs(dialog.panels.side_pages.currentWidget(),editor.preview_holder)
            self.assertEqual(editor.preview.toPlainText(),'garden, daylight')
            self.assertEqual(dialog.form_scroll.verticalScrollBar().maximum(),0,self.combination_geometry(dialog))
            self.click(dialog.pages.group_details)
            self.assertTrue(dialog.pages.group_details.isChecked());self.assertFalse(editor.preview_toggle.isChecked())
            self.assertIs(dialog.panels.side_pages.currentWidget(),dialog.pages.details_container)
            self.assertEqual(dialog.form_scroll.verticalScrollBar().maximum(),0,self.combination_geometry(dialog))
            self.click(editor.preview_toggle)
            self.assertTrue(editor.preview_toggle.isChecked());self.assertFalse(dialog.pages.group_details.isChecked())
            self.assertIs(dialog.panels.side_pages.currentWidget(),editor.preview_holder)
            self.click(dialog.panels.collapse)
            self.assertFalse(dialog.panels.opened);self.assertFalse(editor.preview_toggle.isChecked())
            if not existing:
                self.click(dialog.save_button);self.assertIsNone(self.page.active_dialog)
                dialog=self.page.edit_material(ident);editor=dialog.pages.group_editor
            else:dialog.reject()

    def test_type_switch_releases_old_controls_and_preview_state(self):
        dialog,variant=self.variants();pages=dialog.pages;picker=pages.type_picker
        self.click(pages.details_toggle)
        picker.setCurrentIndex(picker.findData('combination'));self.settle();combination=pages.group_editor
        self.assert_combination_placement(dialog,combination)
        self.assertFalse(variant.choose.isVisible());self.assertFalse(pages.variant_label.isVisible())
        self.assertEqual(pages.details.indexOf(variant.management),-1)
        self.click(combination.preview_toggle)
        picker.setCurrentIndex(picker.findData('flow'));self.settle();flow=pages.group_editor
        self.assertFalse(combination.preview_toggle.isVisible())
        self.assertFalse(combination.preview_toggle.isChecked())
        self.assertFalse(dialog.panels.opened)
        self.assertGreaterEqual(pages.name_row.indexOf(pages.group_details),0)
        self.assertEqual(pages.group_details.text(),'管理')
        self.click(pages.group_details)
        self.assertEqual(dialog.panels.heading.text(),'管理')
        picker.setCurrentIndex(picker.findData('text'));self.settle()
        self.assertTrue(variant.choose.isVisible());self.assertTrue(pages.variant_label.isVisible())
        self.assertGreaterEqual(pages.details.indexOf(variant.management),0)
        self.assertFalse(pages.group_details.isVisible());self.assertFalse(flow.preview_toggle.isVisible())
        self.assertFalse(dialog.panels.opened)
        self.assertEqual(variant.stack.currentWidget().toPlainText(),'smile')
        picker.setCurrentIndex(picker.findData('combination'));self.settle()
        self.assert_combination_placement(dialog,combination)
        self.assertFalse(combination.preview_toggle.isChecked());self.assertFalse(dialog.panels.opened)
        dialog.reject()

    def test_management_collapse_restores_card_height_and_center_without_drift(self):
        self.w.resize(1700,980);dialog,editor=self.variants();panels=dialog.panels
        self.settle();before=QRect(dialog.geometry());main_before=QRect(panels.main.geometry())
        for _ in range(2):
            self.click(dialog.pages.details_toggle);editor.open_details();self.app.processEvents()
            self.assertEqual(dialog.height(),before.height())
            self.assertEqual(panels.main.height(),panels.side.height())
            self.assertEqual(panels.main.geometry().top(),panels.side.geometry().top())
            self.click(panels.collapse)
            self.assertFalse(panels.side.isVisible());self.assertFalse(dialog.pages.details_toggle.isChecked())
            self.assertEqual(dialog.geometry(),before);self.assertEqual(panels.main.geometry(),main_before)
            center=panels.main.mapToGlobal(panels.main.rect().center())
            self.assertLessEqual((center-panels.bounds().center()).manhattanLength(),2)
        dialog.reject()

    def test_picker_escape_from_search_list_and_category_returns_without_rejecting_draft(self):
        original=copy.deepcopy(self.w.state['items'])
        for width,height in ((1700,980),(980,740)):
            self.w.resize(width,height);self.app.processEvents()
            dialog,editor=self.combination();picker=dialog.pages.type_picker
            for kind in ('text','combination'):
                if kind=='combination':
                    picker.setCurrentIndex(picker.findData('flow'));editor=dialog.pages.group_editor
                    editor.edit_group(editor.sequence['groups'][0]['id'])
                    editor.direct_text.setPlainText('keep flow draft');editor.back()
                snapshot=copy.deepcopy(editor.sequence)
                for target in ('search','candidates','category'):
                    editor.open_picker() if kind=='text' else editor.open_combination_picker()
                    self.app.processEvents();editor.search.setText('Bravo' if kind=='text' else 'unknown combination')
                    control=(editor.category_combo if editor.category_combo.isVisible() else editor.folders) if target=='category' else getattr(editor,target)
                    self.assertTrue(control.isVisible(),(width,kind,target))
                    control.setFocus();self.app.processEvents();QTest.keyClick(control,Qt.Key.Key_Escape);self.app.processEvents()
                    self.assertTrue(dialog.isVisible(),(width,kind,target,'Escape rejected whole editor'))
                    self.assertIs(self.page.active_dialog,dialog)
                    self.assertEqual(editor.stack.currentIndex(),1 if kind=='text' else 0)
                    self.assertEqual(editor.sequence,snapshot)
                    self.assertEqual(editor.search.text(),'Bravo' if kind=='text' else 'unknown combination')
            dialog.reject()
        self.assertEqual(self.w.state['items'],original)

    def test_nested_picker_escape_only_returns_to_its_own_group(self):
        dialog,editor=self.combination();editor.add_material(self.ids[0]);editor.input_tabs.setCurrentIndex(0)
        part=editor.current_group['modules'][0];child=groups.new_group('內嵌組合','nested draft')
        part['sequence']=dict(version=1,mode='fixed',start=child['id'],selected=child['id'],groups=[child])
        editor.edit_module(part['id']);editor.edit_nested()
        nested_page=editor.stack.currentWidget();nested=nested_page.findChild(GroupEditor)
        self.assertIsNotNone(nested);nested.edit_group(child['id']);nested.direct_text.setPlainText('keep nested draft')
        snapshot=copy.deepcopy(nested.sequence)
        nested.open_picker();self.app.processEvents();nested.search.setText('Bravo');nested.search.setFocus()
        QTest.keyClick(nested.search,Qt.Key.Key_Escape);self.app.processEvents()
        self.assertTrue(dialog.isVisible());self.assertIs(editor.stack.currentWidget(),nested_page)
        self.assertEqual(nested.stack.currentIndex(),1);self.assertEqual(nested.sequence,snapshot)
        self.assertEqual(nested.direct_text.toPlainText(),'keep nested draft');dialog.reject()


if __name__=='__main__':unittest.main()
