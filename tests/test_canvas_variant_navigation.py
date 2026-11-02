"""Canvas pager wraps through the normal transaction and output path."""
import copy
import unittest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio import materials,composition as comp,prompt_materials as pm
import test_editor_reorder as fixture
from test_prompt_material_resolution import text,flow


class CanvasVariantNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    setUp=fixture.EditorReorderTests.setUp
    tearDown=fixture.EditorReorderTests.tearDown

    def add(self,root):
        target=next(iter(self.w.state['multi_output']['canvases']));keys=[]
        def apply(state):
            ident=materials.save(state,dict(name=root['name'],prompt='',composition=root,module=self.cat,material_type=pm.kind(root)))
            keys.append(materials.add_to_canvas(state,ident,target))
        self.assertTrue(self.w.canvas.commit(apply));self.w.enter_canvas();self.app.processEvents()
        self.ident=keys[0]

    def root(self):return self.w.state['uses'][self.ident]

    def card(self):
        return next(c for k,c in self.w.canvas.cards.items() if self.w.canvas.entries[k][0]==self.ident)

    def click(self,action):
        card=self.card();view=self.w.canvas.view;view.centerOn(card);self.app.processEvents()
        point=view.mapFromScene(card.mapToScene(card.group_rects[action].center()))
        self.assertTrue(view.viewport().rect().contains(point))
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point);self.app.processEvents()

    def test_sixteen_variants_wrap_both_directions_output_undo_redo_and_save(self):
        self.add(text('Variant',16,'fixed'))
        initial=copy.deepcopy(self.root());groups=self.root()['variants']['groups']
        self.click('group_previous')
        self.assertEqual(self.root()['variants']['selected'],groups[-1]['id'])
        self.assertEqual(comp.render(self.root()),'Variant-16');self.assertIn('Variant-16',self.card().own)
        last=copy.deepcopy(self.root())
        self.click('group_next')
        self.assertEqual(self.root()['variants']['selected'],groups[0]['id'])
        self.assertEqual(comp.render(self.root()),'Variant-1');self.assertIn('Variant-1',self.card().own)
        first=copy.deepcopy(self.root())
        self.w.canvas.undo();self.assertEqual(self.root(),last)
        self.w.canvas.redo();self.assertEqual(self.root(),first)
        self.w.persist();saved=self.w.store.load_current(multi=True)
        self.assertEqual(saved['uses'][self.ident]['variants']['selected'],groups[0]['id'])
        self.assertEqual(self.root()['variants']['groups'],initial['variants']['groups'])
        self.assertEqual(self.root()['variants']['mode'],'fixed')
        self.assertEqual(self.root()['variants']['start'],initial['variants']['start'])

    def test_variant_exclusions_and_automatic_progress_stay_attached_after_wrap(self):
        root=text('Variant',3,'increment');groups=root['variants']['groups']
        pm.set_variant_excludes(root['variants'],groups[0]['id'],['first_only'])
        pm.set_variant_excludes(root['variants'],groups[-1]['id'],['last_only'])
        self.add(root);self.click('group_previous')
        self.assertEqual(self.root()['variants']['selected'],groups[-1]['id'])
        self.assertEqual(pm.position(self.root())['group'],groups[-1]['id'])
        self.assertEqual(self.root()['variants']['groups'][-1]['excludes'],['last_only'])
        current,following=pm.resolve(self.root())
        self.assertEqual(comp.render(current),'Variant-3');self.assertEqual(comp.render(following),'Variant-1')
        self.click('group_next');self.assertEqual(pm.position(self.root())['group'],groups[0]['id'])
        self.assertEqual(self.root()['variants']['groups'][0]['excludes'],['first_only'])

    def test_single_variant_has_no_spurious_edit_and_shared_flow_pager_wraps(self):
        self.add(text('Single',1));before=self.w.canvas.history_projection(self.w.state)
        for action in ('group_previous','group_next'):self.card().group_action(action)
        self.assertEqual(self.w.canvas.history_projection(self.w.state),before)
        self.add(flow('abc','decrement'));self.click('group_previous')
        self.assertEqual(comp.render(self.root()),'c');self.click('group_next')
        self.assertEqual(comp.render(self.root()),'a')

    def test_round_editor_wrap_preserves_both_drafts_apply_cancel_and_history(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QPushButton
        self.add(text('Variant',16,'fixed'));original=copy.deepcopy(self.root())
        def edit_round(action):
            errors=[]
            def operate():
                dialog=self.w.canvas.active_node_dialog
                try:action(dialog)
                except BaseException as exc:errors.append(exc);dialog.reject()
            key=next(k for k,v in self.w.canvas.entries.items() if v[0]==self.ident)
            QTimer.singleShot(0,operate);accepted=self.w.canvas.edit(key)
            if errors:raise errors[0]
            return accepted
        def click_button(dialog,text):
            control=next(b for b in dialog.findChildren(QPushButton) if b.text()==text)
            QTest.mouseClick(control,Qt.MouseButton.LeftButton)
        def apply(dialog):
            self.assertEqual(dialog.index.value(),1);self.assertTrue(dialog.previous.isEnabled())
            dialog.prompt.setPlainText('edited first');dialog.repeat.setValue(2)
            QTest.mouseClick(dialog.previous,Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.index.value(),16);self.assertEqual(dialog.prompt.toPlainText(),'Variant-16')
            self.assertTrue(dialog.next.isEnabled())
            dialog.prompt.setPlainText('edited last');dialog.repeat.setValue(3)
            QTest.mouseClick(dialog.next,Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.index.value(),1);self.assertEqual(dialog.prompt.toPlainText(),'edited first')
            self.assertEqual(dialog.repeat.value(),2)
            QTest.mouseClick(dialog.previous,Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.prompt.toPlainText(),'edited last');self.assertEqual(dialog.repeat.value(),3)
            click_button(dialog,'套用')
        self.assertTrue(edit_round(apply));edited=copy.deepcopy(self.root())
        self.assertEqual(comp.render(edited),'edited last')
        self.assertEqual(edited['variants']['start'],original['variants']['start'])
        self.w.persist();saved=self.w.store.load_current(multi=True)['uses'][self.ident]
        self.assertEqual(saved['variants'],edited['variants'])
        def cancel(dialog):
            self.assertEqual(dialog.index.value(),16);self.assertEqual(dialog.prompt.toPlainText(),'edited last')
            QTest.mouseClick(dialog.next,Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.prompt.toPlainText(),'edited first')
            dialog.prompt.setPlainText('discard');click_button(dialog,'取消')
        self.assertFalse(edit_round(cancel));self.assertEqual(self.root(),edited)
        self.w.canvas.undo();self.assertEqual(self.root(),original)
        self.w.canvas.redo();self.assertEqual(self.root(),edited)

    def test_round_editor_flow_wrap_keeps_overrides_and_single_variant_hidden(self):
        from prompt_calculus_studio.text_canvas import NodeDialog
        root=flow('abc','decrement');before=copy.deepcopy(root)
        dialog=NodeDialog(self.w,root);dialog.show();self.app.processEvents()
        try:
            dialog.prompt.setPlainText('first draft')
            QTest.mouseClick(dialog.previous,Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.index.value(),3);dialog.prompt.setPlainText('last draft')
            QTest.mouseClick(dialog.next,Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.index.value(),1);self.assertEqual(dialog.prompt.toPlainText(),'first draft')
            drafts=dialog.values()['round_drafts'];rows=root['sequence']['groups']
            self.assertEqual(drafts,{rows[0]['id']:'first draft',rows[-1]['id']:'last draft'})
        finally:dialog.reject()
        self.assertEqual(root,before)
        single=NodeDialog(self.w,text('Single',1));single.show();self.app.processEvents()
        self.assertTrue(single.navigation.isHidden());self.assertFalse(single.previous.isEnabled())
        self.assertFalse(single.next.isEnabled());single.reject()


if __name__=='__main__':unittest.main()
