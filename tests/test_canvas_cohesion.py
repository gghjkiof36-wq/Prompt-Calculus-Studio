"""Stable picker slots and shared material-list motion using real Qt events."""
import copy
from unittest.mock import patch
from PySide6.QtCore import Qt,QObject,QEvent,QPoint,QPointF,QRect,QSize
from PySide6.QtGui import QScreen,QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFrame,QPushButton
from stage_fixture import StageFixture,APP
from prompt_calculus_studio import materials
from prompt_calculus_studio.canvas_palette import CanvasPalette
from prompt_calculus_studio.function_picker import FunctionPicker,FUNCTIONS
from prompt_calculus_studio.widgets import StudioDialog
from prompt_calculus_studio.text_canvas import NodeDialog


class BarEvents(QObject):
    def __init__(self):super().__init__();self.shows=[]
    def eventFilter(self,widget,event):
        if event.type()==QEvent.Type.Show:self.shows.append(widget)
        return False


class CanvasCohesionTests(StageFixture):
    def setUp(self):
        super().setUp()
        self.w.state['settings'].update(visual_palette='graphite',ui_size=11,reduce_motion=False)
        self.w.apply_theme(preserve_layout=True)

    def test_switching_real_nodes_and_empty_search_never_resizes_or_flashes_bars(self):
        with patch.object(QScreen,'availableGeometry',return_value=QRect(0,0,1920,1080)):
            dialog=FunctionPicker(self.c);dialog.show();QTest.qWait(30)
        try:
            geometry=dialog.geometry();slot=dialog.preview.geometry();watch=BarEvents()
            for bar in (dialog.preview.verticalScrollBar(),dialog.preview.horizontalScrollBar()):bar.installEventFilter(watch)
            for _ in range(2):
                for i,(kind,*_) in enumerate(FUNCTIONS):
                    dialog.list.setCurrentRow(i);QTest.qWait(20)
                    self.assertEqual(dialog.geometry(),geometry,kind)
                    self.assertEqual(dialog.preview.geometry(),slot,kind)
                    view=dialog.preview
                    self.assertTrue(view.viewport().rect().adjusted(-1,-1,1,1).contains(
                        view.mapFromScene(view.sceneRect()).boundingRect()),kind)
                dialog.search.setText('no-such-module');QTest.qWait(10)
                self.assertEqual(dialog.geometry(),geometry)
                dialog.search.clear();QTest.qWait(10)
            self.assertFalse(watch.shows)
        finally:dialog.close();dialog.deleteLater();QTest.qWait(1)

    def test_short_screen_and_large_font_keep_search_list_and_add_action_reachable(self):
        self.w.state['settings']['ui_size']=16;self.w.apply_theme(preserve_layout=True)
        with patch.object(QScreen,'availableGeometry',return_value=QRect(0,0,1000,760)):
            dialog=FunctionPicker(self.c);dialog.show();QTest.qWait(20)
            try:
                geometry=dialog.geometry()
                for i in range(len(FUNCTIONS)):
                    dialog.list.setCurrentRow(i);QTest.qWait(10)
                    self.assertEqual(dialog.geometry(),geometry)
                    self.assertLessEqual(dialog.surface.height(),720)
                    for child in (dialog.search,dialog.list,dialog.add_button,dialog.description):
                        rect=QRect(child.mapTo(dialog,QPoint()),child.size())
                        self.assertTrue(dialog.rect().contains(rect),rect)
            finally:dialog.close();dialog.deleteLater();QTest.qWait(1)

    def palette(self):
        for i in range(28):materials.save(self.w.state,dict(name=f'素材 {i:02}',prompt='sample text',module=None))
        dialog=CanvasPalette(self.c,QPointF(800,700),destination_id=self.cid)
        dialog.sheet_size=QSize(900,600);dialog.show();QTest.qWait(25)
        self.addCleanup(dialog.reject)
        return dialog

    def test_stacked_picker_click_uses_visible_card_once_and_undo_restores(self):
        dialog=self.palette();listing=dialog.assets;start,layers=listing._stack()
        targets={}
        for y in range(start,listing.viewport().height()):
            point=QPoint(listing.viewport().width()//2,y);index=listing.indexAt(point)
            if index.isValid():targets.setdefault(index.row(),point)
        self.assertGreaterEqual(len(targets),2)
        row,point=list(targets.items())[-1]
        ident=listing.item(row).data(Qt.ItemDataRole.UserRole)
        before=copy.deepcopy(self.c.data());uses=copy.deepcopy(self.w.state['uses'])
        count=len(self.c.undo_stack)
        with patch.object(self.c,'insert_asset_with_destination',wraps=self.c.insert_asset_with_destination) as insert:
            QTest.mouseClick(listing.viewport(),Qt.MouseButton.LeftButton,pos=point);QTest.qWait(15)
            self.assertEqual(insert.call_count,1)
            self.assertEqual(insert.call_args.args[0]['id'],ident)
        self.assertEqual(len(self.c.undo_stack),count+1)
        self.c.undo();self.assertEqual(self.c.data(),before);self.assertEqual(self.w.state['uses'],uses)

    def test_stack_unfolds_at_end_and_reduced_motion_keeps_direct_wheel_input(self):
        dialog=self.palette();listing=dialog.assets;bar=listing.verticalScrollBar()
        self.assertIsNotNone(listing._stack())
        bar.setValue(bar.maximum());QTest.qWait(10)
        self.assertIsNone(listing._stack())
        self.assertTrue(listing.viewport().rect().contains(listing.visualItemRect(listing.item(listing.count()-1))))
        bar.setValue(0);self.w.state['settings']['reduce_motion']=True
        self.assertIsNone(listing._stack())
        event=QWheelEvent(QPointF(60,60),QPointF(60,60),QPoint(),QPoint(0,-120),
                         Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
        APP.sendEvent(listing.viewport(),event)
        self.assertGreater(bar.value(),0)
        self.assertEqual(listing.scroll_motion.state(),listing.scroll_motion.State.Stopped)

    def test_canvas_operation_and_nested_dialogs_share_finish_without_editing_data(self):
        QTest.qWait(400);self.w.persist()
        before=copy.deepcopy(self.w.state);dialogs=[]
        def inspect(dialog):
            dialogs.append(dialog)
            dialog.show();QTest.qWait(10)
            self.assertTrue(hasattr(dialog,'_module_dialog_context'))
            close=dialog.findChild(QPushButton,'DialogClose')
            self.assertFalse(close.icon().isNull())
            nested=NodeDialog(dialog);nested.show();QTest.qWait(10)
            self.assertEqual(nested.styleSheet(),dialog.styleSheet())
            self.assertEqual(nested.findChild(QFrame,'DialogSurface').palette().window().color(),
                             dialog.findChild(QFrame,'DialogSurface').palette().window().color())
            nested.reject();nested.deleteLater();dialog.reject();return 0
        with patch.object(StudioDialog,'exec',inspect):
            self.c.rename_function('outputs',self.out)
            self.c.size_dialog(self.out)
        self.assertEqual(len(dialogs),2)
        self.assertEqual(self.w.state,before)
        for dialog in dialogs:dialog.deleteLater()

    def test_canvas_dialog_finish_does_not_leak_to_other_main_pages(self):
        canvas_dialog=StudioDialog(self.w)
        self.assertTrue(canvas_dialog.styleSheet())
        canvas_dialog.deleteLater()
        for target in ('settings','gallery','clean_export'):
            if target=='settings':self.w.settings()
            else:self.w.show_page(getattr(self.w,target))
            dialog=StudioDialog(self.w)
            self.assertFalse(dialog.styleSheet(),target)
            self.assertFalse(hasattr(dialog,'_module_dialog_context'),target)
            dialog.deleteLater()
