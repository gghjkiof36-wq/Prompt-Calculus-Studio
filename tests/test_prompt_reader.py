import copy
import unittest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit
import test_editor_reorder as fixture
from prompt_calculus_studio import composition as comp, materials, prompt_groups as groups, prompt_sources as sources
from prompt_calculus_studio.prompt_source_widgets import GroupSourceDialog
from prompt_calculus_studio.text_canvas import NodeDialog


class ReaderTests(unittest.TestCase):
    setUp=fixture.EditorReorderTests.setUp
    tearDown=fixture.EditorReorderTests.tearDown
    setUpClass=classmethod(lambda cls:setattr(cls,'app',QApplication.instance() or QApplication([])))

    def setup_reader(self, loaded=True):
        root=comp.node('A16');a=groups.new_group('B1',modules=[comp.node('C1','red dress'),comp.node('C2','white shirt')])
        a['repeat']=2;b=groups.new_group('B2','garden')
        root['sequence']=dict(version=1,mode='increment',start=a['id'],selected=a['id'],groups=[a,b])
        ids={}
        def setup(state):
            ids['material']=materials.save(state,dict(name='A16',prompt='',module=self.cat,composition=root))
            other=copy.deepcopy(root);other['name']='A21'
            ids['other']=materials.save(state,dict(name='A21',prompt='',module=self.cat,composition=other))
            ids['key']=sources.add(state)
            if loaded:sources.read_library(state,ids['key'],ids['material'])
        self.w.canvas.commit(setup);self.app.processEvents()
        return ids

    def show_reader(self,key):
        dialog=GroupSourceDialog(self.w.canvas,key);dialog.show()
        for _ in range(3):self.app.processEvents()
        return dialog

    def click_row(self,listing,at=0,double=False):
        self.app.processEvents();rect=listing.regions(listing.visualItemRect(listing.item(at)))['name']
        action=QTest.mouseDClick if double else QTest.mouseClick
        action(listing.viewport(),Qt.MouseButton.LeftButton,pos=rect.center());self.app.processEvents()

    def edit_child(self,owner,text,accept=True):
        seen=[]
        def fill():
            dialog=owner.active_node_dialog
            seen.append(isinstance(dialog,NodeDialog));dialog.prompt.setPlainText(text)
            if accept:dialog.save()
            else:dialog.reject()
        self.app.processEvents();rect=owner.modules.regions(owner.modules.visualItemRect(owner.modules.item(0)))['name']
        QTimer.singleShot(0,fill)
        QTest.mouseDClick(owner.modules.viewport(),Qt.MouseButton.LeftButton,pos=rect.center());self.app.processEvents()
        self.assertEqual(seen,[True])

    def test_select_only_existing_groups_and_cancel_save_undo_reopen(self):
        ids=self.setup_reader(False);before=copy.deepcopy(sources.definitions(self.w.state)[ids['key']])
        dialog=self.show_reader(ids['key'])
        self.assertEqual({dialog.library.itemData(i) for i in range(dialog.library.count())},{ids['material'],ids['other']})
        self.assertFalse(dialog.library.isEditable());self.assertFalse(hasattr(dialog,'name'))
        self.assertFalse(dialog.apply_button.isEnabled());self.assertFalse(dialog.preview.isVisible())
        dialog.library.setCurrentIndex(dialog.library.findData(ids['other']));self.app.processEvents()
        self.assertTrue(dialog.apply_button.isEnabled());dialog.reject()
        self.assertEqual(sources.definitions(self.w.state)[ids['key']],before)
        dialog=self.show_reader(ids['key']);dialog.library.setCurrentIndex(dialog.library.findData(ids['material']))
        QTest.mouseClick(dialog.apply_button,Qt.MouseButton.LeftButton);self.app.processEvents()
        saved=copy.deepcopy(sources.definitions(self.w.state)[ids['key']])
        self.assertEqual(saved['root']['source_id'],ids['material']);self.assertEqual(saved['name'],'A16')
        self.w.canvas.undo();self.assertEqual(sources.definitions(self.w.state)[ids['key']],before)
        self.w.canvas.redo();self.assertEqual(sources.definitions(self.w.state)[ids['key']],saved)
        self.w.persist();self.assertEqual(sources.definitions(self.w.store.load())[ids['key']],saved)
        dialog=self.show_reader(ids['key']);self.assertEqual(dialog.root,saved['root']);dialog.reject()

    def test_three_levels_double_click_edits_copy_and_cancels_transaction(self):
        ids=self.setup_reader();original=copy.deepcopy(materials.item(self.w.state,ids['material']))
        saved=copy.deepcopy(sources.definitions(self.w.state)[ids['key']])
        dialog=self.show_reader(ids['key']);self.click_row(dialog.listing)
        self.assertEqual(dialog.stack.currentIndex(),1);self.assertEqual(dialog.heading.text(),'B1')
        self.assertFalse(dialog.preview.isVisible());self.assertFalse(dialog.preview_toggle.isChecked())
        self.click_row(dialog.modules);self.assertIsNone(dialog.active_node_dialog)
        self.edit_child(dialog,'discarded',False);self.assertEqual(dialog.current_group['modules'][0]['prompt'],'red dress')
        opened=[]
        def close_child():
            opened.append(dialog.active_node_dialog.windowTitle());dialog.active_node_dialog.reject()
        rect=dialog.modules.regions(dialog.modules.visualItemRect(dialog.modules.item(0)))['open']
        QTimer.singleShot(0,close_child)
        QTest.mouseClick(dialog.modules.viewport(),Qt.MouseButton.LeftButton,pos=rect.center())
        self.assertEqual(opened,['編輯文字模組'])
        self.edit_child(dialog,'local change');self.assertEqual(dialog.current_group['modules'][0]['prompt'],'local change')
        self.assertEqual(dialog.current_group['modules'][1]['prompt'],'white shirt')
        QTest.mouseClick(dialog.preview_toggle,Qt.MouseButton.LeftButton);self.app.processEvents()
        self.assertTrue(dialog.preview.isVisible());self.assertIn('local change',dialog.preview.toPlainText())
        self.assertFalse(dialog.findChildren(QLineEdit));dialog.reject()
        self.assertEqual(sources.definitions(self.w.state)[ids['key']],saved)
        self.assertEqual(materials.item(self.w.state,ids['material']),original)
        dialog=self.show_reader(ids['key']);self.click_row(dialog.listing);self.edit_child(dialog,'saved locally')
        QTest.mouseClick(dialog.done_button,Qt.MouseButton.LeftButton);self.app.processEvents()
        self.assertFalse(dialog.preview.isVisible());QTest.mouseClick(dialog.apply_button,Qt.MouseButton.LeftButton)
        self.assertEqual(sources.definitions(self.w.state)[ids['key']]['root']['sequence']['groups'][0]['modules'][0]['prompt'],'saved locally')
        self.assertEqual(materials.item(self.w.state,ids['material']),original)
        self.w.canvas.undo();self.assertEqual(sources.definitions(self.w.state)[ids['key']],saved)
        self.w.canvas.redo();self.w.persist()
        self.assertEqual(sources.definitions(self.w.store.load())[ids['key']],sources.definitions(self.w.state)[ids['key']])

    def test_library_update_is_manual_and_frozen_plan_keeps_old_content(self):
        ids=self.setup_reader();value=sources.definitions(self.w.state)[ids['key']];before=copy.deepcopy(value)
        plan=sources.build(self.w.state,[ids['key']])
        def edit_original(state):
            material=copy.deepcopy(materials.item(state,ids['material']));material['name']='A16 new'
            material['composition']['sequence']['groups'][0]['modules'][0]['prompt']='from new library'
            materials.save(state,material)
        self.w.canvas.commit(edit_original)
        self.assertEqual(sources.definitions(self.w.state)[ids['key']],before)
        dialog=self.show_reader(ids['key']);self.assertEqual(dialog.library.currentText(),'A16')
        QTest.mouseClick(dialog.reload_button,Qt.MouseButton.LeftButton);self.app.processEvents()
        self.assertEqual(dialog.library.currentText(),'A16 new');self.assertIn('from new library',comp.render(dialog.root))
        dialog.reject();self.assertEqual(sources.definitions(self.w.state)[ids['key']],before)
        dialog=self.show_reader(ids['key']);QTest.mouseClick(dialog.reload_button,Qt.MouseButton.LeftButton)
        QTest.mouseClick(dialog.apply_button,Qt.MouseButton.LeftButton);self.app.processEvents()
        self.assertEqual(sources.definitions(self.w.state)[ids['key']]['name'],'A16 new')
        projected=copy.deepcopy(self.w.state);sources.project(projected,plan,plan['rounds'][0])
        self.assertIn('red dress',comp.render(sources.definitions(projected)[ids['key']]['root']))
        self.assertNotIn('from new library',comp.render(sources.definitions(projected)[ids['key']]['root']))
        self.w.canvas.undo();self.assertEqual(sources.definitions(self.w.state)[ids['key']],before)
        self.w.canvas.redo()

    def test_inspecting_other_round_does_not_advance_reserved_cursor(self):
        ids=self.setup_reader();plan=sources.build(self.w.state,[ids['key']],count=1)
        sources.reserve(self.w.state,plan);saved=copy.deepcopy(sources.definitions(self.w.state)[ids['key']])
        dialog=self.show_reader(ids['key']);self.click_row(dialog.listing,1)
        self.assertEqual(dialog.preview.toPlainText(),'garden');self.assertTrue(dialog.preview.isVisible())
        QTest.mouseClick(dialog.done_button,Qt.MouseButton.LeftButton)
        QTest.mouseClick(dialog.apply_button,Qt.MouseButton.LeftButton)
        self.assertEqual(sources.definitions(self.w.state)[ids['key']],saved)
        dialog=self.show_reader(ids['key']);self.click_row(dialog.listing);self.edit_child(dialog,'retain reservation')
        QTest.mouseClick(dialog.done_button,Qt.MouseButton.LeftButton);QTest.mouseClick(dialog.apply_button,Qt.MouseButton.LeftButton)
        current=sources.definitions(self.w.state)[ids['key']]['root']
        self.assertEqual(current['supply_cursor']['position'],saved['root']['supply_cursor']['position'])
        self.assertEqual(current['supply_cursor']['signature'],sources.signature(current))

    def test_reread_same_material_ignores_older_reservations(self):
        ids=self.setup_reader()
        def direct_only(state):
            material=copy.deepcopy(materials.item(state,ids['material']))
            for group in material['composition']['sequence']['groups']:
                group.update(input='text',text='unchanged text',modules=[])
            materials.save(state,material);sources.read_library(state,ids['key'],ids['material'])
        self.w.canvas.commit(direct_only);plan=sources.build(self.w.state,[ids['key']],count=1)
        dialog=self.show_reader(ids['key']);QTest.mouseClick(dialog.reload_button,Qt.MouseButton.LeftButton)
        QTest.mouseClick(dialog.apply_button,Qt.MouseButton.LeftButton)
        current=sources.definitions(self.w.state)[ids['key']]['root'];before=copy.deepcopy(current)
        self.assertNotEqual(current['id'],plan['sources'][ids['key']]['root']['id'])
        self.assertEqual(sources.signature(current),plan['sources'][ids['key']]['signature'])
        sources.reserve(self.w.state,plan);self.assertEqual(current,before)

    def test_empty_or_removed_library_does_not_erase_copy_or_supply_blank_round(self):
        key=sources.add(self.w.state)
        with self.assertRaisesRegex(ValueError,'先選擇'):sources.build(self.w.state,[key])
        self.assertEqual(sources.resolved_value(self.w.state,key,'content')['value']['roots'],[])
        dialog=self.show_reader(key);self.assertEqual(dialog.library.count(),0);dialog.reject()
        ids=self.setup_reader();before=copy.deepcopy(sources.definitions(self.w.state)[ids['key']])
        dialog=self.show_reader(ids['key'])
        self.w.state['items']=[i for i in self.w.state['items'] if i['id']!=ids['other']]
        dialog.library.setCurrentIndex(dialog.library.findData(ids['other']))
        self.assertEqual(dialog.library.currentData(),ids['material']);self.assertEqual(dialog.root,before['root'])
        self.assertTrue(dialog.error.isVisible());dialog.reject()
        self.w.state['items']=[i for i in self.w.state['items'] if i['id']!=ids['material']]
        dialog=self.show_reader(ids['key']);QTest.mouseClick(dialog.reload_button,Qt.MouseButton.LeftButton)
        self.assertTrue(dialog.error.isVisible());self.assertEqual(dialog.root,before['root']);dialog.reject()
        self.assertEqual(sources.definitions(self.w.state)[ids['key']],before)

    def test_material_editor_uses_same_text_dialog_on_double_click(self):
        ids=self.setup_reader();original=copy.deepcopy(materials.item(self.w.state,ids['material']))
        dialog=self.page.edit_material(ids['material']);editor=dialog.pages.group_editor
        editor.edit_group(editor.sequence['groups'][0]['id']);self.app.processEvents()
        self.click_row(editor.modules);self.assertFalse(editor.inspector.isVisible())
        self.edit_child(editor,'editor child');self.assertEqual(editor.current_group['modules'][0]['prompt'],'editor child')
        dialog.reject();self.assertEqual(materials.item(self.w.state,ids['material']),original)


if __name__=='__main__':unittest.main()
