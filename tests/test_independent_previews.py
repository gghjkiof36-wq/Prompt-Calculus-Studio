"""Actual Canvas cards, clipboard operations and persistence for image views."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt,QPointF,QCoreApplication,QEvent
from PySide6.QtTest import QTest
import test_084_stages as prior
from prompt_calculus_studio import multi_output as model
from prompt_calculus_studio.preview_nodes import LEGACY
from prompt_calculus_studio.module_chrome import toggle_module
from prompt_calculus_studio.canvas_operations import capture,paste


class PreviewStateTests(unittest.TestCase):
    def test_first_preview_initializes_a_valid_document_and_snapshot(self):
        from prompt_calculus_studio.core import initial_state,validate_state
        from prompt_calculus_studio.preview_nodes import add
        from prompt_calculus_studio.snapshots import make_snapshot,restore_snapshot
        state=initial_state();key=add(state)
        validate_state(state)
        self.assertEqual(state['version'],6)
        restored=restore_snapshot(state,make_snapshot(state))
        self.assertIn(key,restored['canvas_functions']['previews'])
        self.assertEqual(restored['canvas_functions']['images'],{})


class PreviewTests(unittest.TestCase):
    setUp=prior.StageTests.setUp
    def tearDown(self):
        # The Windows offscreen plugin crashes at Python shutdown if it still
        # owns QMimeData. A Qt-only probe reproduces this without PCS widgets.
        prior.fixtures.APP.clipboard().clear()
        prior.StageTests.tearDown(self)
        self.w.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    image_batch=prior.StageTests.image_batch

    def add_preview(self,x=1100):
        from prompt_calculus_studio.function_picker import FunctionPicker
        before=set(self.c.preview_cards)
        dialog=FunctionPicker(self.c,QPointF(x,300));dialog.show()
        for i in range(dialog.list.count()):
            if dialog.list.item(i).data(Qt.ItemDataRole.UserRole)=='preview':dialog.list.setCurrentRow(i);break
        QTest.mouseClick(dialog.add_button,Qt.MouseButton.LeftButton);QTest.qWait(20)
        created=set(self.c.preview_cards)-before
        self.assertEqual(len(created),1,self.notices)
        dialog.deleteLater();return created.pop()

    def wired_pair(self):
        source,items,_=self.image_batch(count=2,auto=False)
        first=self.add_preview();second=self.add_preview(1700)
        self.assertTrue(self.c.commit(lambda s:(model.connect(s,source,first,'image'),model.connect(s,source,second,'image'))),self.notices)
        return source,items,first,second

    def test_two_views_share_readonly_images_without_source_advancement(self):
        source,items,first,second=self.wired_pair()
        a=self.c.preview_cards[first].panel;b=self.c.preview_cards[second].panel
        self.assertIsNot(a,b);self.assertIsNot(a.input_records,b.input_records)
        self.assertEqual(a.input_records,b.input_records);self.assertEqual(len(a.input_records),2)
        before=copy.deepcopy(self.w.state['canvas_functions']['images'][source])
        with patch('prompt_calculus_studio.image_viewer.show_image') as show:
            a.show_image(1);b.show_image(0)
            self.assertNotEqual(show.call_args_list[0].args[1],show.call_args_list[1].args[1])
        self.assertEqual(self.w.state['canvas_functions']['images'][source],before)
        self.assertFalse(self.executor.submissions)
        toggle_module(self.c,first);QTest.qWait(20)
        self.assertTrue(self.c.preview_cards[first].collapsed)
        self.assertFalse(self.c.preview_cards[second].collapsed)

    def test_replace_one_input_and_remove_one_preserve_other_view(self):
        source,items,first,second=self.wired_pair()
        other=self.c.functions.add_image(source=items[1],enhanced=False)
        self.assertTrue(self.c.commit(lambda s:model.connect(s,other,first,'image')),self.notices)
        self.assertEqual(len(self.c.preview_cards[first].panel.input_records),1)
        unchanged=copy.deepcopy(self.c.preview_cards[second].panel.input_records)
        self.c.functions.remove(first);QTest.qWait(20)
        self.assertNotIn(first,self.c.preview_cards)
        self.assertEqual(self.c.preview_cards[second].panel.input_records,unchanged)
        self.c.view.setFocus();QTest.keyClick(self.c.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        self.assertIn(first,self.c.preview_cards)
        self.assertEqual(len(self.c.preview_cards[first].panel.input_records),1)
        QTest.keyClick(self.c.view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        self.assertNotIn(first,self.c.preview_cards)

    def test_clipboard_copy_creates_independent_instance_without_external_edge(self):
        source,items,first,second=self.wired_pair()
        toggle_module(self.c,first)
        self.c.view.scene().clearSelection();self.c.preview_cards[first].setSelected(True)
        self.c.view.setFocus();QTest.keyClick(self.c.view,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
        before=set(self.c.preview_cards)
        QTest.keyClick(self.c.view,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        created=set(self.c.preview_cards)-before;self.assertEqual(len(created),1,self.notices)
        clone=created.pop();self.assertTrue(self.c.preview_cards[clone].collapsed)
        self.assertFalse(any(e['destination']==clone for e in self.c.data()['connections']))
        toggle_module(self.c,clone)
        self.assertTrue(self.c.preview_cards[first].collapsed)
        self.assertFalse(self.c.preview_cards[clone].collapsed)

    def test_copy_source_and_preview_keeps_only_internal_edge(self):
        source,items,first,second=self.wired_pair();clones=[]
        payload=capture(self.w.state,[source,first])
        payload['images'][source]['attached']=True
        self.assertEqual(len(payload['connections']),1)
        self.assertTrue(self.c.commit(lambda s:clones.extend(paste(s,payload,(2400,300)))),self.notices)
        preview=next(k for k in clones if k in self.c.preview_cards)
        image=next(k for k in clones if k in self.w.state['canvas_functions']['images'])
        self.assertTrue(any(e['source']==image and e['destination']==preview for e in self.c.data()['connections']))
        self.assertEqual(self.c.preview_cards[preview].panel.input_records,self.c.preview_cards[first].panel.input_records)
        self.assertFalse(self.w.state['canvas_functions']['images'][image]['attached'])
        self.w.state['canvas_functions']['images'][image]['index']=1
        self.assertEqual(self.w.state['canvas_functions']['images'][source]['index'],0)

    def test_legacy_preview_copy_and_reopen_preserve_display_and_wiring(self):
        from prompt_calculus_studio.window import Window
        source,items,_=self.image_batch(count=2,auto=False)
        self.assertTrue(self.c.commit(lambda s:(s['canvas_functions'].update(preview=True),model.connect(s,source,LEGACY,'image'))))
        toggle_module(self.c,LEGACY);clones=[]
        self.assertTrue(self.c.commit(lambda s:clones.extend(paste(s,capture(s,[LEGACY]),(1200,500)))))
        key=clones[0]
        self.assertTrue(self.c.commit(lambda s:model.connect(s,source,key,'image')))
        self.w.persist();self.w.close();QTest.qWait(20)
        self.w=Window(self.tmp.name);self.w.comfy.timer.stop();self.w.comfy.enabled=False
        self.w.show();self.w.set_interface_mode('canvas');QTest.qWait(25);self.c=self.w.canvas
        self.assertEqual(self.w.state['version'],6)
        self.assertTrue(self.c.preview_cards[LEGACY].collapsed)
        self.assertTrue(self.c.preview_cards[key].collapsed)
        self.assertEqual(self.c.preview_cards[LEGACY].panel.input_records,self.c.preview_cards[key].panel.input_records)
        toggle_module(self.c,key)
        self.assertTrue(self.c.preview_cards[LEGACY].collapsed)


if __name__=='__main__':unittest.main()
