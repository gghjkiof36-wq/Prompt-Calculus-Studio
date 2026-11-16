"""B2-SRC v7 reachable operations, asynchronous ownership and saved contracts."""
import copy
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QPoint,QPointF,QMimeData
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QDialog,QFileDialog
from PySide6.QtTest import QTest
from _qt_wait import wait_ms, wait_until
from stage_fixture import StageFixture,APP
import test_workspace_panes as pane_fixture
from test_comfy_integration import png
from _source_import import wait_import
from prompt_calculus_studio.source_controls import load_many,step,choose_prompt,edit_list
from prompt_calculus_studio.source_list_dialog import SourceListDialog,ROLE
from prompt_calculus_studio.source_picker import CatalogPicker,add_menu
from prompt_calculus_studio.widgets import RoundMenu,StudioDialog,ComboBox
from prompt_calculus_studio import multi_output as model

SOURCE_CHANGED='已取消匯入圖片：圖片來源已變更。'


@contextmanager
def held_import(panel,paths):
    """Hold real file IO until the caller has exercised the in-flight UI."""
    from prompt_calculus_studio import source_import
    real=source_import.import_source;gate=threading.Event()
    def held(*args):
        assert gate.wait(10),'Timed out waiting for import test actions'
        return real(*args)
    with patch.object(source_import,'import_source',held):
        try:
            assert load_many(panel,paths)
            yield panel.import_task
        finally:
            gate.set();wait_import(panel.owner.window)


class SourceRedesignTests(StageFixture):
    def setUp(self):
        from test_084_stages import StageTests
        StageTests.setUp(self)

    def source(self,n=3):
        key=self.c.functions.add_image();panel=self.c.functions.cards[key].panel
        paths=[]
        for i in range(n):
            path=Path(self.tmp.name)/f'image-{i}.png'
            png(path,{'prompt':{'s':dict(inputs={'positive':['p',0],'negative':['n',0]}),
                'p':dict(inputs={'text':f'positive {i}'}),'n':dict(inputs={'text':f'negative {i}'})}})
            paths.append(str(path))
        if paths:
            self.assertTrue(load_many(panel,paths));wait_import(self.w)
        return key,panel,paths

    def value(self,key):return self.w.state['canvas_functions']['images'][key]

    def test_four_add_routes_append_preserve_cursor_and_one_undo(self):
        key,panel,paths=self.source();step(panel,1);before=copy.deepcopy(self.value(key))
        self.assertEqual(before['index'],1)
        titles=['本機檔案…','資料夾…','媒體庫…','最近生成…']
        for title in titles:
            undo=len(self.c.undo_stack)
            def choose(dialog):
                for i in range(dialog.listing.count()):dialog.listing.item(i).setSelected(True)
                return QDialog.DialogCode.Accepted
            rows=[dict(name=Path(p).name,path=p) for p in paths[:2]]
            with patch.object(QFileDialog,'getOpenFileNames',return_value=(paths[:2],'')),patch.object(QFileDialog,'getExistingDirectory',return_value=self.tmp.name), \
                 patch('prompt_calculus_studio.source_picker.folder_files',return_value=paths[:2]),patch.object(self.w.catalog,'rows',return_value=rows),patch.object(CatalogPicker,'exec',choose):
                menu=add_menu(panel);self.assertEqual([a.text() for a in menu.actions()],titles)
                next(a for a in menu.actions() if a.text()==title).trigger();wait_import(self.w);menu.deleteLater()
            value=self.value(key)
            self.assertEqual(value['items'],before['items']+before['items'][:2]);self.assertEqual(value['index'],1)
            self.assertEqual(value['source'],before['source']);self.assertNotEqual(value['batch_id'],before['batch_id'])
            self.assertEqual(len(self.c.undo_stack),undo+1);self.c.undo();self.assertEqual(self.value(key),before)

    def test_empty_and_single_import_and_cancelled_picker(self):
        key,panel,_=self.source(0);self.assertTrue(panel.pager.isHidden());self.assertTrue(panel.supply_row.isHidden())
        path=Path(self.tmp.name)/'one.png';png(path,{'parameters':'one'})
        with patch.object(QFileDialog,'getOpenFileNames',return_value=([str(path)],'')):panel.preview.clicked.emit()
        wait_import(self.w);self.assertEqual(self.value(key)['index'],0);self.assertFalse(self.value(key)['iterate'])
        before=copy.deepcopy(self.value(key))
        with patch.object(QFileDialog,'getOpenFileNames',return_value=([],'')):panel.choose_file()
        self.assertEqual(self.value(key),before);self.assertIsNone(self.w.jobs.active)

    def test_list_candidate_close_sort_remove_start_clear_and_undo(self):
        key,panel,_=self.source();before=copy.deepcopy(self.value(key));undo=len(self.c.undo_stack)
        dialog=SourceListDialog(panel);dialog.show();APP.processEvents()
        last=dialog.listing.takeItem(2);dialog.listing.insertItem(0,last);dialog.start_from(last)
        dialog.remove_item(dialog.listing.item(1));self.assertEqual(self.value(key),before)
        dialog.reject();dialog.deleteLater();self.assertEqual(self.value(key),before)
        dialog=SourceListDialog(panel);last=dialog.listing.takeItem(2);dialog.listing.insertItem(0,last);dialog.start_from(last)
        dialog.remove_item(dialog.listing.item(1));dialog.done_button.click()
        self.assertEqual(self.value(key)['items'],[before['items'][2],before['items'][1]])
        self.assertEqual(self.value(key)['source'],before['items'][2]);self.assertEqual(self.value(key)['index'],0)
        self.assertEqual(len(self.c.undo_stack),undo+1);self.c.undo();self.assertEqual(self.value(key),before)
        dialog=SourceListDialog(panel);dialog.clear_button.click();self.assertEqual(self.value(key),before)
        dialog.done_button.click();self.assertIsNone(self.value(key)['source']);self.assertNotIn('items',self.value(key));self.assertTrue(panel.pager.isHidden())
        empty=self.c.functions.add_image();self.assertEqual(self.value(key),self.value(empty))

    def test_list_import_waits_for_done_and_close_discards(self):
        key,panel,paths=self.source(1);before=copy.deepcopy(self.value(key));undo=len(self.c.undo_stack)
        dialog=SourceListDialog(panel)
        from prompt_calculus_studio.source_import import start
        self.assertTrue(start(panel,paths,dialog.append_images));wait_import(self.w)
        self.assertEqual(self.value(key),before);self.assertEqual(dialog.listing.count(),2)
        dialog.done_button.click();self.assertEqual(len(self.value(key)['items']),2);self.assertEqual(len(self.c.undo_stack),undo+1)
        self.c.undo();dialog=SourceListDialog(panel);start(panel,paths,dialog.append_images);dialog.reject();wait_import(self.w)
        self.assertEqual(self.value(key),before)

    def test_grid_drag_hover_remove_and_context_start_are_candidate_only(self):
        key,panel,_=self.source();before=copy.deepcopy(self.value(key))
        dialog=SourceListDialog(panel);dialog.show();APP.processEvents();grid=dialog.listing
        last=grid.item(2);grid._source=last.data(ROLE);grid._drag_token=b'source-grid'
        mime=QMimeData();mime.setData(grid.mime,grid._drag_token)
        at=grid.visualItemRect(grid.item(0)).topLeft()+QPoint(8,35)
        event=QDropEvent(QPointF(at),Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        grid.dropEvent(event);self.assertTrue(event.isAccepted());self.assertIs(grid.item(0),last)
        grid._source=None;grid._drag_token=b'';APP.processEvents()
        menus=[]
        with patch.object(RoundMenu,'open_at',lambda menu,*_:menus.append(menu)):grid.context(grid.visualItemRect(last).center())
        menus[0].actions()[0].trigger();self.assertEqual(dialog.start_token,last.data(ROLE)[0])
        rect=grid.visualItemRect(grid.item(1)).adjusted(4,4,-4,-4)
        QTest.mouseClick(grid.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(rect.right()-15,rect.top()+15))
        self.assertEqual(grid.count(),2);self.assertEqual(self.value(key),before)
        dialog.done_button.click();self.assertEqual(self.value(key)['items'],[before['items'][2],before['items'][1]])

    def test_running_list_is_read_only_and_append_rejected(self):
        key,panel,paths=self.source();before=copy.deepcopy(self.value(key))
        with patch.object(self.w.comfy.input_flow,'source_active',return_value=True):
            dialog=SourceListDialog(panel);self.assertFalse(dialog.add_button.isEnabled());self.assertFalse(dialog.clear_button.isEnabled())
            dialog.clear_items();dialog.remove_item(dialog.listing.item(0));dialog.start_from(dialog.listing.item(2));dialog.done_button.click()
            self.assertFalse(load_many(panel,paths));self.assertEqual(self.value(key),before)

    def test_supply_segments_and_menu_prompt_source(self):
        key,panel,_=self.source()
        for mode in ('step','batch'):
            panel.supply_row.buttons[mode].click();self.assertEqual(self.value(key)['supply'],mode)
        menus=[]
        with patch.object(RoundMenu,'open_at',lambda menu,*_:menus.append(menu)):self.c.functions.cards[key].open_menu(QPoint())
        actions={a.text():a for a in menus[0].actions()};self.assertNotIn('圖片管理',actions)
        def choose(dialog):
            picker=dialog.findChild(ComboBox);picker.setCurrentIndex(next(i for i in range(picker.count()) if picker.itemData(i)==['negative','n','text']))
            return QDialog.DialogCode.Accepted
        with patch.object(StudioDialog,'exec',choose):actions['提示詞來源…'].trigger()
        self.assertEqual(self.value(key)['content']['text'],'negative 0')
        actions['顯示圖片'].trigger();self.assertFalse(self.value(key)['show_image']);self.assertTrue(panel.preview.isHidden())

    def test_connected_and_legacy_reference_same_execution_and_all_selection(self):
        from test_084_stages import StageTests
        key,panel,_=self.source();stage=StageTests.stages(self)[0]
        self.c.commit(lambda s:s['canvas_functions']['images'][key].update(stage_reference=stage,iterate=True))
        self.executor.graphs['flow']['5']['inputs']['batch_size']=3
        self.click();self.executor.finish(count=3);wait_ms(60)
        from prompt_calculus_studio.flow_data import image_list
        def resolved():return image_list(self.runner.context(self.runner.results),key)
        before=resolved();self.assertEqual(len(before),3);self.assertFalse(panel.origin.isHidden());self.assertTrue(panel.add_row.isHidden())
        with patch.object(QFileDialog,'getOpenFileNames') as picker:panel.choose_file();picker.assert_not_called()
        step(panel,1);self.assertEqual(self.value(key)['input_index'],0);step(panel,1);self.assertEqual(self.value(key)['input_index'],1)
        panel.input_mode.buttons['all'].click();self.assertNotIn('input_index',self.value(key));self.assertEqual(resolved(),before)
        panel.input_mode.buttons['one'].click();self.assertEqual(self.value(key)['input_index'],0)
        panel.input_mode.buttons['all'].click()
        connected=copy.deepcopy(self.runner.context(self.runner.results));model.connect(connected,stage,key,'image')
        self.assertEqual(image_list(connected,key),before)
        self.c.commit(lambda s:model.connect(s,stage,key,'image'));self.assertTrue(panel.origin.isHidden())
        with patch.object(StudioDialog,'exec') as execute:edit_list(panel);execute.assert_not_called()
        link=next(c for c in self.c.data()['connections'] if c['destination']==key)
        self.c.commit(lambda s:model.disconnect(s,link['id']))
        menu=RoundMenu();panel.menu_actions(menu);next(a for a in menu.actions() if a.text()=='改用圖片清單').trigger()
        self.assertNotIn('stage_reference',self.value(key));self.assertEqual(len(resolved()),3)

    def test_cancel_errors_stale_owner_and_import_execution_lock(self):
        key,panel,paths=self.source();before=copy.deepcopy(self.value(key));undo=len(self.c.undo_stack)
        self.c.commit(lambda s:model.connect(s,key,self.out,'clip'));undo=len(self.c.undo_stack)
        from prompt_calculus_studio import source_import
        real=source_import.import_source;gate=threading.Event()
        def slow(*args):gate.wait(2);return real(*args)
        with patch.object(source_import,'import_source',slow):
            self.assertTrue(load_many(panel,paths));self.assertFalse(load_many(panel,paths))
            with patch.object(QFileDialog,'getOpenFileNames') as picker:panel.choose_file();picker.assert_not_called()
            self.assertFalse(self.controls.run_button.isEnabled())
            with self.assertRaisesRegex(ValueError,'匯入'):self.runner.start()
            panel.cancel_import();gate.set();wait_import(self.w)
        self.assertEqual(self.value(key),before);self.assertEqual(len(self.c.undo_stack),undo)
        broken=Path(self.tmp.name)/'broken.png';broken.write_text('invalid')
        with patch.object(self.c,'commit',wraps=self.c.commit) as commits:
            self.assertTrue(load_many(panel,[paths[0],str(broken),paths[1]]));wait_import(self.w);self.assertEqual(commits.call_count,1)
        self.assertEqual(len(self.value(key)['items']),5);self.assertEqual(sum('未能匯入' in m for m in self.notices),1)
        before=copy.deepcopy(self.value(key));gate.clear();self.notices.clear()
        with patch.object(source_import,'import_source',slow):
            load_many(panel,paths);panel.import_task.timer.stop()
            self.w.canvas_edit_context=object();gate.set();wait_import(self.w)
        self.assertEqual(self.value(key),before);self.assertEqual(self.notices,[SOURCE_CHANGED])

    def test_import_locks_edit_controls_and_restores_after_success(self):
        key,panel,paths=self.source();step(panel,1)
        before=copy.deepcopy(self.value(key));undo=len(self.c.undo_stack)
        menus=[RoundMenu()];panel.menu_actions(menus[0])
        with held_import(panel,paths):
            menus.append(RoundMenu());panel.menu_actions(menus[1])
            for control in (panel.previous,panel.next,panel.choose,panel.list_button,
                            *panel.supply_row.buttons.values(),*panel.input_mode.buttons.values()):
                self.assertFalse(control.isEnabled());control.click()
            for menu in menus:
                actions={a.text():a for a in menu.actions()}
                for title in ('顯示圖片','提示詞來源…'):
                    self.assertFalse(actions[title].isEnabled());actions[title].trigger()
            self.assertTrue(panel.cancel_button.isEnabled());self.assertTrue(panel.preview.isEnabled())
            with patch('prompt_calculus_studio.image_viewer.show_image') as preview:
                panel.preview.clicked.emit();preview.assert_called_once()
            panel.refresh()
            self.assertFalse(panel.previous.isEnabled());self.assertFalse(panel.next.isEnabled())
            self.assertFalse(panel.supply_row.buttons['step'].isEnabled())
            self.assertEqual(self.value(key),before);self.assertEqual(len(self.c.undo_stack),undo)
        self.assertEqual(len(self.value(key)['items']),6);self.assertEqual(self.value(key)['index'],1)
        self.assertEqual(len(self.c.undo_stack),undo+1)
        for control in (panel.previous,panel.next,panel.choose,panel.list_button,
                        *panel.supply_row.buttons.values(),*panel.input_mode.buttons.values()):self.assertTrue(control.isEnabled())
        for menu in menus:
            self.assertTrue(all(a.isEnabled() for a in menu.actions() if not a.isSeparator()));menu.deleteLater()

    def test_import_empty_preview_is_disabled_and_restored(self):
        key,panel,_=self.source(0);before=copy.deepcopy(self.value(key))
        path=Path(self.tmp.name)/'empty-import.png';png(path,{'parameters':'first'})
        with held_import(panel,[str(path)]):
            self.assertFalse(panel.preview.isEnabled())
            with patch.object(QFileDialog,'getOpenFileNames') as picker:
                QTest.mouseClick(panel.preview,Qt.MouseButton.LeftButton,pos=QPoint(8,8))
                panel.preview.clicked.emit();picker.assert_not_called()
            self.assertEqual(self.value(key),before)
        self.assertTrue(panel.preview.isEnabled());self.assertIsNotNone(self.value(key)['source'])
        self.assertFalse(panel.previous.isEnabled());self.assertFalse(panel.next.isEnabled())

    def test_import_undo_cancels_once_without_overwriting_undo(self):
        key,panel,paths=self.source();step(panel,1);self.notices.clear()
        with held_import(panel,paths) as task:
            self.c.undo();undo=len(self.c.undo_stack)
            self.assertTrue(wait_until(lambda:task.cancelled))
            # Persist captures the restored viewport and workspace-scene cache;
            # compare the canonical document, not a pending autosave projection.
            self.assertTrue(self.w.persist());saved=self.w.store.load()
            after_undo=copy.deepcopy(self.w.state)
            self.assertEqual(self.value(key)['index'],0);self.assertEqual(len(self.value(key)['items']),3)
            self.assertTrue(task.cancelled);self.assertTrue(task.job.cancel.is_set())
            self.assertEqual(self.notices,[SOURCE_CHANGED])
            task.progress();self.assertEqual(self.notices,[SOURCE_CHANGED])
        self.assertEqual(self.w.state,after_undo);self.assertEqual(len(self.c.undo_stack),undo)
        self.assertTrue(self.w.persist());self.assertEqual(self.w.store.load(),saved)
        self.assertEqual(self.notices,[SOURCE_CHANGED]);self.assertTrue(panel.next.isEnabled())
        self.c.redo();self.assertEqual(self.value(key)['index'],1);self.assertEqual(len(self.value(key)['items']),3)

    def test_import_user_cancel_is_quiet(self):
        key,panel,paths=self.source();self.assertTrue(self.w.persist())
        before=copy.deepcopy(self.w.state);saved=self.w.store.load();undo=len(self.c.undo_stack);self.notices.clear()
        with held_import(panel,paths) as task:
            panel.cancel_button.click();panel.cancel_button.click();task.progress()
            self.assertTrue(task.cancelled);self.assertTrue(task.job.cancel.is_set());self.assertEqual(self.notices,[])
        self.assertEqual(self.notices,[]);self.assertEqual(self.w.state,before);self.assertEqual(len(self.c.undo_stack),undo)
        self.assertTrue(self.w.persist());self.assertEqual(self.w.store.load(),saved)
        self.assertFalse(panel.previous.isEnabled());self.assertTrue(panel.next.isEnabled())
        self.assertTrue(panel.supply_row.buttons['step'].isEnabled())

    def test_import_legacy_source_menu_is_locked_until_invalidation_finishes(self):
        from test_084_stages import StageTests
        key,panel,paths=self.source();stage=StageTests.stages(self)[0];self.notices.clear()
        with held_import(panel,paths) as task:
            # An external edit can introduce a reference while the worker runs.
            self.c.commit(lambda s:s['canvas_functions']['images'][key].update(stage_reference=stage))
            changed=copy.deepcopy(self.w.state)
            menu=RoundMenu();panel.menu_actions(menu)
            action=next(a for a in menu.actions() if a.text()=='改用圖片清單')
            self.assertFalse(action.isEnabled());action.trigger();self.assertEqual(self.w.state,changed)
            task.progress();task.progress();self.assertEqual(self.notices,[SOURCE_CHANGED])
        self.assertEqual(self.w.state,changed);self.assertEqual(self.notices,[SOURCE_CHANGED])
        self.assertTrue(action.isEnabled());menu.deleteLater()

    def test_import_delete_and_workspace_switch_cancel_once(self):
        from prompt_calculus_studio import workspace_scene
        workspace=[]
        self.c.commit(lambda s:workspace.append(workspace_scene.create(s,'Other')))
        for action in ('delete','workspace'):
            with self.subTest(action=action):
                key,panel,paths=self.source();self.notices.clear()
                with held_import(panel,paths) as task:
                    if action=='delete':self.c.functions.remove(key)
                    else:self.w.change_workspace(workspace[0])
                    self.assertTrue(wait_until(lambda:task.cancelled));self.assertTrue(self.w.persist())
                    after=copy.deepcopy(self.w.state);undo=len(self.c.undo_stack)
                    self.assertTrue(task.cancelled);self.assertEqual(self.notices,[SOURCE_CHANGED])
                self.assertEqual(self.w.state,after);self.assertEqual(len(self.c.undo_stack),undo)
                self.assertEqual(self.notices,[SOURCE_CHANGED])

    def test_output_preview_copy_empty_manual_and_order_focus_no_navigation_compile(self):
        panel=self.c.outputs[self.out].panel
        # Other module dialogs may have been the active top-level window.
        # Focus assertions require an active host, as a real user click does.
        self.w.activateWindow();self.assertTrue(wait_until(self.w.isActiveWindow))
        self.assertEqual(panel.preview.text(),model.compile_output(self.w.state,self.out)['final_prompt'])
        with patch.object(self.w,'copy_text') as copied:panel.copy_button.click();copied.assert_called_once_with(panel.preview.text())
        panel.copy_button.click();self.assertEqual(APP.clipboard().text(),panel.preview.text())
        panel.open_button.click();page=panel.page;self.assertTrue(wait_until(page.content.order.hasFocus))
        # Legacy QPushButton.clicked may pass False; raising an already open
        # page must keep its existing focus, as the original no-arg slot did.
        self.assertIs(panel.open_page(False),page);self.assertTrue(page.content.order.hasFocus())
        page.content.copy_button.click();self.assertEqual(APP.clipboard().text(),panel.preview.text())
        page.finish()
        from prompt_calculus_studio.drafts import edit
        for text in ('manual\n' * 20,''):
            self.c.commit(lambda s:edit(s,text,self.out));self.assertEqual(panel.preview.text(),text);self.assertEqual(panel.summary.text(),'手動')
            old_clipboard=APP.clipboard().text();panel.copy_button.click()
            self.assertEqual(APP.clipboard().text(),text or old_clipboard)
            QTest.mouseClick(panel.preview,Qt.MouseButton.LeftButton,pos=QPoint(5,5))
            self.assertTrue(wait_until(lambda:panel.page is not None and panel.page.content.editor.hasFocus()))
            self.assertEqual(panel.page.content.editor.toPlainText(),text);self.assertTrue(panel.page.content.editor.hasFocus())
            panel.page.content.copy_button.click();self.assertEqual(APP.clipboard().text(),text or old_clipboard);panel.page.finish()
            self.assertLessEqual(len(panel.preview.lines),6)
        with patch.object(model,'compile_output',wraps=model.compile_output) as compiled:
            self.c.outputs[self.out].setPos(QPointF(10,20));self.c.update_lines();self.c.view.scale(1.1,1.1)
            self.c.view.horizontalScrollBar().setValue(10);APP.processEvents();self.assertEqual(compiled.call_count,0)
            self.c.move_cards({self.out:[20,30]});panel.refresh();APP.processEvents()
            self.assertEqual(compiled.call_count,0)
        self.c.commit(lambda s:edit(s,'updated after geometry',self.out))
        self.assertEqual(panel.preview.text(),'updated after geometry')
        from prompt_calculus_studio.drafts import clear
        self.c.commit(lambda s:clear(s,self.out))
        self.c.commit(lambda s:s['uses']['root'].update(prompt='changed composition'))
        self.assertEqual(panel.preview.text(),model.compile_output(self.w.state,self.out)['final_prompt'])
        self.assertIn('changed composition',panel.preview.text())

    def test_ports_do_not_move_when_body_state_changes_and_save_reopen(self):
        key,panel,_=self.source()
        def geometry():return {str(k):(p.scenePos(),p.caption.text(),p.kind) for k,p in self.c.ports.items() if p.parentItem() in (self.c.functions.cards[key],self.c.outputs[self.out])}
        before=geometry();panel.toggle_preview(False);self.assertEqual(geometry(),before)
        from prompt_calculus_studio.drafts import edit
        self.c.commit(lambda s:edit(s,'long text\n'*30,self.out));self.assertEqual(geometry(),before)
        expected=copy.deepcopy((self.w.state['canvas_functions']['images'],self.c.data()['outputs']))
        self.w.persist();saved=self.w.store.load()
        self.assertEqual((saved['canvas_functions']['images'],saved['multi_output']['outputs']),expected)

    def test_close_barrier_cancels_worker_before_store_release(self):
        key,panel,paths=self.source();before=copy.deepcopy(self.value(key));gate=threading.Event();self.notices.clear()
        from prompt_calculus_studio import source_import
        real=source_import.import_source
        def slow(*args):gate.wait(2);return real(*args)
        with patch.object(source_import,'import_source',slow):
            load_many(panel,paths);self.w.close();self.assertTrue(self.w.closing);self.assertFalse(getattr(self.w,'_close_completed',False))
            self.assertTrue(self.w.jobs.active.cancel.is_set());gate.set();wait_import(self.w)
        self.assertEqual(self.value(key),before)
        self.assertFalse(any(message.startswith('已取消') for message in self.notices))
        self.w.close();self.assertTrue(self.w._close_completed)

    def test_close_barrier_waits_for_thumbnail_file_readers(self):
        key,panel,_=self.source();gate=threading.Event();entered=threading.Event()
        from prompt_calculus_studio import source_thumbnails
        source_thumbnails._cache.clear();real=source_thumbnails.QImageReader
        class HeldReader:
            def __init__(self,path):self.reader=real(path)
            def __getattr__(self,name):return getattr(self.reader,name)
            def read(self):entered.set();gate.wait(2);return self.reader.read()
        with patch.object(source_thumbnails,'QImageReader',HeldReader):
            dialog=SourceListDialog(panel);dialog.show()
            self.assertTrue(wait_until(entered.is_set));job=dialog.thumbs.job
            self.w.close();self.assertTrue(self.w.closing);self.assertTrue(job.cancel.is_set())
            self.assertFalse(getattr(self.w,'_close_completed',False))
            self.w.store.db.execute('SELECT 1').fetchone()
            gate.set()
            self.assertTrue(wait_until(job.done.is_set))
            self.w.close();self.assertTrue(self.w._close_completed)


class SourceImportViewTests(unittest.TestCase):
    setUpClass=classmethod(pane_fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp=pane_fixture.WorkspacePaneTests.setUp
    tearDown=pane_fixture.WorkspacePaneTests.tearDown
    events=pane_fixture.WorkspacePaneTests.events
    open=pane_fixture.WorkspacePaneTests.open

    def test_other_view_source_edit_cancels_once_without_overwriting_edit(self):
        left=self.open('Street.pcs-workflow');host=left.host;canvas=host.canvas
        key=canvas.functions.add_image();panel=canvas.functions.cards[key].panel;paths=[]
        for i in range(2):
            path=self.root/f'view-source-{i}.png';png(path,{'parameters':f'image {i}'});paths.append(str(path))
        self.assertTrue(load_many(panel,paths));wait_import(host)
        right=self.w.duplicate_view(left,True);self.events()
        other=right.host.canvas.functions.cards[key].panel;notices=[]
        self.assertIs(host.state,right.host.state)
        with patch.object(host,'notice',notices.append),held_import(panel,paths) as task:
            self.assertFalse(panel.supply_row.buttons['step'].isEnabled())
            self.assertTrue(other.supply_row.buttons['step'].isEnabled())
            other.supply_row.buttons['step'].click();self.events()
            # The other view queues its edit; snapshot only after that edit
            # and the owner-invalidating timer are observable in shared state.
            self.assertTrue(wait_until(lambda:host.state['canvas_functions']['images'][key].get('supply')=='step'
                                      and task.cancelled))
            self.assertTrue(host.persist())
            expected=copy.deepcopy(host.state);undo=len(canvas.undo_stack)
            self.assertTrue(task.cancelled);self.assertEqual(notices,[SOURCE_CHANGED])
        self.assertEqual(notices,[SOURCE_CHANGED]);self.assertEqual(host.state,expected)
        self.assertEqual(right.host.state,expected);self.assertEqual(len(canvas.undo_stack),undo)
        self.assertEqual(host.state['canvas_functions']['images'][key]['supply'],'step')
        self.assertEqual(len(host.state['canvas_functions']['images'][key]['items']),2)
