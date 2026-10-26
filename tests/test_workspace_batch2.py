"""Second-batch UI regressions: direct deletion, library imports and file drops."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QPointF, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from prompt_calculus_studio.canvas_palette import CanvasPalette
from prompt_calculus_studio.local_documents import io, assets, library, schema
from prompt_calculus_studio.workspace_locations import path_key
from prompt_calculus_studio.workspace_move_menu import MoveMenu
from prompt_calculus_studio.widgets import RoundMenu
from test_workspace_file_interactions import Enter, Move, Drop
import test_workspace_shell as fixture


class WorkspaceBatch2Tests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    setUp = fixture.WorkspaceShellTests.setUp
    events = fixture.WorkspaceShellTests.events

    def tearDown(self):
        for palette in getattr(self, 'palettes', []):
            if not palette.closed:
                palette.reject()
        self.w.tree.file_drag.finish()
        fixture.WorkspaceShellTests.tearDown(self)

    def context(self, path):
        return self.w.commands.context('tree', self.w.path_items[path_key(path)])

    def test_empty_folder_delete_releases_move_menu_and_stale_rename(self):
        path = self.root/'Folder'
        context = self.context(path)
        menu = RoundMenu(self.w)
        moving = MoveMenu(self.w.commands.files, menu, context)
        moving.start()
        stale = moving.op
        recycled = self.root.parent/'recycled'
        with patch.object(self.w.commands, 'move_to_trash', side_effect=lambda p: p.rename(recycled)), \
                patch.object(QDialog, 'exec', side_effect=AssertionError('no delete dialog')):
            self.assertTrue(self.w.commands.trash(context))
        self.assertTrue(recycled.is_dir())
        self.assertEqual(self.w.commands.files.pending, [])
        self.assertTrue(moving.closed)
        with self.assertRaisesRegex(ValueError, '已結束'):
            self.w.commands.files.rename(stale, 'stale')
        recycled.rename(path)
        self.w.refresh_tree()
        self.w.commands.files.run('rename', self.context(path))
        self.assertIsNotNone(self.w.commands.files.inline)
        with patch.object(self.w.commands, 'move_to_trash', side_effect=lambda p: p.rename(recycled)):
            self.assertTrue(self.w.commands.trash(self.context(path)))
        self.assertIsNone(self.w.commands.files.inline)
        self.assertEqual(self.w.commands.files.pending, [])
        menu.deleteLater()

    def test_delete_failure_preserves_dirty_content_and_shows_top_right_reason(self):
        path = self.root/'Notes.md'
        tab = self.w.open_path(path, True)
        QTest.keyClicks(tab.widget, 'unsaved ')
        old = path.read_bytes()
        with patch.object(self.w.commands, 'move_to_trash', side_effect=OSError('檔案被其他程式鎖定')):
            self.assertFalse(self.w.commands.trash(self.context(path)))
        notice = self.w.commands.delete_notice
        self.assertTrue(notice.isVisible())
        self.assertIn('Notes.md', notice.message.text())
        self.assertIn('檔案被其他程式鎖定', notice.message.text())
        self.assertGreater(notice.x(), notice.parentWidget().width()//2)
        self.assertIsNone(QApplication.activeModalWidget())
        self.assertEqual(path.read_bytes(), old)
        self.assertTrue(tab.dirty)
        self.assertTrue(tab.widget.document().isUndoAvailable())
        with patch.object(self.w.commands, 'move_to_trash', side_effect=lambda p: p.rename(self.root.parent/'recycled.md')):
            self.assertTrue(self.w.commands.trash(self.context(path)))
        self.assertNotIn(tab.key, self.w.tabs_by_key)
        self.assertEqual(self.w.commands.files.pending, [])

    def palette(self):
        tab = self.w.open_path(self.root/'Street.pcs-workflow', True, new_tab=True)
        canvas = tab.host.canvas
        destination = next(iter(tab.host.state['multi_output']['canvases']))
        palette = CanvasPalette(canvas, None, destination)
        palette.show()
        self.events()
        self.palettes = getattr(self, 'palettes', []) + [palette]
        return tab, palette

    def test_management_page_import_is_one_undo_saved_with_unused_materials(self):
        tab, palette = self.palette()
        path = self.root/'Scene.pcs-category'
        original = path.read_bytes()
        state = copy.deepcopy(tab.host.state)
        history = len(tab.host.canvas.undo_stack)
        self.assertEqual(palette.new_button.text(), '')
        self.assertEqual(palette.new_button.toolTip(), '新增文字模塊或匯入管理頁')
        self.assertNotIn('未分類', [row['name'] for row in palette.scope.rows])
        QTest.mouseClick(palette.new_button, Qt.MouseButton.LeftButton)
        menu = palette.import_menu
        action = next(a for a in menu.actions() if a.data() == str(path))
        with patch.object(tab.host.comfy, 'schedule_sync') as sync, \
                patch.object(tab.host.comfy.input_flow.chain, 'route_changed') as route:
            action.trigger()
            menu.close()
            self.events()
            sync.assert_not_called()
            route.assert_not_called()
        ident = palette.get_browser_category()
        self.assertIn(ident, tab.host.state[library.FIELD])
        self.assertEqual(palette.category_name(), 'Scene')
        self.assertGreater(palette.assets.count(), 0)
        self.assertTrue(palette.isVisible())  # Continue choosing a copied material.
        self.assertEqual(len(tab.host.canvas.undo_stack), history+1)
        for field in ('output_order', 'uses', 'multi_output', 'draft'):
            self.assertEqual(tab.host.state.get(field), state.get(field), field)
        self.assertEqual(path.read_bytes(), original)
        palette.reject()
        self.w.autosave.flush(tab)
        self.assertFalse(tab.dirty, tab.document.error)
        saved = io.load(tab.path.parent, tab.path.name).document
        self.assertIn(library.FEATURE, saved['required_features'])
        self.assertIn(ident, saved['payload']['state'][library.FIELD])
        self.assertEqual(len([i for i in saved['payload']['state']['items'] if i['module']==ident]), palette.assets.count())
        # Use the document's actual shared undo history after close/reopen.
        target = tab.path
        self.w.close_tab(self.w.index_of(tab.key), pane=self.w.pane_for(tab))
        reopened = self.w.open_path(target, True)
        self.assertIn(ident, reopened.host.state[library.FIELD])
        reopened.host.canvas.undo()
        self.assertNotIn(ident, reopened.host.state.get(library.FIELD, []))
        reopened.host.canvas.redo()
        self.assertIn(ident, reopened.host.state[library.FIELD])
        self.w.autosave.flush(reopened)
        self.assertFalse(reopened.dirty, reopened.document.error)
        # A fresh document parser sees the library too, not just the cached host.
        self.assertIn(ident, io.load(target.parent, target.name).document['payload']['state'][library.FIELD])

    def test_import_bad_attachment_is_atomic_and_same_named_pages_get_independent_ids(self):
        tab, palette = self.palette()
        path = self.root/'Scene.pcs-category'
        source = io.load(path.parent, path.name)
        value = copy.deepcopy(source.document)
        value['payload']['items'][0]['preview'] = 'missing.png'
        broken = self.root/'broken.pcs-category'
        broken.write_bytes(schema.encode(value))
        before = copy.deepcopy(tab.host.state)
        self.assertIsNone(palette.import_category(broken))
        self.assertEqual(tab.host.state, before)
        first = palette.import_category(path)
        second = palette.import_category(path)
        self.assertNotEqual(first, second)
        names = {m['id']: m['name'] for m in tab.host.state['modules']}
        self.assertEqual((names[first], names[second]), ('Scene', 'Scene (2)'))
        sources = {i['id'] for i in source.document['payload']['items']}
        imported = {i['id'] for i in tab.host.state['items'] if i['module'] in (first, second)}
        self.assertFalse(sources & imported)
        malformed = copy.deepcopy(tab.document.project())
        malformed['required_features'] = []
        with self.assertRaises(schema.DocumentError):
            schema.validate(malformed)

    def test_imported_attachment_and_material_survive_source_changes_and_canvas_insertion(self):
        from local_document_fixtures import p0
        from prompt_calculus_studio import core, snapshots
        from prompt_calculus_studio.local_documents import identity
        tab, palette = self.palette()
        path = self.root/'Scene.pcs-category'
        loaded = io.load(path.parent, path.name)
        value = copy.deepcopy(loaded.document)
        raw = p0.png()
        image = self.root/'preview.png'
        image.write_bytes(raw)
        value['payload']['items'][0]['preview'] = image.name
        value = assets.prepare(value, self.root, self.root)
        self.assertEqual(io.save(self.root, path.name, value, loaded.receipt).status, 'saved')
        original = path.read_bytes()
        ident = palette.import_category(path)
        self.assertIsNotNone(ident, palette.status.text())
        copied = next(i for i in tab.host.state['items'] if i['module'] == ident and i.get('preview'))
        self.assertEqual((tab.host.store.directory/copied['preview']).read_bytes(), raw)
        # Both the original logical image and the original document can change.
        image.write_bytes(b'new source image')
        source = io.load(self.root, path.name)
        changed = copy.deepcopy(source.document)
        changed['payload']['items'][0]['prompt'] = 'different source text'
        self.assertEqual(io.save(self.root, path.name, changed, source.receipt).status, 'saved')
        self.assertNotEqual(path.read_bytes(), original)
        self.assertNotEqual(copied['prompt'], 'different source text')
        before = copy.deepcopy(tab.host.state['uses'])
        palette.restore_selection(palette.assets, copied['id'])
        palette.insert()
        self.events()
        self.assertTrue(palette.closed)
        self.assertNotEqual(tab.host.state['uses'], before)
        self.w.autosave.flush(tab)
        self.assertFalse(tab.dirty, tab.document.error)
        saved = io.load(tab.path.parent, tab.path.name)
        self.assertEqual(saved.issues, ())
        for reference, _, _ in assets.references(saved.document):
            self.assertEqual(assets.resolve(saved.document, tab.path.parent, reference).read_bytes(), raw)
        duplicate = identity.duplicate(saved.document).document
        schema.validate(duplicate)
        self.assertIn(ident, duplicate['payload']['state'][library.FIELD])
        snapshot = snapshots.make_snapshot(tab.host.state)
        restored = snapshots.restore_snapshot(tab.host.state, snapshot)
        core.validate_state(restored)
        self.assertIn(ident, restored[library.FIELD])

    def test_interrupted_attachment_write_removes_partial_file_and_allows_retry(self):
        tab, palette = self.palette()
        source = self.root/'Scene.pcs-category'
        payload, _ = library.prepare(source)
        before = copy.deepcopy(tab.host.state)
        relative = 'imports/interrupted.png'
        target = tab.host.store.directory/relative
        original_open = Path.open
        class InterruptedWrite:
            def __enter__(self):
                self.stream = original_open(target, 'xb')
                return self
            def write(self, raw):
                self.stream.write(raw[:3])
                self.stream.flush()
                raise OSError('synthetic disk full')
            def __exit__(self, *args):
                self.stream.close()
        def open_file(path, *args, **kwargs):
            if path == target and args == ('xb',):
                return InterruptedWrite()
            return original_open(path, *args, **kwargs)
        with patch.object(library, 'prepare', return_value=(payload, {relative: b'partial image'})), \
                patch.object(Path, 'open', open_file):
            self.assertIsNone(palette.import_category(source))
        self.assertFalse(target.exists())
        self.assertEqual(tab.host.state, before)
        self.assertFalse(palette.busy)
        self.assertTrue(palette.new_button.isEnabled())
        self.assertIn('synthetic disk full', palette.status.text())
        self.assertIsNotNone(palette.import_category(source))

    def test_drag_file_opens_and_splits_without_moving_or_losing_existing_edits(self):
        path = self.root/'Notes.md'
        existing = self.w.open_path(self.root/'Empty.md', True)
        QTest.keyClicks(existing.widget, 'keep this edit')
        drag = self.w.tree.file_drag
        self.w.tree.setCurrentItem(self.w.path_items[path_key(path)])
        self.assertTrue(drag.begin())
        original = path.read_bytes()
        pane = self.w.panes[0]
        def deliver(widget, point):
            for cls in (Enter, Move, Drop):
                mime = drag.mime()
                event = cls(QPointF(point) if cls is Drop else point,
                            Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                event.origin = self.w.tree
                # Retain mime data for the native event's complete dispatch.
                QApplication.sendEvent(widget, event)
                self.assertTrue(event.isAccepted(), cls)
                self.assertEqual(event.dropAction(), Qt.DropAction.CopyAction)
        deliver(pane.pages, pane.pages.rect().center()/2)
        self.assertIn(existing.key, self.w.tabs_by_key)
        opened = self.w.tabs_by_key[self.w.active_key]
        self.assertEqual(opened.path, path)
        count = len(self.w.tabs_by_key)
        deliver(pane.strip.bar, pane.strip.bar.tabRect(0).center())
        self.assertEqual(len(self.w.tabs_by_key), count)  # Same pane reuses its view.
        deliver(pane.pages, pane.pages.rect().topRight()/2 + pane.pages.rect().center()/2)
        self.assertEqual(len(self.w.panes), 2)
        views = [tab for tab in self.w.tabs_by_key.values() if tab.path == path]
        self.assertEqual(len(views), 2)
        self.assertIs(views[0].widget.buffer, views[1].widget.buffer)
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue(existing.dirty)
        drag.finish()
        self.assertEqual(self.w.commands.files.pending, [])


if __name__ == '__main__':
    unittest.main()
