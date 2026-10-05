import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt, QPoint, QPointF, QObject, QEvent
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.core import (initial_state, build_prompt, validate_state, output_groups,
    activate_selection_view, set_selection_separation, reorder_output)
from prompt_calculus_studio import composition as c
from prompt_calculus_studio.snapshots import make_snapshot, restore_snapshot, validate_snapshot, portable_state
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.dialogs import SettingsDialog

APP=QApplication.instance() or QApplication([])

def people():
    s=initial_state(); s.update(version=3,selection_view='canvas',prompt_layout='paragraphs')
    girl=c.node('白襯衫女孩','1girl, white shirt',children=[c.node('坐姿','sitting')],grouped=True)
    other=c.node('紅裙女孩','1girl, red dress',children=[c.node('看向鏡頭','looking at viewer')],grouped=True)
    s['uses']={'white':girl,'red':other}; return s

class ParagraphCoreTests(unittest.TestCase):
    def test_root_paragraphs_preserve_nested_weights_order_and_omissions(self):
        s=people(); s['uses']['red']['weight']=12
        expected='(1girl, white shirt, sitting:1.0),\n\n(1girl, red dress, looking at viewer:1.2)'
        self.assertEqual(build_prompt(s),expected)
        reorder_output(s,('use','white'),('use','red'),True)
        self.assertEqual(build_prompt(s),',\n\n'.join(reversed(expected.split(',\n\n'))))
        s['uses']['white']['enabled']=False
        self.assertEqual(build_prompt(s),'(1girl, red dress, looking at viewer:1.2)')
        s['uses']['red']['children'][0]['enabled']=False
        self.assertEqual(build_prompt(s),'(1girl, red dress:1.2)')
        s['uses']['red']['enabled']=False; self.assertEqual(build_prompt(s),'')

    def test_snapshot_preserves_paragraphs_and_restores_only_its_view(self):
        s=people(); item=s['items'][0]; s['selections']={item['module']:[item['id']]}; s['draft']='canvas manual'
        snap=make_snapshot(s); validate_snapshot(snap)
        self.assertEqual(snap['state']['selections'],{}); self.assertIn('\n\n',snap['generated_prompt'])
        current=initial_state(); item=current['items'][1]; current['selections']={item['module']:[item['id']]}; current['draft']='list manual'
        restored=restore_snapshot(current,snap)
        self.assertEqual(restored['selections'],current['selections']); self.assertEqual(restored['draft'],'canvas manual')
        self.assertEqual(build_prompt(restored),snap['generated_prompt'])
        activate_selection_view(restored,'list'); self.assertEqual(restored['draft'],'list manual')
        self.assertEqual(build_prompt(restored),build_prompt(current))
        activate_selection_view(restored,'canvas'); self.assertEqual(restored['draft'],'canvas manual')
        set_selection_separation(current,False); current['draft']='shared manual'
        restored=restore_snapshot(current,snap)
        activate_selection_view(restored,'list'); self.assertEqual(restored['draft'],'list manual')
        set_selection_separation(restored,False); self.assertEqual(restored['draft'],'shared manual')

    def test_old_compact_snapshot_still_valid_after_paragraph_support(self):
        s=people(); s.pop('prompt_layout'); s['settings']['separate_selections']=False
        old=make_snapshot(s); old['state']['settings']={}; old['state'].pop('selection_view')
        validate_snapshot(old)
        current=people(); restored=restore_snapshot(current,old)
        self.assertNotIn('\n\n',build_prompt(restored)); self.assertEqual(build_prompt(restored),old['generated_prompt'])
        self.assertEqual(restored['prompt_layout'],'compact')

    def test_scopes_exclude_other_view_rules_and_keep_both_orders(self):
        s=people(); first,second=s['items'][4:6]; mid=first['module']
        s['selections']={mid:[first['id'],second['id']]}; s['items'][4]['excludes']=['white shirt']
        s['output_order']=['red',mid,'white']; original=copy.deepcopy(s['selections'])
        self.assertEqual(portable_state(s)['output_order'],s['output_order'])
        self.assertIn('white shirt',build_prompt(s))
        activate_selection_view(s,'list'); self.assertEqual(output_groups(s),[mid]); self.assertNotIn('1girl',build_prompt(s))
        set_selection_separation(s,False); self.assertNotIn('white shirt',build_prompt(s))
        set_selection_separation(s,True); activate_selection_view(s,'canvas')
        self.assertEqual(output_groups(s),['red','white']); self.assertEqual(s['selections'],original)
        self.assertIn('white shirt',build_prompt(s)); validate_state(s)

class PaintObserver(QObject):
    def __init__(self,parent): super().__init__(parent); self.regions=[]
    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.Paint: self.regions.append(event.region().boundingRect())
        return False

class ParagraphAndIsolationUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'qa'); self.w=Window(Path(self.tmp.name)/'data')
        self.w.state['settings'].update(online=False,material='solid'); self.w.apply_theme()
        self.w.resize(1280,900); self.w.show(); APP.processEvents()

    def tearDown(self): self.w.close(); APP.processEvents(); self.tmp.cleanup()

    def fill_both(self):
        item=self.w.state['items'][0]; self.w.state['selections']={item['module']:[item['id']]}
        self.w.state['temporary']=['list only']; self.w.refresh_builder()
        list_prompt=build_prompt(self.w.state)
        self.w.enter_canvas(); self.w.canvas.insert_asset(self.w.state['items'][1]); self.w.canvas.add_tag('simple background')
        self.w.canvas.fit(); APP.processEvents(); return list_prompt

    def test_default_checkbox_cancel_and_toggle_keep_choices_and_drafts(self):
        dialog=SettingsDialog(self.w); self.assertTrue(dialog.separate_selections.isChecked())
        dialog.separate_selections.setChecked(False); dialog.reject(); self.assertTrue(self.w.state['settings']['separate_selections'])
        list_prompt=self.fill_both(); expected='(1girl, white shirt:1.0),\n\n(simple background:1.0)'
        self.assertEqual(self.w.final.toPlainText(),expected); self.assertEqual(len(self.w.canvas.cards),2)
        self.w.final.setPlainText('canvas manual'); self.w.leave_canvas(); self.assertEqual(self.w.final.toPlainText(),list_prompt)
        self.w.final.setPlainText('list manual'); self.w.enter_canvas(); self.assertEqual(self.w.final.toPlainText(),'canvas manual')
        picks=copy.deepcopy(self.w.state['selections']); uses=copy.deepcopy(self.w.state['uses'])
        dialog=SettingsDialog(self.w); dialog.separate_selections.setChecked(False); dialog.save()
        self.w.leave_canvas(); self.assertEqual(self.w.final.toPlainText(),'canvas manual')
        self.assertIn('red dress',build_prompt(self.w.state)); self.assertIn('white shirt',build_prompt(self.w.state))
        dialog=SettingsDialog(self.w); dialog.separate_selections.setChecked(True); dialog.save()
        self.assertEqual(self.w.final.toPlainText(),'list manual')
        self.assertEqual(self.w.state['selections'],picks); self.assertEqual(self.w.state['uses'],uses)
        self.assertEqual(build_prompt(self.w.state),list_prompt)
        self.w.enter_canvas(); self.assertEqual(build_prompt(self.w.state),expected)
        self.assertEqual(self.w.final.toPlainText(),'canvas manual')
        self.w.persist(); stored=self.w.store.load(); self.assertTrue(stored['settings']['separate_selections'])
        self.assertEqual(stored['selection_view'],'canvas'); self.assertEqual(stored['uses'],uses)

    def test_copy_run_snapshot_and_clear_use_current_view_paragraphs(self):
        list_prompt=self.fill_both(); expected=self.w.final.toPlainText(); self.assertIn('\n\n',expected)
        self.w.copy_final(); self.assertEqual(APP.clipboard().text(),expected)
        client=self.w.comfy; client.ready=True
        with patch.object(client,'request') as request:
            client.run(2); payload=request.call_args.args[1]
        self.assertEqual(payload['snapshot']['final_prompt'],expected)
        self.assertEqual(payload['snapshot']['state']['selections'],{})
        self.w.canvas.add_tag('smile'); self.assertEqual(payload['snapshot']['final_prompt'],expected)
        client.ready=False; client.run_id=''
        self.w.final.setPlainText('canvas draft'); self.w.state['settings']['confirm_clear_draft']=False; self.w.regenerate()
        self.assertIn('\n\n',self.w.final.toPlainText())
        self.w.leave_canvas(); self.assertEqual(self.w.final.toPlainText(),list_prompt)
        snap=client.snapshot(); self.assertEqual(snap['state']['uses'],{}); self.assertEqual(snap['final_prompt'],list_prompt)

    def test_reorder_then_view_switch_preserves_hidden_order(self):
        self.fill_both(); keys=list(self.w.state['uses']); mid=next(iter(self.w.state['selections']))
        self.w.reorder_builder(('use',keys[0]),('use',keys[1]),True)
        self.assertEqual(output_groups(self.w.state),list(reversed(keys)))
        self.w.leave_canvas(); self.assertEqual(output_groups(self.w.state),[mid,'__temporary_group__'])
        self.w.enter_canvas(); self.assertEqual(output_groups(self.w.state),list(reversed(keys)))

    def test_canvas_fills_content_edges_with_compact_header_and_floating_status(self):
        self.w.enter_canvas(); self.w.notice('已加入子元素；可在目前組合調整輸出順序。')
        view=self.w.canvas.view; status=self.w.canvas_status
        for width,height,font_size in ((1440,900,14),(960,650,14),(960,650,18)):
            self.w.state['settings']['ui_size']=font_size; self.w.apply_theme()
            self.w.resize(width,height); APP.processEvents()
            origin=view.viewport().mapTo(self.w.host_shell,QPoint())
            self.assertEqual(origin.x(),0)
            self.assertEqual(view.viewport().width(),self.w.host_shell.width())
            self.assertEqual(origin.y()+view.viewport().height(),self.w.host_shell.height())
            self.assertLessEqual(self.w.canvas_header.height(),100)
            for entry in self.w.canvas_navigation.values():
                self.assertTrue(entry.isVisible()); self.assertGreaterEqual(entry.width(),entry.minimumSizeHint().width())
            self.assertTrue(status.isVisible()); self.assertEqual(status.parent(),view.viewport())
            self.assertTrue(view.viewport().rect().contains(status.geometry()))
            self.assertTrue(status.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            self.assertGreater(self.w.canvas_connection_status.width(),50)
        self.w.notice(''); self.assertFalse(status.isVisible())
        self.w.leave_canvas(); self.assertTrue(self.w.library_panel.isVisible())
        self.assertIs(self.w.surface_stack.currentWidget(),self.w.list_shell)
        self.assertGreater(self.w.list_shell.layout().contentsMargins().left(),0)

    def test_drag_pan_resize_and_zoom_repaint_visible_background(self):
        self.fill_both(); view=self.w.canvas.view; observer=PaintObserver(view.viewport())
        view.viewport().installEventFilter(observer); QTest.qWait(500); observer.regions.clear()
        for factor in (1.0,.73,1.25):
            self.w.canvas.fit(); view.scale(factor,factor); APP.processEvents()
            card=next(iter(self.w.canvas.cards.values()))
            start=view.mapFromScene(card.mapToScene(QPointF(35,45)))
            QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=start)
            for offset in (QPoint(24,16),QPoint(65,38),QPoint(-28,54)):
                QTest.mouseMove(view.viewport(),start+offset); QTest.qWait(20)
            QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=start+QPoint(-28,54)); QTest.qWait(25)
            QTest.mousePress(view.viewport(),Qt.MouseButton.MiddleButton,pos=QPoint(30,30))
            QTest.mouseMove(view.viewport(),QPoint(110,80)); QTest.mouseRelease(view.viewport(),Qt.MouseButton.MiddleButton,pos=QPoint(110,80)); QTest.qWait(25)
            self.w.canvas.resize_card('__text_output__',640,710); QTest.qWait(25)
        self.assertGreater(len(observer.regions),8)
        for rect in observer.regions: self.assertEqual(rect,view.viewport().rect())
        # Background painting must replace arbitrary previous pixels opaquely.
        image=QImage(240,180,QImage.Format.Format_ARGB32); image.fill(QColor('#ff00ff'))
        painter=QPainter(image); view.drawBackground(painter,image.rect()); painter.end()
        for x,y in ((15,15),(135,91),(239,179)): self.assertEqual(image.pixelColor(x,y),QColor('#17191e'))
        view.viewport().removeEventFilter(observer)

if __name__=='__main__': unittest.main()
