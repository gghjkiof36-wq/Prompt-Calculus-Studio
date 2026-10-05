"""Synthetic release-review screens; never opens production images."""
import os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage,QColor,QFontDatabase
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.clean_export import scan,prepare,execute
from prompt_calculus_studio.media import import_image
app=QApplication([])
for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'): QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
root=ROOT/'qa'/'visual-071'/str(time.time_ns()); root.mkdir(parents=True)
source=root/'masters'; source.mkdir(); output=root/'share'; output.mkdir()
for i in range(3):
    image=QImage(320,240,QImage.Format.Format_RGB32); image.fill(QColor('#527988')); image.setText('prompt','test scene'); image.save(str(source/f'scene-{i}.png'))
w=Window(root/'data'); w.state['settings'].update(material='solid',ui_size=11,font_family='Microsoft JhengHei'); w.apply_theme()
w.resize(1440,940); w.show(); p=w.clean_export; w.tabs.setCurrentWidget(p)
p.rows=scan([source]); p.directory.setText(str(output)); p.plan=prepare(p.rows,p.values(),str(output)); p.render()
app.processEvents(); w.grab().save(str(ROOT/'qa'/'desktop-071-pending.png'))
p.result=execute(p.plan); p.plan=None; p.result['results'][1].update(status='Failed',error='測試失敗訊息'); p.render()
app.processEvents(); w.grab().save(str(ROOT/'qa'/'desktop-071-results.png'))
w.resize(960,650); app.processEvents(); w.grab().save(str(ROOT/'qa'/'desktop-071-small.png'))
w.resize(1440,940); w.catalog.put('album',dict(id='qa-album',name='測試原圖'))
record=import_image(str(source/'scene-0.png'),w.store.directory,'qa-album'); w.catalog.put('image',record,'qa-album')
w.gallery.refresh_albums(); w.tabs.setCurrentWidget(w.gallery); app.processEvents(); w.gallery.images.setCurrentRow(0)
app.processEvents(); w.grab().save(str(ROOT/'qa'/'desktop-071-location.png'))
w.tabs.setCurrentWidget(w.models); app.processEvents(); w.grab().save(str(ROOT/'qa'/'desktop-071-models.png'))
w.close(); app.processEvents(); print('Rendered 5 synthetic review screens.')
