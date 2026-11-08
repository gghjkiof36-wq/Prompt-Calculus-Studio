"""User-visible nested material controls and current-group scheduler progress."""
import copy
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio import composition as comp, prompt_materials as pm
from test_prompt_material_resolution import text, flow
import test_canvas_variant_navigation as canvas_fixture
import test_stage_refresh_projection as schedule_fixture


class NestedCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    setUp=canvas_fixture.CanvasVariantNavigationTests.setUp
    tearDown=canvas_fixture.CanvasVariantNavigationTests.tearDown
    add=canvas_fixture.CanvasVariantNavigationTests.add
    root=canvas_fixture.CanvasVariantNavigationTests.root
    card=canvas_fixture.CanvasVariantNavigationTests.card

    def test_combination_keeps_nested_controls_delete_undo_and_saved_structure(self):
        root=pm.new('combination','組合');child=text('Child',3);leaf=text('Leaf',2)
        child['children']=[leaf];root['combination']['modules']=[child]
        draft=pm.new('text','inactive draft');draft['prompt']='must not display'
        root['children']=[draft]
        self.add(root)
        card=self.card();self.assertEqual([p.value['name'] for p in card.parts],['Child'])
        nested=card.parts[0];self.assertTrue(nested.group_rects)
        self.assertEqual(nested.parts[0].value['name'],'Leaf')
        view=self.w.canvas.view;view.centerOn(nested);self.app.processEvents()
        point=view.mapFromScene(nested.mapToScene(nested.group_rects['group_next'].center()))
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point);self.app.processEvents()
        self.assertEqual(comp.render(self.root()),'Child-2, Leaf-1')
        self.assertNotIn('variants',self.root())
        before=copy.deepcopy(self.root())
        # Placement IDs are fresh; use the actual displayed card's identity.
        key=self.card().parts[0].key
        self.w.canvas.remove_keys([key]);self.assertFalse(self.root()['combination']['modules'])
        self.w.canvas.undo();self.assertEqual(self.root(),before)
        self.w.canvas.redo();self.assertFalse(self.root()['combination']['modules'])
        self.w.canvas.undo();self.w.persist()
        self.assertEqual(self.w.store.load_current(multi=True)['uses'][self.ident],before)

    def test_flow_displays_only_active_group_and_respects_order_and_internal_progress(self):
        root=flow('ab');a,b=root['sequence']['groups']
        a.update(input='modules',modules=[text('A',2)])
        b.update(input='modules',modules=[text('B',2)])
        root['sequence']['groups'].reverse()
        root['sequence']['start']=root['sequence']['selected']=b['id']
        self.add(root)
        self.assertEqual(self.card().parts[0].value['name'],'B')
        current,following=pm.resolve(self.root());self.assertEqual(comp.render(current),'B-1')
        current,following=pm.resolve(following);self.assertEqual(comp.render(current),'A-1')
        current,following=pm.resolve(following);self.assertEqual(comp.render(current),'B-2')
        self.card().group_action('group_next');self.app.processEvents()
        self.assertEqual(self.card().parts[0].value['name'],'A')


class ScheduleCardTests(unittest.TestCase):
    setUp=schedule_fixture.ScheduleRefreshProjectionTests.setUp
    tearDown=schedule_fixture.ScheduleRefreshProjectionTests.tearDown

    def test_current_group_completed_progress_queue_groups_and_empty_pause_control(self):
        from prompt_calculus_studio.flow_data import add_scheduler
        keys=[];self.assertTrue(self.c.commit(lambda state:keys.append(add_scheduler(state))))
        key=keys[0];panel=self.c.flow_cards[key].panel;store=self.runner.store;workspace=self.w.state['workspace']
        self.assertEqual(panel.summary.text(),'沒有等待執行的任務')
        self.assertTrue(panel.progress.isHidden())
        QTest.mouseClick(panel.pause_button,Qt.MouseButton.LeftButton)
        self.assertTrue(store.scheduler_paused(workspace,key))
        self.assertEqual(panel.pause_button.accessibleName(),'繼續預排程')
        self.assertEqual(panel.summary.text(),'沒有等待執行的任務')
        QTest.mouseClick(panel.pause_button,Qt.MouseButton.LeftButton)
        self.assertFalse(store.scheduler_paused(workspace,key))
        entries=[]
        for i in range(10):
            entries.append(store.add('entry',workspace,'owner',run='synthetic',scheduler=key,
                execution_group='current',label='Current',status='complete' if i<2 else 'running' if i==2 else 'waiting',saved={}))
        queued=[store.add('entry',workspace,'owner',run='synthetic',scheduler=key,
            execution_group=f'next-{i}',label='Next',status='waiting',saved={}) for i in range(3)]
        with patch.object(store,'read',side_effect=AssertionError('No frozen content read')):
            panel.refresh()
        self.assertEqual(panel.summary.text(),'2/10 已完成．3 組佇列中')
        self.assertEqual((panel.progress.value(),panel.progress.maximum()),(2,10))
        store.set_batch_control(workspace,key,'current',bypassed=True,cancelled=True)
        panel.refresh();self.assertEqual(panel.summary.text(),'2/10 已完成．3 組佇列中')
        store.set_batch_control(workspace,key,'current',bypassed=False,cancelled=False)
        for entry in queued:store.update(entry['id'],status='cancelled')
        for entry in entries[:9]:store.update(entry['id'],status='complete')
        panel.refresh();self.assertEqual(panel.summary.text(),'9/10 已完成')
        self.assertEqual((panel.progress.value(),panel.progress.maximum()),(9,10))
        store.update(entries[-1]['id'],status='complete');panel.refresh()
        self.assertEqual(panel.summary.text(),'沒有等待執行的任務');self.assertTrue(panel.progress.isHidden())
        store.add('entry',workspace,'owner',run='synthetic',scheduler=key,execution_group='retained',
            label='Stopped',status='retained',saved={})
        panel.refresh();self.assertEqual(panel.summary.text(),'沒有等待執行的任務')
