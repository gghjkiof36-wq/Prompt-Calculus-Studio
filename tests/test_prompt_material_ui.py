import copy
import unittest
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
import test_editor_reorder as fixture
from prompt_calculus_studio import materials, composition as comp


class MaterialLevelsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    setUp=fixture.EditorReorderTests.setUp
    tearDown=fixture.EditorReorderTests.tearDown

    def test_independent_combination_save_two_input_drafts_and_filter(self):
        dialog=self.page.new_material();dialog.name.setText('可重用組合')
        dialog.pages.type_picker.setCurrentIndex(dialog.pages.type_picker.findData('combination'))
        editor=dialog.pages.group_editor
        self.assertTrue(editor.standalone);self.assertEqual(editor.stack.currentIndex(),1)
        self.assertTrue(editor.group_metadata.isHidden());self.assertTrue(dialog.save_button.isVisible())
        editor.add_material(self.ids[0]);editor.input_tabs.setCurrentIndex(1);editor.direct_text.setPlainText('direct draft')
        editor.input_tabs.setCurrentIndex(0);editor.deduplicate.setChecked(True)
        ident=dialog.form.base['id'];self.assertTrue(dialog.save(),dialog.error.text());self.app.processEvents()
        saved=materials.item(self.w.state,ident)
        self.assertEqual(saved['material_type'],'combination');self.assertNotIn('sequence',saved['composition'])
        self.assertEqual(saved['composition']['combination']['text'],'direct draft')
        self.assertEqual(comp.render(saved['composition']),'shared Bravo')
        self.page.type_filter.setCurrentIndex(self.page.type_filter.findData('combination'))
        self.assertEqual(self.page.listing.count(),1)
        dialog=self.page.edit_material(ident);self.assertEqual(dialog.pages.material_type,'combination')
        dialog.pages.group_editor.input_tabs.setCurrentIndex(1)
        self.assertEqual(dialog.pages.group_editor.direct_text.toPlainText(),'direct draft');dialog.reject()

    def test_text_variants_have_separate_undo_documents_and_cancel_does_not_publish(self):
        before=self.w.canvas.history_projection(self.w.state);dialog=self.page.new_material();dialog.name.setText('表情變體')
        dialog.prompt.setPlainText('smile');QTest.mouseClick(dialog.pages.add_variants,Qt.MouseButton.LeftButton)
        editor=dialog.pages.variant_editor;self.assertEqual(len(editor.sequence['groups']),2)
        self.app.processEvents()
        self.assertGreaterEqual(editor.stack.height(),240)
        self.assertGreaterEqual(editor.height(),editor.minimumSizeHint().height())
        second=editor.stack.currentWidget();second.insertPlainText('laughing')
        editor.choose.setCurrentIndex(0);first=editor.stack.currentWidget();first.insertPlainText(', happy')
        first.undo();self.assertEqual(first.toPlainText(),'smile')
        editor.choose.setCurrentIndex(1);self.assertEqual(second.toPlainText(),'laughing')
        second.undo();self.assertEqual(second.toPlainText(),'')
        dialog.reject();self.assertEqual(self.w.canvas.history_projection(self.w.state),before)

    def test_variant_save_reopen_and_source_copy_isolation(self):
        dialog=self.page.new_material();dialog.name.setText('測試變體');dialog.prompt.setPlainText('first')
        dialog.pages.enable_variants();editor=dialog.pages.variant_editor
        editor.stack.currentWidget().setPlainText('second');editor.mode.setCurrentIndex(editor.mode.findData('increment'))
        editor.repeat.setValue(3);editor.move(-1)
        ident=dialog.form.base['id'];self.assertTrue(dialog.save(),dialog.error.text());self.app.processEvents()
        saved=materials.item(self.w.state,ident);self.assertEqual(saved['material_type'],'text')
        self.assertEqual([g['text'] for g in saved['composition']['variants']['groups']],['second','first'])
        dialog=self.page.edit_material(ident);self.assertIsNotNone(dialog.pages.variant_editor)
        self.assertEqual(dialog.pages.variant_editor.sequence['mode'],'increment');dialog.reject()
        self.w.canvas.undo();self.assertFalse(any(i['id']==ident for i in self.w.state['items']))
        self.w.canvas.redo();self.assertEqual(materials.item(self.w.state,ident),saved)

    def test_flow_picker_only_offers_combinations_module_picker_only_text(self):
        dialog=self.page.new_material();dialog.name.setText('獨立組合')
        dialog.pages.type_picker.setCurrentIndex(dialog.pages.type_picker.findData('combination'))
        dialog.pages.group_editor.add_material(self.ids[0]);ident=dialog.form.base['id']
        self.assertTrue(dialog.save(),dialog.error.text());self.app.processEvents()
        dialog=self.page.new_material();dialog.name.setText('流程')
        dialog.pages.type_picker.setCurrentIndex(dialog.pages.type_picker.findData('flow'));editor=dialog.pages.group_editor
        editor.open_combination_picker();self.assertEqual(editor.candidates.count(),1)
        editor.pick_material(ident);self.assertEqual(len(editor.sequence['groups']),2)
        editor.edit_group(editor.sequence['groups'][1]['id']);editor.open_picker()
        self.assertTrue(editor.candidates.count()>0)
        self.assertNotIn(ident,[editor.candidates.item(i).data(Qt.ItemDataRole.UserRole) for i in range(editor.candidates.count())])
        dialog.reject();self.assertFalse(self.errors,self.errors)

    def test_canvas_variant_current_value_pager_and_header_weight_are_consistent(self):
        from prompt_calculus_studio import prompt_materials as pm
        from prompt_calculus_studio.text_canvas import NodeDialog
        from test_prompt_material_resolution import text
        root=text('表情',3);root['variants']['groups'][0]['repeat']=2
        _,root=pm.resolve(root);before=copy.deepcopy(root)
        dialog=NodeDialog(self.w,root);dialog.show();self.app.processEvents()
        self.assertEqual(dialog.prompt.toPlainText(),'表情-1')
        self.assertEqual(dialog.mode.height(),dialog.repeat.height())
        dialog.prompt.setPlainText('edited first');dialog.index.setValue(2)
        self.assertEqual(dialog.prompt.toPlainText(),'表情-2');dialog.index.setValue(1)
        self.assertEqual(dialog.prompt.toPlainText(),'edited first')
        local=dict(root,**dialog.values());dialog.reject();self.assertEqual(root,before)
        current,following=pm.resolve(local)
        self.assertEqual(comp.render(current),'edited first');self.assertEqual(comp.render(following),'表情-2')
        target=next(iter(self.w.state['multi_output']['canvases']));keys=[]
        def add(state):
            ident=materials.save(state,dict(name=root['name'],prompt='',composition=root,module=self.cat,material_type='text'))
            keys.append(materials.add_to_canvas(state,ident,target))
        self.assertTrue(self.w.canvas.commit(add));self.w.enter_canvas();self.app.processEvents()
        card=next(c for k,c in self.w.canvas.cards.items() if self.w.canvas.entries[k][0]==keys[0])
        self.assertLess(card.weight_rect.bottom(),44)
        self.assertGreater(card.group_rects['group_index'].top(),44)
        self.assertLessEqual(card.title_rect.right(),card.minus_rect.left())
        self.assertIn('表情-1',card.own);card.group_action('group_next');self.app.processEvents()
        self.assertEqual(comp.render(self.w.state['uses'][keys[0]]),'表情-2')
        self.w.canvas.undo();self.assertEqual(comp.render(self.w.state['uses'][keys[0]]),'表情-1')

    def test_reader_search_picker_returns_to_copy_and_keeps_filter(self):
        from prompt_calculus_studio import prompt_sources as sources
        from prompt_calculus_studio.prompt_source_widgets import GroupSourceDialog
        from test_prompt_material_resolution import flow
        refs={}
        def setup(state):
            refs['item']=materials.save(state,dict(name='搜尋流程',prompt='',module=self.cat,composition=flow(),aliases=['find flow']))
            refs['reader']=sources.add(state)
        self.w.canvas.commit(setup)
        # Settle the setup's pending workspace snapshot, so a later autosave
        # cannot be mistaken for a mutation caused by browsing the picker.
        self.w.persist();before=copy.deepcopy(self.w.state)
        dialog=GroupSourceDialog(self.w.canvas,refs['reader']);dialog.show();self.app.processEvents()
        self.assertLess(dialog.panels.main.width(),480);self.assertTrue(dialog.footer_divider.isHidden())
        dialog.open_picker()
        dialog.search.setText('find flow');self.app.processEvents()
        self.assertEqual(dialog.candidates.count(),1);dialog.pick_material(refs['item'])
        self.assertEqual(dialog.stack.currentIndex(),0);self.assertEqual(dialog.library.text(),'搜尋流程')
        self.app.processEvents()
        # The offscreen platform may expose only an 800px screen. The selected
        # layout grows to its intended width when enough screen space exists.
        self.assertGreaterEqual(dialog.panels.main.width(),min(600,dialog.panels.bounds().width()-dialog.panels.side_width-26))
        self.assertTrue(dialog.footer_divider.isVisible())
        dialog.open_picker();self.assertEqual(dialog.search.text(),'find flow')
        dialog.reject();self.assertEqual(self.w.state,before)

    def test_large_flow_and_thousand_material_picker_use_rows_not_per_item_widgets(self):
        from PySide6.QtWidgets import QWidget
        from prompt_calculus_studio import prompt_materials as pm,prompt_groups as groups
        root=pm.new('flow','十六組測試')
        root['sequence']['groups']=[groups.new_group(f'組合 {i+1}',modules=[comp.node(f'文字 {i}-{j}',f'tag_{i}_{j}') for j in range(8)]) for i in range(16)]
        root['sequence'].update(start=root['sequence']['groups'][0]['id'],selected=root['sequence']['groups'][0]['id'])
        template=copy.deepcopy(materials.item(self.w.state,self.ids[0]))
        self.w.state['items'].extend(dict(copy.deepcopy(template),id=f'scale-{i}',name=f'搜尋素材 {i:04d}') for i in range(1000))
        ident=materials.save(self.w.state,dict(name=root['name'],prompt='',module=self.cat,composition=root))
        self.w.resize(980,760);dialog=self.page.edit_material(ident);self.app.processEvents()
        editor=dialog.pages.group_editor;self.assertEqual(editor.listing.count(),16)
        editor.edit_group(root['sequence']['groups'][0]['id']);self.assertEqual(editor.modules.count(),8)
        editor.open_picker();self.app.processEvents()
        self.assertGreaterEqual(editor.candidates.count(),1000)
        self.assertLess(len(editor.candidates.findChildren(QWidget)),30)
        editor.search.setText('搜尋素材 0999');self.app.processEvents();self.assertEqual(editor.candidates.count(),1)
        editor.pick_material('scale-999');self.app.processEvents();self.assertEqual(editor.modules.count(),9)
        self.assertTrue(dialog.panels.bounds().contains(dialog.geometry()))
        editor.preview_toggle.setChecked(True);QTest.qWait(250)
        main=dialog.panels.main.geometry();side=dialog.panels.side.geometry()
        self.assertGreaterEqual(side.left()-main.right(),dialog.panels.GAP)
        self.assertEqual(side.top(),main.top());dialog.reject()


if __name__=='__main__':unittest.main()
