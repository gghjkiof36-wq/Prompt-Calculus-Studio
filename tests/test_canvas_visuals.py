"""Theme and viewport changes must not rewrite an active Canvas document."""
import copy,os,tempfile,unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase,QFontMetricsF,QImage,QPainter,QColor
from PySide6.QtCore import QPoint,QPointF,QRect,Qt
from PySide6.QtTest import QTest
from prompt_calculus_studio.core import Storage
from prompt_calculus_studio.canvas_starter import guide
from prompt_calculus_studio.theme import visual_tokens


class CanvasVisualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)

    def setUp(self):
        from prompt_calculus_studio.window import Window
        self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_DATA':self.tmp.name})
        self.env.start();self.w=Window(self.tmp.name);self.w.state['settings'].update(online=False,material='solid')
        self.w.apply_theme();self.w.resize(1280,800);self.w.show();self.w.enter_canvas();QTest.qWait(60)
        self.canvas=self.w.canvas;self.refs=guide(self.w.state)

    def tearDown(self):
        self.w.close();self.app.processEvents();self.env.stop();self.tmp.cleanup()

    def test_live_light_dark_theme_preserves_items_draft_selection_and_view(self):
        from prompt_calculus_studio.drafts import edit
        key=self.refs['output'];self.canvas.commit(lambda state:edit(state,'',key));QTest.qWait(30)
        card=self.canvas.outputs[key];card.setSelected(True);self.canvas.onboarding.focus(key)
        graph=copy.deepcopy(self.canvas.data());positions=copy.deepcopy(self.w.state['text_positions'])
        transform=self.canvas.view.transform();center=self.canvas.view.mapToScene(self.canvas.view.viewport().rect().center())
        for name in ('paper','graphite','mist'):
            self.w.state['settings']['visual_palette']=name
            self.canvas.refresh_visual_theme()
            self.assertIs(self.canvas.outputs[key],card);self.assertTrue(card.isSelected())
            self.assertEqual(self.canvas.data(),graph);self.assertEqual(self.w.state['text_positions'],positions)
            self.assertEqual(self.canvas.view.transform(),transform)
            self.assertLess((self.canvas.view.mapToScene(self.canvas.view.viewport().rect().center())-center).manhattanLength(),2)
            colors=visual_tokens(self.w.state['settings'])
            self.assertEqual(self.canvas.view.backgroundBrush().color().name(),colors['base'])
            surface=QImage(int(card.width),int(card.height),QImage.Format.Format_ARGB32);surface.fill(Qt.GlobalColor.transparent)
            painter=QPainter(surface);card.paint(painter,None);painter.end()
            self.assertEqual(surface.pixelColor(8,60).name(),colors.get('canvas_body',colors['surface']))
            self.assertEqual(self.canvas.data()['outputs'][key]['draft'],'')

    def test_visible_zoom_controls_keep_saved_graph_and_work_after_guide_dismissal(self):
        self.canvas.onboarding.dismiss();QTest.qWait(20)
        graph=copy.deepcopy(self.canvas.data());positions=copy.deepcopy(self.w.state['text_positions'])
        controls=self.canvas.zoom_controls
        self.assertTrue(controls.isVisible())
        controls.overview.click();QTest.qWait(10);small=self.canvas.view.transform().m11()
        controls.value.click();self.assertEqual(self.canvas.view.transform().m11(),1)
        controls.plus.click();self.assertGreater(self.canvas.view.transform().m11(),1)
        controls.minus.click();self.assertAlmostEqual(self.canvas.view.transform().m11(),1)
        self.assertLess(small,1);self.assertEqual(self.canvas.data(),graph);self.assertEqual(self.w.state['text_positions'],positions)
        self.assertTrue(self.canvas.view.viewport().rect().contains(controls.geometry()))

    def test_paper_modules_distinguish_header_body_and_embedded_fields(self):
        document=copy.deepcopy(self.canvas.data());history=copy.deepcopy(self.canvas.undo_stack)
        for palette in ('paper','graphite','mist'):
            self.w.state['settings']['visual_palette']=palette;self.w.apply_theme(preserve_layout=True);QTest.qWait(20)
            colors=visual_tokens(self.w.state['settings'])
            cards=(self.canvas.containers[self.refs['canvas']],self.canvas.outputs[self.refs['output']],
                   self.canvas.clips[self.refs['clip']],self.canvas.flow_cards[self.refs['stage']])
            for card in cards:
                image=QImage(int(card.width),int(card.height),QImage.Format.Format_ARGB32)
                image.fill(Qt.GlobalColor.transparent);painter=QPainter(image);card.paint(painter,None);painter.end()
                self.assertEqual(image.pixelColor(8,30).name(),colors['canvas_header'])
                self.assertEqual(image.pixelColor(8,80).name(),colors['canvas_body'])
            panel=self.canvas.outputs[self.refs['output']].panel
            self.assertEqual(panel.grab().toImage().pixelColor(1,1).name(),colors['canvas_body'])
            field=panel.editor.viewport().grab().toImage()
            self.assertEqual(field.pixelColor(20,field.height()-5).name(),colors['canvas_field'])
            if palette=='paper':
                self.assertEqual(len({colors[key] for key in ('canvas_header','canvas_body','canvas_field')}),3)
            self.assertEqual(self.canvas.data(),document);self.assertEqual(self.canvas.undo_stack,history)

    def test_compact_scheduler_pause_keeps_underlying_pause_resume_behavior(self):
        panel=self.canvas.flow_cards[self.refs['scheduler']].panel
        store=panel.runner.store;workspace=self.w.state['workspace'];key=self.refs['scheduler']
        self.assertFalse(store.scheduler_paused(workspace,key));self.assertTrue(panel.empty.isVisible())
        panel.pause_button.click();self.assertTrue(store.scheduler_paused(workspace,key));self.assertEqual(panel.pause_button.text(),'繼續')
        panel.pause_button.click();self.assertFalse(store.scheduler_paused(workspace,key));self.assertEqual(panel.pause_button.text(),'暫停')

    def test_setup_emphasis_follows_binding_then_stage_without_mutating_document(self):
        from prompt_calculus_studio import clip_flow
        from prompt_calculus_studio.stage_model import choose
        from prompt_calculus_studio.generation import store_profile
        from prompt_calculus_studio.drafts import edit
        from test_multi_output import workflow
        self.canvas.onboarding.dismiss()
        clip=self.canvas.clips[self.refs['clip']].panel;stage=self.canvas.flow_cards[self.refs['stage']].panel
        clip.refresh();stage.refresh()
        self.assertEqual(clip.setup_status.text(),'待設定')
        self.assertTrue(clip.binding.property('setupCurrent'))
        self.assertFalse(stage.edit_button.property('setupCurrent'))
        self.assertEqual(clip.binding.objectName(),'Primary')
        self.assertEqual(stage.edit_button.objectName(),'Primary')
        with patch.object(self.canvas,'bind_dialog') as bind:
            clip.binding.click();bind.assert_called_once_with(self.refs['clip'])
        def bind(state):
            store_profile(state,workflow());clip_flow.set_binding(state,'flow',self.refs['clip'],('6','text'))
            edit(state,'',self.refs['output'])
        self.assertTrue(self.canvas.commit(bind));QTest.qWait(20)
        self.assertEqual(clip.setup_status.text(),'已綁定')
        self.assertFalse(clip.binding.property('setupCurrent'))
        self.assertTrue(stage.edit_button.property('setupCurrent'))
        with patch('prompt_calculus_studio.stage_parameter_panel.open_parameters') as params:
            stage.edit_button.click();params.assert_called_once_with(self.canvas,self.refs['stage'])
        self.assertTrue(self.canvas.commit(lambda s:choose(s,self.refs['stage'],'flow')));QTest.qWait(20)
        self.assertFalse(stage.edit_button.property('setupPending'))
        self.assertNotEqual(stage.edit_button.objectName(),'Primary')
        self.assertEqual(stage.edit_button.text(),'調整參數')
        document=copy.deepcopy(self.canvas.data());history=copy.deepcopy(self.canvas.undo_stack)
        transform=self.canvas.view.transform()
        for palette in ('paper','graphite'):
            self.w.state['settings']['visual_palette']=palette;self.canvas.refresh_visual_theme()
            self.assertEqual(self.canvas.data(),document);self.assertEqual(self.canvas.undo_stack,history)
            self.assertEqual(self.canvas.view.transform(),transform)
            self.assertEqual(self.canvas.data()['outputs'][self.refs['output']]['draft'],'')
            self.assertFalse(clip.binding.property('setupCurrent'));self.assertFalse(stage.edit_button.property('setupCurrent'))
        self.canvas.undo();QTest.qWait(20)
        self.assertTrue(stage.edit_button.property('setupCurrent'))
        self.assertEqual(self.canvas.data()['outputs'][self.refs['output']]['draft'],'')

    def test_stale_binding_is_pending_and_does_not_silently_repair_it(self):
        from prompt_calculus_studio.canvas_onboarding import pending_setup
        from prompt_calculus_studio import clip_flow
        from prompt_calculus_studio.generation import store_profile
        from test_multi_output import workflow
        def bind(state):
            store_profile(state,workflow());clip_flow.set_binding(state,'flow',self.refs['clip'],('6','text'))
        self.assertTrue(self.canvas.commit(bind));QTest.qWait(20)
        self.w.state['generation']['profiles'][0]['graph']['6']['inputs']['text']=['7',0]
        document=copy.deepcopy(self.w.state)
        self.canvas.clips[self.refs['clip']].panel.refresh()
        self.assertEqual(pending_setup(self.w.state)[0],('clip_inputs',self.refs['clip']))
        self.assertEqual(self.canvas.clips[self.refs['clip']].panel.binding.text(),'重新綁定 CLIP')
        self.assertEqual(self.w.state,document)

    def test_light_port_captions_are_deeper_without_recoloring_wires(self):
        from prompt_calculus_studio.flow_items import wire_color,caption_color
        def light(color):
            values=[color.redF(),color.greenF(),color.blueF()]
            return sum(c*w for c,w in zip([v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in values],(.2126,.7152,.0722)))
        ports=[p for p in self.canvas.ports.values() if p.kind=='image']
        self.assertTrue(ports)
        # Derived palette roles use the same 4.5:1 text rule as the rest of
        # the UI, on actual module surfaces rather than an unrelated white card.
        for palette in ('paper','mist','graphite'):
            self.w.state['settings']['visual_palette']=palette;self.canvas.refresh_visual_theme()
            colors=visual_tokens(self.w.state['settings'])
            for port in ports:
                self.assertEqual(port.caption.brush().color(),caption_color(self.canvas,'image'))
                for role in ('canvas_body','canvas_header'):
                    a,b=sorted((light(QColor(colors[role])),light(port.caption.brush().color())))
                    self.assertGreaterEqual((b+.05)/(a+.05),4.5)
                if palette=='paper':self.assertNotEqual(port.caption.brush().color(),wire_color(self.canvas,'image'))
        self.w.state['settings']['visual_palette']='graphite';self.canvas.refresh_visual_theme()
        for port in ports:self.assertEqual(port.caption.brush().color(),wire_color(self.canvas,'image'))

    def test_execution_hint_explains_setup_without_changing_run_eligibility(self):
        from prompt_calculus_studio import clip_flow
        from prompt_calculus_studio.generation import store_profile
        from prompt_calculus_studio.stage_model import choose
        from test_multi_output import workflow
        bar=self.canvas.execution_bar;client=self.w.comfy
        self.assertTrue(bar.status.isVisible())
        self.assertNotIn('尚未連線 ComfyUI',bar.status.text())
        self.w.run_controls.refresh();bar.update_progress()
        self.assertNotIn('尚未連線 ComfyUI',bar.status.text())
        self.assertIn('CLIP 待綁定',bar.status.text())
        self.assertFalse(self.w.run_controls.run_button.isEnabled())
        def configure(state):
            store_profile(state,workflow());clip_flow.set_binding(state,'flow',self.refs['clip'],('6','text'))
            choose(state,self.refs['stage'],'flow')
        self.canvas.commit(configure)
        client.connected=client.native_supported=client.flow_supported=True
        self.w.run_controls.refresh();enabled=self.w.run_controls.run_button.isEnabled()
        document=copy.deepcopy(self.w.state);bar.update_progress()
        self.assertFalse(bar.status.isVisible());self.assertEqual(self.w.state,document)
        self.assertEqual(self.w.run_controls.run_button.isEnabled(),enabled)
        client.running=1;client.input_flow.last_status['executing_node']='6';bar.update_progress()
        self.assertEqual(bar.status.text(),'正在生成 · 節點 #6')

    def test_small_canvas_execution_actions_and_zoom_do_not_overlap(self):
        self.canvas.onboarding.dismiss()
        document=copy.deepcopy(self.canvas.data())
        bar=self.canvas.execution_bar;controls=self.w.run_controls
        eligibility=(controls.run_button.isEnabled(),controls.stop.isEnabled())
        actions=(controls.count,controls.run_button,controls.stop,bar.more)
        for size in (11,18):
            self.w.state['settings']['ui_size']=size
            self.w.apply_theme(preserve_layout=True);self.w.resize(640,480)
            QTest.qWait(40);bar.update_progress();QTest.qWait(20)
            self.assertTrue(self.canvas.view.viewport().rect().contains(bar.geometry()))
            self.assertLessEqual(bar.minimumSizeHint().width(),self.canvas.view.viewport().width()-24)
            rectangles=[]
            for action in actions:
                self.assertTrue(action.isVisible())
                rectangle=QRect(action.mapTo(bar,QPoint(0,0)),action.size())
                self.assertTrue(bar.rect().contains(rectangle))
                self.assertFalse(any(rectangle.intersects(other) for other in rectangles))
                rectangles.append(rectangle)
            self.assertLessEqual(bar.height(),96)
            self.assertTrue(self.canvas.zoom_controls.isHidden())
            self.assertTrue(controls.activity.isHidden())
            self.assertFalse(hasattr(self.canvas,'recent_corner'))
            self.assertEqual((controls.run_button.isEnabled(),controls.stop.isEnabled()),eligibility)
            self.assertEqual(self.canvas.data(),document)
        self.assertTrue(controls.compact)
        self.assertIn('生成紀錄',controls.activity.accessibleName())
        # A taller status can appear without any viewport resize.
        self.w.comfy.running=1
        self.w.comfy.input_flow.last_status['executing_node']='A long workflow node identifier '*3
        bar.update_progress();QTest.qWait(20)
        self.assertLessEqual(bar.height(),96)
        self.assertIn('A long workflow node identifier',bar.status.toolTip())
        self.assertEqual(bar.status.toolTip(),bar.status.accessibleName())
        self.assertTrue(self.canvas.view.viewport().rect().contains(bar.geometry()))
        self.w.resize(1280,800);QTest.qWait(40)
        self.assertFalse(controls.compact)
        self.assertFalse(self.canvas.zoom_controls.isHidden())
        self.assertFalse(controls.activity.isHidden());self.assertTrue(bar.more.isHidden())
        self.assertIn('個活動任務',controls.activity.text())
        self.assertFalse(hasattr(self.canvas,'recent_corner'))

    def test_compact_more_keeps_zoom_history_without_duplicate_recent(self):
        self.canvas.onboarding.dismiss();self.w.resize(640,480);QTest.qWait(40)
        bar=self.canvas.execution_bar;self.assertTrue(bar.more.isVisible())
        graph=copy.deepcopy(self.canvas.data());history=copy.deepcopy(self.canvas.undo_stack)
        with patch.object(self.w.generation_panel,'history') as tasks,patch.object(self.w,'show_recent_sheet') as recent:
            menu=bar.tools_menu();actions={action.text():action for action in menu.actions() if not action.isSeparator()}
            self.assertEqual(len(actions),5)
            self.assertNotIn('最近生成',actions)
            actions['原始大小（100%）'].trigger();self.assertEqual(self.canvas.view.transform().m11(),1)
            actions['放大畫布'].trigger();self.assertGreater(self.canvas.view.transform().m11(),1)
            actions['縮小畫布'].trigger();self.assertAlmostEqual(self.canvas.view.transform().m11(),1)
            actions['流程總覽'].trigger();self.assertLess(self.canvas.view.transform().m11(),1)
            next(action for text,action in actions.items() if text.startswith('任務紀錄')).trigger();tasks.assert_called_once_with()
            recent.assert_not_called()
            menu.deleteLater()
        self.assertEqual(self.canvas.data(),graph);self.assertEqual(self.canvas.undo_stack,history)
        with patch('prompt_calculus_studio.clip_widgets.RoundMenu.open_at') as open_menu:
            bar.more.click();open_menu.assert_called_once()

    def test_setup_badges_fit_when_interface_font_is_large(self):
        class BadgePainter(QPainter):
            def __init__(self,image):
                super().__init__(image);self.badges=[]
            def drawText(self,*args):
                if len(args)==3 and args[-1] in ('待設定','已綁定'):
                    bounds,_,text=args;metrics=QFontMetricsF(self.font(),self.device())
                    self.badges.append((self.font().pointSizeF(),metrics.horizontalAdvance(text),metrics.height(),bounds))
                return super().drawText(*args)
        self.w.state['settings']['ui_size']=18
        self.w.apply_theme(preserve_layout=True)
        cards=(self.canvas.clips[self.refs['clip']],self.canvas.flow_cards[self.refs['stage']])
        for card in cards:
            surface=QImage(int(card.width),int(card.height),QImage.Format.Format_ARGB32)
            surface.fill(Qt.GlobalColor.transparent)
            painter=BadgePainter(surface);card.paint(painter,None);painter.end()
            self.assertEqual(len(painter.badges),1)
            points,width,height,bounds=painter.badges[0]
            self.assertLessEqual(points,11)
            self.assertLessEqual(width,bounds.width()-8)
            self.assertLessEqual(height,bounds.height())


if __name__=='__main__':unittest.main()
