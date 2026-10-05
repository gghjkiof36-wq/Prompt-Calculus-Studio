"""Explore and model-library changes preserve navigation and personal edits."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtGui import QFontDatabase, QImage, QColor
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

    def test_narrow_large_type_detail_stays_reachable_and_returns_without_losing_edits(self):
        self.w.settings('models'); self.add_model(); model=self.w.models
        for palette,width,size in (('graphite',1440,11),('paper',800,18),('mist',640,18)):
            self.w.resize(width,640); self.w.state['settings'].update(visual_palette=palette,ui_size=size)
            self.w.apply_theme(preserve_layout=True); self.settle()
            self.assertEqual(self.w.width(),width)
            model.list.setCurrentRow(0); model.select(model.list.currentItem()); self.settle()
            self.assertTrue(model.detail_scroll.isVisible())
            self.assertLessEqual(model.detail.width(),model.detail_scroll.viewport().width())
            model.detail_scroll.ensureWidgetVisible(model.notes); self.settle()
            local=model.notes.mapTo(model.detail_scroll.viewport(),QPoint())
            self.assertLess(local.y(),model.detail_scroll.viewport().height())
            model.notes.setPlainText('preserved '+palette)
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
        self.assertLess(model.notes.mapTo(model.detail,QPoint()).y(),model.source_version.mapTo(model.detail,QPoint()).y())
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
        self.assertLessEqual(model.preview.height(),120)
        self.assertLessEqual(model.preview.pixmap().height(),model.preview.height())
        self.assertEqual(model.preview.pixmap().width()*2,model.preview.pixmap().height())
        self.assertEqual(model.detail_title.text(),record['name'])
        viewport=model.detail_scroll.viewport()
        first_edit=model.name.mapTo(viewport,QPoint())
        self.assertGreaterEqual(first_edit.y(),0)
        self.assertLessEqual(first_edit.y()+model.name.height(),viewport.height())
        model.notes.setPlainText('retained narrow edit'); model.list.setCurrentRow(-1); self.settle()
        self.assertEqual(model.base_filter.currentText(),'SDXL')
        self.assertEqual(self.w.catalog.get('model')['notes'],'retained narrow edit')


if __name__=='__main__':unittest.main()
