"""Reachable inline ordering with isolated Qt data and real undo transactions."""
import copy
from pathlib import Path
from PySide6.QtCore import Qt,QPointF,QPoint,QMimeData
from PySide6.QtGui import QDropEvent,QImage,QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from stage_fixture import StageFixture,APP
from prompt_calculus_studio.composition import node
from prompt_calculus_studio.multi_output import assign,connect,compile_output
from prompt_calculus_studio.flow_data import text_sources
from prompt_calculus_studio.changes import layout_only


class SourceTreeTests(StageFixture):
    def prepare(self):
        second=self.c.add_canvas(QPointF(1500,100));self.other=second
        def setup(s):
            s['uses']['root'].update(name='角色',prompt='red dress')
            s['uses']['pose']=node('動作','standing');s['uses']['pose']['children']=[node('手部','hands behind back')]
            s['uses']['scene']=node('場景','sunlit garden')
            assign(s,'pose',self.cid);assign(s,'scene',second);connect(s,second,self.out,'text')
            s['multi_output']['canvases'][self.cid]['name']='人物畫布'
            s['multi_output']['canvases'][second]['name']='場景畫布'
        self.c.commit(setup);APP.processEvents()
        return self.c.outputs[self.out].panel.order

    def group(self,tree,key):
        return next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.token(tree.topLevelItem(i))[1]==key)

    def drop(self,tree,item,target,after=True,process=True,foreign=False):
        tree._source=tree.token(item);tree._drag_token=b'inline-source-order'
        mime=QMimeData();mime.setData(tree.mime,b'foreign' if foreign else tree._drag_token)
        rect=tree.visualItemRect(target);pos=rect.bottomLeft()+QPoint(20,-1) if after else rect.topLeft()+QPoint(20,1)
        event=QDropEvent(QPointF(pos),Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        tree.dropEvent(event);tree._source=None;tree._drag_token=b''
        if process:APP.processEvents()
        return event.isAccepted()

    def test_inline_members_sort_preview_undo_redo_and_reopen(self):
        tree=self.prepare();group=self.group(tree,self.cid)
        self.assertEqual([group.child(i).text(0) for i in range(group.childCount())],['角色','動作'])
        self.assertTrue(group.isExpanded());before=copy.deepcopy(self.w.state)
        self.assertTrue(self.drop(tree,group.child(0),group.child(1)))
        self.assertEqual(self.c.data()['canvases'][self.cid]['members'],['pose','root'])
        self.assertEqual(self.c.data()['canvases'][self.other]['members'],['scene'])
        text=self.c.outputs[self.out].panel.editor.toPlainText()
        self.assertLess(text.index('standing'),text.index('red dress'));self.assertIn('hands behind back',text)
        self.assertEqual(self.w.state['uses'],before['uses']);self.assertEqual(self.c.data()['connections'],before['multi_output']['connections'])
        self.c.undo();self.assertEqual(self.c.data()['canvases'][self.cid]['members'],['root','pose'])
        self.c.redo();self.w.persist();saved=self.w.store.load()
        self.assertEqual(saved['multi_output']['canvases'][self.cid]['members'],['pose','root'])
        self.assertEqual(saved['uses'],before['uses'])

    def test_fold_uses_header_and_keyboard_without_changing_prompt_or_history(self):
        tree=self.prepare();group=self.group(tree,self.cid);before=copy.deepcopy(self.w.state);history=len(self.c.undo_stack)
        text=compile_output(self.w.state,self.out)['final_prompt'];tree.setCurrentItem(group)
        pos=tree.visualItemRect(group).center()
        QTest.mouseClick(tree.viewport(),Qt.MouseButton.LeftButton,pos=pos);APP.processEvents()
        group=self.group(tree,self.cid);self.assertFalse(group.isExpanded())
        self.assertTrue(layout_only(before,self.w.state));self.assertEqual(len(self.c.undo_stack),history)
        self.w.persist();self.assertIn(self.cid,self.w.store.load()['multi_output']['outputs'][self.out]['source_collapsed'])
        QTest.keyClick(tree,Qt.Key.Key_Space);APP.processEvents();self.assertTrue(self.group(tree,self.cid).isExpanded())
        self.assertEqual(compile_output(self.w.state,self.out)['final_prompt'],text)
        buttons=[b.text() for b in self.c.outputs[self.out].panel.findChildren(QPushButton)]
        self.assertNotIn('排序',buttons)
        group=self.group(tree,self.cid);rect=tree.visualItemRect(group)
        self.assertEqual(rect.left(),tree.visualItemRect(group.child(0)).left())
        QTest.mouseClick(tree.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(rect.right()-20,rect.center().y()))
        APP.processEvents();self.assertFalse(self.group(tree,self.cid).isExpanded())
        group=self.group(tree,self.cid);rect=tree.visualItemRect(group)
        QTest.mouseClick(tree.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(rect.right()-20,rect.center().y()))
        APP.processEvents();self.assertTrue(self.group(tree,self.cid).isExpanded())

    def test_source_order_keeps_members_and_manual_empty_draft(self):
        tree=self.prepare()
        from prompt_calculus_studio.drafts import edit
        self.c.commit(lambda s:edit(s,'',self.out));before=copy.deepcopy(self.w.state)
        self.assertTrue(self.drop(tree,self.group(tree,self.cid),self.group(tree,self.other)))
        self.assertEqual(text_sources(self.c.data()['outputs'][self.out]),[self.other,self.cid])
        self.assertEqual(self.c.data()['canvases'],before['multi_output']['canvases'])
        self.assertEqual(self.w.state['draft'],'');self.assertEqual(self.c.outputs[self.out].panel.editor.toPlainText(),'')
        self.c.undo();self.assertEqual(text_sources(self.c.data()['outputs'][self.out]),[self.cid,self.other])
        self.assertEqual(self.w.state['draft'],'')

    def test_image_prompt_source_moves_with_canvas_sources_without_changing_image(self):
        tree=self.prepare();key=self.c.functions.add_image()
        path=Path(self.tmp.name)/'source.png';image=QImage(64,48,QImage.Format.Format_RGB32)
        image.fill(QColor('#8a969d'));image.save(str(path))
        from prompt_calculus_studio.source_controls import load_many
        self.assertTrue(load_many(self.c.functions.cards[key].panel,[str(path)]))
        def setup(s):
            s['canvas_functions']['images'][key]['content']=dict(kind='raw',text='cinematic light')
            connect(s,key,self.out,'clip')
        self.c.commit(setup);APP.processEvents();before=copy.deepcopy(self.w.state['canvas_functions'])
        item=self.group(tree,key);self.assertEqual(item.childCount(),0)
        self.assertTrue(self.drop(tree,item,self.group(tree,self.cid),after=False))
        self.assertEqual(text_sources(self.c.data()['outputs'][self.out]),[key,self.cid,self.other])
        self.assertTrue(self.c.outputs[self.out].panel.editor.toPlainText().startswith('cinematic light'))
        self.assertEqual(self.w.state['canvas_functions'],before)
        self.c.undo();self.assertEqual(text_sources(self.c.data()['outputs'][self.out]),[self.cid,self.other,key])

    def test_member_drop_cannot_cross_canvas_or_accept_foreign_drag(self):
        tree=self.prepare();group=self.group(tree,self.cid);before=copy.deepcopy(self.w.state)
        self.assertFalse(self.drop(tree,group.child(0),self.group(tree,self.other).child(0)))
        self.assertFalse(self.drop(tree,group.child(0),group.child(1),foreign=True))
        tree.setCurrentItem(group.child(0));QTest.keyClick(tree,Qt.Key.Key_Delete)
        self.assertEqual(self.w.state,before)

    def test_stale_queued_reorder_does_not_discard_new_module(self):
        tree=self.prepare();group=self.group(tree,self.cid)
        self.assertTrue(self.drop(tree,group.child(0),group.child(1),process=False))
        def add(s):s['uses']['new']=node('新模組','smile');assign(s,'new',self.cid)
        self.c.commit(add);APP.processEvents()
        self.assertEqual(self.c.data()['canvases'][self.cid]['members'],['root','pose','new'])
        self.assertIn('排序內容已更新，請重新拖曳。',self.notices)

    def test_theme_change_keeps_collapsed_groups_and_visible_content(self):
        tree=self.prepare();self.group(tree,self.cid).setExpanded(False)
        self.w.state['settings'].update(visual_palette='paper',ui_size=16);self.w.apply_theme(preserve_layout=True);APP.processEvents()
        tree=self.c.outputs[self.out].panel.order;group=self.group(tree,self.cid)
        self.assertFalse(group.isExpanded());self.assertEqual(group.childCount(),2)
        self.assertEqual(group.child(0).text(0),'角色');self.assertFalse(group.icon(0).isNull())
        self.assertGreaterEqual(group.sizeHint(0).height(),tree.fontMetrics().height()+20)

    def test_expanded_rows_fit_and_long_lists_remain_bounded(self):
        tree=self.prepare();self.w.resize(1600,960);APP.processEvents()
        last=self.group(tree,self.other).child(0)
        self.assertTrue(tree.viewport().rect().contains(tree.visualItemRect(last)))
        expanded_height=tree.minimumHeight()
        for i in range(tree.topLevelItemCount()):tree.topLevelItem(i).setExpanded(False)
        APP.processEvents();self.assertLess(tree.minimumHeight(),expanded_height)
        self.group(tree,self.cid).setExpanded(True);APP.processEvents()
        group=self.group(tree,self.cid)
        self.assertTrue(tree.viewport().rect().contains(tree.visualItemRect(group.child(1))))
        def add(s):
            for i in range(12):
                key='extra'+str(i);s['uses'][key]=node('模組 '+str(i),'smile');assign(s,key,self.cid)
        self.c.commit(add);APP.processEvents()
        self.assertLessEqual(tree.minimumHeight(),6*max(42,tree.fontMetrics().height()+20)+8)
        self.assertGreater(tree.verticalScrollBar().maximum(),0)
