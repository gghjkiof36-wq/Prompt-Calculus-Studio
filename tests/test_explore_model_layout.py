"""Explore and model-library changes preserve navigation and personal edits."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtGui import QFontDatabase, QImage, QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from prompt_calculus_studio.window import Window

APP=QApplication.instance() or QApplication([])
if APP.platformName()=='offscreen':
    for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)


class ExploreModelLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.environment=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1'}); self.environment.start()
        self.requests=patch('prompt_calculus_studio.comfy_client.ComfyClient.request'); self.requests.start()
        self.w=Window(self.temp.name); self.w.first_models=False; self.w.display_recovery.stop()
        self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.apply_theme(); self.w.resize(1440,900); self.w.show()

    def tearDown(self):
        self.w.close(); APP.processEvents(); self.requests.stop(); self.environment.stop(); self.temp.cleanup()

    def settle(self):QTest.qWait(60)

    def add_model(self):
        root=Path(self.temp.name)/'models'; root.mkdir()
        path=root/'example.safetensors'; path.write_bytes(b'fixture')
        record=dict(id='model',name='柔光人像',path=str(path),root=str(root.resolve()),kind='LoRA',size=7,
            mtime=path.stat().st_mtime_ns,thumb='',trigger='original trigger',notes='original notes',url='',base_model='SDXL')
        self.w.catalog.put('model',record,str(root.resolve()))
        self.w.models.root.setText(str(root)); self.w.models.refresh(); self.settle()
        return record

    def test_explore_entries_stay_compact_and_keep_their_destinations(self):
        self.w.settings('explore'); self.settle()
        for i,route in enumerate(('workflows','civitai')):
            card=self.w.settings_page.explore_cards.itemAt(i).widget()
            self.assertLess(card.height(),390)
            entry=next(b for b in card.findChildren(QPushButton) if b.text().startswith('開啟 '))
            with patch.object(self.w,'settings') as navigate:
                QTest.mouseClick(entry,Qt.MouseButton.LeftButton); navigate.assert_called_once_with(route)
        self.w.resize(1920,1080); self.w.settings('workflows'); self.settle()
        self.assertLessEqual(self.w.settings_page.workflows.width(),980)

    def test_empty_and_filtered_states_keep_actions_and_preserve_pending_notes(self):
        self.w.settings('models'); model=self.w.models; model.refresh(); self.settle()
        self.assertTrue(model.library_empty.isVisible()); self.assertFalse(model.filters.isVisible())
        self.assertFalse(model.import_button.isVisible())
        with patch.object(model,'choose_root') as choose:
            QTest.mouseClick(model.empty_action,Qt.MouseButton.LeftButton); choose.assert_called_once()
        record=self.add_model(); model.list.setCurrentRow(0); self.settle()
        model.notes.setPlainText('pending personal edit'); model.search.setText('missing'); self.settle()
        self.assertEqual(self.w.catalog.get(record['id'])['notes'],'pending personal edit')
        self.assertTrue(model.filters.isVisible()); self.assertTrue(model.library_empty.isVisible())
        self.assertFalse(model.detail_scroll.isVisible())
        QTest.mouseClick(model.empty_action,Qt.MouseButton.LeftButton); self.settle()
        self.assertEqual(model.search.text(),''); self.assertEqual(model.list.count(),1)
        self.assertEqual(self.w.catalog.get(record['id'])['trigger'],'original trigger')

    def test_failed_model_edit_is_cached_and_can_retry_after_dismissal(self):
        self.w.settings('models');record=self.add_model();model=self.w.models
        model.list.setCurrentRow(0);model.open_details();self.settle()
        model.notes.setPlainText('keep this draft')
        with patch.object(model.catalog,'put',side_effect=OSError('write failed')):
            model.details_editor.reject()
        self.assertFalse(model.details_editor.isVisible())
        self.assertEqual(model.draft_cache.read('model:'+record['id'])['notes'],'keep this draft')
        self.assertNotEqual(model.catalog.get(record['id'])['notes'],'keep this draft')
        model.save()
        self.assertEqual(model.catalog.get(record['id'])['notes'],'keep this draft')
        self.assertIsNone(model.draft_cache.read('model:'+record['id']))

    def test_failed_preview_removal_preserves_current_image_without_modal(self):
        self.w.settings('models');record=self.add_model();model=self.w.models
        record['thumb']='previews/retained.png'
        model.catalog.put('model',record,record['root'])
        model.refresh();model.list.setCurrentRow(0)
        with patch.object(model.catalog,'put',side_effect=OSError('write failed')),patch.object(self.w,'error') as modal:
            model.clear_preview()
        modal.assert_not_called()
        self.assertEqual(model.record['thumb'],record['thumb'])
        self.assertEqual(model.catalog.get(record['id'])['thumb'],record['thumb'])

    def test_preview_regions_and_copy_use_current_draft(self):
        self.w.settings('models');self.add_model();model=self.w.models
        model.list.setCurrentRow(0);model.open_details();self.settle()
        model.trigger.setPlainText('edited trigger')
        QTest.mouseClick(model.copy_trigger,Qt.MouseButton.LeftButton)
        self.assertEqual(APP.clipboard().text(),'edited trigger')
        model.trigger.clear();self.assertFalse(model.copy_trigger.isEnabled())
        picture=QPixmap(80,80);picture.fill(QColor('#567a82'));model.preview.setPixmap(picture)
        width,height=model.preview.width(),model.preview.height();split=height*2//3
        with patch.object(model,'choose_image') as choose,patch.object(model,'clear_preview') as remove:
            for point in (QPoint(2,2),QPoint(width-3,split-1),QPoint(width//2,height*3//5)):
                QTest.mouseClick(model.preview,Qt.MouseButton.LeftButton,pos=point)
            self.assertEqual(choose.call_count,3);remove.assert_not_called()
            for point in (QPoint(2,split),QPoint(width-3,height-3)):
                QTest.mouseClick(model.preview,Qt.MouseButton.LeftButton,pos=point)
            self.assertEqual(choose.call_count,3);self.assertEqual(remove.call_count,2)
            model.preview.setPixmap(QPixmap())
            QTest.mouseClick(model.preview,Qt.MouseButton.LeftButton,pos=QPoint(width//2,height-3))
            self.assertEqual(choose.call_count,4);self.assertEqual(remove.call_count,2)

    def test_narrow_large_type_detail_stays_reachable_and_returns_without_losing_edits(self):
        self.w.settings('models'); self.add_model(); model=self.w.models
        for palette,width,size in (('graphite',1440,11),('paper',800,18),('mist',640,18)):
            self.w.resize(width,640); self.w.state['settings'].update(visual_palette=palette,ui_size=size)
            self.w.apply_theme(preserve_layout=True); self.settle()
            self.assertEqual(self.w.width(),width)
            model.list.setCurrentRow(0); model.select(model.list.currentItem()); self.settle()
            self.assertTrue(model.detail_scroll.isVisible())
            self.assertLessEqual(model.detail.width(),model.detail_scroll.viewport().width())
            self.assertFalse(model.name.isVisible())
            QTest.mouseClick(model.details_button,Qt.MouseButton.LeftButton); self.settle()
            content=model.details_editor.tabs.widget(0)
            local=model.notes.mapTo(content,model.notes.rect().bottomRight())
            self.assertLess(local.y(),content.height())
            self.assertGreater(model.notes.height(),model.notes.fontMetrics().height())
            model.notes.setPlainText('preserved '+palette)
            QTest.keyClick(model.notes,Qt.Key.Key_Escape);self.settle()
            self.assertFalse(model.details_editor.isVisible())
            model.list.setCurrentRow(-1); self.settle()
            self.assertFalse(model.detail_scroll.isVisible()); self.assertTrue(model.library_panel.isVisible())
            self.assertEqual(self.w.catalog.get('model')['notes'],'preserved '+palette)
            self.assertGreater(model.root_summary.width(),0)

    def test_model_columns_and_detail_groups_preserve_source_and_personal_fields(self):
        from prompt_calculus_studio.model_library import model_row_columns
        self.w.resize(1680,1000); self.w.settings('models'); record=self.add_model(); model=self.w.models
        record.update(name='超長名稱_保留完整模型識別_'+('範例_'*12),civitai_status='matched',creator='Example author',
            civitai={'model_type':'LORA','version_name':'Version one','trained_words':['source trigger'],'url':'https://civitai.com/models/1'},
            url='https://example.invalid/personal')
        self.w.catalog.put('model',record,record['root']); model.refresh(); self.settle()
        self.assertLessEqual(model.root_panel.height(),40)
        self.assertEqual(model.filter_line.direction().name,'LeftToRight')
        row=model.list.visualItemRect(model.list.item(0))
        self.assertGreaterEqual(row.height(),88); self.assertLessEqual(row.height(),96)
        self.assertEqual(set(model_row_columns(row)),{'preview','name','type','status'})
        self.assertGreater(model.count.mapTo(model,QPoint()).y(),model.search.mapTo(model,QPoint()).y())
        model.list.setCurrentRow(0); self.settle()
        self.assertEqual(model.detail_title.text(),record['name'])
        self.assertLessEqual(model.detail_scroll.width(),400)
        self.assertLessEqual(abs(model.detail.mapTo(model,QPoint()).y()-model.list.mapTo(model,QPoint()).y()),4)
        self.assertFalse(model.name.isVisible())
        model.open_details();self.settle()
        self.assertTrue(model.name.isVisible());self.assertFalse(model.source_version.isVisible())
        model.details_editor.tabs.setCurrentIndex(1);self.settle()
        self.assertTrue(model.source_version.isVisible());self.assertFalse(model.name.isVisible())
        self.assertEqual(model.details_editor.tabs.count(),2)
        self.assertTrue(model.path.isVisible());self.assertIn(record['path'],model.path.text())
        before=dict(self.w.catalog.get('model')['civitai'])
        model.trigger.setPlainText('personal trigger'); model.notes.setPlainText('personal notes'); model.save()
        saved=self.w.catalog.get('model')
        self.assertEqual(saved['civitai'],before); self.assertEqual(saved['url'],'https://example.invalid/personal')
        self.assertEqual(saved['trigger'],'personal trigger'); self.assertEqual(saved['notes'],'personal notes')
        self.assertEqual(model.list.currentItem().data(Qt.ItemDataRole.UserRole)['trigger'],'personal trigger')
        for width,expected in ((680,{'preview','name','status'}),(480,{'preview','name'})):
            columns=model_row_columns(QRect(0,0,width,92)); self.assertEqual(set(columns),expected)
            ordered=sorted(columns.values(),key=lambda r:r.left())
            self.assertTrue(all(a.right()<b.left() for a,b in zip(ordered,ordered[1:])))

    def test_short_windows_keep_filter_values_and_first_edit_field_visible(self):
        self.w.settings('models'); record=self.add_model(); model=self.w.models
        record['name']='00 柔光人像工作室_長名稱與來源版本辨識_保留完整檔案名稱測試'
        from prompt_calculus_studio.media import thumbnail
        picture=QImage(100,200,QImage.Format.Format_RGB32); picture.fill(QColor('#526f74'))
        preview=Path(self.temp.name)/'preview.png'; picture.save(str(preview))
        record['thumb']=thumbnail(preview,self.temp.name)
        self.w.catalog.put('model',record,record['root']); model.refresh()
        self.w.state['settings'].update(ui_size=18); self.w.apply_theme(preserve_layout=True)
        self.w.resize(800,640); self.settle()
        self.assertTrue(model.header_count.isVisible()); self.assertFalse(model.results_header.isVisible())
        self.assertEqual(model.base_filter.itemText(0),'底模')
        self.assertIn('Base Model',model.base_filter.accessibleName())
        model.base_filter.setCurrentText('SDXL'); self.settle()
        self.assertEqual(model.list.count(),1)
        self.assertLess(model.base_filter.fontMetrics().horizontalAdvance(model.base_filter.currentText()),model.base_filter.width()-40)
        self.assertIn('SDXL',model.base_filter.toolTip())
        self.w.resize(640,480); model.list.setCurrentRow(0); self.settle()
        self.assertTrue(model.summary_preview.isVisible())
        self.assertLessEqual(model.summary_preview.pixmap().height(),model.summary_preview.height())
        self.assertEqual(model.summary_preview.pixmap().width()*2,model.summary_preview.pixmap().height())
        self.assertEqual(model.detail_title.text(),record['name'])
        viewport=model.detail_scroll.viewport()
        viewport_scroll=model.detail_scroll
        viewport_scroll.ensureWidgetVisible(model.details_button);self.settle()
        action=model.details_button.mapTo(viewport,QPoint())
        self.assertGreaterEqual(action.y(),0)
        self.assertLessEqual(action.y()+model.details_button.height(),viewport.height())
        model.open_details();self.settle()
        content=model.details_editor.tabs.widget(0)
        first_edit=model.name.mapTo(content,QPoint())
        self.assertGreaterEqual(first_edit.y(),0)
        self.assertLessEqual(first_edit.y()+model.name.height(),content.height())
        model.notes.setPlainText('retained narrow edit'); model.list.setCurrentRow(-1); self.settle()
        self.assertEqual(model.base_filter.currentText(),'SDXL')
        self.assertEqual(self.w.catalog.get('model')['notes'],'retained narrow edit')


if __name__=='__main__':unittest.main()
