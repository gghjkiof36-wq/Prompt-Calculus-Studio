"""Render only isolated synthetic data for layout review."""
import os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from prompt_studio.window import Window
from prompt_studio.dialogs import ItemDialog,WorkspaceDialog,CategoryDialog
APP=QApplication([])
for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf','consola.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
w=Window(ROOT/'qa'/'visual-060'/str(time.time_ns()))
w.state['settings'].update(material='solid',font_family='Microsoft JhengHei',ui_size=12)
a,b=w.state['items'][0],w.state['items'][2]
a.update(name='角色範例',prompt='1girl, red eyes, long hair')
b.update(name='閉眼',prompt='closed eyes',excludes=['red eyes','blindfold'])
w.state['selections']={a['module']:[a['id']],b['module']:[b['id']]}; w.state['weights']={a['id']:11}
w.comfy.request=lambda *a,**kw:None; w.comfy.connected=True; w.comfy.ready=True; w.comfy.target='正面提示詞 · #6 / text'; w.comfy.message='已連線 · '+w.comfy.target
w.catalog.put('album',dict(id='favorites',name='我的收藏')); w.state['settings']['recent_destination']='album:favorites'
w.refresh_library(); w.refresh_builder(); w.recent.refresh_destinations(); w.comfy_changed(); w.apply_theme()
w.resize(1440,940); w.show(); APP.processEvents()
w.grab().save(str(ROOT/'qa'/'desktop-060-prompt.png'))
w.tabs.setCurrentWidget(w.recent); APP.processEvents(); w.grab().save(str(ROOT/'qa'/'desktop-060-recent.png'))
w.tabs.setCurrentIndex(0)
for name,dialog in [('item',ItemDialog(w,b)),('workspace',WorkspaceDialog(w)),('category',CategoryDialog(w))]:
    dialog.show(); APP.processEvents(); dialog.grab().save(str(ROOT/'qa'/('desktop-060-'+name+'.png'))); dialog.close()
w.close(); APP.processEvents()
print('Rendered 5 isolated UI screens.')
