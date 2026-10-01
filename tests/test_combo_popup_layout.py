"""Short ComboBox geometry in the real Stage sheet; no external service."""
import copy
import json
import os
import re
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyleFactory
from stage_fixture import StageFixture,APP
from stage_parameter_fixture import add_samplers,inspection
from prompt_studio import stage_model
from prompt_studio.stage_parameter_choices import ParameterChoices
from prompt_studio.stage_parameter_panel import open_parameters


for filename in ('msjh.ttc','consola.ttf','segoeui.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+filename)


class ComboPopupLayoutTests(StageFixture):
    def setUp(self):
        super().setUp()
        original_style=APP.style().objectName();self.addCleanup(lambda:APP.setStyle(original_style))
        self.base_style=self.w.styleSheet();self.w.resize(1280,900)
        self.profile=add_samplers(copy.deepcopy(self.w.state['generation']['profiles'][0]))
        self.profile['origin']=dict(server=self.w.comfy.url,path='A.json')
        self.w.generation_panel.save_profile(self.profile)
        self.stage=stage_model.add(self.w.state,workflow=self.profile['id']);self.c.refresh()
        catalog=self.w.settings_page.workflow_manager.catalog
        catalog.loaded=True;catalog.busy=False;catalog.files=['A.json'];catalog.server=self.w.comfy.url
        self.choices=['None','Band Pass','Half Tile','Half Tile + Intersections']
        self.selected=0
        def read(path,profile,done,failed):
            current=copy.deepcopy(self.profile)
            current['graph']['35']['inputs']['sampler_name']=self.choices[self.selected]
            result=inspection(current)
            for node in result['parameters']['nodes']:
                if node['id']=='35':
                    next(f for f in node['fields'] if f['field']=='sampler_name')['choices']=list(self.choices)
            QTimer.singleShot(0,lambda:done(result,'native'))
        reader=patch.object(catalog,'read_draft',side_effect=read);reader.start();self.addCleanup(reader.stop)

    def sheet(self):
        sheet=open_parameters(self.c,self.stage);QTest.qWait(25);sheet.jump(['35']);QTest.qWait(15)
        self.assertFalse(sheet.loading,sheet.status.text())
        combo=sheet.findChild(ParameterChoices,'parameter_35_sampler_name');self.assertIsNotNone(combo)
        sheet.scroll.ensureWidgetVisible(combo);QTest.qWait(10)
        return sheet,combo

    def check_open(self,combo):
        # Do not inspect visualRect before opening: that can refresh the stale
        # layout and conceal the original first-open regression.
        QTest.mouseClick(combo,Qt.MouseButton.LeftButton);QTest.qWait(15)
        self.assertIsNone(combo._choices_popup)
        self.assertIs(APP.activePopupWidget(),combo._popup)
        view=combo.view();rects=[view.visualRect(combo.model().index(i,0)) for i in range(combo.count())]
        for previous,current in zip(rects,rects[1:]):
            self.assertEqual(current.y()-previous.y(),previous.height()+2*view.spacing())
        last=rects[-1];viewport=view.viewport().rect()
        if viewport.contains(last):
            # Item-based scrolling can leave part of one row beneath the last
            # choice; an unscrolled short list should have only its spacing.
            slack=last.height() if view.verticalScrollBar().maximum()>0 else 0
            self.assertLessEqual(viewport.height()-last.y()-last.height(),slack+2*view.spacing()+2)
        current=rects[combo.currentIndex()]
        self.assertTrue(viewport.intersects(current))
        self.assertTrue(combo.screen().availableGeometry().adjusted(-2,-2,2,2).contains(combo._popup.frameGeometry()))
        self.assertFalse(combo._popup.mask().isEmpty())
        return dict(popup=[combo._popup.width(),combo._popup.height()],viewport=[viewport.width(),viewport.height()],
                    rows=[[r.x(),r.y(),r.width(),r.height()] for r in rects])

    def test_short_popup_layout_across_styles_fonts_lengths_and_reopening(self):
        target=Path(os.environ.get('PCS_TEST_ARTIFACT_DIR',self.tmp.name));target.mkdir(parents=True,exist_ok=True)
        available={name.lower():name for name in QStyleFactory.keys()}
        styles=[available[name] for name in ('windows11','windowsvista','fusion') if name in available]
        results=[]
        for style in styles:
            APP.setStyle(style)
            for scale in (1,1.5):
                self.w.setStyleSheet(re.sub(r'font-size:(\d+)px',lambda m:'font-size:'+str(round(int(m[1])*scale))+'px',self.base_style))
                for count in (1,4,19):
                    with self.subTest(style=style,scale=scale,count=count):
                        self.choices=(['None','Band Pass','Half Tile','Half Tile + Intersections'] if count==4 else ['choice-'+str(i) for i in range(count)])
                        self.selected=count-1;sheet,combo=self.sheet();changes=[];combo.currentIndexChanged.connect(changes.append)
                        try:
                            for opening in (1,2):
                                row=self.check_open(combo)
                                self.assertEqual(combo.currentData(),self.choices[-1]);self.assertFalse(sheet.dirty());self.assertEqual(changes,[])
                                if count==4:
                                    name=f'short-popup-{style}-font-{int(scale*100)}-open-{opening}.png'
                                    self.assertTrue(combo._popup.grab().save(str(target/name)));row['image']=name
                                results.append(dict(style=style,font_scale=scale,count=count,opening=opening,**row))
                                QTest.keyClick(combo.view(),Qt.Key.Key_Escape);QTest.qWait(10)
                                self.assertEqual(combo.currentData(),self.choices[-1]);self.assertEqual(changes,[])
                        finally:
                            combo.hidePopup();sheet.finish();QTest.qWait(10)
        self.assertFalse(self.executor.submissions)
        (target/'SHORT_POPUP_LAYOUT.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')

    def test_keyboard_and_mouse_choice_preserve_value_and_apply_intent(self):
        available={name.lower():name for name in QStyleFactory.keys()}
        APP.setStyle(available.get('windows11',available['fusion']))
        self.selected=1;sheet,combo=self.sheet();self.check_open(combo)
        QTest.keyClick(combo.view(),Qt.Key.Key_Down);QTest.keyClick(combo.view(),Qt.Key.Key_Return);QTest.qWait(15)
        self.assertEqual(combo.currentData(),'Half Tile')
        self.assertEqual(sheet.proposed()['patches'][0]['value'],'Half Tile')
        self.check_open(combo);view=combo.view();target=combo.model().index(0,0)
        point=view.visualRect(target).center();QTest.mouseMove(view.viewport(),point);QTest.qWait(20)
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=point);QTest.qWait(15)
        self.assertEqual(combo.currentData(),'None');self.assertTrue(combo.hasFocus())
        self.assertEqual(sheet.proposed()['patches'][0]['value'],'None')
        self.assertNotIn('parameters',self.c.data()['stages'][self.stage]);self.assertFalse(self.executor.submissions)
        sheet.finish()
