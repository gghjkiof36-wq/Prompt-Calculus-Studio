"""Visual system checks on isolated native Qt controls, without a PCS Window."""
import copy
import os
from pathlib import Path
import unittest

from PySide6.QtCore import QFile, QPoint, QSize, Qt, qInstallMessageHandler
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QLabel, QLineEdit,
    QListWidget, QPushButton, QSpinBox, QVBoxLayout,
)

from prompt_calculus_studio.core import DEFAULT_SETTINGS
from prompt_calculus_studio.theme import font_pixels, stylesheet, visual_tokens, widget_palette, shell_color
from prompt_calculus_studio.ui_icons import ALIASES, ICON_NAMES, icon, stylesheet_icon_paths


APP=QApplication.instance() or QApplication([])
# The Windows offscreen plugin has no system font database. Load a real font
# for readable artifacts without changing production font or global settings.
if not QFontDatabase.families() and Path('C:/Windows/Fonts/msjh.ttc').is_file():
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')


def contrast(a,b):
    def luminance(text):
        values=[int(text[i:i+2],16)/255 for i in (1,3,5)]
        linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in values]
        return sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))
    values=sorted((luminance(a),luminance(b)))
    return (values[1]+.05)/(values[0]+.05)


class VisualThemeTests(unittest.TestCase):
    def make_panel(self,palette='graphite',size=11):
        settings={**DEFAULT_SETTINGS,'visual_palette':palette,'ui_size':size,'material':'solid'}
        panel=QFrame(); panel.setObjectName('ContentSurface'); panel.resize(760,680)
        panel.setPalette(widget_palette(settings)); panel.setStyleSheet(stylesheet(settings))
        body=QVBoxLayout(panel); body.setContentsMargins(24,24,24,24)
        heading=QLabel('介面個人化 · Personalization'); heading.setObjectName('Heading'); body.addWidget(heading)
        grid=QGridLayout(); body.addLayout(grid)
        field=QLineEdit('我的工作區'); grid.addWidget(QLabel('工作區名稱'),0,0); grid.addWidget(field,0,1)
        combo=QComboBox(); combo.addItems(('中性石墨','霧藍','暖紙白')); grid.addWidget(QLabel('色彩風格'),1,0); grid.addWidget(combo,1,1)
        spin=QSpinBox(); spin.setValue(18); grid.addWidget(QLabel('介面字級'),2,0); grid.addWidget(spin,2,1)
        check=QCheckBox('清除手動內容前先詢問'); check.setChecked(True); body.addWidget(check)
        nav=QPushButton('探索'); nav.setObjectName('Navigation'); nav.setCheckable(True); nav.setChecked(True)
        nav.setIcon(icon('explore',visual_tokens(settings)['text'])); body.addWidget(nav)
        primary=QPushButton('套用設定'); primary.setObjectName('Primary'); body.addWidget(primary)
        listing=QListWidget(); listing.addItems(('連線與工作流','模型資產','節點套件')); listing.setCurrentRow(1); body.addWidget(listing)
        panel.show(); QTest.qWait(20)
        self.addCleanup(panel.close)
        return panel,field,combo,spin,check,nav,primary,listing

    def test_tokens_preserve_old_settings_and_independent_copies(self):
        settings=copy.deepcopy(DEFAULT_SETTINGS); settings.pop('visual_palette',None)
        before=copy.deepcopy(settings)
        self.assertEqual(visual_tokens(settings)['palette'],'graphite')
        first=visual_tokens(settings); first['text']='#000000'
        self.assertNotEqual(first['text'],visual_tokens(settings)['text'])
        self.assertEqual(settings,before)
        self.assertEqual(visual_tokens({'visual_palette':'unknown'})['palette'],'graphite')

    def test_reading_and_primary_colors_across_palettes(self):
        for palette in ('graphite','mist','paper'):
            for accent in ('neutral','blue','green'):
                t=visual_tokens({'visual_palette':palette,'accent':accent})
                with self.subTest(palette=palette,accent=accent):
                    for text in ('text','secondary','muted','success','warning','error','info'):
                        for background in ('base','surface','field'):
                            self.assertGreaterEqual(contrast(t[text],t[background]),4.5,(text,background))
                    self.assertGreaterEqual(contrast(t['accent'],t['on_accent']),4.5)

    def test_material_alpha_and_font_sizes_remain_independent(self):
        settings={**DEFAULT_SETTINGS,'material':'mica','mica_transparency':20,'acrylic_transparency':80,'ui_size':18,'prompt_size':12,'density':'compact'}
        before=copy.deepcopy(settings)
        mica=stylesheet(settings)
        acrylic=stylesheet({**settings,'material':'acrylic'})
        self.assertEqual('rgba(32,37,39,244)',shell_color(settings))
        self.assertEqual('rgba(32,37,39,211)',shell_color({**settings,'material':'acrylic'}))
        self.assertIn(f'font-size:{font_pixels(18)}px',mica)
        self.assertIn(f'font-size:{font_pixels(12)}px',mica)
        self.assertEqual(settings,before)

    def test_native_controls_keep_values_readable_fonts_and_opaque_surfaces(self):
        messages=[]
        previous=qInstallMessageHandler(lambda kind,context,message: messages.append(message))
        try:
            for palette in ('graphite','mist','paper'):
                for size in (11,18):
                    panel,field,combo,spin,check,nav,primary,listing=self.make_panel(palette,size)
                    with self.subTest(palette=palette,size=size):
                        self.assertEqual(field.font().pixelSize(),font_pixels(size))
                        self.assertEqual(field.text(),'我的工作區')
                        self.assertTrue(check.isChecked()); self.assertTrue(nav.isChecked())
                        self.assertEqual(listing.currentRow(),1)
                        image=panel.grab().toImage()
                        self.assertEqual(image.pixelColor(30,30).alpha(),255)
                        self.assertEqual(field.palette().color(QPalette.ColorRole.Text).name(),visual_tokens({'visual_palette':palette})['text'])
                        folder=os.environ.get('PCS_VISUAL_QA_DIR')
                        if folder and size == 11:
                            Path(folder).mkdir(parents=True,exist_ok=True)
                            image.save(str(Path(folder)/f'theme-{palette}.png'))
                    panel.close()
            self.assertFalse([m for m in messages if 'stylesheet' in m.lower() or 'unknown property' in m.lower()],messages)
        finally: qInstallMessageHandler(previous)

    def test_native_combo_keyboard_and_checkbox_label_still_work(self):
        for palette in ('graphite','paper'):
            panel,field,combo,spin,check,*_=self.make_panel(palette)
            combo.setFocus(); QTest.keyClick(combo,Qt.Key.Key_Down)
            self.assertEqual(combo.currentIndex(),1)
            QTest.mouseClick(check,Qt.MouseButton.LeftButton,pos=QPoint(55,check.height()//2))
            self.assertFalse(check.isChecked())
            spin.setFocus(); QTest.keyClick(spin,Qt.Key.Key_Up); self.assertEqual(spin.value(),19)
            panel.close()

    def test_icons_render_without_fonts_at_multiple_device_ratios(self):
        for name in sorted(ICON_NAMES|ALIASES.keys()):
            with self.subTest(icon=name):
                asset=icon(name,'#292b29',20)
                self.assertFalse(asset.isNull())
                for mode in (QIcon.Mode.Normal,QIcon.Mode.Disabled):
                    image=asset.pixmap(20,20,mode).toImage()
                    self.assertTrue(any(image.pixelColor(x,y).alpha()>0 for y in range(image.height()) for x in range(image.width())))
                scaled=asset.pixmap(QSize(20,20),2.0)
                self.assertEqual(scaled.size(),QSize(40,40))
                self.assertEqual(scaled.devicePixelRatio(),2.0)
        panel=QFrame(); panel.setStyleSheet('background:white'); grid=QGridLayout(panel)
        for index,name in enumerate(sorted(ICON_NAMES)):
            button=QPushButton(name); button.setIcon(icon(name,'#292b29',24)); grid.addWidget(button,index//5,index%5)
        panel.show(); APP.processEvents()
        folder=os.environ.get('PCS_VISUAL_QA_DIR')
        if folder:
            Path(folder).mkdir(parents=True,exist_ok=True); panel.grab().save(str(Path(folder)/'icons.png'))
        panel.close()

    def test_existing_auto_color_icons_follow_palette_and_qss_assets_resolve(self):
        previous=APP.palette()
        previous_accent=APP.property('pcsIconOnAccent')
        dynamic=icon('search'); fixed=icon('search','#336699')
        primary=icon('play','on-accent')
        try:
            APP.setPalette(widget_palette({'visual_palette':'graphite'}))
            APP.setProperty('pcsIconOnAccent',visual_tokens()['on_accent'])
            dark=dynamic.pixmap(24,24).toImage(); fixed_dark=fixed.pixmap(24,24).toImage()
            primary_dark=primary.pixmap(24,24).toImage()
            APP.setPalette(widget_palette({'visual_palette':'paper'}))
            APP.setProperty('pcsIconOnAccent',visual_tokens({'visual_palette':'paper'})['on_accent'])
            light=dynamic.pixmap(24,24).toImage()
            self.assertNotEqual(dark,light)
            self.assertNotEqual(primary_dark,primary.pixmap(24,24).toImage())
            self.assertEqual(fixed_dark,fixed.pixmap(24,24).toImage())
            for palette in ('graphite','mist','paper'):
                for name,path in stylesheet_icon_paths(visual_tokens({'visual_palette':palette})).items():
                    with self.subTest(palette=palette,asset=name):
                        resource=QFile(path)
                        self.assertTrue(resource.open(QFile.OpenModeFlag.ReadOnly))
                        data=bytes(resource.readAll()); resource.close()
                        self.assertIn(b'<svg',data)
                        self.assertFalse(QIcon(path).pixmap(16,16).isNull())
        finally:
            APP.setPalette(previous)
            APP.setProperty('pcsIconOnAccent',previous_accent)


if __name__ == '__main__': unittest.main()
