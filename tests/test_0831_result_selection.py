"""Native receipts/history plus actual downstream reader controls, without GPU."""
import copy
import unittest
from PySide6.QtCore import Qt,QPointF,QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QMenu,QLineEdit,QDialogButtonBox
import stage_fixture as fixture
import test_0831_stages as stages
from prompt_studio import multi_output as model
from prompt_studio.flow_data import image_list


class ResultSelectionTests(unittest.TestCase):
    setUp=stages.StageTests.setUp
    tearDown=fixture.StageFixture.tearDown
    click=fixture.StageFixture.click
    workflows=fixture.StageFixture.workflows
    stages=stages.StageTests.stages

    def outputs(self,stage,*,enhanced=False,batch=2):
        key=self.c.functions.add_image(enhanced=enhanced)
        def setup(s):
            p=next(p for p in s['generation']['profiles'] if p['id']=='flow')
            for node,kind in [('12','PreviewImage'),('13','SaveImage')]:
                p['graph'][node]=dict(class_type=kind,inputs=dict(images=['5',0]))
            model.connect(s,stage,key,'image');model.connect(s,key,'__result_preview__','image')
        self.assertTrue(self.c.commit(setup),self.notices)
        self.executor.graphs['flow']=copy.deepcopy(next(p for p in self.w.state['generation']['profiles'] if p['id']=='flow')['graph'])
        self.executor.graphs['flow']['5']['inputs']['batch_size']=batch
        return key

    def select_node(self,key,node):
        panel=self.c.functions.cards[key].panel
        self.c.view.centerOn(self.c.functions.cards[key]);self.c.view.resetTransform();QTest.qWait(10)
        selector=panel.result_node;index=selector.findData(node)
        self.assertGreaterEqual(index,0,(node,[selector.itemData(i) for i in range(selector.count())]))
        QTest.mouseClick(selector,Qt.MouseButton.LeftButton)
        QTest.keyClick(selector,Qt.Key.Key_Home)
        for _ in range(index):QTest.keyClick(selector,Qt.Key.Key_Down)
        QTest.keyClick(selector,Qt.Key.Key_Return);QTest.qWait(20)
        self.assertEqual(self.w.state['canvas_functions']['images'][key].get('output_node'),node)

    def items(self,key):return image_list(self.runner.context(self.runner.results),key)

    def test_both_readers_filter_one_node_keep_batch_and_preview_ownership(self):
        stage=self.stages()[0]
        for enhanced in (False,True):
            with self.subTest(enhanced=enhanced):
                key=self.outputs(stage,enhanced=enhanced,batch=3)
                self.click();self.executor.finish(3,nodes=['9','12','13'])
                self.assertIn(stage,self.runner.results,(self.notices,[(a['status'],a.get('error')) for a in self.runner.store.rows('attempt')]))
                prompt=self.executor.submissions[-1]['prompt_id']
                for node in ('12','13','9'):
                    self.select_node(key,node);items=self.items(key)
                    self.assertEqual([(v['reference']['node'],v['reference']['index']) for v in items],[(node,i) for i in range(3)])
                    self.assertEqual({v['reference']['prompt_id'] for v in items},{prompt})
                    self.assertEqual({v['reference']['stage'] for v in items},{stage})
                    self.assertEqual(self.c.results.input_records,[dict(path=str(self.w.store.directory/v['relative']),name=v['name']) for v in items])
                    self.assertEqual(self.w.comfy.images.source(key)['reference'],items[0]['reference'])

    def test_result_only_node_appears_after_formal_native_completion(self):
        stage=self.stages()[0];key=self.outputs(stage)
        self.executor.graphs['flow']['24']=dict(class_type='SaveImage',inputs=dict(images=['5',0]))
        self.assertLess(self.c.functions.cards[key].panel.result_node.findData('24'),0)
        self.click();self.executor.finish(2,nodes=['9','24'])
        self.select_node(key,'24')
        self.assertEqual([(v['reference']['node'],v['reference']['index']) for v in self.items(key)],[('24',0),('24',1)])

    def test_later_generation_missing_selected_node_clears_image_without_fallback(self):
        stage=self.stages()[0];key=self.outputs(stage)
        self.click();self.executor.finish(2,nodes=['9','12'])
        self.select_node(key,'12');old=self.items(key)[0]['reference']['prompt_id']
        self.click();self.executor.finish(2,nodes=['9'])
        self.assertNotEqual(self.executor.submissions[-1]['prompt_id'],old)
        with self.assertRaisesRegex(ValueError,'沒有圖片'):self.items(key)
        self.assertIsNone(self.w.comfy.images.source(key))
        self.assertIsNone(self.w.state['canvas_functions']['images'][key]['source'])
        self.assertFalse(self.c.results.input_records)
        self.assertFalse(self.c.results.preview.isEnabled())

    def test_same_workflow_stages_never_mix_attempt_results(self):
        a,b=self.stages(('flow','flow'));key=self.outputs(a)
        self.select_node(key,'12');self.click()
        self.executor.finish(2,nodes=['9','12']);first=self.executor.submissions[0]['prompt_id']
        self.executor.finish(2,nodes=['9','12']);second=self.executor.submissions[1]['prompt_id']
        self.assertNotEqual(first,second)
        self.assertEqual({v['reference']['prompt_id'] for v in self.items(key)},{first})
        self.assertEqual({v['reference']['prompt_id'] for v in self.runner.results[b]['images']},{second})
        self.assertFalse({v['reference']['attempt'] for v in self.items(key)}&{v['reference']['attempt'] for v in self.runner.results[b]['images']})

    def test_node_selection_updates_derived_source_and_item_menu_indexes_within_node(self):
        stage=self.stages()[0];key=self.outputs(stage,batch=3)
        canvas=self.c.add_canvas(QPointF(2500,100))
        self.c.commit(lambda s:model.connect(s,key,canvas,'clip'))
        self.click();self.executor.finish(3,nodes=['9','12','13'])
        self.select_node(key,'12')
        current=self.w.state['canvas_functions']['images'][key]['source']
        self.assertEqual(current['reference']['node'],'12')
        def assert_source(node):
            value=self.w.state['canvas_functions']['images'][key]
            self.assertEqual(value['source']['reference']['node'],node,(value.get('output_node'),self.notices))
            roots=self.c.data()['canvases'][canvas]['source_members'];self.assertEqual(len(roots),1)
            self.assertEqual(self.w.state['uses'][roots[0]]['name'],value['source']['name'])
            self.assertEqual(self.w.state['uses'][roots[0]]['prompt'],value['content']['text'])
        assert_source('12')
        self.c.view.setFocus();self.c.view.scene().clearFocus()
        QTest.keyClick(self.c.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        assert_source('9')
        self.assertIsNone(self.c.view.scene().focusItem())
        QTest.keyClick(self.c.view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        assert_source('12')
        panel=self.c.functions.cards[key].panel
        QTest.mouseClick(panel.choose,Qt.MouseButton.LeftButton);QTest.qWait(10)
        menu=QApplication.activePopupWidget();self.assertIsInstance(menu,QMenu)
        action=next(a for a in menu.actions() if a.text().startswith('2 · '))
        QTest.mouseClick(menu,Qt.MouseButton.LeftButton,pos=menu.actionGeometry(action).center());QTest.qWait(20)
        self.assertEqual(self.w.state['canvas_functions']['images'][key]['input_index'],1)
        self.assertEqual([(v['reference']['node'],v['reference']['index']) for v in self.items(key)],[('12',1)])
        self.select_node(key,'13')
        self.assertNotIn('input_index',self.w.state['canvas_functions']['images'][key])
        self.assertEqual(len(self.items(key)),3)

    def test_text_selection_materializes_canvas_and_undo_without_changing_tasks(self):
        from prompt_studio.result_data import add_text_reader
        stage=self.stages()[0];keys=[];canvas=self.c.add_canvas(QPointF(2400,100))
        def setup(state):
            key=add_text_reader(state,(1800,100));keys.append(key)
            state['canvas_functions']['images'][key]['text_field']=['6','text']
            model.connect(state,stage,key,'clip');model.connect(state,key,canvas,'clip')
        self.assertTrue(self.c.commit(setup),self.notices)
        undo_depth=len(self.c.undo_stack);self.click();self.executor.finish();key=keys[0]
        self.assertEqual(len(self.c.undo_stack),undo_depth)
        records=copy.deepcopy(self.runner.store.rows('attempt'));runs=copy.deepcopy(self.runner.runs())
        def assert_text(value):
            current=self.c.data()['canvases'][canvas]
            self.assertNotIn('source_error',current)
            self.assertEqual([self.w.state['uses'][k]['prompt'] for k in current['source_members']],[value])
            self.assertNotIn('_stage_results',self.w.state)
            self.assertEqual(self.runner.store.rows('attempt'),records)
            self.assertEqual(self.runner.runs(),runs)
        assert_text('white shirt')
        selector=self.c.functions.cards[key].panel.selector
        self.c.view.centerOn(self.c.functions.cards[key]);self.c.view.resetTransform();QTest.qWait(10)
        index=selector.findData(['7','text']);self.assertGreaterEqual(index,0)
        QTest.mouseClick(selector,Qt.MouseButton.LeftButton);QTest.keyClick(selector,Qt.Key.Key_Home)
        for _ in range(index):QTest.keyClick(selector,Qt.Key.Key_Down)
        QTest.keyClick(selector,Qt.Key.Key_Return);QTest.qWait(20)
        self.assertEqual(self.w.state['canvas_functions']['images'][key]['text_field'],['7','text'])
        assert_text('original negative')
        self.c.view.setFocus();self.c.view.scene().clearFocus()
        QTest.keyClick(self.c.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        self.assertEqual(self.w.state['canvas_functions']['images'][key]['text_field'],['6','text'],self.notices)
        assert_text('white shirt')
        QTest.keyClick(self.c.view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        assert_text('original negative')
        # Ordinary edits also materialize their inputs. A Canvas rename must
        # not erase a text result just because task data is stored separately.
        original_name=self.c.data()['canvases'][canvas]['name']
        container=self.c.containers[canvas];self.c.view.centerOn(container);QTest.qWait(10)
        pos=self.c.view.mapFromScene(container.mapToScene(QPointF(80,24)))
        QApplication.sendEvent(self.c.view.viewport(),QContextMenuEvent(QContextMenuEvent.Reason.Mouse,pos,
            self.c.view.viewport().mapToGlobal(pos)));QTest.qWait(10)
        menu=QApplication.activePopupWidget();self.assertIsInstance(menu,QMenu)
        action=next(a for a in menu.actions() if a.text()=='重新命名')
        def rename():
            dialog=QApplication.activeModalWidget();editor=dialog.findChild(QLineEdit)
            QTest.keyClick(editor,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier);QTest.keyClicks(editor,'Text results')
            buttons=dialog.findChild(QDialogButtonBox)
            QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Save),Qt.MouseButton.LeftButton)
        QTimer.singleShot(20,rename)
        QTest.mouseClick(menu,Qt.MouseButton.LeftButton,pos=menu.actionGeometry(action).center());QTest.qWait(20)
        self.assertEqual(self.c.data()['canvases'][canvas]['name'],'Text results');assert_text('original negative')
        self.c.view.setFocus();self.c.view.scene().clearFocus()
        QTest.keyClick(self.c.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        self.assertEqual(self.c.data()['canvases'][canvas]['name'],original_name);assert_text('original negative')
        QTest.keyClick(self.c.view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        self.assertEqual(self.c.data()['canvases'][canvas]['name'],'Text results');assert_text('original negative')
        self.assertEqual(self.workflows(),['flow'])


if __name__=='__main__':unittest.main()
