"""Download-style history uses per-row actions and keeps files independent."""
import base64
from datetime import datetime, timedelta
import hashlib
import os
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch
import uuid

from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton

from prompt_calculus_studio.connector_store import atomic_json
from prompt_calculus_studio.conversation_archive import canonical, load_archive
from prompt_calculus_studio.conversation_import import Destination, import_selected
from test_connector_capture import sample
import test_connector_runtime as fixture


class ConnectorHistoryInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.ConnectorInterfaceTests.setUpClass.__func__(cls)

    events = fixture.ConnectorInterfaceTests.events
    wait_job = fixture.ConnectorInterfaceTests.wait_job

    def setUp(self):
        fixture.ConnectorInterfaceTests.setUp(self)
        self.w.open_route('explore', 'connector')
        self.events()
        self.page = self.w.utility.settings_page.connector_content.load().content
        self.default_destination = self.service.settings['destination']

    def tearDown(self):
        self.assertEqual(self.service.settings['destination'], self.default_destination)
        fixture.ConnectorInterfaceTests.tearDown(self)

    def timestamp(self, operation, days=0):
        record = self.service.store.get(operation)
        record['updated_at'] = (datetime.now() - timedelta(days=days)).timestamp()
        atomic_json(self.service.store.path(operation), record)

    def saved(self, title, days=0):
        value = sample()
        value['title'] = title
        data = canonical(value)
        operation = uuid.uuid4().hex
        store = self.service.store
        store.begin(operation, len(data), hashlib.sha256(data).hexdigest())
        store.chunk(operation, 0, base64.b64encode(data).decode())
        record = store.finish(operation)
        destination = Destination.choose(self.root)
        store.accept(operation, destination)
        result = import_selected(load_archive(record['source']), (0,), destination, Event(),
                                 operation_id=operation, on_started=store.started, on_reused=store.reused)
        self.assertFalse(result.failed)
        store.complete(operation, result)
        self.timestamp(operation, days)
        return operation, store.saved_path(operation)

    def unfinished(self, title, *, state='receiving', days=0):
        operation = uuid.uuid4().hex
        record = self.service.store.begin(operation, 100, '0' * 64)
        record.update(title=title, state=state)
        if state == 'failed':
            record['message'] = '目的資料夾已移除。'
        self.service.store.put(record)
        self.timestamp(operation, days)
        return operation

    def refresh(self):
        self.service.changed.emit()
        self.events()

    def test_dates_search_inline_actions_and_removal_keep_real_saved_files(self):
        today, target = self.saved('京都四天旅行安排')
        yesterday, _ = self.saved('工程數值與表格', 1)
        older, _ = self.saved('較早的會議紀錄', 3)
        files = {p: p.read_bytes() for p in target.parent.rglob('*') if p.is_file()}
        self.refresh()
        self.assertEqual([group.text() for group in self.page.group_labels][:2], ['今天', '昨天'])
        self.assertEqual(set(self.page.rows), {today, yesterday, older})
        row = self.page.rows[today]
        self.assertIn('對話.md', row.metadata.text())
        self.assertNotIn('已儲存', row.metadata.text())
        self.assertEqual(row.location.text(), str(target.parent))
        with patch.object(self.service, 'open_saved') as opened:
            row.open_button.click()
            opened.assert_called_once_with(today)
            row.reveal_button.click()
            self.assertEqual(opened.call_args.args, (today,))
            self.assertEqual(opened.call_args.kwargs, {'reveal': True})
        with patch.object(self.service, 'open_saved', side_effect=ValueError('檔案已移除。')):
            row.reveal_button.click()
        self.assertEqual(row.error.text(), '檔案已移除。')
        self.assertTrue(row.error.isVisible())
        self.page.search.setText('工程')
        self.assertEqual(list(self.page.rows), [yesterday])
        self.page.search.setText('不存在的檔名')
        self.assertEqual(self.page.rows, {})
        self.assertTrue(self.page.empty.isVisible())
        self.page.search.clear()
        self.page.rows[today].remove_button.click()
        self.assertNotIn(today, self.page.rows)
        self.assertEqual(self.service.store.get(today)['state'], 'committed')
        self.assertTrue(self.service.store.get(today)['history_hidden'])
        self.assertEqual(self.service.store.saved_path(today), target)
        self.assertEqual({p: p.read_bytes() for p in files}, files)

    def test_visible_history_polls_transfer_progress_without_rebuilding_controls(self):
        operation = self.unfinished('正在傳送的對話')
        self.refresh()
        widget = self.page.rows[operation]
        widget.cancel_button.setFocus()
        self.assertTrue(self.page.poll.isActive())
        self.service.store.chunk(operation, 0, base64.b64encode(b'x' * 40).decode())
        # chunk deliberately emits no service.changed; the visible page polls.
        QTest.qWait(750)
        self.assertIs(self.page.rows[operation], widget)
        self.assertEqual(widget.progress.value(), 40)
        self.assertIn('40%', widget.metadata.text())
        self.assertTrue(widget.cancel_button.hasFocus())
        self.assertFalse(widget.remove_button.isVisible())
        widget.cancel_button.click()
        self.assertEqual(self.service.store.get(operation)['state'], 'cancelled')
        self.assertFalse(self.page.poll.isActive())
        self.assertTrue(self.page.rows[operation].remove_button.isVisible())
        self.assertFalse(self.page.rows[operation].progress.isVisible())

    def test_completion_is_quiet_and_no_conversation_management_controls_remain(self):
        before = set(self.w.tabs_by_key)
        with patch.object(self.w, 'open_path') as opened, patch.object(self.w, 'show_notice') as notices:
            operation, _ = self.saved('無通知的完成紀錄')
            self.refresh()
            self.assertIn(operation, self.page.rows)
            opened.assert_not_called()
            notices.assert_not_called()
        self.assertEqual(set(self.w.tabs_by_key), before)
        self.assertEqual(self.w.active_route, 'explore')
        texts = [control.text() for control in self.page.findChildren(QPushButton)]
        labels = [control.text() for control in self.page.findChildren(QLabel)]
        self.assertIn('匯入檔案…', texts)
        self.assertIn('下載紀錄', labels)
        for unwanted in ('整理對話', '開啟對話', '查看預覽', '變更…', '保存位置'):
            self.assertNotIn(unwanted, texts + labels)
        self.assertFalse(self.page.status.isVisible())

    def test_dates_states_and_row_controls_fit_wide_and_narrow_views(self):
        self.saved('京都四天旅行安排')
        self.saved('專案會議紀錄與接續事項', 1)
        self.saved('工程量測：上下標與原始數值', 3)
        receiving = self.unfinished('網頁內容擷取')
        self.service.store.chunk(receiving, 0, base64.b64encode(b'x' * 65).decode())
        self.unfinished('上次傳送', state='failed', days=1)
        self.refresh()
        for width in (1180, 760):
            self.w.resize(width, 800)
            self.events()
            for widget in self.page.rows.values():
                self.assertLessEqual(widget.geometry().right(), self.page.width())
                for control in (widget.open_button, widget.reveal_button, widget.cancel_button, widget.remove_button):
                    if control.isVisible():
                        self.assertLessEqual(control.mapTo(widget, control.rect().topRight()).x(), widget.width())
            folder = os.environ.get('PCS_CONNECTOR_HISTORY_SCREENSHOTS')
            if folder:
                Path(folder).mkdir(parents=True, exist_ok=True)
                self.assertTrue(self.w.grab().save(str(Path(folder) / f'history-{width}.png')))
        self.page.search.setText('沒有這筆')
        self.events()
        folder = os.environ.get('PCS_CONNECTOR_HISTORY_SCREENSHOTS')
        if folder:
            self.assertTrue(self.w.grab().save(str(Path(folder) / 'history-empty-search.png')))
        self.w.open_route('home')
        self.events()
        self.assertFalse(self.page.poll.isActive())


if __name__ == '__main__':
    unittest.main()
