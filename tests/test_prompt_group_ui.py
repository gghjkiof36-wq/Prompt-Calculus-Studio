import copy
import unittest
from PySide6.QtCore import Qt, QCoreApplication, QEvent,QPointF,QPoint,QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication,QLineEdit,QCheckBox,QListWidget
from PySide6.QtTest import QTest
import test_editor_reorder as fixture
from prompt_calculus_studio import materials, composition as comp, prompt_groups as groups
from prompt_calculus_studio.text_canvas import NodeDialog


class GroupUITests(unittest.TestCase):
    setUp=fixture.EditorReorderTests.setUp
    tearDown=fixture.EditorReorderTests.tearDown

    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)

    def create(self):
        dialog=self.page.new_material();dialog.name.setText('旅行組合');dialog.pages.type_picker.setCurrentIndex(1)
        return dialog,dialog.pages.group_editor

    def test_create_cancel_and_form_switch_retain_both_drafts(self):
        before=copy.deepcopy(self.w.state);dialog,ed=self.create()
        dialog.prompt.setPlainText('single draft')
        ed.edit_group(ed.sequence['groups'][0]['id']);ed.direct_text.setPlainText('group text')
        ed.input_tabs.setCurrentIndex(0);ed.add_empty();ed.module_text.setPlainText('module draft')
        ed.input_tabs.setCurrentIndex(1);self.assertEqual(ed.direct_text.toPlainText(),'group text')
        self.assertEqual(ed.current_group['modules'][0]['prompt'],'module draft')
        ed.back();dialog.pages.type_picker.setCurrentIndex(0);self.assertEqual(dialog.prompt.toPlainText(),'single draft')
        dialog.pages.type_picker.setCurrentIndex(1);self.assertEqual(ed.sequence['groups'][0]['text'],'group text')
        QTest.mouseClick(dialog.cancel_button,Qt.MouseButton.LeftButton);self.assertEqual(self.w.state,before)

    def test_save_reopen_undo_redo_and_original_copy_isolation(self):
        dialog,ed=self.create();first=ed.sequence['groups'][0]
        ed.edit_group(first['id']);ed.direct_text.setPlainText('A');ed.repeat.setValue(2);ed.back();ed.add_group();ed.direct_text.setPlainText('B');ed.back()
        ident=dialog.form.base['id'];QTest.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton);self.app.processEvents()
        self.assertIsNone(self.page.active_dialog);saved=copy.deepcopy(materials.item(self.w.state,ident))
        dialog=self.page.edit_material(ident);self.assertEqual(dialog.pages.group_editor.sequence,saved['composition']['sequence']);dialog.reject()
        self.w.canvas.undo();self.assertFalse(any(i['id']==ident for i in self.w.state['items']))
        self.w.canvas.redo();self.assertEqual(materials.item(self.w.state,ident),saved)
        target=next(iter(self.w.state['multi_output']['canvases']));added=[]
        self.w.canvas.commit(lambda state:added.append(materials.add_to_canvas(state,ident,target)))
        self.w.state['uses'][added[0]]['sequence']['groups'][0]['text']='copy edit'
        self.assertEqual(materials.item(self.w.state,ident)['composition']['sequence']['groups'][0]['text'],'A')
        self.w.persist();loaded=self.w.store.load();self.assertEqual(materials.item(loaded,ident)['composition']['sequence'],saved['composition']['sequence'])

    def test_picker_single_click_preserves_query_category_and_source(self):
        dialog,ed=self.create();ed.edit_group(ed.sequence['groups'][0]['id']);ed.input_tabs.setCurrentIndex(0)
        original=copy.deepcopy(materials.item(self.w.state,self.ids[0]));ed.open_picker();ed.search.setText('Bravo')
        self.app.processEvents();self.assertEqual(ed.candidates.count(),1)
        rect=ed.candidates.visualItemRect(ed.candidates.item(0));QTest.mouseClick(ed.candidates.viewport(),Qt.MouseButton.LeftButton,pos=rect.center())
        self.assertEqual(len(ed.current_group['modules']),1);self.assertEqual(ed.stack.currentIndex(),2)
        node=ed.current_group['modules'][0];self.assertEqual(node['source_id'],self.ids[0]);self.assertNotEqual(node['id'],comp.prototype(original)['id'])
        ed.stack.setCurrentIndex(1);ed.open_picker();self.assertEqual(ed.search.text(),'Bravo')
        ed.current_group['modules'][0]['prompt']='local';self.assertEqual(materials.item(self.w.state,self.ids[0]),original)
        dialog.reject()

    def test_three_group_rows_visible_and_inline_weight_stays_in_the_row(self):
        dialog,ed=self.create()
        ed.sequence['groups'].extend([groups.new_group('第二組','B'),groups.new_group('第三組','C')]);ed.refresh_overview()
        dialog.resize(900,740);self.app.processEvents()
        for index in range(3):
            rect=ed.listing.visualItemRect(ed.listing.item(index))
            self.assertTrue(ed.listing.viewport().rect().contains(rect),f'group {index+1} is clipped')
        self.assertEqual(ed.listing.verticalScrollBar().maximum(),0)
        ed.edit_group(ed.sequence['groups'][0]['id']);ed.input_tabs.setCurrentIndex(0);ed.add_material(self.ids[0])
        self.app.processEvents()
        rect=ed.modules.regions(ed.modules.visualItemRect(ed.modules.item(0)))['value']
        QTest.mouseClick(ed.modules.viewport(),Qt.MouseButton.LeftButton,pos=rect.center());self.app.processEvents()
        self.assertTrue(ed.inspector.isHidden())
        edit=self.app.focusWidget();self.assertIsInstance(edit,QLineEdit)
        QTest.keyClicks(edit,'1.7');QTest.keyClick(edit,Qt.Key.Key_Return);self.app.processEvents()
        self.assertEqual(ed.current_group['modules'][0]['weight'],17)
        self.assertIn(':1.7',ed.preview.toPlainText())
        dialog.reject()

    def test_picker_folders_counts_collapse_and_reopen_match_canvas(self):
        from prompt_calculus_studio import material_navigation as nav
        from prompt_calculus_studio.material_folder_navigation import ROW
        from prompt_calculus_studio.canvas_palette import CanvasPalette
        folders=[]
        def setup(state):
            fid=nav.add_folder(state,'人物');folders.append(fid)
            nav.move(state,self.cat,fid);nav.move(state,self.other_cat,fid)
            state['settings']['material_folder_collapsed']=[fid]
        self.w.canvas.commit(setup);fid=folders[0]
        dialog,ed=self.create();ed.edit_group(ed.sequence['groups'][0]['id']);ed.open_picker();self.app.processEvents()
        entries=lambda listing:{listing.item(i).data(Qt.ItemDataRole.UserRole):listing.item(i).data(ROW) for i in range(listing.count())}
        initial=entries(ed.folders);self.assertEqual(initial[fid]['count'],5);self.assertNotIn(self.cat,initial)
        item=next(ed.folders.item(i) for i in range(ed.folders.count()) if ed.folders.item(i).data(Qt.ItemDataRole.UserRole)==fid)
        arrow=ed.folders.regions(ed.folders.visualItemRect(item),item.data(ROW))[0]
        QTest.mouseClick(ed.folders.viewport(),Qt.MouseButton.LeftButton,pos=arrow.center());self.app.processEvents()
        self.assertNotIn(fid,self.w.state['settings']['material_folder_collapsed'])
        self.assertEqual(entries(ed.folders)[self.cat]['count'],3)
        canvas=CanvasPalette(self.w.canvas,QPointF(0,0),destination_id=next(iter(self.w.state['multi_output']['canvases'])))
        self.assertEqual(entries(canvas.modules),entries(ed.folders));canvas.reject()
        ed.toggle_folder(fid);ed.stack.setCurrentIndex(1);ed.open_picker()
        self.assertNotIn(self.cat,entries(ed.folders));self.w.persist()
        self.assertIn(fid,self.w.store.load()['settings']['material_folder_collapsed'])
        dialog.reject()

    def test_group_preview_is_collapsed_below_full_width_modules(self):
        self.w.resize(1440,1000)
        dialog,ed=self.create();ed.edit_group(ed.sequence['groups'][0]['id']);ed.input_tabs.setCurrentIndex(0)
        for count in (2,6):
            ed.current_group['modules']=[comp.node(str(i),'cat, garden') for i in range(count)]
            ed.refresh_modules();dialog.resize(820,800)
            for _ in range(3):self.app.processEvents()
            dialog.fit_page();self.app.processEvents()
            self.assertEqual(ed.group_name.height(),ed.repeat.height())
            self.assertTrue(ed.preview.isHidden());self.assertFalse(ed.preview_toggle.isChecked())
            QTest.mouseClick(ed.preview_toggle,Qt.MouseButton.LeftButton)
            for _ in range(3):self.app.processEvents()
            self.assertTrue(ed.preview.isVisible())
            listing=ed.modules.mapTo(dialog,QPoint(0,0));preview=ed.preview.mapTo(dialog,QPoint(0,0))
            self.assertGreater(preview.y(),listing.y()+ed.modules.height())
            self.assertEqual(preview.x(),listing.x());self.assertEqual(ed.preview.width(),ed.modules.width())
            self.assertEqual(dialog.form_scroll.horizontalScrollBar().maximum(),0)
            QTest.mouseClick(ed.preview_toggle,Qt.MouseButton.LeftButton);self.app.processEvents()
        dialog.reject()

    def test_round_dialog_overrides_only_first_repetition_and_keeps_other_group_draft(self):
        root=comp.node('round');a=groups.new_group('A','A');b=groups.new_group('B','B');a['repeat']=2
        root['sequence']=dict(version=1,mode='increment',start=a['id'],selected=a['id'],groups=[a,b])
        dialog=NodeDialog(self.w,root);dialog.show();dialog.prompt.setPlainText('A edited');QTest.mouseClick(dialog.next,Qt.MouseButton.LeftButton)
        dialog.prompt.setPlainText('B edited');QTest.mouseClick(dialog.previous,Qt.MouseButton.LeftButton);self.assertEqual(dialog.prompt.toPlainText(),'A edited')
        root.update(dialog.values());dialog.reject();comp.validate_node(root)
        record,pos=groups.take(root['sequence'],groups.cursor(root['sequence']));one=groups.resolve_round(root,record,'one',use_override=True)
        record,pos=groups.take(root['sequence'],pos);two=groups.resolve_round(root,record,'two')
        self.assertEqual(comp.render(one),'A edited');self.assertEqual(comp.render(two),'A')
        self.assertEqual(root['sequence']['groups'][0]['text'],'A')

    def test_new_canvas_module_entry_adds_local_copy_and_can_undo(self):
        original=copy.deepcopy(self.w.state['items']);before=set(self.w.state['uses'])
        titles=[]
        def fill():
            dialog=self.w.canvas.active_node_dialog;titles.append(dialog.windowTitle())
            dialog.name.setText('local module');dialog.prompt.setPlainText('cat, garden');dialog.save()
        QTimer.singleShot(0,fill)
        self.assertTrue(self.w.canvas.add_new())
        self.assertEqual(titles,['新增畫布模組'])
        self.assertEqual(self.w.state['items'],original)
        added=set(self.w.state['uses'])-before;self.assertEqual(len(added),1)
        self.assertEqual(comp.render(self.w.state['uses'][added.pop()]),'(cat, garden:1.0)')
        self.w.canvas.undo();self.assertEqual(set(self.w.state['uses']),before)

    def test_source_card_controls_without_duplicate_prompt_preview(self):
        from PySide6.QtWidgets import QPlainTextEdit
        from prompt_calculus_studio import prompt_sources as sources
        keys=[]
        def add(state):
            root=comp.node('source');a=groups.new_group('first','A');b=groups.new_group('second','B')
            root['sequence']=dict(version=1,mode='increment',start=a['id'],selected=a['id'],groups=[a,b])
            keys.append(sources.add(state,root=root))
        self.w.canvas.commit(add);self.w.enter_canvas();self.app.processEvents()
        panel=self.w.canvas.functions.cards[keys[0]].panel
        self.assertEqual(panel.findChildren(QPlainTextEdit),[])
        QTest.mouseClick(panel.next,Qt.MouseButton.LeftButton);self.app.processEvents()
        self.assertEqual(panel.group_name.text(),'second')
        panel.supply.setFocus();QTest.keyClick(panel.supply,Qt.Key.Key_End);self.app.processEvents()
        self.assertEqual(sources.definitions(self.w.state)[keys[0]]['supply'],'batch')
        for mode,name in groups.MODES.items():
            def select_mode():
                menu=self.app.activePopupWidget();action=next(a for a in menu.actions() if a.text()==name)
                action.trigger();menu.close()
            QTimer.singleShot(0,select_mode);QTest.mouseClick(panel.mode,Qt.MouseButton.LeftButton);self.app.processEvents()
            self.assertEqual(sources.definitions(self.w.state)[keys[0]]['root']['sequence']['mode'],mode)
            self.assertEqual(panel.mode.text(),'');self.assertEqual(panel.mode.toolTip(),'變量：'+name)
        self.assertTrue(panel.limit_row.isVisible());panel.limit.setValue(7)
        self.assertEqual(sources.definitions(self.w.state)[keys[0]]['limit'],7)
        panel.supply.setFocus();QTest.keyClick(panel.supply,Qt.Key.Key_Home);self.app.processEvents()
        self.assertTrue(panel.limit_row.isHidden())

    def test_canvas_group_footer_controls_preserve_round_and_inline_weight_undo(self):
        ids=[];canvas=self.w.canvas
        def add(state):
            root=comp.node('流程','');a=groups.new_group('A','red dress');b=groups.new_group('B','white shirt')
            root['sequence']=dict(version=1,mode='increment',start=a['id'],selected=a['id'],groups=[a,b])
            material=materials.save(state,dict(name='流程',prompt='',module=self.cat,composition=root))
            ids.append(materials.add_to_canvas(state,material,next(iter(state['multi_output']['canvases']))))
        canvas.commit(add);self.w.enter_canvas();self.app.processEvents();key=ids[0]
        def click(action):
            card=canvas.cards[key];canvas.view.centerOn(card);self.app.processEvents()
            point=canvas.view.mapFromScene(card.mapToScene(card.actions()[action].center()))
            QTest.mouseClick(canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=point);QTest.qWait(20)
        click('group_next');root=self.w.state['uses'][key]
        self.assertEqual(groups.selected(root['sequence'])['name'],'B')
        click('weight');self.assertIsNotNone(canvas.cards[key].weight_editor)
        edit=canvas.cards[key].weight_editor.widget();self.assertIsInstance(edit,QLineEdit)
        QTest.keyClicks(edit,'1.7');QTest.keyClick(edit,Qt.Key.Key_Return);QTest.qWait(20)
        self.assertEqual(self.w.state['uses'][key]['weight'],17)
        canvas.undo();self.assertEqual(self.w.state['uses'][key]['weight'],10)
        self.assertEqual(groups.selected(self.w.state['uses'][key]['sequence'])['name'],'B')
        canvas.undo();self.assertEqual(groups.selected(self.w.state['uses'][key]['sequence'])['name'],'A')

    def test_deduplication_is_inside_excludes_without_per_tag_controls(self):
        root=comp.node('tags','cat, cat ears, cat, (cat:1.2)')
        dialog=NodeDialog(self.w,root);dialog.show();self.app.processEvents()
        self.assertFalse(dialog.findChildren(QListWidget));self.assertFalse(dialog.excludes_panel.isVisible())
        self.assertEqual([c.text() for c in dialog.findChildren(QCheckBox) if c.isVisible()],['啟用'])
        QTest.mouseClick(dialog.excludes_toggle,Qt.MouseButton.LeftButton);self.app.processEvents()
        self.assertTrue(dialog.excludes.isVisible());self.assertTrue(dialog.dedupe.isVisible())
        QTest.mouseClick(dialog.dedupe,Qt.MouseButton.LeftButton,pos=QPoint(10,dialog.dedupe.height()//2))
        self.assertTrue(dialog.dedupe.isChecked())
        self.assertEqual(comp.render(dict(root,**dialog.values())),'cat, cat ears, (cat:1.2)')
        QTest.mouseClick(dialog.excludes_toggle,Qt.MouseButton.LeftButton);self.app.processEvents()
        self.assertFalse(dialog.dedupe.isVisible());self.assertTrue(dialog.values()['deduplicate']);dialog.reject()
        self.assertEqual(root['prompt'],'cat, cat ears, cat, (cat:1.2)')
        # Existing optional fields remain readable; removing controls must not erase old data.
        legacy=dict(root,disabled_tags=['cat'],deduplicate=True)
        dialog=NodeDialog(self.w,legacy);self.assertEqual(comp.render(dict(legacy,**dialog.values())),comp.render(legacy));dialog.reject()
        prose=comp.node('prose','cat, cat');prose.update(content_kind='text',deduplicate=True,disabled_tags=['cat'])
        dialog=NodeDialog(self.w,prose);dialog.show()
        self.assertEqual(comp.render(dict(prose,**dialog.values())),'cat, cat');dialog.reject()
        dialog,ed=self.create();ed.edit_group(ed.sequence['groups'][0]['id']);ed.input_tabs.setCurrentIndex(0);ed.add_empty()
        self.assertFalse(hasattr(ed,'tag_list'));self.assertFalse(ed.module_dedupe.isVisible())
        ed.excludes_toggle.setChecked(True);ed.module_dedupe.setChecked(True)
        self.assertTrue(ed.current_group['modules'][0]['deduplicate']);dialog.reject()

    def test_compact_text_controls_keep_edits_and_canvas_uses_same_dialog(self):
        root=comp.node('文字模組','cat, garden, cat ears')
        dialog=NodeDialog(self.w,root);dialog.show()
        dialog.excludes_toggle.setChecked(True);dialog.dedupe.setChecked(True)
        dialog.excludes.setPlainText('garden');dialog.enabled.setChecked(False)
        expected=dialog.values()
        for width in (580,430,580):
            dialog.resize(width,760)
            for _ in range(4):self.app.processEvents()
            self.assertGreater(dialog.enabled.y(),dialog.prompt.geometry().bottom())
            self.assertGreater(dialog.excludes_panel.y(),dialog.enabled.geometry().bottom())
            self.assertEqual(dialog.excludes_panel.width(),dialog.prompt.width())
            self.assertEqual(dialog.form_scroll.horizontalScrollBar().maximum(),0)
            self.assertEqual(dialog.values(),expected)
        dialog.resize(430,440)
        for _ in range(4):self.app.processEvents()
        self.assertGreater(dialog.form_scroll.verticalScrollBar().maximum(),0)
        for control in (dialog.enabled,dialog.excludes,dialog.dedupe):
            dialog.form_scroll.ensureWidgetVisible(control)
            self.app.processEvents()
            point=control.mapTo(dialog.form_scroll.viewport(),control.rect().center())
            self.assertTrue(dialog.form_scroll.viewport().rect().contains(point))
        dialog.reject();self.assertTrue(root['enabled']);self.assertEqual(root['excludes'],[])
        use=[];target=next(iter(self.w.state['multi_output']['canvases']))
        self.w.canvas.commit(lambda state:use.append(materials.add_to_canvas(state,self.ids[0],target)))
        self.w.enter_canvas();self.app.processEvents();canvas=self.w.canvas
        key=next(k for k,(ident,_) in canvas.entries.items() if ident==use[0])
        saved=[]
        def fill():
            current=canvas.active_node_dialog
            saved.append(isinstance(current,NodeDialog));current.prompt.setPlainText('Canvas edit')
            current.enabled.setChecked(False);current.excludes_toggle.setChecked(True);current.dedupe.setChecked(True);current.save()
        QTimer.singleShot(0,fill);canvas.edit(key)
        self.assertEqual(saved,[True]);self.assertFalse(self.w.state['uses'][use[0]]['enabled'])
        self.assertEqual(self.w.state['uses'][use[0]]['prompt'],'Canvas edit')
        self.assertTrue(self.w.state['uses'][use[0]]['deduplicate'])
        canvas.undo();self.assertTrue(self.w.state['uses'][use[0]]['enabled'])
        canvas.redo();self.w.persist();self.assertEqual(self.w.store.load()['uses'][use[0]]['prompt'],'Canvas edit')

    def test_authoring_rounds_and_modules_drag_order_is_saved(self):
        dialog,ed=self.create();first=ed.sequence['groups'][0]
        second=groups.new_group('第二輪','B');ed.sequence['groups'].append(second);ed.refresh_overview()
        fixture.EditorReorderTests.drag(self,ed.listing,second['id'],first['id'],grip=True)
        self.assertEqual([g['id'] for g in ed.sequence['groups']],[second['id'],first['id']])
        ed.edit_group(first['id']);ed.input_tabs.setCurrentIndex(0)
        ed.add_material(self.ids[0]);ed.add_material(self.ids[2]);self.app.processEvents()
        modules=first['modules'];before=[m['id'] for m in modules]
        fixture.EditorReorderTests.drag(self,ed.modules,before[1],before[0],grip=True)
        self.assertEqual([m['id'] for m in modules],before[::-1])
        expected=copy.deepcopy(ed.sequence);ed.back();ident=dialog.form.base['id']
        QTest.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton)
        self.assertEqual(materials.item(self.w.state,ident)['composition']['sequence'],expected)
        self.w.canvas.undo();self.assertFalse(any(i['id']==ident for i in self.w.state['items']))
        self.w.canvas.redo();self.w.persist()
        self.assertEqual(materials.item(self.w.store.load(),ident)['composition']['sequence'],expected)


if __name__=='__main__':unittest.main()
