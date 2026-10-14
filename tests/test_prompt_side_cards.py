"""Native editor placement and interruptible side cards use one geometry path."""
import copy
import unittest
from PySide6.QtCore import Qt, QEvent, QObject, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
import test_prompt_group_ui as prior
from prompt_calculus_studio import composition as comp, prompt_groups as groups, materials
from prompt_calculus_studio.material_editor_browser import MaterialEditDialog
from prompt_calculus_studio.prompt_group_editor import GroupEditor


class SideCardTests(unittest.TestCase):
    setUpClass=classmethod(prior.GroupUITests.setUpClass.__func__)
    setUp=prior.GroupUITests.setUp
    tearDown=prior.GroupUITests.tearDown

    def grouped(self):
        rows=[groups.new_group(f'組合 {i+1}', modules=[comp.node(f'模組 {j+1}',f'tag_{i}_{j}') for j in range(4)]) for i in range(6)]
        root=comp.node('六輪流程');root['sequence']=dict(version=1,mode='increment',start=rows[0]['id'],selected=rows[0]['id'],groups=rows)
        ident=materials.save(self.w.state,dict(name=root['name'],prompt='',composition=root,module=self.cat))
        return ident

    def test_first_painted_geometry_is_final_and_centered(self):
        self.w.resize(1440,1000)
        for ident in (None,self.grouped()):
            dialog=MaterialEditDialog(self.page,ident);frames=[]
            class PaintProbe(QObject):
                def eventFilter(probe,widget,event):
                    if event.type()==QEvent.Type.Paint:frames.append(dialog.geometry())
                    return False
            probe=PaintProbe(dialog);dialog.installEventFilter(probe)
            dialog.show();QTest.qWait(100)
            self.assertTrue(frames)
            self.assertEqual(frames[0],dialog.geometry())
            center=dialog.panels.bounds().center()
            self.assertLessEqual((dialog.geometry().center()-center).manhattanLength(),2)
            self.assertEqual(dialog.windowOpacity(),1.)
            self.assertEqual(dialog.reveal.state(),dialog.reveal.State.Stopped)
            dialog.reject();dialog.deleteLater();self.app.processEvents()

    def test_reversing_animation_keeps_card_width_and_final_position(self):
        self.w.resize(1440,1000);dialog=self.page.new_material();QTest.qWait(30)
        panels=dialog.panels;closed=dialog.geometry();width=panels.main.width();frames=[]
        panels.motion.valueChanged.connect(lambda _:frames.append(panels.main.width()))
        dialog.pages.details_toggle.setChecked(True);QTest.qWait(70)
        before=panels.main.mapToGlobal(QPoint())
        dialog.pages.details_toggle.setChecked(False)
        self.assertLessEqual((panels.main.mapToGlobal(QPoint())-before).manhattanLength(),2)
        QTest.qWait(35);before=panels.main.mapToGlobal(QPoint())
        dialog.pages.details_toggle.setChecked(True)
        self.assertLessEqual((panels.main.mapToGlobal(QPoint())-before).manhattanLength(),2)
        QTest.qWait(240);panels.collapse.click();QTest.qWait(210)
        self.assertTrue(frames);self.assertEqual(set(frames),{width})
        self.assertEqual(dialog.geometry(),closed)
        self.assertFalse(panels.side.isVisible());dialog.reject()

    def test_group_format_is_a_compact_aligned_field(self):
        dialog=self.page.edit_material(self.grouped());dialog.pages.show_group_details();QTest.qWait(240)
        value=dialog.pages.group_format;fields=dialog.pages.info_fields
        caption=fields.labelForField(value)
        self.assertTrue(value.isVisible());self.assertEqual(value.text(),'組合')
        self.assertLessEqual(value.height(),dialog.form.aliases.height()+4)
        self.assertLessEqual(abs(value.geometry().center().y()-caption.geometry().center().y()),4)
        self.assertLessEqual(dialog.form.aliases.y()-value.geometry().bottom(),20)
        dialog.reject()

    def test_preview_tracks_both_input_drafts_in_the_same_side_card(self):
        dialog=self.page.edit_material(self.grouped());ed=dialog.pages.group_editor
        before=copy.deepcopy(materials.item(self.w.state,dialog.target_id))
        ed.edit_group(ed.sequence['groups'][0]['id']);QTest.qWait(30)
        height=dialog.height();ed.preview_toggle.setChecked(True);QTest.qWait(240)
        self.assertTrue(dialog.panels.side.isAncestorOf(ed.preview))
        self.assertEqual(dialog.height(),height);self.assertIn('tag_0_0',ed.preview.toPlainText())
        ed.input_tabs.setCurrentIndex(1);ed.direct_text.setPlainText('direct draft')
        self.assertEqual(ed.preview.toPlainText(),'direct draft')
        ed.input_tabs.setCurrentIndex(0);self.assertIn('tag_0_0',ed.preview.toPlainText())
        ed.back();QTest.qWait(220)
        self.assertFalse(dialog.panels.side.isVisible());self.assertFalse(ed.preview_toggle.isChecked())
        self.assertEqual(materials.item(self.w.state,dialog.target_id),before)
        dialog.reject()

    def test_parent_resize_and_reduced_motion_leave_both_cards_reachable(self):
        dialog=self.page.edit_material(self.grouped());ed=dialog.pages.group_editor
        ed.edit_group(ed.sequence['groups'][0]['id']);ed.preview_toggle.setChecked(True);QTest.qWait(50)
        self.w.resize(960,640);QTest.qWait(50)
        self.assertTrue(dialog.panels.bounds().contains(dialog.geometry()))
        self.assertTrue(dialog.panels.rect().contains(dialog.panels.side.geometry()))
        self.assertTrue(dialog.done_button.isVisible())
        dialog.panels.collapse.click();QTest.qWait(210)
        self.w.state['settings']['reduce_motion']=True
        ed.preview_toggle.setChecked(True)
        self.assertEqual(dialog.panels.progress,1.)
        self.assertEqual(dialog.panels.motion.state(),dialog.panels.motion.State.Stopped)
        ed.preview_toggle.setChecked(False)
        self.assertFalse(dialog.panels.side.isVisible());dialog.reject()

    def test_returning_from_nested_group_closes_and_releases_its_preview(self):
        dialog=self.page.edit_material(self.grouped());ed=dialog.pages.group_editor
        ed.edit_group(ed.sequence['groups'][0]['id'])
        part=ed.current_group['modules'][0];child=groups.new_group('子組合','nested prompt')
        part['sequence']=dict(version=1,mode='fixed',start=child['id'],selected=child['id'],groups=[child])
        ed.edit_module(part['id']);ed.edit_nested()
        page=ed.stack.currentWidget();nested=page.findChild(GroupEditor)
        nested.edit_group(child['id']);nested.preview_toggle.setChecked(True);QTest.qWait(260)
        self.assertIn('nested prompt',nested.preview.toPlainText())
        self.assertTrue(dialog.panels.side.isVisible())
        finish=next(button for button in page.findChildren(QPushButton) if button.text()=='完成' and button.parentWidget() is page)
        QTest.mouseClick(finish,Qt.MouseButton.LeftButton);QTest.qWait(30)
        self.assertFalse(dialog.panels.side.isVisible())
        self.assertEqual(dialog.panels.side_pages.count(),1)
        ed.preview_toggle.setChecked(True);QTest.qWait(260)
        self.assertTrue(dialog.panels.side.isVisible());self.assertIn('nested prompt',ed.preview.toPlainText())
        self.assertFalse(self.errors);dialog.reject()


if __name__=='__main__':unittest.main()
