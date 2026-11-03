"""Native resource-list gestures use shared file transactions, never model drops."""
from pathlib import Path
import os
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.work_resources.file_drag import MIME
import test_work_resources_page as helpers


@unittest.skipUnless(os.name == 'nt', 'Windows file moves')
class ResourceFileDragTests(unittest.TestCase):
    setUpClass = classmethod(helpers.ResourceWindowTests.setUpClass.__func__)
    setUp = helpers.ResourceWindowTests.setUp
    tearDown = helpers.ResourceWindowTests.tearDown
    open_shell = helpers.ResourceWindowTests.open_shell
    close_shell = helpers.ResourceWindowTests.close_shell
    wait = helpers.ResourceWindowTests.wait
    idle = helpers.ResourceWindowTests.idle
    picture = helpers.ResourceWindowTests.picture

    def prepare(self):
        self.target = self.root/'整理'; self.target.mkdir()
        self.a, self.b = self.root/'甲.md', self.root/'乙.md'
        self.a.write_text('# 甲\n\n[乙](乙.md)', encoding='utf8')
        self.b.write_text('# 乙\n\n[甲](甲.md)', encoding='utf8')
        self.ids = {}
        for path in (self.a, self.b):
            sid = self.page.store.register(path, 'copy')
            self.page.store.rebuild(sid)
            self.ids[path.name] = sid
        self.page.enter_folder(self.root); self.idle()
        self.tree = self.page.results
        self.notices = []
        self.w.show_notice = self.notices.append

    def item(self, name):
        return next(self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(i).text(1) == name)

    def select(self, *names):
        self.tree.clearSelection()
        self.tree.setCurrentItem(self.item(names[0]))
        for name in names:
            self.item(name).setSelected(True)

    def event(self, cls, point, mime=None, origin=None):
        class NativeEvent(cls):
            def source(event):
                return origin or self.tree
        payload = mime or self.tree.file_drag.mime()
        event = NativeEvent(QPointF(point) if cls is QDropEvent else point,
                            Qt.DropAction.MoveAction, payload,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        event.payload = payload  # Qt drag events borrow, rather than own, MIME data.
        QApplication.sendEvent(self.tree.viewport(), event)
        return event

    def drop(self, name='整理', *, picture=False):
        point = self.tree.visualItemRect(self.item(name)).center()
        self.assertTrue(self.event(QDragEnterEvent, point).isAccepted())
        moved = self.event(QDragMoveEvent, point)
        if picture:
            self.assertTrue(moved.isAccepted())
            self.assertEqual(self.tree.file_drag.target_row, self.tree.indexFromItem(self.item(name)).row())
            self.picture('07-文件拖入資料夾')
        return self.event(QDropEvent, point)

    def gesture(self, operation):
        view = self.tree.viewport()
        start = self.tree.visualItemRect(self.tree.currentItem()).center()
        def execute(*_):
            return operation()
        with patch.object(QDrag, 'exec', side_effect=execute) as native_drag:
            QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=start)
            point = QPointF(start + QPoint(QApplication.startDragDistance()+12, 0))
            QApplication.sendEvent(view, QMouseEvent(QEvent.Type.MouseMove, point,
                QPointF(view.mapToGlobal(point.toPoint())), Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
            QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=start)
            native_drag.assert_called_once()
        self.idle()
        self.assertFalse(self.tree.file_drag.ops)
        self.assertFalse(self.w.commands.files.pending)
        self.assertFalse(self.tree.file_drag.scroll_timer.isActive())

    def test_pointer_multiselect_move_collision_links_draft_and_restart(self):
        self.prepare()
        (self.target/'甲.md').write_text('保留既有文件', encoding='utf8')
        self.page.open_home(self.a)
        tab = self.w.tab_for_path(self.a)
        tab.widget.md_surface.apply_mode('source')
        tab.widget.insertPlainText('未保存的編輯\n')
        self.assertIn('未保存的編輯', tab.widget.toPlainText())
        self.w.open_route('explore', 'work_resources'); self.idle()
        self.select('甲.md', '乙.md')
        self.assertFalse(self.item('甲.md').flags() & Qt.ItemFlag.ItemIsUserCheckable)
        def operation():
            self.assertEqual(len(self.tree.file_drag.ops), 2)
            self.assertTrue(self.drop(picture=True).isAccepted())
            return Qt.DropAction.MoveAction
        self.gesture(operation)
        first, second = self.target/'甲(1).md', self.target/'乙.md'
        self.assertFalse(self.a.exists()); self.assertFalse(self.b.exists())
        self.assertEqual((self.target/'甲.md').read_text(encoding='utf8'), '保留既有文件')
        self.assertEqual(tab.path, first)
        self.assertIn('未保存的編輯', tab.widget.toPlainText())
        self.assertTrue(self.w.save_tab(tab))
        self.assertIn('(乙.md)', unquote(first.read_text(encoding='utf8')))
        self.assertIn('(甲(1).md)', unquote(second.read_text(encoding='utf8')))
        for old_name, new in [('甲.md', first), ('乙.md', second)]:
            self.assertEqual(Path(self.page.store.source(self.ids[old_name])['path']), new)
            self.assertTrue(self.w.select_path(new))
        self.page.enter_folder(self.target); self.idle()
        self.assertEqual({self.tree.topLevelItem(i).text(1) for i in range(self.tree.topLevelItemCount())},
                         {'甲.md','甲(1).md','乙.md'})
        self.assertFalse(self.notices)
        self.close_shell(); self.open_shell()
        self.page.open_home(first)
        self.assertTrue(self.w.select_path(first))
        self.assertIn('未保存的編輯', self.w.tab_for_path(first).widget.toPlainText())

    def test_cancel_external_payload_nonfolder_and_changed_source_do_not_move(self):
        self.prepare()
        original = self.a.read_bytes()
        self.select('甲.md')
        self.gesture(lambda: Qt.DropAction.IgnoreAction)
        self.assertEqual(self.a.read_bytes(), original)
        self.select('甲.md')
        def invalid():
            fake = QMimeData(); fake.setData(MIME, b'foreign')
            point = self.tree.visualItemRect(self.item('整理')).center()
            self.assertFalse(self.event(QDragEnterEvent, point, fake).isAccepted())
            self.assertFalse(self.drop('乙.md').isAccepted())
            self.a.write_text('外部修改', encoding='utf8')
            self.assertFalse(self.drop().isAccepted())
            return Qt.DropAction.IgnoreAction
        self.gesture(invalid)
        self.assertEqual(self.a.read_text(encoding='utf8'), '外部修改')
        self.assertFalse((self.target/'甲.md').exists())
        self.assertTrue(any('已變更' in n for n in self.notices))
        self.page.add_source(self.docs, 'folder'); self.idle()
        self.tree = self.page.results
        self.select('時間.md')
        self.assertFalse(self.item('時間.md').flags() & Qt.ItemFlag.ItemIsDragEnabled)
        self.assertFalse(self.tree.file_drag.begin())

    def test_partial_batch_failure_keeps_remaining_file_and_reports_count(self):
        self.prepare()
        # Plain notes avoid incidental link edits in the failure injection.
        self.a.write_text('甲', encoding='utf8'); self.b.write_text('乙', encoding='utf8')
        self.select('甲.md', '乙.md')
        files = self.w.commands.files
        move = files.move
        count = 0
        def limited(op, destination):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('測試目的地寫入失敗')
            return move(op, destination)
        def operation():
            with patch.object(files, 'move', side_effect=limited):
                self.assertTrue(self.drop().isAccepted())
            return Qt.DropAction.MoveAction
        self.gesture(operation)
        self.assertEqual(len(list(self.target.glob('*.md'))), 1)
        self.assertEqual(len(list(self.root.glob('*.md'))), 1)
        self.assertTrue(any('已移動 1 項，尚有 1 項未移動' in n for n in self.notices))
