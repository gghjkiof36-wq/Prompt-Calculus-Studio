"""Actual Qt events, local Stage Undo, task edits and responsive panel geometry.

Native inspection is a bounded fixture; no user workflow or service is changed.
"""
import copy
import json
import os
import re
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QTimer,QPoint,QPointF
from PySide6.QtGui import QFont,QFontDatabase,QContextMenuEvent,QPalette
from PySide6.QtWidgets import QLineEdit,QWidget,QPushButton,QLabel,QFrame,QComboBox,QToolButton
from PySide6.QtTest import QTest
from stage_fixture import StageFixture,APP
from stage_parameter_fixture import add_samplers,inspection,intention
from prompt_studio import stage_model,multi_output as model
from prompt_studio.stage_parameter_panel import open_parameters,SeedEditor,ParameterSheet,NodeName
from prompt_studio.stage_parameter_choices import ParameterChoices

for filename in ('msjh.ttc','consola.ttf','segoeui.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+filename)


class StageParameterUITests(StageFixture):
    def setUp(self):
        super().setUp()
        self.profile=add_samplers(copy.deepcopy(self.w.state['generation']['profiles'][0]))
        self.profile['origin']=dict(server=self.w.comfy.url,path='A.json')
        self.w.generation_panel.save_profile(self.profile)
        self.stage=stage_model.add(self.w.state,workflow=self.profile['id'])
        model.connect(self.w.state,self.clip,self.stage,'control');self.c.refresh()
        self.catalog=self.w.settings_page.workflow_manager.catalog
        self.catalog.loaded=True;self.catalog.busy=False;self.catalog.files=['A.json'];self.catalog.server=self.w.comfy.url
        self.reads=[]
        def read(path,profile,done,failed):
            self.reads.append(path);QTimer.singleShot(0,lambda:done(inspection(self.profile),'native'))
        self.stub=patch.object(self.catalog,'read_draft',side_effect=read);self.stub.start();self.addCleanup(self.stub.stop)

    def sheet(self,**kw):
        sheet=open_parameters(self.c,self.stage,**kw);QTest.qWait(30);self.assertFalse(sheet.loading,sheet.status.text())
        self.assertTrue(sheet.list.count(),sheet.status.text());sheet.jump(['35']);QTest.qWait(15);return sheet

    def edit(self,sheet,node,field,value):
        sheet.jump([node]);QTest.qWait(10)
        editor=sheet.findChild(QLineEdit,'parameter_'+node+'_'+field)
        self.assertIsNotNone(editor)
        editor.setFocus();QTest.keyClick(editor,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier);QTest.keyClicks(editor,value)
        return editor

    def assert_contained_fields(self,sheet):
        for editor in sheet.form.findChildren(QWidget):
            if not editor.objectName().startswith('parameter_'):continue
            self.assertTrue(editor.parentWidget().rect().contains(editor.geometry()),editor.objectName())
            self.assertGreaterEqual(editor.height(),min(40,editor.minimumSizeHint().height()),editor.objectName())
        for names in sheet.list.findChildren(NodeName):
            if not names.isVisible():continue
            for child in names.findChildren(QLabel):
                self.assertTrue(names.rect().contains(child.geometry()),(names.node['id'],child.geometry()))
                self.assertGreaterEqual(child.height(),child.fontMetrics().height(),child.text())

    def test_edit_drafts_apply_undo_and_discard_do_not_generate(self):
        readers=dict(output='9',text_output=['6','text'],selection='single',index=2)
        self.c.commit(lambda state:state['multi_output']['stages'][self.stage].update(readers))
        before=copy.deepcopy(self.w.state)
        sheet=self.sheet();self.edit(sheet,'35','cfg','3')
        self.edit(sheet,'58','cfg','4');sheet.jump(['35']);QTest.qWait(10)
        self.assertEqual(sheet.findChild(QLineEdit,'parameter_35_cfg').text(),'3')
        QTest.mouseClick(sheet.diff_button,Qt.MouseButton.LeftButton);self.assertIn('#35',sheet.diff.toPlainText())
        QTest.mouseClick(sheet.apply_button,Qt.MouseButton.LeftButton);QTest.qWait(35)
        cfg=self.c.data()['stages'][self.stage]['parameters'];self.assertEqual([p['value'] for p in cfg['patches']],[3.0,4.0])
        self.assertEqual({k:self.c.data()['stages'][self.stage][k] for k in readers},readers)
        self.assertFalse(self.executor.submissions);self.assertEqual(self.profile['graph']['35']['inputs']['cfg'],8.0)
        self.c.view.setFocus();QTest.keyClick(self.c.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertNotIn('parameters',self.c.data()['stages'][self.stage])
        QTest.keyClick(self.c.view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.c.data()['stages'][self.stage]['parameters'],cfg)
        sheet=self.sheet();self.edit(sheet,'35','cfg','-')
        self.assertFalse(sheet.apply_button.isEnabled());self.assertEqual(sheet.findChild(QLineEdit,'parameter_35_cfg').text(),'-')
        with patch('prompt_studio.stage_parameter_panel.ask',return_value=True):QTest.mouseClick(sheet.close_button,Qt.MouseButton.LeftButton)
        QTest.qWait(10);self.assertEqual(self.c.data()['stages'][self.stage]['parameters'],cfg);self.assertFalse(self.executor.submissions)

    def test_compact_float_does_not_create_or_round_intentions(self):
        exact=0.15000000000000002
        self.profile['graph']['35']['inputs']['denoise']=exact
        sheet=self.sheet();editor=sheet.findChild(QLineEdit,'parameter_35_denoise')
        self.assertEqual(editor.text(),'0.15');self.assertIn(str(exact),editor.toolTip())
        editor.setFocus();QTest.keyClick(editor,Qt.Key.Key_Tab)
        sheet.jump(['58']);sheet.jump(['35']);sheet.reload();QTest.qWait(30)
        self.assertFalse(sheet.raw);self.assertFalse(sheet.dirty());self.assertFalse(sheet.proposed()['patches'])
        self.edit(sheet,'35','cfg','4');QTest.mouseClick(sheet.apply_button,Qt.MouseButton.LeftButton);QTest.qWait(30)
        self.assertEqual([p['field'] for p in self.c.data()['stages'][self.stage]['parameters']['patches']],['cfg'])
        self.assertEqual(self.profile['graph']['35']['inputs']['denoise'],exact)
        self.assertFalse(self.executor.submissions)

    def test_enum_numeric_and_text_values_keep_their_type_on_reopen(self):
        self.profile['graph']['35']['inputs']['sampler_name']='1'
        def read(path,profile,done,failed):
            data=inspection(self.profile)
            field=next(f for n in data['parameters']['nodes'] if n['id']=='35' for f in n['fields'] if f['field']=='sampler_name')
            field['choices']=[1,'1',1.5,'1.5']
            QTimer.singleShot(0,lambda:done(data,'native'))
        self.catalog.read_draft.side_effect=read
        sheet=self.sheet();editor=sheet.findChild(ParameterChoices,'parameter_35_sampler_name')
        self.assertEqual(editor.currentIndex(),1);self.assertIs(type(editor.currentData()),str)
        self.assertFalse(sheet.dirty())
        editor.setFocus();QTest.keyClick(editor,Qt.Key.Key_Down);QTest.keyClick(editor,Qt.Key.Key_Down)
        patch=sheet.proposed()['patches'][0];self.assertEqual(patch['value'],'1.5');self.assertIs(type(patch['value']),str)
        QTest.mouseClick(sheet.apply_button,Qt.MouseButton.LeftButton);QTest.qWait(30)
        sheet=self.sheet();editor=sheet.findChild(ParameterChoices,'parameter_35_sampler_name')
        self.assertEqual(editor.currentIndex(),3);self.assertEqual(editor.currentData(),'1.5');self.assertFalse(sheet.dirty())
        sheet.finish();self.assertFalse(self.executor.submissions)

    def test_compact_saved_float_preserves_exact_value_until_explicit_edit(self):
        exact=0.15000000000000002
        config=intention(self.profile,field='denoise',value=exact)
        self.c.commit(lambda state:state['multi_output']['stages'][self.stage].update(parameters=config))
        sheet=self.sheet();self.assertEqual(sheet.findChild(QLineEdit,'parameter_35_denoise').text(),'0.15')
        self.assertEqual(sheet.proposed(),config);self.assertFalse(sheet.dirty())
        self.edit(sheet,'35','cfg','4');QTest.mouseClick(sheet.apply_button,Qt.MouseButton.LeftButton);QTest.qWait(30)
        saved=self.c.data()['stages'][self.stage]['parameters']
        self.assertEqual(next(p for p in saved['patches'] if p['field']=='denoise')['value'],exact)
        sheet=self.sheet();self.edit(sheet,'35','denoise','0.15')
        self.assertTrue(sheet.dirty());patch=next(p for p in sheet.proposed()['patches'] if p['field']=='denoise')
        self.assertEqual(patch['value'],0.15);self.assertEqual(patch['base'],1.0)
        QTest.mouseClick(sheet.apply_button,Qt.MouseButton.LeftButton);QTest.qWait(30)
        sheet=self.sheet();self.assertFalse(sheet.dirty());sheet.finish()
        self.c.view.setFocus();QTest.keyClick(self.c.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(next(p for p in self.c.data()['stages'][self.stage]['parameters']['patches'] if p['field']=='denoise')['value'],exact)
        self.assertFalse(self.executor.submissions)

    def test_external_field_conflict_and_stale_read_keep_draft(self):
        sheet=self.sheet();self.edit(sheet,'35','cfg','3');self.profile['graph']['35']['inputs']['cfg']=9.0
        QTest.mouseClick(sheet.apply_button,Qt.MouseButton.LeftButton);QTest.qWait(30)
        self.assertIn('同一欄位',sheet.status.text());self.assertFalse(sheet.closed);self.assertNotIn('parameters',self.c.data()['stages'][self.stage])
        self.assertEqual(sheet.findChild(QLineEdit,'parameter_35_cfg').text(),'3')
        pending=[];self.catalog.read_draft.side_effect=lambda path,profile,done,failed:pending.append(done)
        sheet.reload();self.assertFalse(sheet.findChildren(SeedEditor))
        sheet.workflow.setCurrentIndex(0);pending[0](inspection(self.profile),'native');QTest.qWait(10)
        self.assertIsNone(sheet.workflow.currentData());self.assertFalse(sheet.list.count());self.assertFalse(sheet.apply_button.isEnabled());sheet.finish()

    def test_seed_mode_selected_node_reset_focus_and_escape_keep_main_geometry(self):
        sheet=self.sheet();rect=sheet.panel.geometry();button_pos=sheet.apply_button.mapTo(sheet,sheet.apply_button.rect().center())
        seed=sheet.findChild(SeedEditor,'parameter_35_seed');seed.text.setFocus();QTest.qWait(10)
        self.assertIn(sheet.tokens['accent'],seed.styleSheet())
        QTest.mouseClick(seed.action,Qt.MouseButton.LeftButton);QTest.qWait(15)
        menu=APP.activePopupWidget();self.assertIsNotNone(menu)
        QTest.mouseClick(menu,Qt.MouseButton.LeftButton,pos=menu.actionGeometry(menu.actions()[1]).center());QTest.qWait(15)
        self.assertEqual(seed.mode,'increment')
        self.assertEqual(seed.text.text(),'10');self.assertEqual(sheet.panel.geometry(),rect)
        QTest.mouseClick(seed.action,Qt.MouseButton.LeftButton);QTest.qWait(15)
        QTest.keyClick(APP.activePopupWidget(),Qt.Key.Key_Escape);QTest.qWait(10)
        self.assertFalse(sheet.closed);self.assertEqual(seed.mode,'increment')
        self.edit(sheet,'5','width','1024');sheet.jump(['35']);QTest.qWait(15)
        self.assertIsNone(sheet.findChild(QLineEdit,'parameter_5_width'))
        sheet.jump(['5']);QTest.qWait(15);self.assertEqual(sheet.findChild(QLineEdit,'parameter_5_width').text(),'1024')
        QTest.mouseClick(sheet.diff_button,Qt.MouseButton.LeftButton);QTest.keyClick(sheet,Qt.Key.Key_Escape);self.assertFalse(sheet.drawer.isVisible())
        self.assertEqual(sheet.apply_button.mapTo(sheet,sheet.apply_button.rect().center()),button_pos)
        QTest.mouseClick(sheet.reset_button,Qt.MouseButton.LeftButton);self.assertFalse(sheet.proposed()['patches']);sheet.finish()

    def test_parameter_surfaces_seed_glyph_and_focus_follow_each_palette(self):
        for palette in ('graphite','mist','paper'):
            with self.subTest(palette=palette):
                self.w.state['settings']['visual_palette']=palette;self.w.apply_theme()
                sheet=self.sheet();t=sheet.tokens
                self.assertEqual(sheet.panel.grab().toImage().pixelColor(10,30).name(),t['base'])
                self.assertEqual(sheet.editor.grab().toImage().pixelColor(10,10).name(),t['surface'])
                self.assertEqual(sheet.footer.grab().toImage().pixelColor(10,10).name(),t['surface'])
                seed=sheet.findChild(SeedEditor,'parameter_35_seed')
                seed.text.setFocus();QTest.qWait(10)
                image=seed.grab().toImage()
                self.assertEqual(image.pixelColor(image.width()//2,image.height()-1).name(),t['accent'])
                self.assertEqual(seed.text.palette().color(QPalette.ColorRole.Text).name(),t['text'])
                glyph=seed.action.icon().pixmap(16,16).toImage()
                inks={glyph.pixelColor(x,y).name() for x in range(glyph.width()) for y in range(glyph.height())
                      if glyph.pixelColor(x,y).alpha()>200}
                self.assertIn(t['text'],inks)
                self.assertFalse(sheet.dirty());sheet.finish();QTest.qWait(10)
        self.assertFalse(self.executor.submissions)

    def test_actual_card_entry_points_close_hit_area_and_focused_card_shortcuts(self):
        view=self.c.view;view.resetTransform();view.scale(.8,.8)
        card=self.c.flow_cards[self.stage];view.centerOn(card.sceneBoundingRect().center());QTest.qWait(20)
        header=lambda:view.mapFromScene(self.c.flow_cards[self.stage].mapToScene(QPointF(95,22)))
        def current():
            QTest.qWait(30);sheet=self.w.findChild(ParameterSheet)
            self.assertIsNotNone(sheet)
            for _ in range(15):
                if not sheet.loading:break
                QTest.qWait(20)
            self.assertFalse(sheet.loading,sheet.status.text());return sheet
        def close(sheet):
            self.assertGreaterEqual(sheet.close_button.width(),44);self.assertGreaterEqual(sheet.close_button.height(),44)
            QTest.mouseClick(sheet.close_button,Qt.MouseButton.LeftButton,pos=QPoint(3,3));QTest.qWait(25)
            self.assertFalse(self.w.findChildren(ParameterSheet))
        QTest.mouseDClick(view.viewport(),Qt.MouseButton.LeftButton,pos=header());close(current())
        card=self.c.flow_cards[self.stage];point=view.mapFromScene(card.mapToScene(card.actions()['size'].center()))
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point);close(current())
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=header())
        QTest.keyClick(view,Qt.Key.Key_Return);close(current())
        point=header();QTest.mouseClick(view.viewport(),Qt.MouseButton.RightButton,pos=point)
        APP.sendEvent(view.viewport(),QContextMenuEvent(QContextMenuEvent.Reason.Mouse,point,view.viewport().mapToGlobal(point)));QTest.qWait(15)
        menu=APP.activePopupWidget();self.assertIsNotNone(menu)
        QTest.mouseClick(menu,Qt.MouseButton.LeftButton,pos=menu.actionGeometry(menu.actions()[0]).center());close(current())
        sheet=self.sheet();self.edit(sheet,'35','cfg','3');QTest.mouseClick(sheet.apply_button,Qt.MouseButton.LeftButton);QTest.qWait(35)
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=header());QTest.keyClick(view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertNotIn('parameters',self.c.data()['stages'][self.stage])
        QTest.keyClick(view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.c.data()['stages'][self.stage]['parameters']['patches'][0]['value'],3)
        QTest.keyClick(view,Qt.Key.Key_Delete);QTest.qWait(25)
        self.assertNotIn(self.stage,self.c.data()['stages'])
        QTest.keyClick(view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        self.assertIn(self.stage,self.c.data()['stages']);self.assertFalse(self.executor.submissions)

    def test_task_edit_isolated_and_started_task_keeps_unsaved_draft(self):
        # This case edits a single task's parameter copy, not the empty starter
        # Stage that a fresh canvas now offers. Keep the shared fixture intact.
        planned=copy.deepcopy(self.w.state);data=planned['multi_output']
        other_stages=set(data['stages'])-{self.stage}
        data['stages']={self.stage:data['stages'][self.stage]}
        data['connections']=[c for c in data['connections'] if c['source'] not in other_stages and c['destination'] not in other_stages]
        plan=stage_model.compile_plan(planned);self.assertEqual(set(plan['stages']),{self.stage})
        run=self.runner.create_run(plan,1,status='paused')
        first=self.runner.store.add('entry',self.w.state['workspace'],'owner',run=run['id'],scheduler='q',stages=[self.stage],label='Task',
                                    status='waiting',saved=dict(parameters={self.stage:intention(self.profile,value=3)}))
        second=self.runner.store.copy_entry(first['id'])
        sheet=self.sheet(item=second);self.edit(sheet,'35','cfg','4')
        QTest.mouseClick(sheet.apply_button,Qt.MouseButton.LeftButton);QTest.qWait(30)
        self.assertEqual(self.runner.store.read(first['id'])['saved']['parameters'][self.stage]['patches'][0]['value'],3)
        self.assertEqual(self.runner.store.read(second['id'])['saved']['parameter_overrides'][self.stage]['patches'][0]['value'],4)
        self.assertNotIn('parameters',self.c.data()['stages'][self.stage]);self.assertFalse(self.executor.submissions)
        sheet=self.sheet(item=self.runner.store.read(second['id']));self.edit(sheet,'35','cfg','5')
        self.runner.store.update(second['id'],status='preparing');sheet.check_owner()
        self.assertFalse(sheet.apply_button.isEnabled());self.assertEqual(sheet.findChild(QLineEdit,'parameter_35_cfg').text(),'5');sheet.finish()

    def test_layout_matrix_stable_main_and_reachable_actions(self):
        target=Path(os.environ.get('PCS_TEST_ARTIFACT_DIR',self.tmp.name));target.mkdir(parents=True,exist_ok=True)
        rows=[]
        # The existing application minimum is 960x620. Smaller samples inspect
        # this real Qt sheet in an isolated host, without changing product limits.
        host=QWidget();self.addCleanup(host.close);host.show()
        for width,height,scale in [(1920,1080,1),(1280,900,1),(960,900,1),(960,540,1),(640,480,1),
                                   (1280,900,1.25),(960,900,1.5),(960,540,2)]:
            host.resize(width,height)
            host.setStyleSheet(re.sub(r'font-size:(\d+)px',lambda m:'font-size:'+str(round(int(m[1])*scale))+'px',self.w.styleSheet()))
            with patch.object(self.w,'centralWidget',return_value=host):sheet=self.sheet()
            sheet.place();sheet.jump(['35']);QTest.qWait(30);rect=sheet.panel.geometry()
            self.assert_contained_fields(sheet)
            self.assertTrue(host.rect().contains(rect),(width,height,scale,rect))
            for action in (sheet.close_button,sheet.apply_button,sheet.reset_button):
                actual=action.rect().translated(action.mapTo(sheet,QPoint(0,0)))
                self.assertTrue(rect.contains(actual),(width,height,scale,action.text(),rect,actual))
            positions=[sheet.apply_button.mapTo(sheet,QPoint(0,0)),sheet.close_button.mapTo(sheet,QPoint(0,0))]
            for path in (['5'],['58'],['35']):
                sheet.jump(path);QTest.qWait(10);self.assertEqual(sheet.panel.geometry(),rect)
                self.assertEqual(positions,[sheet.apply_button.mapTo(sheet,QPoint(0,0)),sheet.close_button.mapTo(sheet,QPoint(0,0))])
            sheet.toggle_diff();self.assertEqual(sheet.panel.geometry(),rect);sheet.drawer.hide()
            rail=sheet.scroll.verticalScrollBar()
            name=f'parameters-{width}x{height}-font-{int(scale*100)}.png';host.grab().save(str(target/name))
            if width>=1280 and scale==1:self.assertEqual(rail.maximum(),0)
            rows.append(dict(client=[host.width(),host.height()],font_scale=scale,panel=[rect.x(),rect.y(),rect.width(),rect.height()],
                             vertical_overflow=rail.maximum(),nodes_drawer=sheet.nodes_button.isVisible(),image=name,scope='isolated real Qt sheet'))
            sheet.finish();QTest.qWait(10)
        (target/'PARAMETER_LAYOUT.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')

    def test_actual_desktop_client_layout_and_minimum_remain_usable(self):
        target=Path(os.environ.get('PCS_TEST_ARTIFACT_DIR',self.tmp.name));target.mkdir(parents=True,exist_ok=True)
        style=self.w.styleSheet();rows=[]
        for width,height,scale in [(1920,1080,1),(1280,900,1),(960,900,1),(960,540,1),(640,480,1),
                                   (1280,900,1.25),(960,900,1.5),(960,540,2)]:
            self.w.setStyleSheet(re.sub(r'font-size:(\d+)px',lambda m:'font-size:'+str(round(int(m[1])*scale))+'px',style))
            self.w.resize(width,height);QTest.qWait(20);sheet=self.sheet();sheet.place();QTest.qWait(20)
            rect=sheet.panel.geometry();client=self.w.centralWidget()
            self.assert_contained_fields(sheet)
            self.assertTrue(client.rect().contains(rect),(width,height,scale,client.size(),rect))
            for action in (sheet.close_button,sheet.apply_button,sheet.reset_button):
                self.assertTrue(rect.contains(action.rect().translated(action.mapTo(sheet,QPoint(0,0)))))
            self.assertGreaterEqual(sheet.close_button.width(),44)
            name=f'desktop-parameters-{width}x{height}-font-{int(scale*100)}.png';self.w.grab().save(str(target/name))
            rows.append(dict(requested_client=[width,height],actual_client=[client.width(),client.height()],
                font_scale=scale,panel=[rect.x(),rect.y(),rect.width(),rect.height()],image=name,scope='actual desktop Qt offscreen'))
            QTest.mouseClick(sheet.close_button,Qt.MouseButton.LeftButton);QTest.qWait(15)
            self.assertFalse(self.w.findChildren(ParameterSheet))
        (target/'DESKTOP_PARAMETER_LAYOUT.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')

    def test_selected_node_only_backdrop_and_live_font_change(self):
        self.w.resize(1280,900);QTest.qWait(20);sheet=self.sheet()
        self.assertIsNone(sheet.findChild(QFrame,'StageParameterSource'))
        self.assertIsNone(sheet.findChild(QLineEdit,'parameter_5_width'))
        self.assertFalse([b for b in sheet.form.findChildren(QPushButton) if '↗' in b.text() or b.property('stageSourceJump')])
        latent=next(item for item,node in sheet.node_items if node['id']=='5')
        QTest.mouseClick(sheet.list.viewport(),Qt.MouseButton.LeftButton,pos=sheet.list.visualItemRect(latent).center());QTest.qWait(15)
        self.assertEqual(sheet.selected,['5']);self.assertIsNone(sheet.findChild(QLineEdit,'parameter_35_cfg'))
        batch=sheet.findChild(QLineEdit,'parameter_5_batch_size');width=sheet.findChild(QLineEdit,'parameter_5_width')
        self.assertGreater(batch.width(),width.width()*1.8)
        preview=next(item for item,node in sheet.node_items if node['id']=='9')
        sheet.list.scrollToItem(preview);QTest.qWait(10)
        QTest.mouseClick(sheet.list.viewport(),Qt.MouseButton.LeftButton,pos=sheet.list.visualItemRect(preview).center());QTest.qWait(15)
        self.assertEqual(sheet.selected,['9'])
        self.assertIn('此節點沒有可調整的參數',[item.text() for item in sheet.form.findChildren(QLabel)])
        self.assertFalse([w for w in sheet.form.findChildren(QWidget) if w.objectName().startswith('parameter_')])
        before=copy.deepcopy(self.c.data());self.edit(sheet,'35','cfg','3')
        with patch('prompt_studio.stage_parameter_panel.ask',return_value=False) as ask:
            QTest.mouseClick(sheet,Qt.MouseButton.LeftButton,pos=QPoint(2,2));self.assertTrue(ask.called)
        self.assertFalse(sheet.closed);self.assertEqual(self.c.data(),before)
        style=self.w.styleSheet();self.w.setStyleSheet(re.sub(r'font-size:(\d+)px',lambda m:'font-size:'+str(int(m[1])*2)+'px',style));QTest.qWait(50)
        self.assert_contained_fields(sheet);self.assertEqual(sheet.findChild(QLineEdit,'parameter_35_cfg').text(),'3')
        self.assertTrue(sheet.parentWidget().rect().contains(sheet.panel.geometry()))
        with patch('prompt_studio.stage_parameter_panel.ask',return_value=True):
            QTest.mouseClick(sheet,Qt.MouseButton.LeftButton,pos=QPoint(2,2))
        QTest.qWait(10);self.assertFalse(self.w.findChildren(ParameterSheet));self.assertEqual(self.c.data(),before)
        self.assertFalse(self.executor.submissions)

    def test_category_click_keyboard_search_and_refresh_preserve_selected_draft(self):
        self.w.resize(1280,900);QTest.qWait(15);sheet=self.sheet();self.edit(sheet,'35','cfg','3')
        group=sheet.findChild(QToolButton,'StageNodeGroup_sampling')
        current=sheet.list.currentItem();rect=sheet.panel.geometry()
        QTest.mouseClick(group,Qt.MouseButton.LeftButton);QTest.qWait(15)
        self.assertTrue(current.isHidden());self.assertEqual(sheet.selected,['35'])
        self.assertEqual(sheet.findChild(QLineEdit,'parameter_35_cfg').text(),'3')
        self.assertFalse(group.isChecked());self.assertTrue(group.hasFocus())
        sheet.search.setFocus();QTest.keyClicks(sheet.search,'35');QTest.qWait(15)
        self.assertFalse(current.isHidden());self.assertTrue(group.isChecked());self.assertFalse(group.isEnabled())
        self.assertEqual([node['id'] for item,node in sheet.node_items if not item.isHidden()],['35'])
        QTest.keyClick(sheet.search,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier);QTest.keyClick(sheet.search,Qt.Key.Key_Backspace);QTest.qWait(15)
        self.assertTrue(current.isHidden());self.assertFalse(group.isChecked());self.assertTrue(group.isEnabled())
        group.setFocus();QTest.keyClick(group,Qt.Key.Key_Space);QTest.qWait(15)
        self.assertFalse(current.isHidden());self.assertTrue(group.isChecked())
        QTest.keyClick(group,Qt.Key.Key_Space);QTest.qWait(15);self.assertTrue(current.isHidden())
        sheet.reload();QTest.qWait(40)
        self.assertEqual(sheet.selected,['35']);self.assertTrue(sheet.list.currentItem().isHidden())
        self.assertEqual(sheet.findChild(QLineEdit,'parameter_35_cfg').text(),'3')
        self.assertEqual(sheet.panel.geometry(),rect)
        QTest.mouseClick(sheet.diff_button,Qt.MouseButton.LeftButton);self.assertIn('#35',sheet.diff.toPlainText())
        QTest.keyClick(sheet,Qt.Key.Key_Escape);self.assertFalse(sheet.drawer.isVisible());self.assertFalse(sheet.closed)
        QTest.mouseClick(sheet.apply_button,Qt.MouseButton.LeftButton);QTest.qWait(35)
        self.assertTrue(sheet.closed);self.assertEqual(self.c.data()['stages'][self.stage]['parameters']['patches'][0]['value'],3)
        self.assertFalse(self.executor.submissions)

    def test_linked_seed_mode_is_integrated_and_unknown_policy_stays_readonly(self):
        sheet=self.sheet();description=sheet.description()
        sampler=next(n for n in description['nodes'] if n['id']=='35')
        seed=next(f for f in sampler['fields'] if f['field']=='seed')
        seed.update(seed_control=True,seed_control_mode='fixed')
        sheet.select_node();QTest.qWait(15)
        self.assertIsNone(sheet.findChild(QWidget,'parameter_35_control_after_generate'))
        # Native descriptors omit their linked frontend companion. A separately
        # declared backend field with the same name still belongs in the form.
        sampler['fields'].append(dict(field='control_after_generate',value='backend-value',type='STRING',editable=True,reason='',schema='backend-control'))
        sheet.select_node();QTest.qWait(15)
        self.assertEqual(sheet.findChild(QLineEdit,'parameter_35_control_after_generate').text(),'backend-value')
        editor=sheet.findChild(SeedEditor,'parameter_35_seed');self.assertTrue(editor.action.isEnabled())
        QTest.mouseClick(editor.action,Qt.MouseButton.LeftButton);QTest.qWait(15)
        menu=APP.activePopupWidget();self.assertIsNotNone(menu)
        QTest.mouseClick(menu,Qt.MouseButton.LeftButton,pos=menu.actionGeometry(menu.actions()[3]).center());QTest.qWait(15)
        self.assertEqual(editor.mode,'randomize');self.assertEqual(editor.text.text(),'10');self.assertTrue(editor.action.hasFocus())
        self.assertEqual(sheet.proposed()['patches'][0]['seed_mode'],'randomize')
        sheet.reset();seed.pop('seed_mode');seed.pop('seed_timing')
        seed.update(editable=False,reason='原生種子模式無法確認',seed_control_mode='foreign-policy',seed_control_reason='原生種子模式無法確認')
        sheet.select_node();QTest.qWait(15);editor=sheet.findChild(SeedEditor,'parameter_35_seed')
        self.assertTrue(editor.text.isReadOnly());self.assertFalse(editor.action.isEnabled())
        self.assertIn('無法確認',editor.action.toolTip());self.assertNotEqual(editor.mode,'fixed')
        editor.text.setFocus();QTest.keyClick(editor.text,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier);QTest.keyClicks(editor.text,'123')
        self.assertEqual(editor.text.text(),'10');self.assertFalse(sheet.dirty());self.assertFalse(self.executor.submissions);sheet.finish()

    def test_other_workflow_draft_is_preserved_and_close_requires_discard(self):
        other=copy.deepcopy(self.profile);other.update(id='other-flow',name='other',frontend_id='other-native')
        other['origin']['path']='B.json';self.w.generation_panel.save_profile(other);self.catalog.files=['A.json','B.json']
        self.catalog.read_draft.side_effect=lambda path,profile,done,failed:QTimer.singleShot(0,lambda:done(inspection(other if path=='B.json' else self.profile),'native'))
        sheet=self.sheet();original=sheet.workflow.currentData()
        sheet.workflow.setCurrentIndex(sheet.workflow.findData(other['id']));QTest.qWait(30);self.edit(sheet,'35','cfg','4')
        sheet.workflow.setCurrentIndex(sheet.workflow.findData(original));QTest.qWait(30);self.assertFalse(sheet.dirty())
        with patch('prompt_studio.stage_parameter_panel.ask',return_value=False) as ask:
            QTest.mouseClick(sheet.close_button,Qt.MouseButton.LeftButton);self.assertTrue(ask.called)
        self.assertFalse(sheet.closed)
        self.edit(sheet,'35','cfg','3')
        with patch('prompt_studio.stage_parameter_panel.ask',return_value=False) as ask:
            QTest.mouseClick(sheet.apply_button,Qt.MouseButton.LeftButton);self.assertTrue(ask.called)
        self.assertNotIn('parameters',self.c.data()['stages'][self.stage]);self.assertFalse(sheet.closed)
        sheet.workflow.setCurrentIndex(sheet.workflow.findData(other['id']));QTest.qWait(30);sheet.jump(['35']);QTest.qWait(15)
        self.assertEqual(sheet.findChild(QLineEdit,'parameter_35_cfg').text(),'4')
        with patch('prompt_studio.stage_parameter_panel.ask',return_value=True):QTest.mouseClick(sheet.close_button,Qt.MouseButton.LeftButton)
        self.assertEqual(self.c.data()['stages'][self.stage]['workflow'],original);self.assertFalse(self.executor.submissions)

    def test_refresh_preserves_draft_baseline_and_removed_field_can_be_discarded(self):
        sheet=self.sheet();self.edit(sheet,'35','cfg','3');self.assertEqual(sheet.proposed()['patches'][0]['base'],8)
        self.profile['graph']['35']['inputs']['cfg']=9.0;sheet.reload();QTest.qWait(30)
        self.assertEqual(sheet.proposed()['patches'][0]['base'],8)
        QTest.mouseClick(sheet.apply_button,Qt.MouseButton.LeftButton);QTest.qWait(30)
        self.assertIn('同一欄位',sheet.status.text());self.assertFalse(sheet.closed);self.assertNotIn('parameters',self.c.data()['stages'][self.stage])
        self.profile['graph']['35']['inputs'].pop('cfg');sheet.reload();QTest.qWait(30)
        self.assertFalse(sheet.apply_button.isEnabled())
        with patch('prompt_studio.stage_parameter_panel.ask',return_value=False) as ask:
            QTest.mouseClick(sheet.close_button,Qt.MouseButton.LeftButton);self.assertTrue(ask.called)
        self.assertFalse(sheet.closed)
        with patch('prompt_studio.stage_parameter_panel.ask',return_value=True):QTest.mouseClick(sheet.close_button,Qt.MouseButton.LeftButton)
        self.assertFalse(self.executor.submissions)

    def test_search_while_refresh_pending_and_late_reply_after_close(self):
        sheet=self.sheet();pending=[]
        self.catalog.read_draft.side_effect=lambda path,profile,done,failed:pending.append((done,failed))
        sheet.reload();sheet.search.setFocus();QTest.keyClicks(sheet.search,'35');self.assertTrue(sheet.loading)
        self.assertEqual(sheet.list.count(),0);self.assertEqual(sheet.node_items,[]);self.assertEqual(sheet.group_items,[])
        before=copy.deepcopy(self.c.data());sheet.finish();QTest.qWait(20)
        pending[0][0](inspection(self.profile),'native');pending[0][1]('late');QTest.qWait(15)
        self.assertEqual(self.c.data(),before);self.assertFalse(self.executor.submissions)

    def test_long_choices_keep_panel_draft_and_close_only_popup(self):
        sheet=self.sheet();field=next(f for n in sheet.description()['nodes'] if n['id']=='35' for f in n['fields'] if f['field']=='sampler_name')
        field['choices']=['euler']+['sampler-'+str(i) for i in range(25)];sheet.select_node();QTest.qWait(20)
        combo=sheet.findChild(ParameterChoices,'parameter_35_sampler_name');QTest.mouseClick(combo,Qt.MouseButton.LeftButton);QTest.qWait(15)
        popup=combo._choices_popup;self.assertIsNotNone(popup);QTest.keyClicks(popup.search,'sampler-23')
        self.assertFalse(sheet.dirty());QTest.keyClick(popup.search,Qt.Key.Key_Down);QTest.keyClick(popup.search,Qt.Key.Key_Return);QTest.qWait(15)
        self.assertEqual(sheet.proposed()['patches'][0]['value'],'sampler-23')
        QTest.mouseClick(combo,Qt.MouseButton.LeftButton);QTest.qWait(15);QTest.keyClick(combo._choices_popup.search,Qt.Key.Key_Escape);QTest.qWait(15)
        self.assertFalse(sheet.closed);self.assertEqual(combo.currentData(),'sampler-23')
        QTest.mouseClick(combo,Qt.MouseButton.LeftButton);QTest.qWait(15)
        errors=[]
        with patch('sys.excepthook',side_effect=lambda *args:errors.append(args)):
            sheet.finish();QTest.qWait(20)
        self.assertFalse(errors);self.assertFalse(self.executor.submissions)

    def test_same_named_workflows_show_sources_without_changing_binding_keys(self):
        other=copy.deepcopy(self.profile);other.update(id='other-flow',frontend_id='other-native')
        other['origin']['path']='folder/B.json';self.w.generation_panel.save_profile(other)
        self.catalog.files=['A.json','folder/B.json','a/duplicate.json','b/duplicate.json']
        sheet=self.sheet();first=sheet.workflow.findData(self.profile['id']);second=sheet.workflow.findData(other['id'])
        self.assertNotEqual(sheet.workflow.itemText(first),sheet.workflow.itemText(second))
        self.assertIn('folder/B.json',sheet.workflow.itemText(second))
        self.assertIn(self.profile['origin']['server'],sheet.workflow.itemData(first,Qt.ItemDataRole.ToolTipRole))
        self.assertIn('A.json',sheet.workflow.toolTip());self.assertEqual(sheet.workflow.currentData(),self.profile['id'])
        for path in ('a/duplicate.json','b/duplicate.json'):
            index=sheet.workflow.findData(self.catalog.identity(path));self.assertGreater(index,0)
            self.assertIn(path,sheet.workflow.itemText(index))
        sheet.finish();self.assertFalse(self.executor.submissions)
