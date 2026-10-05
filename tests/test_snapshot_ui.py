import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/'vendor'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.core import build_prompt, Storage
from prompt_calculus_studio.snapshots import make_snapshot
from prompt_calculus_studio.metadata_view import readable_metadata
APP=QApplication.instance() or QApplication([])


class SnapshotUiTests(unittest.TestCase):
    def test_restore_button_keeps_current_library_and_backs_up_current_draft(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'qa') as folder:
            window=Window(folder)
            item=window.state['items'][0]
            window.state['selections']={item['module']:[item['id']]}
            snap=make_snapshot(window.state)
            window.state['draft']='keep my current draft'
            window.state['items'][0]['prompt']='new library text'
            window.gallery.record={'metadata':{'raw':{'prompt_studio':{'schema_version':1,'bindings':[
                {'node_id':'2','snapshot':snap}]}}}}
            with patch('prompt_calculus_studio.pages.ask', return_value=True): window.gallery.restore_combination()
            self.assertEqual(build_prompt(window.state), snap['final_prompt'])
            self.assertEqual(window.state['items'][0]['prompt'], 'new library text')
            self.assertEqual(window.tabs.currentIndex(),0)
            backups=list(Path(folder).glob('before-import-*.sqlite3'))
            self.assertEqual(len(backups),1)
            import sqlite3
            db=sqlite3.connect(backups[0])
            try: saved=json.loads(db.execute('select body from document').fetchone()[0])
            finally: db.close()
            self.assertEqual(saved['draft'],'keep my current draft')
            window.close()

    def test_cancel_restore_has_no_effect(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'qa') as folder:
            window=Window(folder); original=copy.deepcopy(window.state)
            window.gallery.record={'metadata':{'raw':{'prompt_studio':{'schema_version':1,'bindings':[
                {'node_id':'2','snapshot':make_snapshot(window.state)}]}}}}
            with patch('prompt_calculus_studio.pages.ask', return_value=False): window.gallery.restore_combination()
            self.assertEqual(window.state,original)
            self.assertEqual(list(Path(folder).glob('before-import-*.sqlite3')),[])
            window.close()

    def test_png_restore_has_undo_and_redo_and_keeps_empty_manual_draft(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'qa') as folder:
            window=Window(folder)
            snapshot=make_snapshot(window.state)
            window.state['draft']=''
            original=copy.deepcopy(window.state)
            window.gallery.record={'metadata':{'raw':{'prompt_studio':{'schema_version':1,'bindings':[
                {'node_id':'2','snapshot':snapshot}]}}}}
            with patch('prompt_calculus_studio.pages.ask',return_value=True):window.gallery.restore_combination()
            restored_workspace=window.state['workspace']
            self.assertNotEqual(restored_workspace,original['workspace'])
            window.canvas.undo()
            self.assertEqual(window.state['workspace'],original['workspace'])
            self.assertEqual(window.state['draft'],'')
            window.canvas.redo()
            self.assertEqual(window.state['workspace'],restored_workspace)
            window.close()

    def test_metadata_retains_latest_user_requested_heading(self):
        from prompt_calculus_studio.core import initial_state
        state=initial_state(); state['draft']='history manual'
        record={'metadata':{'raw':{'prompt_studio':{'schema_version':1,'bindings':[
            {'node_id':'2','snapshot':make_snapshot(state)}]}}}}
        original=copy.deepcopy(record)
        text=readable_metadata(record)
        self.assertTrue(text.startswith('（此為圖片的內嵌資料）'))
        self.assertIn('Prompt Calculus Studio 模組快照',text)
        self.assertNotIn('Prompt Studio 模組快照',text)
        self.assertEqual(record,original)
        self.assertIn('history manual',text)
        self.assertIn('正在使用手動版本',text)
        self.assertNotIn('手動附上的工作區建議',text)


if __name__=='__main__': unittest.main(verbosity=2)
