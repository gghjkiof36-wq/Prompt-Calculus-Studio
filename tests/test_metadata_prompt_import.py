"""Original PNG bytes, positive-only choices and transactional material drafts."""
import copy
import json
import struct
import zlib
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QTimer, QMimeData, QUrl, QPoint, QPointF
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from prompt_calculus_studio.metadata_prompt_import import positive_prompts
from prompt_calculus_studio import materials
import test_editor_reorder as fixture


def png(path,**entries):
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))
    for key,value in entries.items():
        text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
        data+=chunk(b'iTXt',key.encode()+b'\0\0\0\0\0'+text.encode('utf-8'))
    data+=chunk(b'IDAT',zlib.compress(b'\0\xff\0\0'))+chunk(b'IEND',b'')
    path.write_bytes(data);return path


class MetadataPromptTests(unittest.TestCase):
    setUpClass=classmethod(fixture.EditorReorderTests.setUpClass.__func__)
    setUp=fixture.EditorReorderTests.setUp
    tearDown=fixture.EditorReorderTests.tearDown

    def create(self):
        dialog=self.page.new_material();dialog.name.setText('圖片組合')
        dialog.pages.type_picker.setCurrentIndex(dialog.pages.type_picker.findData('combination'))
        editor=dialog.pages.group_editor;editor.input_tabs.setCurrentIndex(1)
        self.app.processEvents();return dialog,editor

    def source(self,**entries):return png(Path(self.folder.name)/'source.png',**entries)

    def drop(self,editor,urls):
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(p)) for p in urls])
        widget=editor.direct_text.viewport()
        enter=QDragEnterEvent(QPoint(10,10),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(widget,enter);self.assertTrue(enter.isAccepted())
        event=QDropEvent(QPointF(10,10),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(widget,event);self.assertTrue(event.isAccepted())

    def test_drag_positive_prompt_save_reopen_undo_and_original_bytes(self):
        text='一位旅人, (soft light:1.2)\n  coast, blue sky'
        path=self.source(parameters=text+'\nNegative prompt: bad hands\nSteps: 20, Seed: 123')
        original=path.read_bytes()
        dialog,editor=self.create();ident=dialog.form.base['id'];before=copy.deepcopy(self.w.state)
        self.drop(editor,[path]);self.assertEqual(editor.direct_text.toPlainText(),text)
        self.assertEqual(self.w.state,before)
        editor.direct_text.undo();self.assertEqual(editor.direct_text.toPlainText(),'')
        editor.direct_text.redo();self.assertEqual(editor.direct_text.toPlainText(),text)
        dialog.save_button.click();self.app.processEvents()
        saved=copy.deepcopy(materials.item(self.w.state,ident))
        self.assertEqual(saved['composition']['combination']['text'],text)
        self.w.canvas.undo();self.assertFalse(any(i['id']==ident for i in self.w.state['items']))
        self.w.canvas.redo();self.assertEqual(materials.item(self.w.state,ident),saved)
        self.w.persist();self.assertEqual(materials.item(self.w.store.load(),ident),saved)
        reopened=self.page.edit_material(ident)
        self.assertEqual(reopened.pages.group_editor.direct_text.toPlainText(),text)
        reopened.reject();self.assertEqual(path.read_bytes(),original)

    def test_existing_text_cancel_append_replace_and_editor_undo(self):
        path=self.source(parameters='new prompt\nSteps: 10')
        dialog,editor=self.create();editor.direct_text.setPlainText('original')
        QTimer.singleShot(0,lambda:editor.image_import.active_dialog.reject())
        self.assertFalse(editor.image_import.load(path));self.assertEqual(editor.direct_text.toPlainText(),'original')
        QTimer.singleShot(0,lambda:editor.image_import.active_dialog.accept())
        self.assertTrue(editor.image_import.load(path));self.assertEqual(editor.direct_text.toPlainText(),'original\nnew prompt')
        editor.direct_text.undo();self.assertEqual(editor.direct_text.toPlainText(),'original')
        def replace():
            choice=editor.image_import.active_dialog;choice.mode.setCurrentIndex(1);choice.accept()
        QTimer.singleShot(0,replace);self.assertTrue(editor.image_import.load(path))
        self.assertEqual(editor.direct_text.toPlainText(),'new prompt')
        editor.direct_text.undo();self.assertEqual(editor.direct_text.toPlainText(),'original')
        dialog.reject()

    def test_multiple_positive_sources_require_selection_and_exclude_negative(self):
        graph={'1':{'inputs':{'text':'first'}},'2':{'inputs':{'text':'second'}},
               '3':{'inputs':{'text':'negative only'}},
               '4':{'inputs':{'positive':['1',0],'negative':['3',0]}},
               '5':{'inputs':{'positive':['2',0],'negative':['3',0]}}}
        path=self.source(prompt=graph);self.assertEqual([text for _,text in positive_prompts(path)],['first','second'])
        dialog,editor=self.create();observed=[]
        def select():
            choice=editor.image_import.active_dialog
            observed.append((choice.sources.count(),choice.apply.isEnabled(),choice.sources.currentIndex()))
            choice.sources.setCurrentIndex(1);choice.accept()
        QTimer.singleShot(0,select);self.assertTrue(editor.image_import.load(path))
        self.assertEqual(observed,[(2,False,-1)]);self.assertEqual(editor.direct_text.toPlainText(),'second');dialog.reject()

    def test_missing_corrupt_and_non_png_metadata_preserve_draft(self):
        dialog,editor=self.create();editor.direct_text.setPlainText('keep')
        path=self.source();self.assertFalse(editor.image_import.load(path))
        self.assertIn('沒有可辨識',editor.image_import_status.text())
        path.write_bytes(b'not png');self.assertFalse(editor.image_import.load(path))
        self.assertIn('PNG',editor.image_import_status.text())
        path=self.source(parameters='a positive prompt\nSteps: 10')
        data=path.read_bytes().replace(b'a positive prompt',b'b positive prompt');path.write_bytes(data)
        self.assertFalse(editor.image_import.load(path));self.assertIn('損毀',editor.image_import_status.text())
        self.drop(editor,[path,path]);self.assertIn('一次',editor.image_import_status.text())
        self.assertEqual(editor.direct_text.toPlainText(),'keep');dialog.reject()

    def test_negative_and_unclassified_text_are_never_guessed_as_positive(self):
        path=self.source(prompt={'1':{'inputs':{'text':'negative only'}},
            '2':{'inputs':{'negative':['1',0]}},'3':{'inputs':{'text':'unknown text'}}})
        with self.assertRaisesRegex(ValueError,'沒有可辨識'):positive_prompts(path)

    def test_picker_cancel_and_material_cancel_never_publish_draft(self):
        dialog,editor=self.create();before=copy.deepcopy(self.w.state)
        with patch('prompt_calculus_studio.metadata_prompt_import.QFileDialog.getOpenFileName',return_value=('','')):
            editor.image_import_button.click()
        self.assertEqual(editor.direct_text.toPlainText(),'')
        path=self.source(parameters='read only\nSteps: 10');original=path.read_bytes()
        with patch('prompt_calculus_studio.metadata_prompt_import.QFileDialog.getOpenFileName',return_value=(str(path),'PNG')):
            editor.image_import_button.click()
        self.assertEqual(editor.direct_text.toPlainText(),'read only')
        dialog.reject();self.assertEqual(self.w.state,before);self.assertEqual(path.read_bytes(),original)

    def test_changed_group_during_source_choice_rejects_stale_import(self):
        dialog,editor=self.create();editor.direct_text.setPlainText('original')
        path=self.source(parameters='image prompt\nSteps: 10')
        def stale():
            editor.direct_text.setPlainText('newer draft');editor.image_import.active_dialog.accept()
        QTimer.singleShot(0,stale);self.assertFalse(editor.image_import.load(path))
        self.assertEqual(editor.direct_text.toPlainText(),'newer draft')
        self.assertIn('已變更',editor.image_import_status.text());dialog.reject()
