"""Reachable material copy/history and imported combination semantics."""
import copy
import unittest

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import test_editor_reorder as fixture
from test_prompt_material_resolution import flow, text
from prompt_calculus_studio import composition as comp, content_history, core, materials
from prompt_calculus_studio import prompt_materials as pm
from prompt_calculus_studio.prompt_editor import HistoryPanel


def combination(name='combination'):
    root=pm.new('combination',name)
    root['combination']['modules']=[comp.node('subject','girl, red eyes'),comp.node('place','park')]
    return root


def actual_prompt(root):
    state=core.initial_state();state['settings']['separate_selections']=False
    state['uses']={'audit':root}
    return core.build_prompt(state)


class MaterialAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    setUp=fixture.EditorReorderTests.setUp
    tearDown=fixture.EditorReorderTests.tearDown

    def save_root(self,root):
        ids=[]
        self.assertTrue(self.w.canvas.commit(lambda state:ids.append(materials.save(state,
            dict(name=root['name'],prompt='',composition=root,module=self.cat)))))
        return ids[0]

    def test_library_duplicate_retains_flow_combination_and_all_variants(self):
        for root in (flow(),combination(),text('variants',3)):
            with self.subTest(kind=pm.kind(root)):
                ident=self.save_root(root);original=copy.deepcopy(materials.item(self.w.state,ident))
                dialog=self.page.duplicate_material(ident);self.app.processEvents()
                self.assertEqual(dialog.pages.material_type,pm.kind(root))
                self.assertEqual(bool(dialog.pages.variant_editor),bool(root.get('variants')))
                duplicate_id=dialog.form.base['id'];self.assertNotEqual(duplicate_id,ident)
                dialog.name.setText(root['name']+' copy')
                self.assertTrue(dialog.save(),dialog.error.text());self.app.processEvents()
                duplicate=materials.item(self.w.state,duplicate_id)
                self.assertEqual(pm.kind(duplicate),pm.kind(root))
                field='sequence' if root.get('sequence') else 'combination' if root.get('combination') else 'variants'
                expected=copy.deepcopy(root[field]);saved=copy.deepcopy(duplicate['composition'][field])
                if field=='combination':expected['name']=duplicate['name']
                self.assertEqual(saved,expected)
                self.assertEqual(materials.item(self.w.state,ident),original)

    def test_imported_combination_keeps_exclusion_weight_and_original_isolation(self):
        root=combination();root.update(excludes=['red eyes'],weight=15)
        ident=self.save_root(root);original=copy.deepcopy(materials.item(self.w.state,ident))
        dialog=self.page.new_material();dialog.name.setText('flow with rules')
        dialog.pages.type_picker.setCurrentIndex(dialog.pages.type_picker.findData('flow'))
        editor=dialog.pages.group_editor;editor.open_combination_picker();editor.pick_material(ident)
        added=editor.sequence['groups'][-1]
        used=pm.new('flow','used');used['sequence']['groups']=[copy.deepcopy(added)]
        used['sequence'].update(start=added['id'],selected=added['id'])
        self.assertEqual(actual_prompt(used),actual_prompt(root))
        self.assertEqual(actual_prompt(used),'(girl, park:1.5)')
        self.assertEqual(materials.item(self.w.state,ident),original)
        self.assertNotEqual(added['modules'][0]['id'],root['id'])
        editor.edit_group(added['id']);opened=[]
        def inspect():
            inner=editor.active_node_dialog
            opened.append((inner.windowTitle(),inner.prompt.toPlainText()))
            inner.reject()
        QTimer.singleShot(0,inspect);editor.open_module(added['modules'][0]['id'])
        self.assertEqual(opened,[('編輯組合','girl, red eyes, park')])
        dialog.reject()

    def test_plain_combination_import_does_not_add_a_wrapper(self):
        root=combination();group=pm.as_group(root)
        self.assertEqual(group,root['combination'])
        self.assertIsNot(group,root['combination'])
        group['modules'][0]['prompt']='changed copy'
        self.assertEqual(root['combination']['modules'][0]['prompt'],'girl, red eyes')

    def test_noop_editor_save_does_not_apply_outer_rules_twice(self):
        legacy=comp.node('legacy','girl',children=[comp.node('place','park')])
        for root in (combination(),legacy):
            with self.subTest(explicit=bool(root.get('combination'))):
                root.update(weight=15,excludes=['red eyes'])
                before=actual_prompt(root);ident=self.save_root(root)
                dialog=self.page.edit_material(ident)
                self.assertTrue(dialog.save(),dialog.error.text());self.app.processEvents()
                saved=materials.item(self.w.state,ident)['composition']
                self.assertEqual(actual_prompt(saved),before)

    def create_variant_material(self):
        dialog=self.page.new_material();dialog.name.setText('history variants');dialog.prompt.setPlainText('first')
        dialog.pages.enable_variants();editor=dialog.pages.variant_editor
        editor.stack.currentWidget().setPlainText('second')
        ident=dialog.form.base['id'];self.assertTrue(dialog.save(),dialog.error.text());self.app.processEvents()
        return ident

    def test_editing_inactive_variant_creates_a_restorable_content_version(self):
        ident=self.create_variant_material()
        before=copy.deepcopy(materials.item(self.w.state,ident))
        count=len(content_history.entries(self.w.state,target=ident,content_only=True))
        dialog=self.page.edit_material(ident);editor=dialog.pages.variant_editor
        editor.choose.setCurrentIndex(1);editor.stack.currentWidget().setPlainText('edited second')
        self.assertTrue(dialog.save(),dialog.error.text());self.app.processEvents()
        entries=content_history.entries(self.w.state,target=ident,content_only=True)
        self.assertEqual(len(entries),count+1)
        panel=HistoryPanel(self.w,ident)
        self.assertIn('edited second',panel.detail.toPlainText())
        self.assertIn('second',panel.detail.toPlainText())
        QTest.mouseClick(panel.restore_before,Qt.MouseButton.LeftButton);self.app.processEvents()
        self.assertEqual(materials.item(self.w.state,ident)['composition']['variants'],before['composition']['variants'])
        panel.deleteLater()

    def test_combination_history_displays_and_restores_its_actual_contents(self):
        dialog=self.page.new_material();dialog.name.setText('history combination')
        dialog.pages.type_picker.setCurrentIndex(dialog.pages.type_picker.findData('combination'))
        editor=dialog.pages.group_editor;editor.input_tabs.setCurrentIndex(1);editor.direct_text.setPlainText('first combination')
        ident=dialog.form.base['id'];self.assertTrue(dialog.save(),dialog.error.text());self.app.processEvents()
        dialog=self.page.edit_material(ident);dialog.pages.group_editor.direct_text.setPlainText('second combination')
        self.assertTrue(dialog.save(),dialog.error.text());self.app.processEvents()
        panel=HistoryPanel(self.w,ident)
        self.assertIn('first combination',panel.detail.toPlainText())
        self.assertIn('second combination',panel.detail.toPlainText())
        QTest.mouseClick(panel.restore_before,Qt.MouseButton.LeftButton);self.app.processEvents()
        self.assertEqual(materials.item(self.w.state,ident)['prompt'],'first combination')
        self.w.canvas.undo();self.assertEqual(materials.item(self.w.state,ident)['prompt'],'second combination')
        self.w.canvas.redo();self.assertEqual(materials.item(self.w.state,ident)['prompt'],'first combination')
        panel.deleteLater()


if __name__=='__main__':unittest.main()
