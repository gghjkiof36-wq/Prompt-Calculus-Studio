"""Four-card interactions with isolated real Qt/data; no live ComfyUI or GPU."""
import copy
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QPointF,QPoint,QMimeData
from PySide6.QtGui import QImage,QColor,QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog
from stage_fixture import StageFixture,APP
from prompt_calculus_studio.flow_data import add_scheduler,text_sources
from prompt_calculus_studio.multi_output import connect
from prompt_calculus_studio.source_order_dialog import SourceOrderDialog
from prompt_calculus_studio.schedule_order_dialog import ScheduleOrderDialog
from prompt_calculus_studio.module_preview import ModulePreview
from prompt_calculus_studio.source_controls import load_many
from prompt_calculus_studio.queue_history import capture


class FourModuleTests(StageFixture):
    def source(self,count=3):
        key=self.c.functions.add_image();panel=self.c.functions.cards[key].panel
        paths=[]
        for i in range(count):
            path=Path(self.tmp.name)/f'image-{i}.png'
            image=QImage(96,72,QImage.Format.Format_RGB32);image.fill(QColor(80+i*30,110,150));image.save(str(path));paths.append(str(path))
        self.assertTrue(load_many(panel,paths));APP.processEvents();return key,self.c.functions.cards[key].panel

    def queue(self):
        keys=[];self.c.commit(lambda s:keys.append(add_scheduler(s)))
        panel=self.c.flow_cards[keys[0]].panel;store=self.runner.store
        run=store.add('run',self.w.state['workspace'],status='paused',server=self.runner.client.url,
                      plan={'stages':{},'scopes':{}},stack=[{'kind':'loop','initialized':False}])
        entries=[store.add('entry',self.w.state['workspace'],run['id'],run=run['id'],scheduler=keys[0],
                           label='Stage 1',status='running' if i==0 else 'waiting',saved={'values':{}},stages=[])
                 for i in range(4)]
        panel.refresh();return panel,entries

    def drop(self,listing,key,index):
        listing._source=key;listing._drag_token=b'four-modules-drag'
        mime=QMimeData();mime.setData(listing.mime,listing._drag_token)
        pos=listing.visualItemRect(listing.item(index)).bottomLeft()+QPoint(10,-1)
        event=QDropEvent(QPointF(pos),Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        listing.dropEvent(event);listing._source=None;listing._drag_token=b''
        self.assertTrue(event.isAccepted())

    def test_source_management_paging_preview_and_reopen(self):
        key,panel=self.source();card=self.c.functions.cards[key];height=card.height
        self.assertTrue(panel.source_controls.isHidden());self.assertFalse(panel.previous.isEnabled())
        QTest.mouseClick(panel.next,Qt.MouseButton.LeftButton)
        panel=self.c.functions.cards[key].panel
        self.assertEqual(self.c.functions.data()['images'][key]['index'],1);self.assertEqual(panel.counter.text(),'2 / 3')
        with patch('prompt_calculus_studio.image_viewer.show_image') as viewer:
            panel.open_preview();self.assertEqual(viewer.call_args.args[2],'image-1.png')
        QTest.mouseClick(panel.management,Qt.MouseButton.LeftButton);APP.processEvents()
        self.assertFalse(panel.source_controls.isHidden());self.assertGreater(card.height,height)
        self.w.persist();saved=self.w.store.load()
        self.assertFalse(saved['canvas_functions']['images'][key]['management_collapsed'])
        QTest.mouseClick(panel.management,Qt.MouseButton.LeftButton);APP.processEvents()
        self.assertTrue(panel.source_controls.isHidden());self.assertLessEqual(card.height,height+2)
        self.assertEqual(len(self.c.functions.data()['images'][key]['items']),3)

    def test_catalog_uses_same_default_source_and_single_layout(self):
        preview=ModulePreview(self.c)
        for kind,enhanced in (('source',True),('single',False)):
            key=self.c.functions.add_image(enhanced=enhanced);actual=self.c.functions.cards[key]
            preview.show_kind(kind);APP.processEvents();example=preview.card
            self.assertEqual(example.requested_size,actual.requested_size)
            self.assertEqual(example.panel.source_controls.isHidden(),actual.panel.source_controls.isHidden())
            self.assertEqual(example.panel.attach_button.isHidden(),actual.panel.attach_button.isHidden())
            self.assertEqual(example.panel.preview.minimumHeight(),actual.panel.preview.minimumHeight())
        preview.deleteLater()

    def test_source_sort_keeps_empty_manual_draft_and_undo(self):
        second=self.c.add_canvas(QPointF(1500,1200));self.c.commit(lambda s:connect(s,second,self.out,'text'))
        from prompt_calculus_studio.drafts import edit
        self.c.commit(lambda s:edit(s,'',self.out));before=copy.deepcopy(self.w.state['uses'])
        def apply(dialog):
            dialog.show();APP.processEvents();self.drop(dialog.listing,self.cid,1)
            QTest.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton);return dialog.result()
        with patch.object(SourceOrderDialog,'exec',apply):self.c.order_sources(self.out)
        self.assertEqual(text_sources(self.c.data()['outputs'][self.out]),[second,self.cid])
        self.assertEqual(self.c.outputs[self.out].panel.editor.toPlainText(),'');self.assertEqual(self.w.state['draft'],'')
        self.assertEqual(self.w.state['uses'],before);self.c.undo()
        self.assertEqual(text_sources(self.c.data()['outputs'][self.out]),[self.cid,second]);self.assertEqual(self.w.state['draft'],'')

    def test_queue_drag_cancel_apply_remove_undo_redo(self):
        panel,entries=self.queue();ids=[e['id'] for e in entries];before=capture(self.runner.store,ids)
        dialog=ScheduleOrderDialog(panel);dialog.show();APP.processEvents();self.drop(dialog.listing,ids[1],3)
        self.assertEqual(dialog.ordered_ids(),[ids[0],ids[2],ids[3],ids[1]])
        QTest.mouseClick(dialog.cancel_button,Qt.MouseButton.LeftButton)
        self.assertEqual(capture(self.runner.store,ids),before)
        dialog=ScheduleOrderDialog(panel);dialog.show();APP.processEvents();self.drop(dialog.listing,ids[1],3)
        dialog.listing.remove_waiting([ids[2]])
        QTest.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
        self.assertEqual([r['id'] for r in panel.entries()],[ids[0],ids[3],ids[1]])
        self.assertEqual(self.runner.store.read(ids[0]),entries[0])
        self.c.undo();self.assertEqual(capture(self.runner.store,ids),before)
        self.c.redo();self.assertEqual(self.runner.store.read(ids[2])['status'],'removed')

    def test_queue_fixed_rows_and_changed_queue_reject_apply(self):
        panel,entries=self.queue();ids=[e['id'] for e in entries]
        dialog=ScheduleOrderDialog(panel);dialog.listing.move_row(ids[0],4);dialog.listing.move_row(ids[1],0)
        dialog.listing.remove_waiting([ids[0]]);self.assertEqual(dialog.ordered_ids(),ids)
        dialog.listing.move_row(ids[1],4)
        self.runner.store.update(ids[1],status='running');before=capture(self.runner.store,ids)
        dialog.apply_changes();self.assertEqual(dialog.result(),QDialog.DialogCode.Rejected)
        self.assertIn('已更新',dialog.error.text());self.assertEqual(capture(self.runner.store,ids),before);dialog.reject()

    def test_pause_style_and_management_keep_advanced_actions(self):
        panel,entries=self.queue();self.assertEqual(panel.pause_button.styleSheet(),self.c.outputs[self.out].panel.save_material.styleSheet())
        dialog=ScheduleOrderDialog(panel);menus=[]
        from prompt_calculus_studio.widgets import RoundMenu
        with patch.object(RoundMenu,'open_for',lambda menu,*args:menus.append(menu)):
            QTest.mouseClick(dialog.more,Qt.MouseButton.LeftButton)
        self.assertIn('增加資料接點',[a.text() for a in menus[0].actions()])
        self.assertIn('取消此處所有流程（含等待項目）',[a.text() for a in menus[0].actions()]);dialog.reject()

    def test_theme_changes_update_source_actions_without_repolishing_on_refresh(self):
        key,panel=self.source();old=panel.choose.styleSheet()
        self.w.state['settings']['visual_palette']='paper';self.w.apply_theme(preserve_layout=True)
        self.assertNotEqual(panel.choose.styleSheet(),old)
        with patch.object(panel.choose,'setStyleSheet',wraps=panel.choose.setStyleSheet) as change:
            panel.refresh();panel.refresh();self.assertEqual(change.call_count,0)

    def test_connected_collection_list_and_first_next_keep_result_identity(self):
        from prompt_calculus_studio.source_controls import edit_list,step
        from prompt_calculus_studio.widgets import StudioDialog
        from PySide6.QtWidgets import QPushButton,QListWidget
        key,panel=self.source();value=self.c.functions.data()['images'][key]
        value['stage_reference']=next(iter(self.c.data()['stages']))
        collection=dict(collection='owned-result',items=copy.deepcopy(value['items']))
        value.pop('input_index',None)
        def select(dialog):
            dialog.findChild(QListWidget).setCurrentRow(2)
            next(b for b in dialog.findChildren(QPushButton) if b.text()=='使用這張').click()
            return dialog.result()
        with patch.object(self.w.comfy.images,'collection',return_value=collection),patch.object(self.w.comfy.images,'choose') as choose:
            step(panel,1);choose.assert_called_once_with(key,'owned-result',0)
            with patch.object(StudioDialog,'exec',select):edit_list(panel)
            self.assertEqual(choose.call_args.args,(key,'owned-result',2))
        self.assertEqual(value['items'],collection['items'])
