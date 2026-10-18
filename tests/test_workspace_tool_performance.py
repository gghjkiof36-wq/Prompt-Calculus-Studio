"""Tool routes retain pages, editing contracts and workspace motion settings."""
import copy
import json
import unittest
from unittest.mock import patch
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QWidget
from prompt_calculus_studio.core import Storage
from prompt_calculus_studio.dialog_presentation import reduced_motion
from prompt_calculus_studio.snapshots import make_snapshot
import test_workspace_shell as fixture


class Parents(QObject):
    def __init__(self, pages):
        super().__init__()
        self.changes = []
        for page in pages:page.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ParentChange:self.changes.append(watched)
        return False


class WorkspaceToolPerformanceTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    setUp = fixture.WorkspaceShellTests.setUp
    tearDown = fixture.WorkspaceShellTests.tearDown
    events = fixture.WorkspaceShellTests.events

    def test_first_route_has_no_disk_seed_roundtrip_and_revisits_keep_page_owners(self):
        self.assertIsNone(self.w.utility)
        with patch.object(Storage, 'load_current', side_effect=AssertionError('fresh session state is already in memory')):
            self.w.open_route('gallery')
        host = self.w.utility
        for route in ('explore', 'export'):
            self.w.open_route(route)
            self.events()
        pages = (host.gallery, host.clean_export, host.settings_page)
        parents = [page.parentWidget() for page in pages]
        watch = Parents(pages)
        for route in ('home', 'gallery', 'export', 'explore', 'gallery', 'home'):
            self.w.open_route(route)
            self.events()
        self.assertEqual(watch.changes, [])
        self.assertEqual([page.parentWidget() for page in pages], parents)
        self.assertFalse(hasattr(host, 'quick'))
        self.assertFalse(hasattr(host, 'selected'))
        self.assertFalse(hasattr(host, 'prompt_editor'))

    def test_media_restore_keeps_empty_draft_undo_redo_and_storage(self):
        tab = self.w.open_path(self.root/'Street.pcs-workflow')
        original_file = tab.path.read_bytes()
        self.w.open_route('gallery')
        host = self.w.utility
        host.activate_prompt_view('canvas')
        snapshot = make_snapshot(host.state)
        from prompt_calculus_studio.drafts import edit
        edit(host.state, '', host.state['multi_output']['current_output'])
        host.refresh_builder()
        host.persist()
        self.assertEqual(host.store.load()['draft'], '')
        original = copy.deepcopy(host.state)
        record = {'metadata': {'raw': {'prompt_studio': {'schema_version': 1,
                  'bindings': [{'node_id': '2', 'snapshot': snapshot}]}}}}
        with patch('prompt_calculus_studio.pages.ask', return_value=True):
            host.gallery.restore_combination(record)
        restored = host.state['workspace']
        self.assertNotEqual(restored, original['workspace'])
        host.canvas.undo()
        self.assertEqual(host.state['workspace'], original['workspace'])
        self.assertEqual(host.state['draft'], '')
        self.assertEqual(host.final.toPlainText(), '')
        host.canvas.redo()
        self.assertEqual(host.state['workspace'], restored)
        self.assertEqual(tab.path.read_bytes(), original_file)
        self.w.open_route('gallery')
        host.focus_search()  # No reference to the removed hidden quick editor.

    def test_reduce_motion_resolves_nested_dialogs_and_survives_preferences(self):
        self.w.open_route('settings', 'appearance')
        host = self.w.utility
        host.settings_page.preferences.reduce_motion.setChecked(True)
        host.settings_page.flush()
        nested = QWidget(QWidget(self.w))
        self.assertTrue(reduced_motion(nested))
        saved = json.loads((self.w.data/'workspace-ui.json').read_text(encoding='utf-8'))
        self.assertTrue(saved['reduce_motion'])
        self.w.settings['reduce_motion'] = False
        self.w.load_ui_preferences()
        self.assertTrue(self.w.settings['reduce_motion'])
        tab = self.w.open_path(self.root/'Street.pcs-workflow')
        self.assertTrue(reduced_motion(QWidget(tab.host)))
