import copy,os,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QPoint,QPointF,QMimeData
from PySide6.QtGui import QWheelEvent,QDragEnterEvent,QDragMoveEvent,QDropEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from prompt_studio import composition as comp
from prompt_studio.core import build_prompt
from prompt_studio.window import Window
APP=QApplication.instance() or QApplication([])


class CanvasGestureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'qa'); self.directory=Path(self.temp.name)/'data'
        self.w=Window(self.directory); self.w.state['settings'].update(online=False,material='solid'); self.w.apply_theme()
        self.w.resize(1440,950); self.w.show(); self.w.set_interface_mode('canvas'); QTest.qWait(40)
        self.c=self.w.canvas; self.tree=self.w.selected; self.view=self.c.view
    def tearDown(self):
        self.tree._drag_source=None; self.tree._scroll_timer.stop()
        self.w.close(); APP.processEvents(); self.temp.cleanup()
    def populate(self,count=3):
        ids=[]
        self.c.commit(lambda state:[ids.append(self.c.add_root(state,comp.node(f'item {i}',f'item {i}'))) for i in range(count)])
        self.w.builder_fold.set_expanded(True,animated=False); self.tree.setFixedHeight(180)
        self.view.resetTransform(); self.view.scale(.8,.8)
        APP.processEvents(); self.view.centerOn(self.c.output); APP.processEvents()
        return ids
    def point(self,local):
        root_point=self.tree.viewport().mapTo(self.w.builder_panel,local)
        return self.view.mapFromScene(self.c.output.proxy.mapToScene(QPointF(root_point)))
    def wheel(self,point,delta):
        event=QWheelEvent(QPointF(point),QPointF(self.view.viewport().mapToGlobal(point)),QPoint(),QPoint(0,delta),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
        APP.sendEvent(self.view.viewport(),event); APP.processEvents(); self.assertTrue(event.isAccepted())
    def drop(self,source_index,target_index,after=False,foreign=False):
        source=self.tree.topLevelItem(source_index) if isinstance(source_index,int) else source_index
        target=self.tree.topLevelItem(target_index) if isinstance(target_index,int) else target_index
        self.tree.setCurrentItem(source); self.tree._drag_source=self.tree.key(source); self.tree._drag_token=b'active-gesture'
        mime=QMimeData(); mime.setData('application/x-prompt-studio-order',b'foreign' if foreign else self.tree._drag_token)
        rect=self.tree.visualItemRect(target); local=rect.center(); local.setY(rect.bottom()-2 if after else rect.top()+2)
        point=self.point(local); self.assertTrue(self.view.viewport().rect().contains(point))
        enter=QDragEnterEvent(point,Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        enter.ignore(); APP.sendEvent(self.view.viewport(),enter)
        move=QDragMoveEvent(point,Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        move.ignore(); APP.sendEvent(self.view.viewport(),move)
        if foreign:
            self.assertFalse(self.tree.owns_drag(move)); self.assertIsNone(self.tree._drop_hint)
        else: self.assertIsNotNone(self.tree._drop_hint)
        drop=QDropEvent(QPointF(point),Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        drop.ignore(); APP.sendEvent(self.view.viewport(),drop)
        self.tree._drag_source=None; self.tree._drag_token=b''; self.tree._scroll_timer.stop(); APP.processEvents()
        return drop.isAccepted()
    def test_scrollable_builder_owns_wheel_at_both_edges_and_blank_canvas_zooms(self):
        self.populate(24); bar=self.tree.verticalScrollBar(); self.assertGreater(bar.maximum(),0)
        point=self.point(self.tree.viewport().rect().center()); scale=self.view.transform().m11()
        bar.setValue(0); self.wheel(point,-120); self.assertGreater(bar.value(),0); self.assertEqual(self.view.transform().m11(),scale)
        bar.setValue(bar.maximum()); self.wheel(point,-120); self.assertEqual(bar.value(),bar.maximum()); self.assertEqual(self.view.transform().m11(),scale)
        bar.setValue(0); self.wheel(point,120); self.assertEqual(bar.value(),0); self.assertEqual(self.view.transform().m11(),scale)
        blank=next(QPoint(x,y) for x in range(8,self.view.width(),80) for y in range(8,self.view.height(),80) if self.view.itemAt(QPoint(x,y)) is None)
        self.wheel(blank,120); self.assertGreater(self.view.transform().m11(),scale)
    def test_proxy_drop_reorders_prompt_and_persists_with_undo_redo(self):
        ids=self.populate(); before=build_prompt(self.w.state)
        self.assertTrue(self.drop(2,0)); expected='(item 2:1.0),\n\n(item 0:1.0),\n\n(item 1:1.0)'
        self.assertEqual(build_prompt(self.w.state),expected); self.assertEqual(self.w.final.toPlainText(),expected)
        self.assertEqual(self.tree.key(self.tree.topLevelItem(0)),('use',ids[2]))
        self.c.undo(); self.assertEqual(build_prompt(self.w.state),before)
        self.c.redo(); self.assertEqual(build_prompt(self.w.state),expected)
        self.assertTrue(self.w.persist()); stored=self.w.store.load(); self.assertEqual(build_prompt(stored),expected)
        self.assertTrue(self.drop(0,2,after=True)); self.assertEqual(build_prompt(self.w.state),before)
    def test_foreign_drag_does_not_change_canvas_order(self):
        self.populate(); before=copy.deepcopy(self.w.state)
        self.drop(2,0,foreign=True); self.assertEqual(self.w.state,before)
    def test_child_rows_reorder_through_proxy_and_keep_parent(self):
        ids=self.populate(2)
        self.c.commit(lambda state:state['uses'][ids[0]]['children'].extend([comp.node('first','first'),comp.node('second','second')]))
        self.tree.expandAll(); APP.processEvents(); parent=self.tree.topLevelItem(0)
        self.assertTrue(self.drop(parent.child(1),parent.child(0)))
        self.assertEqual([n['prompt'] for n in self.w.state['uses'][ids[0]]['children']],['second','first'])
        self.assertIn('item 0, second, first',self.w.final.toPlainText())
        self.c.undo(); self.assertEqual([n['prompt'] for n in self.w.state['uses'][ids[0]]['children']],['first','second'])
    def test_preview_preserves_grab_point_at_canvas_scales(self):
        self.populate(); item=self.tree.topLevelItem(1); self.tree.setCurrentItem(item)
        for scale in (.5,1.,1.65):
            self.view.resetTransform(); self.view.scale(scale,scale); APP.processEvents()
            rect=self.tree.visualItemRect(item)
            for fraction in (.2,.5,.8):
                press=QPoint(rect.left()+round(rect.width()*fraction),rect.center().y())
                QTest.mousePress(self.tree.viewport(),Qt.MouseButton.LeftButton,pos=press)
                pixmap,hotspot=self.tree.drag_preview(); expected=press-rect.topLeft()
                self.assertEqual(hotspot,QPoint(round(expected.x()*scale),round(expected.y()*scale)))
                self.assertAlmostEqual(pixmap.deviceIndependentSize().width(),rect.width()*scale,delta=1)
                self.assertAlmostEqual(pixmap.deviceIndependentSize().height(),rect.height()*scale,delta=1)
                QTest.mouseRelease(self.tree.viewport(),Qt.MouseButton.LeftButton,pos=press)


if __name__=='__main__': unittest.main()
