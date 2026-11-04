"""Tool routes retain pages, editing contracts and workspace motion settings."""
import copy
import json
import unittest
from unittest.mock import patch
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QWidget, QAbstractButton
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

    def test_document_hosts_use_memory_state_without_hidden_legacy_lists(self):
        with patch.object(Storage,'load_current',side_effect=AssertionError('document is already prepared')):
            for filename in ('Scene.pcs-category','Street.pcs-workflow'):
                tab=self.w.open_path(self.root/filename,new_tab=True)
                self.assertIsNotNone(tab,self.errors)
                self.events()
                self.assertFalse(hasattr(tab.host,'quick'))
                self.assertFalse(hasattr(tab.host,'module_list'))

    def test_reopening_closed_documents_reuses_hosts_and_execution_owners(self):
        for filename in ('Scene.pcs-category', 'Street.pcs-workflow'):
            path = self.root / filename
            first = self.w.open_path(path, new_tab=True)
            self.events()
            document, host = first.document, first.host
            execution, store = document.execution, host.store
            original = path.read_bytes()
            keys = {first.key}
            for _ in range(3):
                current = next(t for t in self.w.all_pages() if t.document is document)
                self.assertTrue(self.w.close_tab(self.w.index_of(current.key),
                    pane=self.w.pane_for(current), make_home=False))
                self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                reopened = self.w.open_path(path, new_tab=True)
                self.events()
                self.assertIs(reopened.host, host)
                self.assertIs(reopened.document.execution, execution)
                self.assertIs(reopened.host.store, store)
                self.assertIs(execution.view_host, host)
                self.assertTrue(host.isVisible())
                self.assertNotIn(reopened.key, keys)
                keys.add(reopened.key)
                self.assertEqual(document.hosts, [host])
            self.assertEqual(path.read_bytes(), original)

    def test_closed_split_views_reuse_distinct_hosts_without_stealing_live_view(self):
        first = self.w.open_path(self.root / 'Street.pcs-workflow', new_tab=True)
        second = self.w.duplicate_view(first, True)
        self.events()
        hosts = {first.host, second.host}
        document = first.document
        self.assertEqual(len(hosts), 2)
        self.assertTrue(self.w.close_tab(self.w.index_of(second.key),
            pane=self.w.pane_for(second), make_home=False))
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        reopened = self.w.duplicate_view(first, True)
        self.events()
        self.assertIs(reopened.host, second.host)
        self.assertIsNot(reopened.host, first.host)
        self.assertTrue(first.host.isVisible())
        self.assertTrue(reopened.host.isVisible())
        self.assertEqual(set(document.hosts), hosts)
        self.assertIs(reopened.host.comfy, first.host.comfy)

    def test_repeated_tabs_release_replaced_native_close_buttons(self):
        from prompt_calculus_studio.document_tabs import DocumentTabs
        strip = DocumentTabs(self.w)
        count = len(strip.bar.findChildren(QAbstractButton))
        for n in range(5):
            index = strip.bar.addTab('文件')
            strip.bar.setTabData(index, str(n))
            strip.decorate(index, '文件', True, False)
            strip.decorate(index, '文件', True, True)
            strip.bar.removeTab(index)
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertEqual(len(strip.bar.findChildren(QAbstractButton)), count)
        strip.deleteLater()

    def test_reopen_does_not_reactivate_a_host_finishing_its_close(self):
        first = self.w.open_path(self.root / 'Street.pcs-workflow', new_tab=True)
        self.assertTrue(self.w.close_tab(self.w.index_of(first.key),
            pane=self.w.pane_for(first), make_home=False))
        first.host.closing = True
        reopened = self.w.open_path(first.path, new_tab=True)
        self.assertIsNot(reopened.host, first.host)
        self.assertIs(reopened.document.execution, first.document.execution)
        self.assertFalse(reopened.host.closing)

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
