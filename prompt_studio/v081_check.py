"""Packaged offscreen UI check with synthetic assets; never contacts a server."""
import copy,hashlib,json,traceback
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QPoint
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication,QListWidgetItem
from .civitai_assets import register_download
from .civitai_controls import DownloadDialog

def start(window):
    result={'platform':QApplication.instance().platformName(),'network':False,'gpu':False}
    # The workflow catalogue loads when its page appears, even if generation
    # is disconnected. Keep this packaged diagnostic fully offline as well.
    window.comfy.request=lambda route,data=None,done=None,**kw:done([]) if done and route.startswith('/userdata?') else None
    directory=window.store.directory
    for name in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
    def check():
        try:
            window.state['settings'].update(online=False,material='solid',reduce_motion=True); window.apply_theme()
            window.resize(1440,920); window.settings('civitai'); p=window.settings_page.civitai
            root=directory/'test-models'; target=root/'loras'; target.mkdir(parents=True)
            file=target/'test-only.safetensors'; file.write_bytes(b'not actual model weights'); stat=file.stat(); digest=hashlib.sha256(file.read_bytes()).hexdigest().upper()
            source=dict(provider='civitai',model_id=123,version_id=456,file_id=789,model_name='Light & Atmosphere',version_name='v2',model_type='LORA',base_model='Illustrious',creator='Example Artist',trained_words=['soft light'],url='https://civitai.com/models/123?modelVersionId=456',sha256=digest)
            receipt=dict(id='1'*32,root=str(root),kind='LoRA',category='未分類',size=stat.st_size,mtime=stat.st_mtime_ns,result=dict(path=str(file),sha256=digest,verification='verified',source=source))
            asset=register_download(window.catalog,receipt); window.models.root.setText(str(root))
            v=dict(id=456,modelId=123,_details_loaded=True,name='v2 · Illustrious',baseModel='Illustrious',publishedAt='2026-08-20T12:00:00Z',trainedWords=['soft light'],stats={'downloadCount':14260,'thumbsUpCount':284},files=[dict(id=789,name=file.name,hashes={'SHA256':digest},sizeKB=120000,downloadUrl='https://civitai.com/api/download/models/456')])
            parent=dict(id=123,name='Light & Atmosphere',type='LORA',creator={'username':'Example Artist'},modelVersions=[v])
            item=QListWidgetItem(p.result_text(parent)); item.setData(Qt.ItemDataRole.UserRole,parent); p.list.addItem(item); p.list.setCurrentItem(item)
            assert p.install_state.text()=='已安裝此版本'; assert p.fields['Usage Tips'].text()=='未提供'
            assert p.tabs.count()==3 and window.settings_page.comfy_tabs.count()==3
            result['search_and_details']=True
            QApplication.processEvents(); window.grab().save(str(directory/'civitai-search.png'))
            window.resize(1040,740); QApplication.processEvents()
            assert p.download.isVisible() and p.download.width()>100
            window.grab().save(str(directory/'civitai-small.png')); result['small_window_download_accessible']=True
            dialog=DownloadDialog(window,parent,v); dialog.show(); QApplication.processEvents(); dialog.grab().save(str(directory/'download-confirmation.png')); dialog.reject(); dialog.deleteLater()
            window.resize(1440,920); p.show_asset(asset); QApplication.processEvents(); window.grab().save(str(directory/'model-assets.png'))
            assert window.models.record['id']==asset['id']; result['view_registered_asset']=True
            assert window.models.base_filter.findText('Illustrious')>=0
            page=window.settings_page
            for section in ('workflows','models','nodes','civitai'):
                window.settings(section); QApplication.processEvents()
                assert page.reading.geometry().right()==page.width()-1 and page.reading.geometry().bottom()==page.height()-1
            assert page.navigation_area.isAncestorOf(page.return_button) and not page.return_button.icon().isNull()
            assert page.return_button.mapTo(page,QPoint(0,page.return_button.height())).y()<page.navigation.mapTo(page,QPoint(0,0)).y()
            page.toggle_navigation(); QApplication.processEvents(); assert page.reading.x()==0
            assert p.list.viewport().mapTo(page,QPoint(0,p.list.viewport().height())).y()==page.height()
            window.grab().save(str(directory/'settings-edge-layout.png')); result['settings_edges_and_return']=True
            window.set_interface_mode('canvas'); QApplication.processEvents(); bar=window.run_controls
            assert bar.stop.text()=='取消' and bar.stop.font().pixelSize()==bar.run_button.font().pixelSize()
            assert bar.stop.height()==bar.run_button.height()==bar.count.height()
            header=window.canvas_header.layout(); assert header.indexOf(window.canvas_settings)+1==header.indexOf(window.canvas_navigation['匯出'])
            window.grab().save(str(directory/'canvas-actions.png')); result['canvas_action_layout']=True
            window.persist(); result['saved']=True; result['error']=None
        except Exception:result['error']=traceback.format_exc()
        (directory/'v081-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        window.close(); QApplication.instance().exit(1 if result.get('error') else 0)
    QTimer.singleShot(100,check)
