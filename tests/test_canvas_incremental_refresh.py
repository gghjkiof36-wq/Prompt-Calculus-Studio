"""S4 contracts through the real EmbeddedHost, with isolated portable files."""
import copy
from contextlib import contextmanager
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog
import shiboken6

import test_workspace_shell as fixture
from prompt_calculus_studio import composition as comp, multi_output as model, workspace_scene
from prompt_calculus_studio.canvas_items import NodeCard
from prompt_calculus_studio.canvas_refresh import difference
from prompt_calculus_studio.changes import layout_only


class IncrementalCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.WorkspaceShellTests.setUpClass()

    def setUp(self):
        self.f=fixture.WorkspaceShellTests();self.f.setUp()
        self.tab=self.f.w.open_path(self.f.root/'Street.pcs-workflow');self.f.events()
        self.w=self.tab.host;self.c=self.w.canvas
        self.cid=next(iter(self.c.data()['canvases']));self.out=self.c.data()['current_output']
        def populate(state):
            state['uses']={}
            for canvas in state['multi_output']['canvases'].values():canvas['members']=[]
            for i in range(3):
                key='root'+str(i)
                state['uses'][key]=comp.node(key,'text '+str(i),children=[comp.node('child','nested')] if i==0 else [])
                model.assign(state,key,self.cid)
                state.setdefault('text_positions',{})[key]=[40+i*380,180]
        self.assertTrue(self.c.commit(populate))
        self.f.events();self.c.view.setFocus();self.c.view.scene().clearFocus()
        self.c.undo_stack=[];self.c.redo_stack=[]
        self.w.changes.stop();self.w.save_timer.stop()

    def tearDown(self):self.f.tearDown()

    @contextmanager
    def builds(self):
        calls=[];original=NodeCard.__init__
        def create(card,canvas,key,*args,**kwargs):
            calls.append((canvas,key));original(card,canvas,key,*args,**kwargs)
        with patch.object(NodeCard,'__init__',create):yield calls

    def edit(self,text='edited'):
        return self.c.commit(lambda s:s['uses']['root0']['children'][0].update(prompt=text),label='編輯文字')

    def test_confirmed_dialog_rebuilds_only_one_root_subtree(self):
        from prompt_calculus_studio.text_canvas import NodeDialog
        old=dict(self.c.cards)
        def accept(dialog):dialog.prompt.setPlainText('confirmed text');return QDialog.DialogCode.Accepted
        with self.builds() as calls,patch.object(NodeDialog,'exec',accept):
            self.assertTrue(self.c.edit('root0'));self.f.events()
        self.assertEqual({key for canvas,key in calls},{'root0',next(k for k in old if ':' in k)})
        for key in ('root1','root2'):self.assertIs(old[key],self.c.cards[key])
        self.assertFalse(shiboken6.isValid(old['root0']))

    def test_child_edit_preserves_others_selection_and_rebuilds_valid_edges(self):
        old=dict(self.c.cards);ports=list(self.c.ports.values())
        self.c.cards['root1'].setSelected(True);self.c.cards['root2'].setSelected(True)
        with self.builds() as calls:self.assertTrue(self.edit());self.f.events()
        self.assertEqual(len(calls),2)
        self.assertEqual({c.key for c in self.c.view.scene().selectedItems()},{'root1','root2'})
        for key in ('root1','root2'):self.assertIs(old[key],self.c.cards[key])
        self.f.app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.assertTrue(all(not shiboken6.isValid(p) for p in ports))
        for line in self.c.lines.values():
            for output in (False,True):
                port=self.c.line_port(line.value,output)
                self.assertIsNotNone(port);self.assertIs(port.scene(),self.c.view.scene())
                self.assertTrue(shiboken6.isValid(port.parentItem()))

    def test_move_resize_and_weight_preserve_identity_and_prompt_cache(self):
        old=dict(self.c.cards)
        before=self.c.history_state()
        with self.builds() as calls,patch.object(model,'compile_output',wraps=model.compile_output) as compile:
            card=self.c.cards['root1']
            self.c.move_cards({'root1':[card.x()+17,card.y()+19]})
            self.c.resize_card('root1',410,240);self.f.events()
            self.assertTrue(layout_only(before,self.c.history_state()))
            self.c.outputs[self.out].update_text()
            self.assertEqual(compile.call_count,0)
            self.c.change_weight('root1',1);self.f.events()
            self.assertGreater(compile.call_count,0)
        self.assertEqual(calls,[])
        for key in old:self.assertIs(old[key],self.c.cards[key])
        self.assertEqual(self.c.outputs[self.out].panel.preview.text(),model.compile_output(self.w.state,self.out)['final_prompt'])

    def test_retained_subtree_matches_full_path_transient_selection(self):
        child=next(card for key,card in self.c.cards.items() if ':' in key)
        child.setSelected(True);child.hot='resize'
        self.c.cards['root0'].setSelected(True)
        self.c.commit(lambda s:s['uses']['root1'].update(prompt='changed elsewhere'));self.f.events()
        self.assertIs(self.c.cards[child.key],child)
        self.assertFalse(child.isSelected());self.assertIsNone(child.hot)
        selected={c.key for c in self.c.view.scene().selectedItems()}
        self.c.refresh();self.f.events()
        self.assertEqual(selected,{c.key for c in self.c.view.scene().selectedItems()})

    def test_active_scene_geometry_only_but_inactive_scene_content_is_not(self):
        before=self.c.history_state();after=copy.deepcopy(before)
        after['text_positions']['root1']=[9,12]
        after['workspace_scenes']['items'][after['workspace']]['text_positions']['root1']=[9,12]
        self.assertTrue(layout_only(before,after));self.assertEqual(difference(before,after).mode,'geometry')
        after['workspace_scenes']['items']['inactive']={'text_positions':{'x':[2,3]}}
        self.assertFalse(layout_only(before,after));self.assertEqual(difference(before,after).mode,'full')

    def test_notifications_coalesce_without_save_or_second_commit_render(self):
        with patch.object(self.c,'refresh',wraps=self.c.refresh) as refresh,patch.object(self.w.store,'save',wraps=self.w.store.save) as save:
            self.assertTrue(self.edit());self.f.events()
            self.assertEqual(refresh.call_count,1);self.assertEqual(save.call_count,0)
            refresh.reset_mock()
            for text in ('first','last'):
                self.w.update_document(lambda s,t=text:s['uses']['root1'].update(prompt=t),fields=('uses',),reason='external')
            self.assertEqual(refresh.call_count,0)
            self.f.events();self.assertEqual(refresh.call_count,1);self.assertEqual(save.call_count,0)
            self.w.persist();refresh.reset_mock();save.reset_mock();self.f.events()
            self.assertEqual(refresh.call_count,0);self.assertEqual(save.call_count,0)

    def test_library_navigation_keeps_its_zero_canvas_render_contract(self):
        from prompt_calculus_studio.material_navigation import add_folder
        with patch.object(self.c,'refresh',wraps=self.c.refresh) as refresh,self.builds() as builds:
            self.c.commit(lambda s:add_folder(s,'New folder'));self.f.events()
        self.assertEqual(refresh.call_count,0);self.assertEqual(builds,[])

    def test_bounds_converge_and_refresh_writers_are_read_only(self):
        self.c.cards['root1'].setPos(-9000,-9000)
        notices=[];self.w.documentChanged.connect(lambda *args:notices.append(args))
        with patch.object(self.w.store,'save',wraps=self.w.store.save) as save:
            self.assertTrue(self.c.ensure_bounds());state=copy.deepcopy(self.w.state)
            self.assertEqual(self.c.ensure_bounds(),frozenset());self.assertEqual(state,self.w.state)
            self.assertEqual(len(notices),1);self.assertEqual(notices[0][1: ],('canvas-bounds','derived'))
            self.assertFalse(self.c.refresh_scheduler.timer.isActive());self.assertEqual(save.call_count,0)
        self.c.update_output();self.w.refresh_builder()
        self.assertEqual(self.w.state,state)
        # The old writer serialized Qt floats. Do not silently keep int input
        # merely because Python considers 100 == 100.0.
        container=self.c.containers[self.cid]
        point=[container.x()+25,container.y()+109]
        self.c.cards['root1'].setPos(*point)
        self.w.state['text_positions']['root1']=[int(v) for v in point]
        self.c.ensure_bounds()
        self.assertTrue(all(type(v) is float for v in self.w.state['text_positions']['root1']))
        self.assertEqual(self.c.ensure_bounds(),frozenset())

    def test_detached_normalization_matches_old_writers_with_empty_drafts(self):
        from prompt_calculus_studio.canvas_normalization import normalize
        for view in ('list','canvas'):
            for draft in ('',None,'manual'):
                with self.subTest(view=view,draft=draft):
                    source=copy.deepcopy(self.w.state);source.update(selection_view=view,draft=draft,draft_base='original')
                    source.setdefault('instances',{})['orphan']={};source['workspaces'][0]['instances']={'orphan':{}}
                    source.pop('prompt_layout',None)
                    source['multi_output']['outputs'][self.out]['list_state']['category_positions']={'group':[13,17]}
                    old=copy.deepcopy(source);new=copy.deepcopy(source)
                    comp.prune_instances(old)
                    if old.get('uses'):old.setdefault('prompt_layout','paragraphs')
                    model.capture_current(old)
                    normalize(new);self.assertEqual(old,new)
                    normalize(new);self.assertEqual(old,new)
                    self.assertNotEqual(source,new)
                    self.assertIsNot(old['multi_output'],new['multi_output'])
                    self.w.state=copy.deepcopy(source);self.w.normalize_canvas_document()
                    self.assertEqual(old,self.w.state)

    def test_all_undo_redo_and_workspace_switch_are_full(self):
        self.edit();self.f.events()
        for action in (self.c.undo,self.c.redo,lambda:self.w.adopt_document(self.w.state,reason='probe')):
            old=dict(self.c.cards)
            with self.builds() as calls:action();self.f.events()
            self.assertEqual(len(calls),len(old))
            self.assertTrue(all(old[k] is not self.c.cards[k] for k in old))
        target=[]
        self.c.commit(lambda s:target.append(workspace_scene.create(s,'Other',duplicate=True)))
        old=dict(self.c.cards)
        with self.builds() as calls:self.w.change_workspace(target[0]);self.f.events()
        self.assertEqual(len(calls),len(old))

    def test_topology_and_unknown_changes_use_full_fallback(self):
        def add(s):s['uses']['new']=comp.node('New','new');model.assign(s,'new',self.cid)
        for change in (add,lambda s:s['multi_output']['canvases'][self.cid]['members'].reverse(),
                       lambda s:s['uses']['root0'].update(collapsed=True)):
            old=dict(self.c.cards)
            with self.builds() as calls:self.c.commit(change);self.f.events()
            self.assertTrue(all(old[k] is not self.c.cards[k] for k in old if k in self.c.cards))
        with self.builds() as calls:
            self.w.update_document(lambda s:s.update(future_scene={'x':1}),fields=('future_scene',),reason='future')
            self.f.events()
        self.assertEqual(len(calls),len(self.c.cards))

    def test_appearance_invalidates_all_and_hidden_view_is_not_acknowledged(self):
        old=dict(self.c.cards)
        with self.builds() as calls:
            self.w.state['settings']['visual_palette']='paper'
            self.w.apply_theme(preserve_layout=True);self.f.events()
        self.assertEqual(len(calls),len(old))
        previous=self.c._visible_scene_state
        self.f.w.open_path(self.f.root/'Night.pcs-workflow',new_tab=True);self.f.events()
        self.assertFalse(self.c.isVisible())
        self.c.move_cards({'root1':[777,555]});self.f.events()
        self.assertTrue(self.c._scene_refresh_pending)
        self.assertIs(self.c._visible_scene_state,previous)
        self.f.focus(self.tab)
        self.assertFalse(self.c._scene_refresh_pending)
        self.assertEqual([self.c.cards['root1'].x(),self.c.cards['root1'].y()],self.w.state['text_positions']['root1'])

    def test_two_hosts_each_present_once_and_cameras_stay_local(self):
        second=self.f.w.duplicate_view(self.tab,True);self.f.events();other=second.host.canvas
        old=dict(other.cards);self.c.view.centerOn(111,222);other.view.centerOn(888,999)
        self.w.remember_canvas_view();second.host.remember_canvas_view()
        cameras=(self.w.local_canvas_view,second.host.local_canvas_view)
        with patch.object(self.c,'refresh',wraps=self.c.refresh) as first,patch.object(other,'refresh',wraps=other.refresh) as sibling:
            self.edit();self.f.events();self.f.events()
            self.assertEqual(first.call_count,1);self.assertEqual(sibling.call_count,1)
        self.assertTrue(all(old[k] is not other.cards[k] for k in old))
        self.assertEqual(cameras,(self.w.local_canvas_view,second.host.local_canvas_view))
        with patch.object(other.refresh_scheduler,'enabled',return_value=False),patch.object(other,'refresh',wraps=other.refresh) as legacy:
            self.w.document.refresh_views()
            self.assertEqual(legacy.call_count,1)

    def test_external_edit_during_editor_or_connection_gesture_stays_full(self):
        page=self.c.outputs[self.out].panel.open_page();editor=page.content.editor
        editor.setPlainText('abcdef');self.f.events();editor.setFocus()
        cursor=editor.textCursor();cursor.setPosition(2);cursor.setPosition(5,QTextCursor.MoveMode.KeepAnchor);editor.setTextCursor(cursor)
        with self.builds() as calls:
            from prompt_calculus_studio.drafts import edit
            self.w.update_document(lambda s:edit(s,'external',self.out),fields=('draft','draft_base','multi_output','view_drafts'),reason='external')
            self.f.events()
        self.assertEqual(len(calls),len(self.c.cards));self.assertEqual(editor.toPlainText(),'external')
        self.assertEqual(editor.textCursor().position(),0)
        port=next(p for p in self.c.ports.values() if p.output)
        self.c.connection_gesture.begin(port,port.scenePos()+QPointF(20,10))
        with self.builds() as calls:self.edit();self.f.events()
        self.assertEqual(len(calls),len(self.c.cards));self.assertIsNone(self.c.connection_gesture.anchor)

    def test_save_close_reopen_and_undo_redo(self):
        before=copy.deepcopy(self.w.state['uses']);self.edit();after=copy.deepcopy(self.w.state['uses'])
        self.assertTrue(self.tab.document.save())
        self.assertTrue(self.f.w.close_tab(self.f.w.index_of(self.tab.key),decision='discard',make_home=False))
        reopened=self.f.w.open_path(self.f.root/'Street.pcs-workflow');self.f.events()
        self.assertEqual(reopened.host.state['uses'],after)
        reopened.host.canvas.undo();self.f.events();self.assertEqual(reopened.host.state['uses'],before)
        reopened.host.canvas.redo();self.f.events();self.assertEqual(reopened.host.state['uses'],after)
