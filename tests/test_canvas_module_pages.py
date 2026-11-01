"""Reachable module pages retain document ownership and bounded scheduling."""
import copy
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPlainTextEdit
from stage_fixture import StageFixture, APP
from prompt_calculus_studio.flow_data import add_scheduler, CAPACITY
from prompt_calculus_studio.stage_store import StageStore


class ModulePageTests(StageFixture):
    def open_output(self):
        panel=self.c.outputs[self.out].panel
        QTest.mouseClick(panel.open_button,Qt.MouseButton.LeftButton)
        APP.processEvents()
        return panel.page

    def test_output_lazy_page_edits_empty_draft_and_reopens_with_undo(self):
        panel=self.c.outputs[self.out].panel
        self.assertFalse(panel.findChildren(QPlainTextEdit))
        transform=self.c.view.transform();page=self.open_output()
        self.assertTrue(page.isVisible());self.assertFalse(self.c.view.isHidden())
        self.assertLess(page.surface.width(),page.width());self.assertLess(page.surface.height(),page.height())
        editor=page.content.editor
        editor.selectAll();QTest.keyClick(editor,Qt.Key.Key_Backspace)
        self.assertEqual(self.c.data()['outputs'][self.out]['draft'],'')
        QTest.mouseClick(page.back,Qt.MouseButton.LeftButton);APP.processEvents()
        self.assertIsNone(panel.page);self.assertFalse(self.c.view.isHidden())
        self.assertEqual(self.c.view.transform(),transform)
        self.w.persist()
        self.assertEqual(self.open_output().content.editor.toPlainText(),'')
        self.c.undo();APP.processEvents()
        self.assertIsNone(self.c.data()['outputs'][self.out]['draft'])
        self.c.redo();self.assertEqual(self.c.data()['outputs'][self.out]['draft'],'')

    def test_switch_and_owner_removal_close_page_without_restoring_old_owner(self):
        page=self.open_output()
        keys=[];self.c.commit(lambda s:keys.append(add_scheduler(s)))
        schedule=self.c.flow_cards[keys[0]].panel.open_page()
        self.assertTrue(page.closed);self.assertIs(self.c._module_page,schedule)
        self.assertFalse(self.c.view.isHidden())
        self.c.commit(lambda s:s['multi_output']['schedulers'].pop(keys[0]))
        APP.processEvents();self.assertTrue(schedule.closed)
        self.assertIsNone(self.c._module_page);self.assertFalse(self.c.view.isHidden())

    def test_schedule_summary_never_reads_frozen_graphs_or_builds_item_list(self):
        keys=[];self.c.commit(lambda s:keys.append(add_scheduler(s)))
        panel=self.c.flow_cards[keys[0]].panel
        rows=self.runner.store.rows
        def bounded(kind,*args,**kwargs):
            self.assertEqual(kind,'control','Only small scheduler controls may be read')
            return rows(kind,*args,**kwargs)
        with (patch.object(self.runner.store,'rows',side_effect=bounded),
              patch.object(self.runner.store,'entry_summaries',side_effect=AssertionError('List projection'))):
            panel.refresh()
        self.assertIsNone(panel.page)

    def test_large_queue_opens_by_group_and_pages_without_modifying_originals(self):
        store=self.runner.store;workspace=self.w.state['workspace']
        keys=[];self.c.commit(lambda s:keys.append(add_scheduler(s)))
        scheduler=keys[0]
        for index in range(15):
            store.add('entry',workspace,owner='test-owner',scheduler=scheduler,status='waiting',
                      run='test-run',parent='',label=str(index),saved={})
        page=self.c.flow_cards[scheduler].panel.open_page()
        self.assertEqual(page.content.groups_list.count(),1)
        self.assertEqual(page.content.list.count(),0)
        group=page.content.groups[0]['id']
        item=page.content.groups_list.item(0)
        QTest.mouseClick(page.content.list_mode,Qt.MouseButton.LeftButton);APP.processEvents()
        self.assertFalse(page.content.groups_list.grid_mode)
        self.assertIs(page.content.groups_list.item(0),item)
        QTest.mouseClick(page.content.groups_list.viewport(),Qt.MouseButton.LeftButton,pos=page.content.groups_list.visualItemRect(item).center())
        self.assertEqual(page.content.group,group)
        self.assertEqual(page.content.list.count(),15)
        QTest.mouseClick(page.content.back_button,Qt.MouseButton.LeftButton);APP.processEvents()
        self.assertIsNone(page.content.group)
        self.assertIs(page.content.groups_list.currentItem(),item)
        self.assertTrue(page.content.list_mode.isChecked())
        QTest.mouseClick(page.content.card_mode,Qt.MouseButton.LeftButton);APP.processEvents()
        self.assertTrue(page.content.groups_list.grid_mode)
        QTest.keyClick(page.content.groups_list,Qt.Key.Key_Return);APP.processEvents()
        self.assertEqual(page.content.group,group)
        page.finish()
        for index in range(15,235):
            store.add('entry',workspace,owner='test-owner',scheduler=scheduler,status='waiting',
                      run='test-run',parent='',label=str(index),saved={})
        before=store.rows('entry');page=self.c.flow_cards[scheduler].panel.open_page()
        page.content.open_group(group)
        self.assertEqual(page.content.list.count(),50)
        for _ in range(4):QTest.mouseClick(page.content.following,Qt.MouseButton.LeftButton)
        self.assertEqual(page.content.list.count(),35)
        self.assertIn('201–235 / 235',page.content.range_label.text())
        self.assertFalse(page.content.following.isEnabled())
        self.assertEqual(store.rows('entry'),before)
        self.assertEqual(StageStore(self.w.store.db).rows('entry'),before)

    def test_close_page_rejects_queued_source_reorder(self):
        from prompt_calculus_studio.composition import node
        from prompt_calculus_studio.multi_output import assign
        self.c.commit(lambda s:(s['uses'].update(second=node('second','second')),assign(s,'second',self.cid)))
        page=self.open_output();tree=page.content.order
        before=copy.deepcopy(self.c.data()['canvases'][self.cid]['members'])
        tree.move_entry(('member',self.cid,before[0]),len(before))
        page.finish();APP.processEvents()
        self.assertEqual(self.c.data()['canvases'][self.cid]['members'],before)

    def test_small_window_large_font_keeps_fields_and_actions_inside_surface(self):
        self.w.resize(760,600);self.w.state['settings']['ui_size']=16;self.w.apply_theme();APP.processEvents()
        page=self.open_output();APP.processEvents()
        for widget in (page.back,page.content.editor,page.content.save_material,page.content.order):
            bounds=widget.rect();bounds.moveTopLeft(widget.mapTo(page.surface,bounds.topLeft()))
            self.assertTrue(page.surface.rect().contains(bounds),(widget.objectName(),bounds,page.surface.rect()))
        self.assertTrue(page.rect().contains(page.surface.geometry()))
        old=page.styleSheet();self.w.state['settings']['visual_palette']='paper';self.w.apply_theme();APP.processEvents()
        self.assertNotEqual(page.styleSheet(),old)
        page.finish();keys=[];self.c.commit(lambda s:keys.append(add_scheduler(s)))
        for number in range(3):
            self.runner.store.add('entry',self.w.state['workspace'],'o',run='r',scheduler=keys[0],execution_group='g',status='waiting',label='sequence',saved={})
        page=self.c.flow_cards[keys[0]].panel.open_page();page.content.open_group('g');APP.processEvents()
        for widget in (page.back,page.content.more,page.content.pause_button,page.content.following):
            bounds=widget.rect();bounds.moveTopLeft(widget.mapTo(page.surface,bounds.topLeft()))
            self.assertTrue(page.surface.rect().contains(bounds),(widget.text(),bounds,page.surface.rect()))
        self.assertTrue(page.rect().contains(page.surface.geometry()))
