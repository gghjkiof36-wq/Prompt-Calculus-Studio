import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QDialog
from prompt_studio import composition as c
from prompt_studio.core import initial_state, validate_state, build_prompt, apply_workspace, Storage, output_groups, reorder_output
from prompt_studio.snapshots import make_snapshot, restore_snapshot, portable_state
from prompt_studio.metadata_view import readable_metadata
from prompt_studio.text_canvas import CanvasPalette
from prompt_studio.window import Window

APP=QApplication.instance() or QApplication([])

def usage(state, asset=None, prompt='raw tag'):
    state['settings']['separate_selections']=False
    root=c.clone_node(c.prototype(asset)) if asset else c.node(prompt,prompt)
    if asset: root.update(source_id=asset['id'],source_module=asset['module'])
    root['grouped']=True; state['version']=3
    ident='use-'+root['id']; state.setdefault('uses',{})[ident]=root
    return ident,root

def legacy():
    s=initial_state(); root=c.node('舊女孩','1girl',children=[c.node('眼睛','red eyes')],grouped=True)
    m=dict(id='old-canvas',name='Canvas 素材',mode='multiple',canvas_library=True); s['modules'].append(m)
    s['items'].append(dict(id='old-use',name='舊女孩',module=m['id'],prompt='1girl',aliases=[],notes='',composition=copy.deepcopy(root)))
    s.update(version=2,selections={m['id']:['old-use']},instances={'old-use':root},weights={'old-use':13},current_module=m['id'])
    s['workspaces'][0].update(fixed=[m['id']],picks=copy.deepcopy(s['selections']),instances=copy.deepcopy(s['instances']),weights=copy.deepcopy(s['weights']))
    s['draft']='keep manual'; s['settings']['separate_selections']=False; return s

class UsageTests(unittest.TestCase):
    def test_duplicate_sources_snapshot_and_metadata_are_self_contained(self):
        s=initial_state(); original=copy.deepcopy(s['items']); a,first=usage(s,s['items'][0]); b,second=usage(s,s['items'][0])
        first['children'].append(c.node('微笑','smile')); second['weight']=13
        s['output_order']=[b,a]; expected='(1girl, red dress:1.3), (1girl, red dress, smile:1.0)'
        self.assertEqual(build_prompt(s),expected); self.assertEqual(s['items'],original)
        snap=make_snapshot(s); self.assertEqual(snap['schema_version'],3)
        restored=restore_snapshot(initial_state(),json.loads(json.dumps(snap)))
        self.assertEqual(build_prompt(restored),expected); self.assertEqual(len(restored['uses']),2)
        for root in restored['uses'].values(): self.assertIn(root['source_id'],{i['id'] for i in restored['items']})
        record=dict(metadata=dict(raw=dict(prompt_studio=dict(schema_version=1,bindings=[dict(snapshot=snap)]))))
        self.assertIn(expected,readable_metadata(record))
        s['items']=[]; validate_state(s); self.assertEqual(build_prompt(s),expected)
        self.assertEqual(build_prompt(restore_snapshot(initial_state(),make_snapshot(s))),expected)

    def test_fixed_uses_restore_only_fixed_source_categories_and_keep_draft(self):
        s=initial_state(); a,first=usage(s,s['items'][0]); b,other=usage(s,s['items'][2]); raw,_=usage(s)
        w=s['workspaces'][0]; w.update(fixed=[s['items'][0]['module']],uses={a:copy.deepcopy(first)})
        first['prompt']='changed'; other['prompt']='keep moving'; s['draft']='keep manual'; s['output_order']=[a,b,raw]
        apply_workspace(s,w['id']); validate_state(s)
        self.assertEqual(s['uses'][a]['prompt'],'1girl, red dress'); self.assertEqual(s['uses'][b]['prompt'],'keep moving')
        self.assertIn(raw,s['uses']); self.assertEqual(s['draft'],'keep manual')
        self.assertEqual(make_snapshot(s)['schema_version'],3)

    def test_migration_keeps_prompt_archive_saved_workspace_and_positions(self):
        s=legacy(); original=copy.deepcopy(s); s['text_positions']={'old-use':[12,34]}
        migrated=c.migrate_canvas_library(s)
        self.assertEqual(build_prompt(migrated),build_prompt(s)); self.assertEqual(migrated['draft'],'keep manual')
        self.assertFalse(any(m.get('canvas_library') for m in migrated['modules']))
        self.assertEqual(migrated['legacy_canvas_assets'][0],original['items'][-1])
        self.assertEqual(migrated['text_positions'],s['text_positions'])
        migrated['uses']['old-use']['prompt']='changed'; apply_workspace(migrated,migrated['workspace'])
        self.assertEqual(build_prompt(migrated),build_prompt(s)); validate_state(migrated)
        self.assertEqual(c.migrate_canvas_library(migrated),migrated)
        self.assertEqual(build_prompt(restore_snapshot(initial_state(),make_snapshot(migrated))),build_prompt(s))

    def test_weights_overlay_and_invalid_use_and_size_data(self):
        s=initial_state(); ident,root=usage(s); root['overlays']=[c.node('替換','replacement')]; root['weight']=12
        self.assertEqual(build_prompt(s),'(replacement:1.2)')
        root['grouped']=False; self.assertEqual(build_prompt(s),'(replacement:1.2)')
        for mutate in (lambda v:v.update(version=2),lambda v:v.update(uses=[]),lambda v:v.update(text_sizes={'bad':[float('nan'),20]}),lambda v:v['uses'].update({v['items'][0]['id']:copy.deepcopy(root)})):
            bad=copy.deepcopy(s); mutate(bad)
            with self.assertRaises(ValueError): validate_state(bad)

class CanvasRefinementUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'qa'); self.w=Window(Path(self.tmp.name)/'data')
        self.w.state['settings'].update(online=False,material='solid'); self.w.apply_theme()
        self.w.state['settings']['separate_selections']=False
        self.w.resize(1440,1000); self.w.show(); self.w.enter_canvas(); APP.processEvents()
        self.canvas=self.w.canvas

    def tearDown(self):
        self.w.close(); APP.processEvents(); self.tmp.cleanup()

    def add(self):
        self.canvas.insert_asset(self.w.state['items'][0]); APP.processEvents()
        return list(self.w.state['uses'])[-1]

    def point(self,card,point): return self.canvas.view.mapFromScene(card.mapToScene(point))

    def click(self,card,rect):
        QTest.mouseClick(self.canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point(card,rect.center())); QTest.qWait(25)

    def test_removed_buttons_and_source_binding_without_library_writes(self):
        before=copy.deepcopy((self.w.state['modules'],self.w.state['items'],self.w.state['selections']))
        a=self.add(); b=self.add(); self.assertNotEqual(a,b)
        self.assertEqual(before,(self.w.state['modules'],self.w.state['items'],self.w.state['selections']))
        self.assertEqual(self.w.state['uses'][a]['source_id'],self.w.state['items'][0]['id'])
        palette=CanvasPalette(self.canvas,None)
        names=[button.text() for parent in (self.w,palette) for button in parent.findChildren(QPushButton)]
        self.assertNotIn('其他操作',names); self.assertNotIn('複製 Prompt',names); palette.reject()
        self.w.leave_canvas(); self.assertFalse(any(self.w.library.item(i).data(Qt.ItemDataRole.UserRole) in (a,b) for i in range(self.w.library.count())))
        self.assertEqual(len(self.w.state['items']),len(before[1]))

    def test_weight_buttons_nested_weights_undo_and_manual_draft(self):
        ident=self.add(); self.canvas.fit(); APP.processEvents()
        self.click(self.canvas.cards[ident],self.canvas.cards[ident].plus_rect)
        self.assertEqual(self.w.state['uses'][ident]['weight'],11)
        self.assertEqual(build_prompt(self.w.state),'(1girl, red dress:1.1)')
        self.canvas.undo(); self.assertEqual(self.w.state['uses'][ident]['weight'],10)
        self.canvas.enter(ident); self.canvas.add_tag('smile'); self.canvas.home()
        child=self.w.state['uses'][ident]['children'][0]; key=ident+':'+child['id']
        self.canvas.change_weight(key,value=14)
        self.assertEqual(build_prompt(self.w.state),'(1girl, red dress, (smile:1.4):1.0)')
        self.w.final.setPlainText('manual'); self.canvas.change_weight(ident,value=12)
        self.assertEqual(self.w.final.toPlainText(),'manual'); self.assertIn(':1.2)',build_prompt(self.w.state))
        self.canvas.change_weight(key,value=1000); self.canvas.change_weight(key,delta=1)
        self.assertEqual(self.w.state['uses'][ident]['children'][0]['weight'],1000)

    def test_wheel_over_cards_and_actual_prompt_editor_always_zooms(self):
        ident=self.add(); self.canvas.enter(ident); self.canvas.add_tag('smile'); self.canvas.home()
        view=self.canvas.view; self.w.final.setPlainText('\n'.join(str(i) for i in range(100)))
        child=self.canvas.cards[ident].parts[0]
        editor_point=self.w.final.mapTo(self.w.builder_panel,self.w.final.rect().center())
        points=[QPointF(18,18),view.mapFromScene(self.canvas.cards[ident].sceneBoundingRect().center()),
                view.mapFromScene(child.sceneBoundingRect().center()),view.mapFromScene(self.canvas.output.proxy.mapToScene(QPointF(editor_point)))]
        for point in points:
            point=QPointF(point); scale=view.transform().m11(); scroll=self.w.final.verticalScrollBar().value()
            event=QWheelEvent(point,QPointF(view.viewport().mapToGlobal(point.toPoint())),QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
            QApplication.sendEvent(view.viewport(),event); APP.processEvents()
            self.assertGreater(view.transform().m11(),scale); self.assertEqual(self.w.final.verticalScrollBar().value(),scroll)

    def test_resize_drag_nested_parent_and_output_persist_across_switch_and_undo(self):
        ident=self.add(); self.canvas.enter(ident); self.canvas.add_tag('smile'); self.canvas.home(); self.canvas.fit()
        view=self.canvas.view; child=self.canvas.cards[ident].parts[0]; key=child.key; old_width=child.width
        start=self.point(child,child.resize_rect().center()); end=start+QPoint(70,35)
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=start); QTest.mouseMove(view.viewport(),end)
        QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=end); QTest.qWait(30)
        resized=self.canvas.cards[key]; self.assertGreater(resized.width,old_width)
        self.assertTrue(self.canvas.cards[ident].boundingRect().contains(resized.mapRectToParent(resized.boundingRect())))
        self.assertIn(key,self.w.state['text_sizes']); self.canvas.undo(); self.assertEqual(self.canvas.cards[key].width,old_width)
        self.canvas.redo(); self.canvas.resize_card('__text_output__',800,900)
        self.assertGreaterEqual(self.canvas.output.width,800); self.assertGreaterEqual(self.canvas.output.height,900)
        self.assertTrue(self.canvas.output.boundingRect().contains(self.canvas.output.proxy.geometry()))
        self.w.leave_canvas(); self.w.enter_canvas(); self.assertGreaterEqual(self.canvas.output.width,800)
        self.w.persist(); self.assertEqual(self.w.store.load()['text_sizes'],self.w.state['text_sizes'])

    def test_add_child_hover_press_feedback_and_flat_output_order(self):
        a=self.add(); b=self.add(); self.canvas.fit(); APP.processEvents(); card=self.canvas.cards[a]
        point=self.point(card,card.add_rect.center())
        QTest.mouseMove(self.canvas.view.viewport(),point); APP.processEvents(); self.assertEqual(card.hot,'add')
        QTest.mousePress(self.canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=point); self.assertEqual(card.pressed,'add')
        def add(palette):
            self.assertEqual(palette.canvas.current()['name'],self.w.state['uses'][a]['name'])
            palette.query.setPlainText('smile'); palette.submit_tag(); self.assertIn('已加入',palette.feedback.text())
            return QDialog.DialogCode.Accepted
        with patch.object(CanvasPalette,'exec',add):
            QTest.mouseRelease(self.canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=point); QTest.qWait(25)
        self.assertEqual(self.w.state['uses'][a]['children'][0]['prompt'],'smile')
        self.assertEqual(self.w.state['uses'][b]['children'],[]); self.assertIn('已加入',self.w.canvas_status.text())
        self.assertEqual(self.w.selected.topLevelItemCount(),2)
        self.w.selected.setCurrentItem(self.w.selected.topLevelItem(0)); QTest.keyClick(self.w.selected,Qt.Key.Key_Down,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(output_groups(self.w.state),[b,a]); self.assertTrue(build_prompt(self.w.state).endswith('smile:1.0)'))

    def test_legacy_database_migration_is_backed_up_and_reopens(self):
        old=legacy(); self.w.store.save(old); self.w.state=self.w.store.load_current()
        self.w.current_module=self.w.state.get('current_module'); self.w.refresh_builder(); self.w.persist()
        backups=list(self.w.store.directory.glob('before-import-*.sqlite3')); self.assertTrue(backups)
        self.assertEqual(build_prompt(self.w.state),'(1girl, red eyes:1.3)')
        self.assertEqual(self.w.store.load()['version'],3); self.assertEqual(self.w.state['draft'],'keep manual')
        self.assertNotEqual(self.w.current_module,'old-canvas')

if __name__=='__main__': unittest.main()
