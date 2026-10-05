"""Isolated 0.7 visual/interaction fixture. Never uses the user's database."""
import os,sys,time,json,copy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; native='--native' in sys.argv
if not native: os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QFontDatabase,QImage,QColor
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.clean_export import scan,prepare
APP=QApplication([])
for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf','consola.ttf'): QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
base=ROOT/'qa'/'visual-070'/str(time.time_ns()); base.mkdir(parents=True)
source=base/'masters'; source.mkdir(); target=base/'share'; target.mkdir()
for i in range(3):
    image=QImage(400+i*100,300,QImage.Format.Format_RGB32); image.fill(QColor('#576977')); image.setText('prompt','blue sky, peaceful scenery'); image.save(str(source/f'場景-{i+1:02}.png'))
w=Window(base/'data'); w.setWindowTitle('Prompt Studio 0.7 · 測試資料')
w.state['settings'].update(material='solid',font_family='Microsoft JhengHei',ui_size=11)
first=w.state['items'][0]; w.current_module=first['module']
for i,name in enumerate(('角色 A','角色 B','角色 C','角色 D','角色 E')):
    item=copy.deepcopy(first); item.update(id=f'qa-item-{i}',name=name,prompt=f'1girl, red eyes, variant {i}'); w.state['items'].append(item)
w.state['selections'][first['module']]=[first['id']]; w.refresh_modules(); w.refresh_library(); w.refresh_builder()
w.comfy.request=lambda *a,**kw:None; w.comfy.connected=True; w.comfy.ready=True; w.comfy.message='已連線 · 正面提示詞 #6 / text'; w.comfy_changed()
w.clean_export.inputs=[str(source)]; w.clean_export.rows=scan([source]); w.clean_export.directory.setText(str(target))
w.clean_export.plan=prepare(w.clean_export.rows,w.clean_export.values(),str(target)); w.clean_export.render(); w.clean_export.export_button.setEnabled(True)
w.apply_theme(); w.resize(1440,940); w.show(); APP.processEvents()
if native:
    def record():
        (ROOT/'qa'/'native-070-state.json').write_text(json.dumps(dict(items=[i['id'] for i in w.state['items']],selections=w.state['selections'],prompt=w.final.toPlainText()),ensure_ascii=False,indent=2),encoding='utf-8')
    timer=QTimer(); timer.timeout.connect(record); timer.start(1000); record()
    APP.exec()
else:
    w.tabs.setCurrentWidget(w.clean_export); APP.processEvents(); w.grab().save(str(ROOT/'qa'/'desktop-070-export.png'))
    w.clean_export.settings_tabs.setCurrentIndex(1); APP.processEvents(); w.grab().save(str(ROOT/'qa'/'desktop-070-naming.png'))
    w.resize(960,650); APP.processEvents(); w.grab().save(str(ROOT/'qa'/'desktop-070-small.png'))
    w.resize(1440,940); w.recent.ensure_destination(); APP.processEvents(); w.grab().save(str(ROOT/'qa'/'desktop-070-warning.png'))
    w.close(); APP.processEvents()
    print('Rendered 4 isolated 0.7 screens.')
