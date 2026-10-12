"""Refresh work stays bounded without losing styles or edit-history semantics."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtCore import QObject, QEvent
from PySide6.QtTest import QTest
from prompt_calculus_studio import content_history as history
from prompt_calculus_studio.changes import layout_only
from prompt_calculus_studio.composition import node
from prompt_calculus_studio.core import initial_state
from prompt_calculus_studio.state_loading import prepare_state
from stage_fixture import StageFixture


class HistoryComparisonTests(unittest.TestCase):
    def setUp(self):
        self.state=prepare_state(initial_state(),multi=True)
        self.state['uses']['root']=node('Root','original',children=[node('Child','smile')])
        self.cid=next(iter(self.state['multi_output']['canvases']))
        self.out=self.state['multi_output']['current_output']
        self.state['multi_output']['canvases'][self.cid]['members']=['root']

    def test_layout_comparison_preserves_inputs_and_recognizes_content(self):
        before=copy.deepcopy(self.state);after=copy.deepcopy(before)
        after['text_positions']['root']=[15,20];after.setdefault('text_sizes',{})['root']=[400,500]
        canvas=after['multi_output']['canvases'][self.cid]
        canvas.update(position=[30,50],display_size=[500,600],edit_view=[.7,0,0],collapsed=True)
        output=after['multi_output']['outputs'][self.out]
        output.update(panel_ratio=.4,preview_collapsed=True,collapsed=True)
        after['canvas_functions'].update(preview_display={'size':[50,60]},images={'test':{'collapsed':True}})
        before['canvas_functions']['images']={'test':{}}
        originals=copy.deepcopy((before,after))
        self.assertTrue(layout_only(before,after))
        self.assertEqual((before,after),originals)
        for change in (
            lambda s:s['uses']['root']['children'][0].update(prompt='changed'),
            lambda s:s['uses']['root'].update(enabled=False),
            lambda s:s['multi_output']['outputs'][self.out].update(draft=''),
            lambda s:s['multi_output']['outputs'][self.out].update(future={'collapsed':True}),
            lambda s:s['canvas_functions']['images']['test'].update(source={'path':'new.png'}),
        ):
            value=copy.deepcopy(after);change(value)
            saved=copy.deepcopy(value)
            self.assertFalse(layout_only(before,value))
            self.assertEqual(value,saved)

    def test_runtime_only_changes_do_not_create_edits_but_geometry_does(self):
        before=copy.deepcopy(self.state);after=copy.deepcopy(before)
        after['items'][0]['last_used']=1234
        after['multi_output']['outputs'][self.out].update(status='running',progress=.5,results=['temporary'])
        originals=copy.deepcopy((before,after))
        self.assertFalse(history.record(after,before,after))
        self.assertEqual((before,after),originals)
        after['text_positions']['root']=[10,20]
        self.assertTrue(history.record(after,before,after,'Move'))
        entry=after['content_history']['entries'][-1]
        self.assertFalse(entry['content_changed']);self.assertEqual(entry['changes'],[])
        self.assertIn(self.cid,entry['canvas_ids'])
        self.assertEqual(before,originals[0])

    def test_changed_snapshots_remain_detached_and_empty_drafts_are_versions(self):
        before=copy.deepcopy(self.state);after=copy.deepcopy(before)
        after['uses']['root']['children'][0]['prompt']='edited'
        after['multi_output']['outputs'][self.out]['draft']=''
        self.assertTrue(history.record(after,before,after,'Edit'))
        changes=after['content_history']['entries'][-1]['changes']
        canvas=next(v for v in changes if v['kind']=='canvas')
        draft=next(v for v in changes if v['kind']=='output')
        self.assertEqual(draft['before']['draft'],None);self.assertEqual(draft['after']['draft'],'')
        before['uses']['root']['children'][0]['prompt']='old mutated'
        after['uses']['root']['children'][0]['prompt']='new mutated'
        self.assertEqual(canvas['before']['children'][0]['prompt'],'smile')
        self.assertEqual(canvas['after']['children'][0]['prompt'],'edited')
        history.validate(after)

    def test_unchanged_materials_do_not_rebuild_content_projections(self):
        before=copy.deepcopy(self.state)
        template=before['items'][0]
        before['items']=[dict(copy.deepcopy(template),id=f'material-{i}') for i in range(300)]
        after=copy.deepcopy(before);after['uses']['root']['weight']=15
        with patch.object(history,'_version_content',wraps=history._version_content) as project:
            self.assertTrue(history.record(after,before,after))
        self.assertEqual(project.call_count,2)
        self.assertEqual(len(after['content_history']['entries'][-1]['changes']),1)


class StyleEvents(QObject):
    def __init__(self):super().__init__();self.count=0
    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.StyleChange:self.count+=1
        return False


class CanvasRefreshStyleTests(StageFixture):
    def panels(self):
        return [card.panel for card in [*self.c.outputs.values(),*self.c.clips.values(),
            *self.c.flow_cards.values(),*self.c.functions.cards.values(),self.c.preview_card] if card is not None]

    def test_refresh_does_not_repolish_unchanged_panels(self):
        self.c.functions.add_image();QTest.qWait(30)
        counter=StyleEvents()
        for panel in self.panels():panel.installEventFilter(counter)
        graph=copy.deepcopy(self.c.data())
        self.c.refresh();QTest.qWait(20)
        self.assertEqual(counter.count,0)
        self.assertEqual(self.c.data(),graph)
        self.assertTrue(all(p.styleSheet()==self.w.styleSheet() for p in self.panels()))

    def test_real_theme_and_font_changes_still_reach_every_panel(self):
        self.c.functions.add_image();QTest.qWait(20)
        self.w.state['settings'].update(visual_palette='paper',ui_size=16)
        self.w.apply_theme(preserve_layout=True);QTest.qWait(30)
        self.assertTrue(all(p.styleSheet()==self.w.styleSheet() for p in self.panels()))
        self.assertEqual(self.c.outputs[self.out].panel.editor.toPlainText(),'white shirt')
        self.w.set_interface_mode('list');self.w.set_interface_mode('canvas');QTest.qWait(20)
        self.assertTrue(all(p.styleSheet()==self.w.styleSheet() for p in self.panels()))
        self.assertFalse(self.executor.submissions)
