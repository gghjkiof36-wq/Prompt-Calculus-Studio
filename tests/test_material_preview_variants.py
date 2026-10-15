"""Library preview changes only the inspected variant, never execution state."""
import copy
import unittest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import test_editor_reorder as fixture
from prompt_calculus_studio import materials, composition as comp
from test_prompt_material_resolution import text


class VariantPreviewTests(unittest.TestCase):
    setUpClass = fixture.EditorReorderTests.setUpClass.__func__
    setUp = fixture.EditorReorderTests.setUp
    tearDown = fixture.EditorReorderTests.tearDown

    def test_arrows_show_three_contents_without_consuming_or_saving_selection(self):
        root=text('表情',3);ident=[]
        def add(state):
            ident.append(materials.save(state,dict(name='表情變體測試',module=self.cat,
                prompt=comp.render(root),material_type='text',composition=root)))
        self.assertTrue(self.w.canvas.commit(add));self.page.refresh();self.page.preview_material(ident[0])
        before=copy.deepcopy(self.w.state);history=self.w.canvas.history_projection(before)
        self.assertEqual(self.page.preview_text.toPlainText(),'表情-1')
        self.assertFalse(self.page.variant_previous.isEnabled())
        for i in (2,3):
            QTest.mouseClick(self.page.variant_next,Qt.MouseButton.LeftButton)
            self.assertEqual(self.page.preview_text.toPlainText(),f'表情-{i}')
        self.assertFalse(self.page.variant_next.isEnabled())
        self.page.preview_material(ident[0]);self.assertEqual(self.page.preview_text.toPlainText(),'表情-3')
        QTest.mouseClick(self.page.variant_previous,Qt.MouseButton.LeftButton)
        self.assertEqual(self.page.preview_text.toPlainText(),'表情-2')
        self.assertEqual(self.w.canvas.history_projection(self.w.state),history)
        self.assertEqual(materials.item(self.w.state,ident[0]),materials.item(before,ident[0]))
        self.w.persist();self.assertEqual(materials.item(self.w.store.load(),ident[0]),materials.item(before,ident[0]))
        self.page.preview_material(self.ids[0]);self.assertTrue(self.page.variant_navigation.isHidden())

    def test_flow_preview_pages_and_shared_order_never_mutate_source(self):
        from test_prompt_material_resolution import flow
        from prompt_calculus_studio import prompt_materials as pm
        root=flow();ident=materials.save(self.w.state,dict(name='流程預覽',module=self.cat,prompt='',composition=root))
        self.page.refresh();before=copy.deepcopy(materials.item(self.w.state,ident))
        self.page.preview_material(ident);self.app.processEvents()
        layout=self.page.preview_card.layout()
        controls=(self.page.preview_name,self.page.preview_image,self.page.preview_text,self.page.variant_navigation,self.page.edit_button)
        self.assertEqual([layout.indexOf(c) for c in controls],sorted(layout.indexOf(c) for c in controls))
        self.assertEqual(self.page.edit_button.text(),'編輯');self.assertFalse(self.page.edit_button.icon().isNull())
        self.assertTrue(self.page.preview_image.isHidden())
        for at,group in enumerate(pm.sequence(root)['groups']):
            expected=copy.deepcopy(root);pm.sequence(expected)['selected']=group['id']
            if at:self.page.move_preview_variant(1)
            self.assertEqual(self.page.preview_text.toPlainText(),comp.render(expected))
            self.assertEqual(self.page.variant_position.text(),f'{at+1} / {len(pm.sequence(root)["groups"])}')
        self.assertEqual(materials.item(self.w.state,ident),before)


VariantPreviewTests.setUpClass=classmethod(VariantPreviewTests.setUpClass)
