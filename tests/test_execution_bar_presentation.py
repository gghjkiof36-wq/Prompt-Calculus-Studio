"""Compact execution controls preserve their real actions and readable paint."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication,QEvent,QPoint,QRect,Qt
from PySide6.QtGui import QFontDatabase,QIcon
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_studio.color_roles import contrast
from prompt_studio.theme import visual_tokens
from prompt_studio.window import Window

APP=QApplication.instance() or QApplication([])
for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)


class ExecutionBarPresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_DATA':self.tmp.name});env.start();self.addCleanup(env.stop)
        transport=patch('prompt_studio.comfy_client.ComfyClient.request',return_value=None);transport.start();self.addCleanup(transport.stop)
        run=patch.object(Window,'copy_final');self.run=run.start();self.addCleanup(run.stop)
        self.w=Window(self.tmp.name);self.w.display_recovery.stop();self.w.comfy.timer.stop()
        self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.comfy.connected=self.w.comfy.native_supported=self.w.comfy.flow_supported=True
        self.active=False
        work=patch.object(self.w.comfy.input_flow,'has_work',side_effect=lambda:self.active);work.start();self.addCleanup(work.stop)
        self.w.resize(1440,900);self.w.show();self.w.enter_canvas()
        self.bar=self.w.canvas.execution_bar;self.controls=self.w.run_controls

    def tearDown(self):
        self.w.comfy.connected=False;self.w.comfy.running=0;self.active=False
        self.w.close();self.w.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)

    def configure(self,palette='graphite',size=11,width=1440,active=False):
        self.active=active;self.w.comfy.running=3 if active else 0
        self.w.comfy.input_flow.last_status['running_ids']=['fixture-'+str(i) for i in range(self.w.comfy.running)]
        self.w.state['settings'].update(visual_palette=palette,ui_size=size,execution_bar_position=[.5,1.])
        self.w.resize(width,900 if width>1000 else 640)
        self.w.apply_theme(preserve_layout=True);self.controls.refresh();self.bar.update_progress();QTest.qWait(30)
        QTest.mouseMove(self.w,QPoint(1,1));APP.processEvents()

    def test_actual_bar_geometry_and_paint_across_themes_sizes_and_activity(self):
        graph=copy.deepcopy(self.w.canvas.data());records=[]
        target=Path(os.environ.get('PCS_TEST_ARTIFACT_DIR',self.tmp.name));target.mkdir(parents=True,exist_ok=True)
        for palette in ('graphite','mist','paper'):
            for size in (11,18,22):
                for width in (1440,640):
                    for active in (False,True):
                        with self.subTest(palette=palette,size=size,width=width,active=active):
                            self.configure(palette,size,width,active);self.controls.count.setValue(100)
                            self.assertTrue(self.w.canvas.view.viewport().rect().contains(self.bar.geometry()))
                            visible=[self.bar.handle,self.controls.count,self.controls.run_button,self.controls.stop]
                            visible+=[self.bar.more] if self.bar.compact else [self.controls.activity]
                            boxes=[]
                            for control in visible:
                                box=QRect(control.mapTo(self.bar,QPoint()),control.size())
                                self.assertTrue(control.isVisible());self.assertTrue(self.bar.rect().contains(box))
                                self.assertFalse(any(box.intersects(other) for other in boxes));boxes.append(box)
                            self.assertGreaterEqual(self.controls.count.lineEdit().width(),self.controls.count.fontMetrics().horizontalAdvance(self.controls.count.text()))
                            self.assertEqual(self.controls.stop.width(),self.controls.stop.height())
                            self.assertEqual(self.controls.stop.text(),'');self.assertEqual(self.controls.stop.isEnabled(),active)
                            self.assertEqual(self.controls.activity_count,3 if active else 0)
                            if size==11 and width==1440:self.assertLess(self.bar.width(),380)
                            tokens=visual_tokens(self.w.state['settings'])
                            if not self.bar.compact:
                                frame=self.controls.activity.grab().toImage()
                                inks={frame.pixelColor(x,y).name() for x in range(frame.width()) for y in range(frame.height())}
                                self.assertIn(tokens['secondary'],inks)
                                self.assertGreaterEqual(contrast(tokens['secondary'],tokens['field']),4.5)
                            for control,role in ((self.controls.run_button,'run'),(self.controls.stop,'stop')):
                                frame=control.grab().toImage();background=tokens[role+'_background'] if control.isEnabled() else tokens[role+'_disabled_background']
                                self.assertEqual(frame.pixelColor(frame.width()//2,5).name(),background)
                                mode=QIcon.Mode.Normal if control.isEnabled() else QIcon.Mode.Disabled
                                glyph=control.icon().pixmap(control.iconSize(),mode).toImage()
                                # A small diagonal glyph is anti-aliased and
                                # need not contain a fully opaque pixel.
                                inks=[glyph.pixelColor(x,y).name() for x in range(glyph.width()) for y in range(glyph.height()) if glyph.pixelColor(x,y).alpha()>160]
                                expected='#ffffff' if control.isEnabled() else tokens['on_'+role+'_disabled']
                                self.assertIn(expected,inks)
                                if control.isEnabled():self.assertGreaterEqual(contrast(expected,background),4.5)
                            name=f'execution-{palette}-{size}pt-{width}-'+('active' if active else 'idle')+'.png'
                            self.bar.grab().save(str(target/name))
                            records.append(dict(palette=palette,size=size,width=width,active=active,bar=[self.bar.width(),self.bar.height()],image=name))
        self.assertEqual(self.w.canvas.data(),graph)
        (target/'EXECUTION_BAR.json').write_text(json.dumps(records,indent=2),encoding='utf-8')

    def test_actions_count_and_task_navigation_keep_existing_contracts(self):
        self.configure(active=True)
        with patch.object(self.w.comfy,'interrupt') as stop,patch.object(self.w.generation_panel,'history') as history:
            QTest.mouseClick(self.controls.run_button,Qt.MouseButton.LeftButton)
            QTest.mouseClick(self.controls.stop,Qt.MouseButton.LeftButton)
            QTest.mouseClick(self.controls.activity,Qt.MouseButton.LeftButton)
            self.run.assert_called_once();stop.assert_called_once_with();history.assert_called_once_with()
            self.controls.count.setValue(100);self.assertEqual(self.w.state['settings']['comfy_count'],100)
            self.assertEqual(self.controls.count.suffix(),' 次')
            self.assertIn('取消目前項目',self.controls.stop.toolTip())
            menus=[]
            with patch('prompt_studio.widgets.RoundMenu.open_at',lambda menu,*_:menus.append(menu)):
                self.controls.stop_menu(QPoint())
            next(a for a in menus[0].actions() if a.text().startswith('取消此工作區全部流程')).trigger()
            self.assertEqual(stop.call_args.args,(True,))
            self.configure(width=640,active=True)
            menu=self.bar.tools_menu()
            next(action for action in menu.actions() if action.text().startswith('任務紀錄')).trigger()
            self.assertEqual(history.call_count,2)
            self.assertIn('3 個活動任務',self.controls.activity.accessibleName())
        from prompt_studio import stage_model
        stage_model.add(self.w.state);self.controls.refresh()
        self.assertEqual(self.controls.count.suffix(),' 輪')
        self.assertEqual(self.controls.count.value(),100)

    def test_disconnected_controls_and_list_mode_keep_their_existing_labels(self):
        self.configure();self.w.comfy.connected=False;self.controls.refresh()
        self.assertFalse(self.controls.run_button.isEnabled());self.assertFalse(self.controls.stop.isEnabled())
        self.w.set_interface_mode('list');QTest.qWait(20)
        self.assertEqual(self.controls.stop.text(),'取消')
        self.assertFalse(self.controls.activity.detailed)
        self.w.set_interface_mode('canvas');QTest.qWait(20)
        self.assertEqual(self.controls.stop.text(),'')
        self.assertTrue(self.controls.activity.detailed)


if __name__=='__main__':unittest.main()
