import asyncio
import base64
import copy
import importlib
import json
import os
import sys
import tempfile
import time
import unittest
import threading
import zipfile
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor')]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt,QPoint,QMimeData,QUrl,QTimer
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from prompt_studio.core import initial_state,build_prompt,compose_details,item_prompt,validate_state,apply_workspace
from prompt_studio.exclusions import filter_tags
from prompt_studio.snapshots import make_snapshot,restore_snapshot
from prompt_studio.window import Window
from prompt_studio.dialogs import WorkspaceDialog,CategoryDialog,ItemDialog
from prompt_studio.image_drop import dropped_source,ImageDropLabel
from prompt_studio.views import weight_buttons,DETAIL_ROLE
from prompt_studio.recent import album_directory,result_id
from prompt_studio.comfy_client import local_address
from prompt_studio.backup import archive_data
from test_comfy_integration import Service,png
Bridge=importlib.import_module('integration_test.bridge').DesktopBridge
APP=QApplication.instance() or QApplication([])


class CompositionTests(unittest.TestCase):
    def test_weights_wrap_intact_prompt_and_snapshot_roundtrip(self):
        state=initial_state(); item=state['items'][0]; item['prompt']='(@na tarapisu153:1.2)'
        state['selections']={item['module']:[item['id']]}; state['weights']={item['id']:11}
        self.assertEqual(build_prompt(state),'((@na tarapisu153:1.2):1.1)')
        state['weights'][item['id']]=12; self.assertEqual(build_prompt(state),'((@na tarapisu153:1.2):1.2)')
        snap=make_snapshot(state); current=initial_state()
        self.assertEqual(build_prompt(restore_snapshot(current,snap)),snap['final_prompt'])
        state['weights'][item['id']]=10; self.assertEqual(build_prompt(state),item['prompt'])

    def test_exclusions_remove_only_exact_tags_preserving_other_attributes(self):
        state=initial_state(); character,pose=state['items'][0],state['items'][2]
        character['prompt']='1girl, red_eyes, (long hair, (blindfold:1.2):1.3), red eyeshadow'
        pose.update(prompt='closed eyes',excludes=['red eyes','blindfold'])
        state['selections']={character['module']:[character['id']],pose['module']:[pose['id']]}
        original=copy.deepcopy(state)
        text,affected=compose_details(state)
        self.assertEqual(text,'1girl, (long hair:1.3), red eyeshadow,\nclosed eyes')
        self.assertIn(character['id'],affected); self.assertEqual(state,original)
        restored=restore_snapshot(initial_state(),make_snapshot(state)); self.assertEqual(build_prompt(restored),text)
        state['selections'][pose['module']]=[]; self.assertIn('red_eyes',build_prompt(state))

    def test_escaped_names_schedules_and_substrings_are_not_rewritten(self):
        original=r'koharu \(blue archive\), [red eyes:blue eyes:0.5], <lora:eyes:1>, red eyeshadow'
        self.assertEqual(filter_tags(original,{'red eyes'}),(original,[]))
        self.assertEqual(filter_tags('((red eyes:1.2):1.1)',{'red eyes'})[0],'')
        self.assertEqual(filter_tags('(red eyes, hair:1.2)',{'red eyes'})[0],'(hair:1.2)')

    def test_workspace_fixed_weight_restore_preserves_other_selections(self):
        state=initial_state(); i,j=state['items'][0],state['items'][2]
        w=state['workspaces'][0]; w.update(fixed=[i['module']],picks={i['module']:[i['id']]},weights={i['id']:13})
        state['selections']={j['module']:[j['id']]}; state['weights']={j['id']:8}
        apply_workspace(state,w['id']); self.assertEqual(state['weights'],{i['id']:13,j['id']:8})

    def test_remote_service_address_rejected(self):
        for address in ('https://example.com','http://127.0.0.1:8188@evil.example','file:///C:/x','http://localhost:8188/path'):
            with self.assertRaises(ValueError): local_address(address)
        self.assertEqual(local_address('http://127.0.0.1:8188/'),'http://127.0.0.1:8188')


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bridge=Bridge(); self.lease=self.bridge.claim('browser','text #2')['lease']; self.snap=make_snapshot(initial_state())

    async def test_sync_coalesces_but_run_is_immutable_and_not_retried(self):
        b=self.bridge
        b.publish(self.lease,'s1','sync',self.snap)
        b.publish(self.lease,'run','run',self.snap,3)
        self.snap['state']['draft']='new desktop text'; self.snap['manual_draft']=True; self.snap['final_prompt']='new desktop text'
        b.publish(self.lease,'s2','sync',self.snap)
        self.assertEqual(b.result('s1')['state'],'superseded')
        command=await b.take('browser',self.lease)
        self.assertEqual(command['id'],'run'); self.assertEqual(command['count'],3)
        self.assertNotEqual(command['snapshot']['final_prompt'],'new desktop text')
        b.publish(self.lease,'run','run',self.snap,3)
        self.assertEqual((await b.take('browser',self.lease))['id'],'s2')
        b.acknowledge('browser',self.lease,'run',prompt_id='accepted'); self.assertEqual(b.result('run')['state'],'done')

    async def test_binding_switch_and_expiry_reject_delayed_commands(self):
        b=self.bridge; b.publish(self.lease,'run','run',self.snap)
        with self.assertRaises(ValueError): b.claim('different browser','other target')
        b.release('browser','deleted'); self.assertEqual(b.result('run')['state'],'error')
        new=b.claim('browser','new node')['lease']
        with self.assertRaises(ValueError): b.publish(self.lease,'late','run',self.snap)
        b.seen=time.monotonic()-40; self.assertFalse(b.status()['ready'])

    async def test_long_poll_wakes_for_new_command(self):
        pending=asyncio.create_task(self.bridge.take('browser',self.lease)); await asyncio.sleep(0)
        self.bridge.publish(self.lease,'wake','sync',self.snap)
        self.assertEqual((await pending)['id'],'wake')

    async def test_stop_drops_undelivered_runs(self):
        self.bridge.publish(self.lease,'run','run',self.snap,10); self.bridge.cancel_runs()
        self.assertEqual(self.bridge.result('run')['state'],'error')
        with self.assertRaises(ValueError): self.bridge.publish(self.lease,'bad','run',self.snap,101)


class DesktopUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'qa'); self.window=Window(self.tmp.name)
        self.window.state['settings']['material']='solid'; self.window.apply_theme(); self.window.show(); APP.processEvents()
    def tearDown(self):
        self.window.close(); APP.processEvents(); self.window.deleteLater(); APP.processEvents(); self.tmp.cleanup()

    def test_weight_buttons_do_not_toggle_selection(self):
        window=self.window; item=window.library.item(0); ident=item.data(Qt.ItemDataRole.UserRole)
        previous=copy.deepcopy(window.state['selections'])
        point=weight_buttons(window.library.visualItemRect(item))[1].center().toPoint()
        QTest.mouseClick(window.library.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
        self.assertEqual(window.state['selections'],previous); self.assertEqual(window.state['weights'][ident],11)

    def test_fast_whole_row_clicks_and_category_dialog_move(self):
        dialog=WorkspaceDialog(self.window); dialog.show(); APP.processEvents()
        item=dialog.fixed.item(0); old=item.checkState(); rect=dialog.fixed.visualItemRect(item); point=QPoint(rect.right()-20,rect.center().y())
        for _ in range(5): QTest.mouseClick(dialog.fixed.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point,1)
        self.assertNotEqual(item.checkState(),old)
        QTest.mouseDClick(dialog.fixed.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point,1)
        self.assertEqual(item.checkState(),old)
        dialog.close()
        category=CategoryDialog(self.window); category.show(); APP.processEvents(); category.move(80,80)
        category.list.setCurrentRow(0); first=category.names()[0]; category.move_category(1)
        self.assertEqual(category.names()[1],first); self.assertTrue(category.isModal()); category.close()

    def test_dropped_native_and_local_image_set_preview_without_file_dialog(self):
        dialog=ItemDialog(self.window,self.window.state['items'][0]); mime=QMimeData()
        image=QImage(8,8,QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.blue); mime.setImageData(image)
        dialog.preview.receive(mime); self.assertTrue(dialog.item['preview'])
        path=Path(self.tmp.name)/'fixture.png'; image.save(str(path)); mime=QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path))])
        self.assertEqual(Path(dropped_source(mime)[1][0]),path); dialog.preview.receive(mime); dialog.close()

    def test_browser_html_data_and_download_preserve_original_bytes(self):
        path=Path(self.tmp.name)/'drop.png'; png(path,{'prompt':{'test':1}}); raw=path.read_bytes()
        label=ImageDropLabel(self.window.store,keep_original=True); delivered=[]; failures=[]
        label.pathReady.connect(delivered.append); label.failed.connect(failures.append)
        mime=QMimeData(); mime.setHtml('<img src="data:image/png;base64,'+base64.b64encode(raw).decode()+'">')
        label.receive(mime); self.assertEqual(Path(delivered[-1]).read_bytes(),raw)
        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler):
                handler.send_response(200); handler.send_header('Content-Type','image/png'); handler.end_headers(); handler.wfile.write(raw)
            def log_message(*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            mime=QMimeData(); mime.setUrls([QUrl('https://example.invalid/page')])
            mime.setHtml(f'<img src="http://127.0.0.1:{server.server_port}/image.png">')
            label.receive(mime); deadline=time.monotonic()+5
            while label.reply and time.monotonic()<deadline: APP.processEvents(); time.sleep(.01)
            self.assertFalse(failures); self.assertEqual(len(delivered),2); self.assertEqual(Path(delivered[-1]).read_bytes(),raw)
        finally: server.shutdown(); server.server_close(); thread.join()

    def test_nested_album_original_is_in_portable_backup(self):
        data=self.window.store.directory; original=data/'originals'/'albums'/'one'/'image.png'
        original.parent.mkdir(parents=True); png(original,{'prompt':{'test':1}})
        with tempfile.TemporaryDirectory(dir=ROOT/'qa') as other:
            archive=Path(other)/'backup.zip'
            archive_data(data,data/'studio.sqlite3',archive)
            with zipfile.ZipFile(archive) as zipped:
                self.assertEqual(zipped.read('data/originals/albums/one/image.png'),original.read_bytes())

    def test_disconnect_discards_inflight_reply_without_reconnecting(self):
        client=self.window.comfy; seen=[]
        client.request('config',done=seen.append,failed=seen.append)
        client.disconnect(); APP.processEvents()
        self.assertEqual(seen,[]); self.assertFalse(client.connected); self.assertFalse(client.polling)

    def test_exclusion_border_and_library_reorder_leave_output_order(self):
        w=self.window; a,b=w.state['items'][0],w.state['items'][2]
        a['prompt']='red eyes, long hair'; b.update(prompt='closed eyes',excludes=['red eyes'])
        w.state['selections']={a['module']:[a['id']],b['module']:[b['id']]}
        w.refresh_library(); w.refresh_builder(); self.assertTrue(w.library.item(0).data(DETAIL_ROLE)['affected'])
        before=build_prompt(w.state); w.move_library_item(w.state['items'][1]['id'],-10000)
        self.assertEqual(build_prompt(w.state),before); self.assertIn('red eyes',w.conflict_notice.text())

    def test_connected_controls_share_count_and_fall_back_to_copy(self):
        w=self.window; w.state['draft']='manual'; w.refresh_builder()
        self.assertEqual(w.copy_button.text(),'複製完整 Prompt')
        w.comfy.ready=True; w.comfy.connected=True; w.comfy_changed()
        self.assertEqual(w.copy_button.text(),'運行')
        w.run_controls.count.setValue(3); self.assertEqual(w.run_controls.count.value(),3)
        self.assertFalse(hasattr(w.recent,'run_controls'))
        w.comfy.ready=False; w.comfy_changed(); w.copy_final()
        self.assertEqual(APP.clipboard().text(),'manual')

    def test_album_destination_and_failed_folder_never_fall_back(self):
        w=self.window; album={'id':'test-album','name':'收藏'}; w.catalog.put('album',album)
        folder=album_directory(w.store,album); self.assertTrue(folder.is_relative_to(w.store.directory/'originals'))
        album['directory']=str(Path(self.tmp.name)/'missing')
        with self.assertRaises(ValueError): album_directory(w.store,album)


if __name__=='__main__': unittest.main(verbosity=2)
