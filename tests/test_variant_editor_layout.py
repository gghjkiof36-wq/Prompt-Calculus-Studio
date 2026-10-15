"""Detached variant management preserves native editing and ordering contracts."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint, QRect, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout

import test_editor_reorder as fixture
from prompt_calculus_studio import prompt_groups as groups
from prompt_calculus_studio.core import DEFAULT_SETTINGS
from prompt_calculus_studio.prompt_variant_editor import VariantEditor, REPEAT_ROLE, START_ROLE
from prompt_calculus_studio.theme import stylesheet


class VariantEditorLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.environment=patch.dict(os.environ,PROMPT_STUDIO_DATA=self.folder.name);self.environment.start()
        rows=[groups.new_group(name,text) for name,text in
              [('晴天','daylight, blue sky'),('夕陽','sunset, golden hour'),('夜景','night, neon lights')]]
        self.original=dict(version=1,mode='increment',start=rows[0]['id'],selected=rows[0]['id'],groups=rows)
        self.before=copy.deepcopy(self.original)
        self.editor=VariantEditor(self.original);self.editor.setStyleSheet(stylesheet(DEFAULT_SETTINGS))
        self.editor.resize(580,480);self.editor.show()
        self.side=QWidget();self.side.setStyleSheet(stylesheet(DEFAULT_SETTINGS));side_layout=QVBoxLayout(self.side)
        side_layout.setContentsMargins(0,0,0,0);side_layout.addWidget(self.editor.management)
        self.editor.management.show();self.side.resize(380,480);self.side.show();self.app.processEvents()

    def tearDown(self):
        self.assertEqual(self.original,self.before,'Unaccepted edits must not change the source')
        self.side.close();self.editor.close();self.side.deleteLater();self.editor.deleteLater()
        self.app.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.environment.stop();self.folder.cleanup()

    drag=fixture.EditorReorderTests.drag

    def test_prompt_is_primary_and_detached_controls_fit_narrow_and_wide(self):
        self.assertTrue(self.editor.choose.isHidden())
        for width in (580,380):
            self.editor.resize(width,480);self.side.resize(width,480);self.app.processEvents()
            self.assertEqual(self.editor.width(),width)
            self.assertGreaterEqual(self.editor.stack.height(),390)
            self.assertEqual(self.side.width(),width)
            controls=(self.editor.listing,self.editor.pin,self.editor.actions)
            for control in controls:
                visible=QRect(control.mapTo(self.side,QPoint()),control.size())
                self.assertTrue(self.side.rect().contains(visible),(width,control.accessibleName(),visible))
            self.assertTrue(self.editor.isAncestorOf(self.editor.mode))
            self.assertFalse(self.editor.management.isAncestorOf(self.editor.mode))
            self.assertFalse(self.editor.name.isVisible());self.assertFalse(self.editor.repeat.isVisible())
            self.editor.open_details();self.app.processEvents()
            for control in (self.editor.name,self.editor.repeat):
                self.assertTrue(self.side.rect().contains(QRect(control.mapTo(self.side,QPoint()),control.size())))
            detail_top=self.editor.details.mapTo(self.side,QPoint()).y()
            list_bottom=self.editor.listing.mapTo(self.side,QPoint(0,self.editor.listing.height())).y()
            self.assertLessEqual(detail_top-list_bottom,12,'Secondary edit controls stay beside the selected rows')
            self.editor.close_details()

    def test_list_selection_and_dropdown_follow_without_changing_start(self):
        first=self.editor.sequence['start'];item=self.editor.listing.item(2)
        QTest.mouseClick(self.editor.listing.viewport(),Qt.MouseButton.LeftButton,
                         pos=self.editor.listing.visualItemRect(item).center())
        self.assertEqual(self.editor.choose.currentIndex(),2)
        self.assertEqual(self.editor.stack.currentWidget().toPlainText(),'night, neon lights')
        self.assertEqual(self.editor.value('tags')['start'],first)
        self.editor.choose.setCurrentIndex(1)
        self.assertEqual(self.editor.listing.currentRow(),1)
        QTest.mouseClick(self.editor.pin,Qt.MouseButton.LeftButton)
        self.assertEqual(self.editor.sequence['start'],self.editor.choose.currentData())
        self.assertTrue(self.editor.listing.item(1).data(START_ROLE))
        self.assertFalse(self.editor.listing.item(0).data(START_ROLE))
        self.assertEqual(self.editor.listing.item(1).text(),'夕陽')
        self.assertEqual(self.editor.pin.text(),'');self.assertTrue(self.editor.pin.accessibleName())
        self.assertFalse(self.editor.pin.isEnabled())

    def test_row_repeats_are_right_aligned_and_names_leave_room_for_maximum(self):
        self.editor.repeat.setValue(groups.MAX_REPEAT)
        self.editor.open_details();self.editor.name.selectAll();QTest.keyClicks(self.editor.name,'A long descriptive variant name'*5)
        for width in (380,580):
            self.side.resize(width,480);self.app.processEvents()
            item=self.editor.listing.currentItem();regions=self.editor.listing.row_regions(item)
            self.assertEqual(item.data(REPEAT_ROLE),groups.MAX_REPEAT)
            self.assertEqual(item.data(Qt.ItemDataRole.AccessibleTextRole),item.text()+f'，{groups.MAX_REPEAT} 次')
            self.assertLess(regions['marker'].right(),regions['name'].left())
            self.assertLess(regions['name'].right(),regions['repeat'].left())
            self.assertLess(regions['repeat'].right(),self.editor.listing.viewport().width())
            self.assertGreaterEqual(regions['repeat'].width(),self.editor.listing.fontMetrics().horizontalAdvance('10000 次'))

    def test_add_focuses_empty_document_and_undo_stays_with_each_variant(self):
        self.editor.activateWindow();self.app.processEvents()
        first=self.editor.stack.currentWidget();first.insertPlainText('bright, ')
        self.editor.add();self.app.processEvents();second=self.editor.stack.currentWidget()
        self.assertTrue(second.hasFocus());self.assertEqual(second.toPlainText(),'')
        second.insertPlainText('snow, winter');self.editor.choose.setCurrentIndex(0)
        first.undo();self.assertEqual(first.toPlainText(),'daylight, blue sky')
        first.redo();self.assertEqual(first.toPlainText(),'bright, daylight, blue sky')
        self.editor.choose.setCurrentIndex(3);self.assertEqual(second.toPlainText(),'snow, winter')
        second.undo();self.assertEqual(second.toPlainText(),'');second.redo()
        self.assertEqual(second.toPlainText(),'snow, winter')

    def test_whole_row_drag_retains_editor_history_start_and_selection(self):
        first=self.editor.sequence['start'];self.editor.choose.setCurrentIndex(1)
        current=self.editor.choose.currentData();document=self.editor.stack.currentWidget()
        document.insertPlainText('warm, ')
        ids=self.editor.listing.order_ids()
        self.drag(self.editor.listing,ids[1],ids[2],after=True)
        self.assertEqual(self.editor.listing.order_ids(),[ids[0],ids[2],ids[1]])
        self.assertEqual(self.editor.choose.currentData(),current)
        self.assertIs(self.editor.stack.currentWidget(),document)
        self.assertEqual(self.editor.sequence['start'],first)
        document.undo();self.assertEqual(document.toPlainText(),'sunset, golden hour')

    def test_double_click_exposes_secondary_fields_and_switch_retains_drafts(self):
        item=self.editor.listing.item(1);point=self.editor.listing.visualItemRect(item).center()
        QTest.mouseClick(self.editor.listing.viewport(),Qt.MouseButton.LeftButton,pos=point)
        self.assertTrue(self.editor.details.isHidden());self.assertFalse(self.editor.repeat.isVisible())
        QTest.mouseDClick(self.editor.listing.viewport(),Qt.MouseButton.LeftButton,pos=point)
        self.app.processEvents();self.assertTrue(self.editor.details.isVisible())
        self.editor.name.selectAll();QTest.keyClicks(self.editor.name,'Golden sunset');self.editor.repeat.setValue(3)
        self.assertEqual(self.editor.choose.currentText(),'Golden sunset')
        self.assertIn('Golden sunset',self.editor.listing.currentItem().text())
        self.assertIn('Golden sunset',self.editor.choose.toolTip())
        self.editor.choose.setCurrentIndex(0);self.assertTrue(self.editor.details.isHidden())
        self.editor.choose.setCurrentIndex(1);self.editor.open_details()
        self.assertEqual(self.editor.name.text(),'Golden sunset');self.assertEqual(self.editor.repeat.value(),3)
        self.editor.name.selectAll();QTest.keyClick(self.editor.name,Qt.Key.Key_Backspace)
        with self.assertRaises(ValueError):self.editor.value('tags')
        self.editor.name.undo();self.assertEqual(self.editor.name.text(),'Golden sunset')
        self.editor.close_details();self.editor.listing.setFocus()
        QTest.keyClick(self.editor.listing,Qt.Key.Key_Return);self.assertTrue(self.editor.details.isVisible())
        self.assertEqual(self.editor.collapse_details.text(),'收合編輯')
        QTest.mouseClick(self.editor.collapse_details,Qt.MouseButton.LeftButton)
        self.assertTrue(self.editor.details.isHidden())

    def test_long_variant_list_scrolls_without_pushing_details_away(self):
        for _ in range(8):self.editor.add()
        self.app.processEvents()
        maximum=6*self.editor.listing.sizeHintForRow(0)+self.editor.listing.frameWidth()*2+4
        self.assertLessEqual(self.editor.listing.height(),maximum)
        self.assertGreater(self.editor.listing.verticalScrollBar().maximum(),0)
        self.editor.open_details();self.app.processEvents()
        self.assertLessEqual(self.editor.details.y()-self.editor.listing.geometry().bottom(),13)

    def test_delete_start_falls_back_without_losing_other_drafts(self):
        first=self.editor.sequence['start'];second=self.editor.sequence['groups'][1]['id']
        self.editor.choose.setCurrentIndex(1);document=self.editor.stack.currentWidget();document.insertPlainText('warm, ')
        self.editor.choose.setCurrentIndex(0);self.editor.remove()
        self.assertEqual(self.editor.sequence['start'],second)
        self.assertEqual(self.editor.choose.currentData(),second);self.assertNotIn(first,self.editor.editors)
        self.assertIs(document,self.editor.stack.currentWidget());document.undo()
        self.assertEqual(document.toPlainText(),'sunset, golden hour')
        self.editor.remove();self.editor.remove()
        self.assertEqual(len(self.editor.sequence['groups']),1);groups.validate(self.editor.value('tags'))

    def test_repeat_and_random_mode_persist_and_hidden_document_updates_own_row(self):
        first=self.editor.stack.currentWidget();self.editor.choose.setCurrentIndex(1)
        self.editor.repeat.setValue(3);self.editor.mode.setCurrentIndex(self.editor.mode.findData('random'))
        self.editor.random_repeats.setChecked(False);self.app.processEvents()
        self.assertTrue(self.editor.random_repeats.isVisible())
        first.insertPlainText('bright, ')
        self.assertIn('bright, ',self.editor.listing.item(0).toolTip())
        self.assertNotIn('bright, ',self.editor.listing.item(1).toolTip())
        value=self.editor.value('text')
        self.assertEqual(value['groups'][1]['repeat'],3);self.assertFalse(value['random_repeats'])
        self.assertEqual(value['mode'],'random');self.assertEqual(value['groups'][0]['content_kind'],'text')
        self.editor.mode.setCurrentIndex(self.editor.mode.findData('fixed'));self.assertTrue(self.editor.random_repeats.isHidden())


if __name__=='__main__':unittest.main()
