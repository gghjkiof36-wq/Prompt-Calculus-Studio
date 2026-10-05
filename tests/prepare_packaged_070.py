import copy,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage,QColor
from prompt_calculus_studio.core import Storage
from prompt_calculus_studio.media import Catalog,import_image
app=QApplication([]); root=ROOT/'qa'/'packaged-070'
if (root/'data'/'studio.sqlite3').exists(): raise RuntimeError('QA database already exists')
store=Storage(root/'data'); state=store.load(); first=state['items'][0]
for i,name in enumerate(('角色 A','角色 B','角色 C','角色 D','角色 E')):
    item=copy.deepcopy(first); item.update(id=f'qa-item-{i}',name=name,prompt=f'1girl, variant {i}'); state['items'].append(item)
state['settings'].update(material='solid',ui_size=11,comfy_enabled=False)
state['current_module']=first['module']; state['selections'][first['module']]=[first['id']]
store.save(state); catalog=Catalog(store); catalog.put('album',dict(id='qa-album',name='測試原圖'))
source=root/'masters'; source.mkdir(); (root/'share').mkdir()
for i in range(3):
    path=source/f'scenery-{i+1:02}.png'
    image=QImage(400+i*100,300,QImage.Format.Format_RGB32); image.fill(QColor('#677984')); image.setText('prompt','blue sky, peaceful scenery'); image.save(str(path))
    record=import_image(str(path),store.directory,'qa-album'); catalog.put('image',record,'qa-album')
store.close(); print(root/'PromptStudio.exe')
