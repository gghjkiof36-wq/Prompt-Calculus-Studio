"""Frozen Windows UI checks using a new diagnostic database, never user data."""
import json,os
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QFileDialog,QLineEdit
from PySide6.QtGui import QImage
from .media import import_image


def start(window):
    def stage(name): (window.store.directory/'interface-progress.txt').write_text(name,encoding='utf-8')
    stage('start')
    window.state['settings'].update(online=False,material='solid'); window.apply_theme()
    def check():
        result={}
        try:
            stage('open-settings')
            window.start_interface(); assert window.surface_stack.currentWidget() is window.welcome
            window.set_interface_mode('canvas'); window.canvas.add_tag('landscape, soft light')
            window.final.setPlainText('preserved manual prompt'); QApplication.processEvents()
            window.settings('workflows'); settings=window.settings_page
            assert settings.pages.currentWidget() is settings.comfy_content
            assert settings.comfy_content.isAncestorOf(window.models)
            result['unified_comfy_settings']=True
            observed=[]
            def cancel():
                dialog=QApplication.activeModalWidget()
                observed.append(isinstance(dialog,QFileDialog) and dialog.parentWidget() is window)
                if dialog is not None: dialog.reject()
            QTimer.singleShot(150,cancel); window.generation_panel.import_workflow()
            stage('import-cancelled')
            assert observed==[True] and window.isVisible()
            assert window.final.toPlainText()=='preserved manual prompt'
            result['import_cancel_preserves_window']=True
            actual=os.environ.get('PROMPT_STUDIO_QA_WORKFLOW')
            if actual:
                file=Path(actual); picker_info=[]
                def choose_actual():
                    dialog=QApplication.activeModalWidget()
                    if not isinstance(dialog,QFileDialog): picker_info.append(False); return
                    picker_info.append(any(url.toLocalFile().lower().startswith(file.drive.lower()) for url in dialog.sidebarUrls()))
                    dialog.setDirectory(str(file.parent))
                    def select_file():
                        field=dialog.findChild(QLineEdit,'fileNameEdit')
                        if field: field.setText(str(file))
                        else: dialog.selectFile(str(file))
                        stage('file-selected: '+repr(dialog.selectedFiles()))
                        dialog.accept()
                    QTimer.singleShot(650,select_file)
                QTimer.singleShot(180,choose_actual)
                stage('import-d-drive')
                profile=window.generation_panel.import_workflow()
                stage('imported-d-drive')
                assert picker_info==[True] and profile['mode']=='img2img'
                assert profile['prompt']==['6','text'] and profile['image']=='8'
                assert window.final.toPlainText()=='preserved manual prompt'
                result['native_d_drive_import']=True
            settings.cancel(); QApplication.processEvents()
            stage('preview-and-puzzle')
            path=window.store.directory/'preview.png'; image=QImage(128,96,QImage.Format.Format_RGB32); image.fill(0xff405b70); image.save(str(path))
            record=import_image(path,window.store.directory,''); window.catalog.put('recent',record)
            window.canvas.results.refresh(); window.canvas.fit(); QApplication.processEvents()
            assert window.canvas.results.record['id']==record['id'] and not window.canvas.results.preview.icon().isNull()
            assert window.canvas.preview_card.proxy.widget() is window.canvas.results
            window.canvas.functions.add_image(path=path,attached=True); QApplication.processEvents()
            attached=next(k for k,v in window.canvas.functions.data()['images'].items() if v.get('attached'))
            card=window.canvas.functions.cards[attached]
            assert card.y()+card.height==window.canvas.output.y()+window.canvas.output.height
            window.generation_panel.mode.setCurrentIndex(0)
            assert window.canvas.functions.data()['images'][attached]['attached']
            result['puzzle_preserved_in_txt2img']=True
            window.canvas.fit(); transform=window.canvas.view.transform()
            window.show_page(window.recent); QApplication.processEvents()
            sheet=window.recent_sheet; assert sheet.isAncestorOf(window.recent) and window.canvas.isVisible() and window.recent.images.isVisible()
            assert sheet.width()<window.width() and sheet.height()<window.height()
            sheet.grab().save(str(window.store.directory/'recent-sheet.png')); sheet.reject(); QApplication.processEvents()
            assert window.canvas.view.transform()==transform
            result['floating_recent_preserves_canvas']=True
            window.notice('已完成'); QApplication.processEvents(); assert window.canvas_status.width()<200
            result.update(ok=True,canvas_preview=True,notice_fits_text=True,interface=window.state['settings']['interface_mode'])
            window.grab().save(str(window.store.directory/'interface-smoke.png'))
        except Exception as exc: result.update(ok=False,error=str(exc))
        (window.store.directory/'interface-smoke.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        window.close()
    QTimer.singleShot(500,check)
