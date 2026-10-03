"""Bounded Qt checks for compact readonly fields and local scrollbar arrows."""
import copy
import re
from unittest.mock import patch
from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QLabel, QWidget, QStyle, QStyleOptionSlider
from stage_fixture import StageFixture
from stage_parameter_fixture import add_samplers, inspection
from prompt_studio import stage_model, multi_output as model
from prompt_studio.stage_parameters import empty
from prompt_studio.stage_parameter_panel import open_parameters,SeedEditor


for filename in ('msjh.ttc','consola.ttf','segoeui.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+filename)


class StageParameterReadOnlyUITests(StageFixture):
    def setUp(self):
        super().setUp()
        self.profile=add_samplers(copy.deepcopy(self.w.state['generation']['profiles'][0]))
        self.profile['origin']=dict(server=self.w.comfy.url,path='A.json')
        self.w.generation_panel.save_profile(self.profile)
        self.stage=stage_model.add(self.w.state,workflow=self.profile['id'])
        model.connect(self.w.state,self.clip,self.stage,'control');self.c.refresh()
        self.profiles={'A.json':self.profile}
        self.reason='此工作流含尚未適配的特殊序列化控制，參數僅供查看。請在 ComfyUI 檢查原生控制的能力與來源。'
        self.field_reason=''
        self.catalog=self.w.settings_page.workflow_manager.catalog
        self.catalog.loaded=True;self.catalog.busy=False;self.catalog.files=['A.json'];self.catalog.server=self.w.comfy.url
        def read(path,profile,done,failed):
            result=inspection(self.profiles[path]);description=result['parameters']
            description['projection_reason']=self.reason
            for node in description['nodes']:
                for field in node['fields']:
                    field.update(editable=False,reason=self.reason or self.field_reason)
            QTimer.singleShot(0,lambda:done(result,'native'))
        self.reader=patch.object(self.catalog,'read_draft',side_effect=read)
        self.reader.start();self.addCleanup(self.reader.stop)

    def sheet(self):
        sheet=open_parameters(self.c,self.stage);QTest.qWait(35)
        self.assertFalse(sheet.loading,sheet.status.text());sheet.jump(['35']);QTest.qWait(15)
        return sheet

    def arrow_rect(self,bar,control):
        option=QStyleOptionSlider();option.initFrom(bar);option.rect=bar.rect()
        option.orientation=Qt.Orientation.Vertical;option.minimum=bar.minimum();option.maximum=bar.maximum()
        option.sliderPosition=bar.sliderPosition();option.sliderValue=bar.value();option.pageStep=bar.pageStep();option.singleStep=bar.singleStep()
        return bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar,option,control,bar)

    def click_arrows(self,bar):
        self.assertTrue(bar.isVisible());self.assertGreater(bar.maximum(),0)
        down=self.arrow_rect(bar,QStyle.SubControl.SC_ScrollBarAddLine)
        up=self.arrow_rect(bar,QStyle.SubControl.SC_ScrollBarSubLine)
        for rect in (down,up):
            self.assertEqual(rect.width(),12);self.assertGreaterEqual(rect.height(),16)
            self.assertTrue(bar.rect().contains(rect),(bar.rect(),rect))
        bar.setValue(0)
        QTest.mouseClick(bar,Qt.MouseButton.LeftButton,pos=down.center());QTest.qWait(10)
        after=bar.value();self.assertGreater(after,0)
        QTest.mouseClick(bar,Qt.MouseButton.LeftButton,pos=up.center());QTest.qWait(10)
        self.assertLess(bar.value(),after)

    def test_workflow_reason_appears_once_and_fields_are_compact_copyable_values(self):
        sheet=self.sheet()
        labels=[widget for widget in sheet.form.findChildren(QLabel) if self.reason in widget.text()]
        self.assertEqual(len(labels),1)
        self.assertEqual(labels[0].objectName(),'StageParameterReadOnlyReason')
        self.assertEqual(labels[0].text(),self.reason)
        editor=sheet.findChild(QLineEdit,'parameter_35_cfg')
        self.assertIsNotNone(editor);self.assertTrue(editor.isReadOnly())
        self.assertIn(self.reason,editor.toolTip());self.assertIn('#35 / cfg',editor.toolTip())
        self.assertLessEqual(editor.height(),max(48,editor.fontMetrics().height()+16))
        editor.setFocus();QTest.keyClick(editor,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(editor,'99');self.assertEqual(editor.text(),'8.0')
        self.assertFalse(sheet.dirty());self.assertFalse(sheet.apply_button.isEnabled())
        self.assertEqual(sheet.status.text(),'未修改');self.assertFalse(self.executor.submissions)
        rect=sheet.panel.geometry();sheet.jump(['5']);QTest.qWait(15)
        self.assertEqual(sheet.panel.geometry(),rect)
        self.assertEqual(len([w for w in sheet.form.findChildren(QLabel) if self.reason in w.text()]),1)
        sheet.finish()

    def test_specific_field_reason_stays_in_tooltip_without_repeated_paragraphs(self):
        self.reason='';self.field_reason='此欄位的原生回呼已變更，無法確認安全回寫。'
        sheet=self.sheet()
        self.assertFalse(sheet.form.findChildren(QLabel,'StageParameterReadOnlyReason'))
        self.assertFalse([w for w in sheet.form.findChildren(QLabel) if self.field_reason in w.text()])
        for field in ('seed','steps','cfg','sampler_name','scheduler','denoise'):
            widget=sheet.findChild(QWidget,'parameter_35_'+field)
            self.assertIsNotNone(widget)
            editor=widget.text if isinstance(widget,SeedEditor) else widget
            self.assertTrue(editor.isReadOnly());self.assertIn(self.field_reason,widget.toolTip())
            if isinstance(widget,SeedEditor):self.assertFalse(widget.action.isEnabled())
        sheet.finish()

    def test_empty_saved_overrides_are_unchanged_but_workflow_selection_can_apply(self):
        self.c.commit(lambda state:state['multi_output']['stages'][self.stage].update(parameters=empty(self.profile)))
        sheet=self.sheet();self.assertFalse(sheet.dirty());self.assertFalse(sheet.apply_button.isEnabled());sheet.finish();QTest.qWait(10)
        alternative=copy.deepcopy(self.profile);alternative.update(id='alternate',frontend_id='native-alternate',name='Alternative')
        alternative['origin']['path']='B.json';self.profiles['B.json']=alternative
        self.w.generation_panel.save_profile(alternative);self.catalog.files.append('B.json')
        sheet=self.sheet();sheet.workflow.setCurrentIndex(sheet.workflow.findData('alternate'));QTest.qWait(35)
        self.assertTrue(sheet.dirty());self.assertTrue(sheet.apply_button.isEnabled())
        self.assertEqual(sheet.status.text(),'工作流選擇尚未套用')
        self.assertFalse(sheet.proposed()['patches'])
        QTest.mouseClick(sheet.apply_button,Qt.MouseButton.LeftButton);QTest.qWait(40)
        self.assertTrue(sheet.closed)
        self.assertEqual(self.c.data()['stages'][self.stage]['workflow'],'alternate')
        self.assertFalse(self.c.data()['stages'][self.stage]['parameters']['patches'])
        self.assertFalse(self.executor.submissions)

    def add_long_content(self):
        for i in range(60,100):
            self.profile['graph'][str(i)]=dict(class_type='Fixture',inputs=dict(value=i))
        for i in range(20):self.profile['graph']['35']['inputs']['extra_'+str(i)]=i

    def test_both_scrollbars_have_clickable_arrows_and_preserve_panel_geometry(self):
        self.add_long_content();self.w.resize(1280,900);QTest.qWait(15)
        sheet=self.sheet();rect=sheet.panel.geometry()
        positions=[widget.mapTo(sheet,QPoint(0,0)) for widget in (sheet.close_button,sheet.apply_button)]
        for bar in (sheet.list.verticalScrollBar(),sheet.scroll.verticalScrollBar()):
            self.click_arrows(bar);bar.setValue(bar.maximum()//2)
            thumb=self.arrow_rect(bar,QStyle.SubControl.SC_ScrollBarSlider)
            self.assertEqual(thumb.width(),12)
            QTest.mouseMove(sheet.close_button,sheet.close_button.rect().center());QTest.qWait(15)
            image=bar.grab().toImage();scale=image.devicePixelRatio()
            y=round(thumb.center().y()*scale)
            visible=[x for x in range(image.width()) if image.pixelColor(x,y).name()==sheet.tokens['scrollbar']]
            self.assertEqual(len(visible),round(6*scale),'The local rail keeps a 6 px visible thumb inside its 12 px hit area.')
            self.assertEqual(visible,list(range(round(3*scale),round(9*scale))))
        bar=sheet.scroll.verticalScrollBar();bar.setValue(bar.maximum()//2)
        thumb=self.arrow_rect(bar,QStyle.SubControl.SC_ScrollBarSlider)
        QTest.mouseMove(sheet.close_button,sheet.close_button.rect().center());QTest.qWait(15);idle=bar.grab().toImage()
        QTest.mouseMove(bar,thumb.center());QTest.qWait(15);hover=bar.grab().toImage()
        self.assertNotEqual(idle,hover)
        self.assertEqual(sheet.panel.geometry(),rect)
        self.assertEqual(positions,[widget.mapTo(sheet,QPoint(0,0)) for widget in (sheet.close_button,sheet.apply_button)])
        self.assertEqual(sheet.rail_effect.opacity(),1);self.assertFalse(self.executor.submissions);sheet.finish()

    def test_short_form_hides_idle_rail_without_changing_reserved_width(self):
        self.w.resize(1920,1080);QTest.qWait(15);sheet=self.sheet();sheet.jump(['5']);QTest.qWait(20)
        rail=sheet.scroll.verticalScrollBar();width=sheet.scroll.viewport().width()
        self.assertEqual(rail.maximum(),0);self.assertEqual(sheet.rail_effect.opacity(),0)
        self.profile['graph']['5']['inputs'].update({f'extra_{i}':i for i in range(30)})
        sheet.reload();QTest.qWait(40);sheet.jump(['5']);QTest.qWait(20)
        self.assertGreater(rail.maximum(),0);self.assertEqual(sheet.rail_effect.opacity(),1)
        self.assertEqual(sheet.scroll.viewport().width(),width);sheet.finish()

    def test_short_large_font_host_keeps_values_and_actions_reachable(self):
        self.add_long_content();host=QWidget();host.show();self.addCleanup(host.close)
        for width,height,scale in ((960,540,1.5),(640,480,2)):
            with self.subTest(client=(width,height),font_scale=scale):
                host.resize(width,height)
                host.setStyleSheet(re.sub(r'font-size:(\d+)px',lambda m:'font-size:'+str(round(int(m[1])*scale))+'px',self.w.styleSheet()))
                with patch.object(self.w,'centralWidget',return_value=host):sheet=self.sheet()
                sheet.place();QTest.qWait(20);rect=sheet.panel.geometry()
                self.assertTrue(host.rect().contains(rect))
                for action in (sheet.close_button,sheet.apply_button,sheet.reset_button):
                    self.assertTrue(rect.contains(action.rect().translated(action.mapTo(sheet,QPoint(0,0)))))
                self.assertGreaterEqual(sheet.close_button.width(),44);self.assertGreaterEqual(sheet.close_button.height(),44)
                for editor in sheet.form.findChildren(QLineEdit):
                    self.assertTrue(editor.parentWidget().rect().contains(editor.geometry()))
                    self.assertGreaterEqual(editor.height(),editor.fontMetrics().height())
                self.click_arrows(sheet.scroll.verticalScrollBar())
                sheet.toggle_nodes();QTest.qWait(10);self.click_arrows(sheet.list.verticalScrollBar())
                self.assertEqual(sheet.panel.geometry(),rect)
                sheet.finish();QTest.qWait(10)
        self.assertFalse(self.executor.submissions)
