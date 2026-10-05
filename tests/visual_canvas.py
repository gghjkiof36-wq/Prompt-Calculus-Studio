"""Synthetic Canvas review; no production database, media or backend calls."""
import os
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path[:0] = [str(ROOT/'vendor'), str(ROOT)]
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from prompt_calculus_studio.window import Window
from prompt_calculus_studio import composition as c
from prompt_calculus_studio.text_canvas import CanvasPalette

app = QApplication([])
for font in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'): QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
folder = ROOT/'qa'/'canvas-review'/str(time.time_ns()); folder.mkdir(parents=True)
w = Window(folder/'data'); w.state['settings'].update(material='solid', online=False, font_family='Microsoft JhengHei')
w.apply_theme(); w.resize(1440, 900); w.show(); app.processEvents()
w.enter_canvas(); app.processEvents(); w.grab().save(str(ROOT/'qa'/'canvas-empty.png'))
palette=CanvasPalette(w.canvas,None); palette.show(); app.processEvents(); palette.grab().save(str(ROOT/'qa'/'canvas-palette.png')); palette.reject()
mid = w.state['items'][0]['module']; next(m for m in w.state['modules'] if m['id']==mid)['mode']='multiple'
w.current_module = mid; w.state['selections'] = {}
eyes = c.node('眼睛', 'red eyes')
hair = c.node('頭髮', 'long hair')
character = c.node('長髮紅色衣服女孩', '1girl', children=[c.node('衣服','red clothes'),eyes, hair, c.node('表情', 'smile'),c.node('動作','dancing')])
w.canvas.commit(lambda s: w.canvas.add_root(s, character))
ident = next(iter(w.state['uses']))
w.canvas.commit(lambda s: w.canvas.add_root(s, c.node('背景', 'simple background',children=[c.node('白色','white background')])))
w.enter_canvas(); w.canvas.fit(); app.processEvents()
QTest.qWait(220)
w.grab().save(str(ROOT/'qa'/'canvas-root.png'))
w.canvas.open_root(ident); app.processEvents(); w.grab().save(str(ROOT/'qa'/'canvas-nested.png'))
palette=CanvasPalette(w.canvas,None); palette.show(); palette.query.setPlainText('紅色衣服'); app.processEvents()
palette.grab().save(str(ROOT/'qa'/'canvas-palette.png')); palette.reject()
w.canvas.commit(lambda s:c.usage_root(s,ident,True)['children'][1]['overlays'].append(c.node('眼罩','blindfold')))
w.canvas.path = [eyes['id']]; w.canvas.refresh(); w.canvas.fit(); app.processEvents()
w.grab().save(str(ROOT/'qa'/'canvas-overlay.png'))
w.resize(960, 650); w.canvas.fit(); app.processEvents(); QTest.qWait(220); w.grab().save(str(ROOT/'qa'/'canvas-small.png'))
w.state['settings']['ui_size'] = 18; w.apply_theme(); app.processEvents(); w.canvas.refresh(); app.processEvents(); w.canvas.fit(); QTest.qWait(220)
w.grab().save(str(ROOT/'qa'/'canvas-large-font.png'))
w.close(); app.processEvents(); print('Saved 7 Canvas review screens.')
