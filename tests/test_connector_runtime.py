"""Transfer acceptance, crash reconciliation and reachable Qt import/lookup."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import subprocess
from threading import Event
import unittest
from unittest.mock import patch
import uuid

from prompt_calculus_studio.conversation_archive import canonical, load_archive
from prompt_calculus_studio.conversation_import import Destination, import_selected
from prompt_calculus_studio.connector_store import TransferStore
from test_connector_capture import sample


def upload(store, value=None):
    value = sample() if value is None else value
    data, operation = canonical(value), uuid.uuid4().hex
    store.begin(operation, len(data), hashlib.sha256(data).hexdigest())
    for index, offset in enumerate(range(0, len(data), 64)):
        store.chunk(operation, index, base64.b64encode(data[offset:offset+64]).decode())
    return operation, store.finish(operation)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.base = Path(self.area.name)
        self.root = self.base / 'files'
        self.root.mkdir()
        self.store = TransferStore(self.base / 'private')

    def test_receive_preview_accept_and_replay_share_one_result(self):
        operation, record = upload(self.store)
        self.assertEqual(record['state'], 'previewing')
        self.assertEqual(list(self.root.iterdir()), [])
        destination = Destination.choose(self.root)
        self.store.accept(operation, destination)
        result = import_selected(load_archive(record['source']), (0,), destination, Event(),
                                 operation_id=operation, on_started=self.store.started)
        self.store.complete(operation, result)
        self.assertEqual(self.store.saved_path(operation), result.completed[0])
        self.assertEqual(self.store.finish(operation)['state'], 'committed')
        self.assertEqual(self.store.begin(operation, record['size'], record['sha256'])['state'], 'committed')
        self.assertEqual(len(list(self.root.iterdir())), 1)
        with self.assertRaises(ValueError):
            self.store.begin(operation, record['size'], '0' * 64)

    def test_writer_success_before_ledger_ack_is_recovered_without_rewrite(self):
        operation, record = upload(self.store)
        destination = Destination.choose(self.root)
        self.store.accept(operation, destination)
        result = import_selected(load_archive(record['source']), (0,), destination, Event(),
                                 operation_id=operation, on_started=self.store.started)
        target = result.completed[0]
        target.write_text('user edit', encoding='utf-8')
        with patch('prompt_calculus_studio.connector_store.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.complete(operation, result)
        self.assertEqual(self.store.get(operation)['state'], 'committing')
        restarted = TransferStore(self.store.directory)
        restarted.recover()
        self.assertEqual(restarted.get(operation)['state'], 'committed')
        self.assertEqual(target.read_text(encoding='utf-8'), 'user edit')

    def test_cancel_and_short_transfer_never_publish(self):
        data = canonical(sample())
        operation = uuid.uuid4().hex
        self.store.begin(operation, len(data), hashlib.sha256(data).hexdigest())
        self.store.chunk(operation, 0, base64.b64encode(data[:20]).decode())
        with self.assertRaises(ValueError):
            self.store.finish(operation)
        self.store.cancel(operation)
        with self.assertRaises(ValueError):
            self.store.chunk(operation, 1, base64.b64encode(data[20:]).decode())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unknown_crash_and_replaced_destination_are_not_success(self):
        operation, _ = upload(self.store)
        destination = Destination.choose(self.root)
        self.store.accept(operation, destination)
        self.store.recover()
        self.assertEqual(self.store.get(operation)['state'], 'unknown')
        operation, record = upload(self.store)
        self.store.accept(operation, destination)
        result = import_selected(load_archive(record['source']), (0,), destination, Event(),
                                 operation_id=operation, on_started=self.store.started)
        self.store.complete(operation, result)
        self.root.rename(self.base / 'original')
        self.root.mkdir()
        with self.assertRaises(ValueError):
            self.store.saved_path(operation)


# The UI fixture is deliberately offscreen and uses only synthetic conversations.
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
import test_workspace_panes as fixture


class ConnectorInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.WorkspacePaneTests.setUpClass.__func__(cls)
        from PySide6.QtGui import QFont, QFontDatabase
        for font in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
            path = Path('C:/Windows/Fonts') / font
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
        cls.app.setFont(QFont('Microsoft JhengHei UI', 10))

    events = fixture.WorkspacePaneTests.events

    def setUp(self):
        self.private = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, PCS_CONNECTOR_DISCOVERY=str(Path(self.private.name) / 'instances'))
        self.env.start()
        fixture.WorkspacePaneTests.setUp(self)
        self.service = self.w.chrome_connector
        self.service.set_enabled(True)

    def tearDown(self):
        for sheet in list(self.service.dialogs.values()):
            if isValid(sheet):
                sheet.reject()
                self.wait_job(sheet)
        fixture.WorkspacePaneTests.tearDown(self)
        self.env.stop()
        self.private.cleanup()

    def wait_job(self, sheet):
        for _ in range(1000):
            self.app.processEvents()
            time.sleep(.01)
            if not isValid(sheet) or sheet.job is None:
                self.events()
                return
        import faulthandler
        faulthandler.dump_traceback()
        self.fail(f'background transfer did not finish: kind={sheet.job_kind}, status={sheet.status.text()}, errors={self.errors}, ledger={self.service.store.records()}')

    def screenshot(self, widget, name):
        directory = os.environ.get('PCS_CONNECTOR_SCREENSHOTS')
        if directory:
            self.events()
            self.assertTrue(widget.grab().save(str(Path(directory) / name)))

    def transfer(self, value=None):
        if value is None:
            value = sample()
            value['title'] = '京都四天旅行'
            value['messages'] = [
                dict(id='dom-1', role='user', content='安排四天京都旅行，兩位大人，住在京都車站附近。\n每天最多兩個主要景點，留時間逛街和喝咖啡。'),
                dict(id='dom-2', role='assistant', content='第一天｜京都車站周邊\n抵達後先寄放行李，下午逛東本願寺，晚上在車站附近吃飯。\n\n第二天｜清水寺與東山\n早上清水寺，下午二年坂、三年坂散步。'),
                dict(id='dom-3', role='user', content='第三天安排嵐山，最後一天中午前回到車站。')]
        data = canonical(value)
        operation = uuid.uuid4().hex
        self.service.dispatch(dict(command='begin', operation_id=operation, size=len(data), sha256=hashlib.sha256(data).hexdigest()))
        self.service.dispatch(dict(command='chunk', operation_id=operation, index=0, data=base64.b64encode(data).decode()))
        self.service.dispatch(dict(command='finish', operation_id=operation))
        self.events()
        dialog = self.service.dialogs[operation]
        self.wait_job(dialog)
        self.assertIsNotNone(dialog.archive, dialog.status.text())
        return operation, dialog

    def off_thread(self, function):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(function)
            deadline = time.monotonic() + 25
            while not future.done() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            self.assertTrue(future.done(), 'native bridge did not reply before deadline')
            return future.result()

    @unittest.skipUnless(os.name == 'nt', 'native Windows named pipe')
    def test_native_host_to_qt_receive_save_and_status(self):
        from tools.connector_host import ConnectorHost, pipe_request
        host = ConnectorHost(self.service.directory)
        def request(command, **fields):
            value = dict(request_id=uuid.uuid4().hex, command=command, **fields)
            response = self.off_thread(lambda: host.handle(value))
            self.assertEqual(response['request_id'], value['request_id'])
            self.assertTrue(response['ok'], response)
            return response['result']
        live = request('hello')
        self.assertEqual(live['instances'][0]['instance_id'], self.service.instance_id)
        self.assertEqual(live['instances'][0]['display_name'], self.root.name)
        self.assertIn('save_destination_v1', live['instances'][0]['capabilities'])
        denied = self.off_thread(lambda: pipe_request(self.service.pipe_name, dict(
            request_id=uuid.uuid4().hex, token='wrong-token', command='hello')))
        self.assertFalse(denied['ok'])
        capture_path = os.environ.get('PCS_CONNECTOR_CAPTURE')
        value = json.loads(Path(capture_path).read_text(encoding='utf-8')) if capture_path else sample()
        data, operation = canonical(value), uuid.uuid4().hex
        fields = dict(instance_id=self.service.instance_id, operation_id=operation)
        self.assertEqual(request('begin', size=len(data), sha256=hashlib.sha256(data).hexdigest(), **fields)['state'], 'receiving')
        request('chunk', index=0, data=base64.b64encode(data).decode(), **fields)
        self.assertEqual(request('finish', **fields)['state'], 'previewing')
        self.events()
        sheet = self.service.dialogs[operation]
        self.wait_job(sheet)
        sheet.import_button.click()
        self.wait_job(sheet)
        result = request('status', **fields)
        self.assertEqual(result['state'], 'committed')
        self.assertTrue(Path(result['path']).is_file())
        saved = json.loads((Path(result['path']).parent / '原始對話.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['pcs_capture']['message_ids'], [message['id'] for message in value['messages']])
        for message in value['messages']:
            if message.get('resources'):
                self.assertEqual(saved['mapping'][message['id']]['message']['metadata']['pcs_capture_resources'], message['resources'])
        self.assertFalse(self.service.writing)
        late = uuid.uuid4().hex
        fields['operation_id'] = late
        self.assertEqual(request('cancel', **fields)['state'], 'cancelled')
        self.assertEqual(request('begin', size=len(data), sha256=hashlib.sha256(data).hexdigest(), **fields)['state'], 'cancelled')

    def test_rich_capture_compact_confirmation_and_saved_source(self):
        value = sample()
        value['title'] = '旅遊資料整理 · 範例'
        value['messages'] = [
            dict(id='question', role='user', content='整理這次旅行的預算與行程，附上來源。'),
            dict(id='answer', role='assistant', content=(
                '## 旅行預算\n\n| 項目 | 金額 |\n| --- | ---: |\n| 交通 | 2,400 元 |\n| 餐飲 | 3,600 元 |\n\n'
                '### 行程\n\n1. 第一日：抵達與入住\n2. 第二日：市區散步\n\n'
                '[查看交通資訊](https://example.org/travel)\n\n```python\ntotal = 2400 + 3600\n```'),
                resources=[dict(kind='chart', label='預算分布', url=None),
                           dict(kind='attachment', label='行程.pdf', url=None)])]
        operation, sheet = self.transfer(value)
        from PySide6.QtWidgets import QTextBrowser
        self.assertEqual(sheet.findChildren(QTextBrowser), [])
        self.assertEqual(sheet.source_name.text(), value['title'])
        self.assertLess(sheet.height(), 380)
        self.screenshot(sheet, 'pcs-legacy-save-location.png')
        sheet.import_button.click()
        self.wait_job(sheet)
        path = self.service.store.saved_path(operation)
        self.assertIn('| 交通 | 2,400 元 |', path.read_text(encoding='utf-8-sig'))
        original = json.loads((path.parent / '原始對話.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(original['mapping']['answer']['message']['metadata']['pcs_capture_resources'][0]['kind'], 'chart')

    @unittest.skipUnless(os.environ.get('PCS_CONNECTOR_HOST'), 'packaged host supplied separately')
    def test_packaged_native_host_uses_actual_qt_pipe(self):
        from tools.connector_host import ALLOWED_ORIGIN, read_frame, write_frame
        request = dict(request_id=uuid.uuid4().hex, command='hello')
        outgoing = io.BytesIO()
        write_frame(outgoing, request)
        result = self.off_thread(lambda: subprocess.run(
            [os.environ['PCS_CONNECTOR_HOST'], ALLOWED_ORIGIN], input=outgoing.getvalue(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b'')
        incoming = io.BytesIO(result.stdout)
        response = read_frame(incoming)
        self.assertTrue(response['ok'], response)
        self.assertEqual(response['result']['instances'][0]['instance_id'], self.service.instance_id)
        self.assertIsNone(read_frame(incoming))

    def test_malformed_settings_cannot_break_workspace_startup(self):
        from prompt_calculus_studio.connector_service import ConnectorService
        self.service.stop()
        self.service.settings_path.write_text('[]', encoding='utf-8')
        other = ConnectorService(self.w)
        self.assertFalse(other.ready)
        self.assertTrue(other.error)
        self.service.settings_path.write_text('{}', encoding='utf-8')
        endpoint = self.service.directory / (self.service.instance_id + '.json')
        endpoint.write_text('[]', encoding='utf-8')
        other.stop()
        endpoint.unlink()
        other.deleteLater()

    def test_compact_save_closes_without_review_or_opening_document(self):
        self.w.resize(720, 640)
        self.w.move(20, 20)
        self.events()
        operation, dialog = self.transfer()
        before_tabs = set(self.w.tabs_by_key)
        center = self.w.mapToGlobal(self.w.rect().center())
        self.assertLessEqual(abs(dialog.frameGeometry().center().x() - center.x()), 4,
                             (dialog.frameGeometry(), self.w.frameGeometry(), dialog.screen().availableGeometry()))
        self.assertLessEqual(abs(dialog.frameGeometry().center().y() - center.y()), 4,
                             (dialog.frameGeometry(), self.w.frameGeometry(), dialog.screen().availableGeometry()))
        self.assertFalse(hasattr(dialog, 'arrange_button'))
        dialog.import_button.click()
        self.wait_job(dialog)
        self.assertEqual(self.service.store.get(operation)['state'], 'committed')
        self.assertTrue(not isValid(dialog) or not dialog.isVisible())
        self.assertEqual(set(self.w.tabs_by_key), before_tabs)
        self.assertFalse(getattr(self.w, '_retained_sheets', []))
        self.service.open_saved(operation)
        target = self.service.store.saved_path(operation)
        self.assertTrue(target.exists())
        self.assertEqual(sum(tab.path == target for tab in self.w.tabs_by_key.values()), 1)
        self.w.open_route('explore', 'connector')
        self.events()
        page = self.w.utility.settings_page.connector_content.load().content
        self.assertEqual(self.w.active_route, 'explore')
        self.assertEqual(page.items.count(), 1)
        self.screenshot(self.w, 'pcs-conversations.png')
        self.assertEqual(page.selected(), operation)
        page.search.setText('no matching conversation')
        self.assertIsNone(page.selected())
        self.assertFalse(page.open_button.isVisible())
        page.search.clear()
        self.assertEqual(page.selected(), operation)
        self.assertTrue(page.open_button.isVisible())

    def test_close_during_published_write_keeps_completion_owner_alive(self):
        operation, dialog = self.transfer()
        written, release = Event(), Event()
        complete = self.service.store.complete

        def hold_completion(*args, **kwargs):
            written.set()
            if not release.wait(5):
                raise TimeoutError('test completion gate timed out')
            return complete(*args, **kwargs)

        with patch.object(self.service.store, 'complete', side_effect=hold_completion):
            dialog.import_button.click()
            try:
                deadline = time.monotonic() + 4
                while not written.is_set() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.01)
                self.assertTrue(written.is_set())
                dialog.close()
                self.events()
                self.assertTrue(isValid(dialog))
                self.assertIn(operation, self.service.writing)
            finally:
                release.set()
            self.wait_job(dialog)
        self.assertFalse(self.service.writing)
        self.assertEqual(self.service.store.get(operation)['state'], 'committed')
        self.assertTrue(self.service.store.saved_path(operation).is_file())

    def test_close_veto_resumes_connection_and_preview_cancel_has_no_output(self):
        operation, dialog = self.transfer()
        with patch.object(self.w, 'prepare_close', return_value=False):
            event = QCloseEvent()
            self.w.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertTrue(self.service.ready)
        dialog.reject()
        self.events()
        self.assertEqual(self.service.store.get(operation)['state'], 'cancelled')
        self.assertEqual(list(self.root.glob('*/匯入完成.json')), [])


if __name__ == '__main__':
    unittest.main()
