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


if __name__=='__main__':unittest.main()
