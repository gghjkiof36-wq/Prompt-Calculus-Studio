"""Real-widget interaction and rendering checks for the first Canvas finish pass."""
from _qt_wait import wait_ms
import copy
import sys
from collections import Counter
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, QRect, QMimeData, QEvent
from PySide6.QtGui import QImage, QPainter, QDropEvent, QFontDatabase, QMouseEvent, QScreen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton
from stage_fixture import StageFixture, APP
from prompt_calculus_studio.canvas_items import canvas_colors
from prompt_calculus_studio.function_picker import FunctionPicker, FUNCTIONS
from prompt_calculus_studio.source_order_dialog import SourceOrderDialog
from prompt_calculus_studio.flow_data import text_sources, add_scheduler


class CanvasVisualBatchTests(StageFixture):
    def setUp(self):
        self.qt_errors=[]
        hook=patch.object(sys,'excepthook',lambda k,v,t:self.qt_errors.append(repr(v)))
        hook.start();self.addCleanup(hook.stop)
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')
        super().setUp()
        self.w.state['settings'].update(visual_palette='graphite',ui_size=11,reduce_motion=True)
        self.w.apply_theme(preserve_layout=True)

    def tearDown(self):
        self.assertFalse(self.executor.submissions)
        super().tearDown()
        self.assertFalse(self.qt_errors,self.qt_errors)

    def test_hover_previews_without_inserting_then_single_click_adds_once(self):
        dialog=FunctionPicker(self.c,QPointF(2100,1200));dialog.show();wait_ms(20)
        try:
            before=copy.deepcopy(self.c.data());count=len(self.c.undo_stack)
            item=next(dialog.list.item(i) for i in range(dialog.list.count()) if dialog.list.item(i).data(Qt.ItemDataRole.UserRole)=='clip')
            point=dialog.list.visualItemRect(item).center()
            # Offscreen has no OS cursor routing. Deliver the real Qt move to
            # the viewport, which still resolves the item and emits itemEntered.
            APP.sendEvent(dialog.list.viewport(),QMouseEvent(QEvent.Type.MouseMove,
                QPointF(point),QPointF(dialog.list.viewport().mapToGlobal(point)),
                Qt.MouseButton.NoButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier))
            wait_ms(15)
            self.assertEqual(dialog.current_id(),'clip')
            self.assertEqual(dialog.preview.card.__class__.__name__,'ClipCard')
            self.assertEqual(self.c.data(),before);self.assertEqual(len(self.c.undo_stack),count)
            self.assertFalse(any('雙擊新增' in label.text() for label in dialog.findChildren(QLabel)))
            QTest.mouseClick(dialog.list.viewport(),Qt.MouseButton.LeftButton,pos=point)
            wait_ms(15);dialog.insert_current()
            added=set(self.c.data()['clip_inputs'])-set(before['clip_inputs'])
            self.assertEqual(len(added),1);self.assertEqual(len(self.c.undo_stack),count+1)
            self.assertFalse(dialog.isVisible())
            self.c.undo();self.assertEqual(self.c.data(),before)
        finally:dialog.close();dialog.deleteLater();wait_ms(1)

    def test_all_real_previews_keep_legible_scale_and_fit_their_width(self):
        wait_ms(400);self.w.persist()
        before=copy.deepcopy(self.w.state);history=len(self.c.undo_stack)
        with patch.object(QScreen,'availableGeometry',return_value=QRect(0,0,1920,1080)):
            dialog=FunctionPicker(self.c);dialog.show();wait_ms(20)
        slot=dialog.preview.size()
        try:
            for i,(kind,*_) in enumerate(FUNCTIONS):
                dialog.list.setCurrentRow(i);wait_ms(25)
                view=dialog.preview
                self.assertGreaterEqual(view.transform().m11(),.85,kind)
                self.assertEqual(view.horizontalScrollBar().maximum(),0,kind)
                self.assertEqual(view.verticalScrollBar().maximum(),0,kind)
                self.assertEqual(view.size(),slot,kind)
                self.assertFalse(view.isInteractive())
                if kind=='stage':
                    actual=next(card for card in self.c.flow_cards.values() if card.kind=='stages')
                    self.assertEqual(view.card.surface_colors(),actual.surface_colors())
            self.assertEqual(self.w.state,before);self.assertEqual(len(self.c.undo_stack),history)
        finally:dialog.close();dialog.deleteLater();wait_ms(1)

    def source_pair(self):
        from prompt_calculus_studio.multi_output import connect
        second=self.c.add_canvas(QPointF(1500,1200))
        self.c.commit(lambda s:connect(s,second,self.out,'text'))
        return second

    def reorder_in_dialog(self,dialog,save):
        dialog.show();wait_ms(10)
        listing=dialog.listing
        from prompt_calculus_studio.ui_icons import ICON_NAMES
        self.assertIn('grip-vertical',ICON_NAMES)
        listing._source=self.cid;listing._drag_token=b'local-order-test'
        mime=QMimeData();mime.setData(listing.mime,listing._drag_token)
        end=listing.visualItemRect(listing.item(1)).bottomLeft()+QPoint(8,-1)
        event=QDropEvent(QPointF(end),Qt.DropAction.MoveAction,mime,
                         Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        listing.dropEvent(event);self.assertTrue(event.isAccepted())
        QTest.mouseClick(dialog.save_button if save else dialog.cancel_button,Qt.MouseButton.LeftButton)
        return dialog.result()

    def test_source_order_save_cancel_undo_redo_and_reopen_preserve_sources(self):
        second=self.source_pair();before=copy.deepcopy(self.c.data());uses=copy.deepcopy(self.w.state['uses'])
        count=len(self.c.undo_stack)
        with patch.object(SourceOrderDialog,'exec',lambda d:self.reorder_in_dialog(d,False)):
            self.c.order_sources(self.out)
        self.assertEqual(self.c.data(),before);self.assertEqual(len(self.c.undo_stack),count)
        with patch.object(SourceOrderDialog,'exec',lambda d:self.reorder_in_dialog(d,True)):
            self.c.order_sources(self.out)
        self.assertEqual(text_sources(self.c.data()['outputs'][self.out]),[second,self.cid])
        self.assertEqual(len(self.c.undo_stack),count+1)
        self.assertEqual(self.w.state['uses'],uses)
        self.assertEqual(self.c.data()['connections'],before['connections'])
        self.c.undo();self.assertEqual(self.c.data(),before)
        self.c.redo();self.w.persist()
        reopened=self.w.store.load()
        self.assertEqual(text_sources(reopened['multi_output']['outputs'][self.out]),[second,self.cid])
        self.assertEqual(reopened['uses'],uses)
        dialog=SourceOrderDialog(self.c,self.out)
        self.assertEqual(dialog.ordered_ids(),[second,self.cid]);dialog.deleteLater()

    def test_solid_port_center_and_hover_ring_keep_generous_hit_area(self):
        port=next(iter(self.c.ports.values()))
        def render():
            image=QImage(32,32,QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.transparent)
            painter=QPainter(image);painter.translate(16,16);port.paint(painter,None);painter.end()
            return image
        normal=render()
        self.assertEqual(normal.pixelColor(16,16).alpha(),255)
        self.assertGreater(normal.pixelColor(19,16).alpha(),200)
        self.assertEqual(normal.pixelColor(24,16).alpha(),0)
        self.assertTrue(port.contains(QPointF(9,10)))
        port.hovered=True;hover=render()
        self.assertGreater(hover.pixelColor(24,16).alpha(),0)
        self.assertEqual(normal.pixelColor(16,16),hover.pixelColor(16,16))

    def test_canvas_background_is_neutral_dark_without_changing_application_base(self):
        colors=canvas_colors(self.c)
        image=QImage(120,120,QImage.Format.Format_ARGB32)
        painter=QPainter(image);self.c.view.drawBackground(painter,QRectF(0,0,120,120));painter.end()
        pixels=Counter(image.pixelColor(x,y).name() for x in range(120) for y in range(120))
        dominant,count=pixels.most_common(1)[0]
        self.assertEqual(dominant,'#111111');self.assertGreater(count,14000)
        self.assertNotEqual(colors['base'],colors['canvas_background'])

    def test_scheduler_management_keeps_the_existing_commands(self):
        from prompt_calculus_studio.widgets import RoundMenu
        from prompt_calculus_studio.schedule_order_dialog import ScheduleOrderDialog
        from prompt_calculus_studio import stage_model
        from prompt_calculus_studio.queue_history import capture
        from _dialog_driver import drive_dialog
        keys=[next(iter(self.c.data()['schedulers']))]
        key=keys[0]
        self.c.commit(lambda s:[stage.update(workflow='flow',output='9') for stage in s['multi_output']['stages'].values()])
        run=self.runner.create_run(stage_model.compile_plan(self.w.state),3)
        from prompt_calculus_studio.stage_batches import expand
        for _ in range(3):expand(self.runner,20)
        self.runner.pause(run['id'])
        entries=self.runner.store.rows('entry');ids=[e['id'] for e in entries]
        self.assertEqual(len(ids),3)
        before=capture(self.runner.store,ids)
        card=self.c.flow_cards[key];panel=card.panel
        menus=[]
        with patch.object(RoundMenu,'open_at',lambda menu,*args:menus.append(menu)):
            card.open_menu(QPoint())
        actions={a.text():a for a in menus[0].actions() if not a.isSeparator()}
        self.assertIn('管理',actions)
        self.assertIn('取消目前項目',actions);self.assertFalse(actions['取消目前項目'].isEnabled())
        self.assertIn('取消整個預排程的所有流程',actions)
        # v7 adds typed inputs when connected; the old manual add-port command
        # is no longer a product capability. Preserve the actual management
        # dialog's order/remove/apply commands and its existing Undo transaction.
        self.assertNotIn('增加資料接點',actions)
        groups=actions['管理'].menu().actions();self.assertEqual(len(groups),1)
        def manage(dialog):
            self.assertEqual(dialog.ordered_ids(),ids)
            dialog.listing.move_row(ids[2],0)
            self.assertEqual(dialog.ordered_ids(),[ids[2],ids[0],ids[1]])
            dialog.listing.setCurrentRow(2);QTest.keyClick(dialog.listing,Qt.Key.Key_Delete)
            self.assertEqual(dialog.ordered_ids(),[ids[2],ids[0]])
            QTest.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton)
        drive_dialog(groups[0].trigger,ScheduleOrderDialog,manage)
        self.assertEqual(self.runner.store.read(ids[1])['status'],'removed')
        self.assertEqual(self.runner.store.waiting_ids(entries[0]['owner']),[ids[2],ids[0]])
        self.c.undo();self.assertEqual(capture(self.runner.store,ids),before)
        self.c.redo();self.assertEqual(self.runner.store.read(ids[1])['status'],'removed')
        self.assertFalse(any(b.text()=='管理' for b in panel.page.content.findChildren(QPushButton)))
        actions['取消整個預排程的所有流程'].trigger()
        self.assertEqual(self.runner.store.read(run['id'])['status'],'cancelled')
        self.assertTrue(all(self.runner.store.read(key)['status']=='retained' for key in (ids[0],ids[2])))
        panel.page.finish();menus[0].deleteLater()
