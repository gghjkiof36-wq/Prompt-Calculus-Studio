import copy
import json
import os
import tempfile
import unittest
import sqlite3
from unittest.mock import patch
from prompt_calculus_studio import materials, composition as comp, content_history as history
from prompt_calculus_studio.core import initial_state, validate_state, Storage, build_prompt
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.canvas_starter import initialize, guide


def state():
    value = initialize(prepare_state(initial_state(), multi=True), fresh_install=True)
    value['settings'].update(online=False, comfy_enabled=False, material='solid')
    return value


class MaterialModelTests(unittest.TestCase):
    def test_create_use_edit_reopen_retains_original_and_other_copy(self):
        s = state(); canvas = guide(s)['canvas']; before = copy.deepcopy(s)
        ident = materials.save(s, dict(name='Character', prompt='1girl, red dress,', module=None,
                                      content_kind='tags', future={'keep': [1, 2]}, pinned=True))
        a = materials.add_to_canvas(s, ident, canvas)
        b = materials.add_to_canvas(s, ident, canvas)
        materials.edit_canvas(s, a, dict(prompt='1girl, blue dress,'))
        self.assertEqual(materials.item(s, ident)['prompt'], '1girl, red dress,')
        self.assertEqual(s['uses'][b]['prompt'], '1girl, red dress,')
        self.assertNotEqual(s['uses'][a]['id'], s['uses'][b]['id'])
        self.assertIn('source_revision', s['uses'][a]); self.assertTrue(s['uses'][a]['collapsed'])
        history.record(s, before, s, 'Create and edit')
        validate_state(s)
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(directory); store.save(s); loaded = store.load(); store.close()
        self.assertEqual(loaded['uses'][a]['prompt'], '1girl, blue dress,')
        self.assertEqual(materials.item(loaded, ident)['future'], {'keep': [1, 2]})
        self.assertTrue(materials.item(loaded, ident)['pinned'])

    def test_tag_join_and_plain_markdown_keep_originals(self):
        parts = [('red dress,', 'tags'), (', smile,', 'tags'), ('<lora:x:0.5>', 'tags')]
        self.assertEqual(comp.join_fragments(parts), 'red dress, smile, <lora:x:0.5>')
        self.assertEqual(comp.join_fragments([(r'hello\,', 'tags'), ('smile', 'tags')]), r'hello\,, smile')
        raw = '  # Header,\n\nA,, B,  '
        node = comp.node('Doc', raw); node['content_kind'] = 'markdown'; node['weight'] = 15
        self.assertEqual(comp.render(node, lambda _: ''), raw)
        s = initial_state(); s.update(version=3, uses={'raw': node}, output_order=['raw'])
        s['settings']['separate_selections'] = False
        self.assertEqual(build_prompt(s), raw)
        self.assertEqual(node['prompt'], raw)

    def test_search_pin_recent_and_unknown_material_fields(self):
        s = state(); first = s['items'][0]; second = s['items'][1]
        first.update(pinned=True, last_used=1, future={'x': 9})
        second.update(last_used=2)
        self.assertEqual(materials.search(s)[0]['id'], first['id'])
        self.assertEqual(materials.search(s, order='recent')[0]['id'], second['id'])
        self.assertEqual(materials.search(s, '紅裙')[0]['id'], first['id'])
        materials.save(s, dict(id=first['id'], name='renamed', prompt='new', module=first['module']))
        self.assertEqual(materials.item(s, first['id'])['future'], {'x': 9})

    def test_delete_original_preserves_copies_and_removes_legacy_references(self):
        s = state(); asset = s['items'][0]; ident = asset['id']; canvas = guide(s)['canvas']
        key = materials.add_to_canvas(s, ident, canvas)
        s['selections'] = {asset['module']: [ident]}; comp.instance(s, ident)
        saved = {'selections': {asset['module']: [ident]}, 'instances': copy.deepcopy(s['instances'])}
        next(iter(s['multi_output']['outputs'].values()))['list_state'] = saved
        materials.remove(s, ident); validate_state(s)
        self.assertEqual(s['uses'][key]['prompt'], asset['prompt'])
        self.assertEqual(saved['selections'][asset['module']], [])

    def test_versions_coalesce_and_restore_preserve_extension_fields(self):
        s = state(); ident = s['items'][0]['id']; before = copy.deepcopy(s)
        s['items'][0]['prompt'] = 'first'; history.record(s, before, s, 'edit', 'item', False)
        next_before = copy.deepcopy(s); s['items'][0]['prompt'] = 'second'
        history.record(s, next_before, s, 'edit', 'item', True)
        entries = history.entries(s, target=ident)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['changes'][0]['before']['prompt'], before['items'][0]['prompt'])
        self.assertEqual(entries[0]['changes'][0]['after']['prompt'], 'second')
        s['items'][0]['future'] = {'keep': True}
        history.restore(s, entries[0]['id'], ident, previous=True)
        self.assertEqual(s['items'][0]['prompt'], before['items'][0]['prompt'])
        self.assertEqual(s['items'][0]['future'], {'keep': True})
        self.assertEqual(len(history.entries(s)), 1)

    def test_history_canvas_ownership_survives_deletion_and_content_restore(self):
        s = state(); canvas = guide(s)['canvas']; asset = s['items'][0]['id']
        key = materials.add_to_canvas(s, asset, canvas); before = copy.deepcopy(s)
        del s['uses'][key]; s['multi_output']['canvases'][canvas]['members'].remove(key)
        history.record(s, before, s, 'remove')
        entry = history.entries(s, target=canvas)[0]
        self.assertEqual(entry['changes'][0]['before_owner'], canvas)
        restored = history.restore(s, entry['id'], key, previous=True)
        self.assertNotEqual(restored, key)
        self.assertIn(restored, s['multi_output']['canvases'][canvas]['members'])
        self.assertEqual(s['uses'][restored]['prompt'], before['uses'][key]['prompt'])

    def test_layout_generation_and_no_op_do_not_make_content_versions(self):
        s = state(); before = copy.deepcopy(s)
        self.assertFalse(history.record(s, before, s))
        s.setdefault('generation', {})['status'] = 'running'
        self.assertFalse(history.record(s, before, s))
        s['text_positions']['probe'] = [10, 20]
        history.record(s, before, s, 'Move')
        self.assertFalse(history.entries(s)[0]['content_changed'])
        self.assertEqual(history.entries(s, content_only=True), [])

    def test_forward_optional_fields_and_unknown_history_schema(self):
        s = state(); s['items'][0]['future'] = {'x': ['unchanged']}
        s['items'][0]['content_kind'] = 'markdown'; validate_state(s)
        self.assertEqual(prepare_state(s, multi=True)['items'][0]['future'], {'x': ['unchanged']})
        s['content_history'] = {'version': 9, 'entries': [], 'future': True}
        original = copy.deepcopy(s)
        with self.assertRaises(ValueError): prepare_state(s, multi=True)
        self.assertEqual(s, original)

    def test_snapshot_preserves_format_not_private_history_or_preview(self):
        from prompt_calculus_studio.snapshots import make_snapshot, restore_snapshot
        s = state(); asset = s['items'][0]
        asset.update(content_kind='markdown', prompt='  # A,, B,  ', pinned=True, preview='C:/private/preview.jpg', last_used=1)
        key = materials.add_to_canvas(s, asset['id'], guide(s)['canvas'])
        history.record_action(s, 'Copy')
        snapshot = make_snapshot(s)
        encoded = json.dumps(snapshot)
        self.assertNotIn('C:/private', encoded)
        self.assertNotIn('content_history', encoded)
        self.assertNotIn('last_used', encoded)
        self.assertEqual(snapshot['state']['uses'][key]['content_kind'], 'markdown')
        current = copy.deepcopy(s); current['items'][0]['content_kind'] = 'tags'
        restored = restore_snapshot(current, snapshot)
        self.assertTrue(any(v.get('content_kind') == 'markdown' for v in restored['items']))

    def test_legacy_drafts_are_read_without_switching_or_losing_empty_manual(self):
        from prompt_calculus_studio.editor_legacy import documents
        s = state(); output = next(iter(s['multi_output']['outputs'].values())); asset = s['items'][0]
        output['list_state'] = {'selections': {asset['module']: [asset['id']]}, 'temporary': ['smile']}
        output['list_draft'] = {'draft': '', 'draft_base': 'old base'}
        expected = copy.deepcopy(s); rows = documents(s)
        self.assertIn(asset['prompt'], rows[0]['generated']); self.assertIn('smile', rows[0]['generated'])
        self.assertEqual(rows[0]['manual'], ''); self.assertEqual(rows[0]['final'], '')
        self.assertEqual(s, expected)

    def test_legacy_snapshot_exact_formatter_tamper_rejection_and_frozen_restore(self):
        from prompt_calculus_studio.snapshots import make_snapshot, validate_snapshot, restore_snapshot
        s = initial_state(); s.update(version=3, uses={'a': comp.node('a', 'red dress,'),
                     'b': comp.node('b', ', smile')}, output_order=['a', 'b'])
        s['settings']['separate_selections'] = False
        current = make_snapshot(s)
        self.assertEqual(current['formatter_version'], 2)
        self.assertEqual(current['generated_prompt'], 'red dress, smile')
        legacy = copy.deepcopy(current); legacy.pop('formatter_version')
        with comp.legacy_format():
            legacy['generated_prompt'] = legacy['final_prompt'] = build_prompt(legacy['state'])
        self.assertEqual(legacy['generated_prompt'], 'red dress,, , smile')
        validate_snapshot(legacy)
        bad = copy.deepcopy(legacy); bad['generated_prompt'] += ' tampered'
        with self.assertRaises(ValueError): validate_snapshot(bad)
        restored = restore_snapshot(initial_state(), legacy)
        self.assertEqual(restored['draft'], legacy['final_prompt'])
        self.assertEqual(restored['draft_base'], legacy['generated_prompt'])
        self.assertEqual(build_prompt(restored), 'red dress, smile')

    def test_legacy_multiple_outputs_freeze_each_original_and_keep_empty_draft(self):
        from test_multi_output import workspace
        from prompt_calculus_studio.snapshots import make_snapshot, validate_snapshot, restore_snapshot
        from prompt_calculus_studio.multi_output import compiled_outputs
        s, canvas, output = workspace()
        s['uses']['one']['prompt'] = 'first,'; s['uses']['canvas2']['prompt'] = 'second,'
        s['multi_output']['outputs']['out3']['draft'] = ''
        legacy = make_snapshot(s); legacy.pop('formatter_version')
        with comp.legacy_format():
            legacy['generated_prompt'] = build_prompt(legacy['state'])
            legacy['final_prompt'] = legacy['state']['draft'] if legacy['state']['draft'] is not None else legacy['generated_prompt']
            legacy['outputs'] = compiled_outputs(legacy['state'])
        validate_snapshot(legacy)
        restored = restore_snapshot(state(), legacy)
        actual = compiled_outputs(restored)
        self.assertEqual(actual[output]['final_prompt'], 'first,')
        self.assertEqual(actual['out2']['final_prompt'], 'second,')
        self.assertEqual(restored['multi_output']['outputs']['out3']['draft'], '')

    def test_old_workspace_scenes_upgrade_with_database_backup_and_no_overwrite(self):
        from test_multi_output import workspace
        from prompt_calculus_studio.workspace_scene import upgrade
        from prompt_calculus_studio.multi_output import compiled_outputs
        old, _, output = workspace()
        old['draft'] = ''; old['multi_output']['outputs'][output]['draft'] = ''
        upgrade(old)
        original = copy.deepcopy(old)
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(directory); store.save(old)
            original = store.load()
            migrated = store.load_current(multi=True)
            self.assertEqual(migrated['draft'], '')
            for saved in migrated['workspace_scenes']['items'].values():
                self.assertEqual(saved['multi_output']['version'], 7)
                self.assertIn('schedulers', saved['multi_output'])
            backups = list(store.directory.glob('before-import-*.sqlite3'))
            self.assertEqual(len(backups), 1)
            backup = sqlite3.connect(backups[0])
            try: self.assertEqual(json.loads(backup.execute('SELECT body FROM document WHERE id=1').fetchone()[0]), original)
            finally: backup.close()
            self.assertEqual(compiled_outputs(store.load_current(multi=True)), compiled_outputs(migrated))
            self.assertEqual(len(list(store.directory.glob('before-import-*.sqlite3'))), 1)
            store.close()
        self.assertEqual(old, original)

    def test_output_history_distinguishes_scopes_and_restores_only_selected_draft(self):
        from prompt_calculus_studio import multi_output
        s = state(); key = s['multi_output']['current_output']; output = s['multi_output']['outputs'][key]
        s['multi_output']['outputs']['other'] = dict(multi_output.new_output('Other'), draft='keep other')
        output.update(draft=None, draft_base='', list_draft={'draft': '', 'draft_base': 'empty base'})
        s.update(draft=None, draft_base=''); before = copy.deepcopy(s)
        output.update(draft='changed canvas', draft_base='auto base', list_draft={'draft': 'changed list', 'draft_base': 'list base'})
        s.update(draft='changed canvas', draft_base='auto base')
        history.record(s, before, s, 'Edit two scopes')
        entry = history.entries(s, target=key, content_only=True)[0]
        self.assertEqual({v['scope'] for v in entry['changes']}, {'canvas','list'})
        unrelated = copy.deepcopy(s['multi_output']); unrelated_output = copy.deepcopy(output)
        with self.assertRaises(ValueError): history.restore(s, entry['id'], key, previous=True)
        history.restore(s, entry['id'], key, previous=True, scope='list')
        self.assertEqual(output['list_draft'], {'draft':'', 'draft_base':'empty base'})
        self.assertEqual(s['draft'], 'changed canvas')
        self.assertEqual(output['draft'], unrelated_output['draft'])
        self.assertEqual(s['multi_output']['outputs']['other'], unrelated['outputs']['other'])
        for field in ('connections','bindings','schedulers','stages'):
            self.assertEqual(s['multi_output'][field], unrelated[field])
        history.restore(s, entry['id'], key, previous=True, scope='canvas')
        self.assertIsNone(output['draft']); self.assertIsNone(s['draft'])
        validate_state(s)

    def test_output_history_coalesces_and_old_journal_entries_remain_valid(self):
        from prompt_calculus_studio.drafts import edit
        s = state(); key = s['multi_output']['current_output']
        before = copy.deepcopy(s); s['items'][0]['prompt'] = 'older material'
        history.record(s, before, s, 'Old entry')
        before = copy.deepcopy(s); edit(s, 'a', key); history.record(s, before, s, 'Edit output', 'output', False)
        before = copy.deepcopy(s); edit(s, '', key); history.record(s, before, s, 'Edit output', 'output', True)
        rows = history.entries(s, target=key, content_only=True)
        self.assertEqual(len(rows), 1); self.assertIsNone(rows[0]['changes'][0]['before']['draft'])
        self.assertEqual(rows[0]['changes'][0]['after']['draft'], '')
        validate_state(s); self.assertEqual(len(s['content_history']['entries']), 2)


class MaterialEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtWidgets import QWidget
        from prompt_calculus_studio.completion import CompletionService
        from prompt_calculus_studio.prompt_editor import PromptEditor
        self.folder = tempfile.TemporaryDirectory()
        self.w = QWidget(); self.w.state = state(); self.w.store = Storage(self.folder.name)
        self.w.closing = False; self.w.notice = lambda message: None
        self.w.completion = CompletionService(self.w)
        window = self.w
        class Canvas:
            # The lightweight editor fixture exposes the Canvas interface but
            # must never stand in for real undo/redo validation (folder/reorder
            # suites construct Window and exercise its actual history).
            def undo(self): raise AssertionError('Canvas undo requires the real Window fixture')
            def redo(self): raise AssertionError('Canvas redo requires the real Window fixture')
            def commit(self, operation, label='Edit', merge_key=None):
                before = copy.deepcopy(window.state); after = copy.deepcopy(window.state)
                operation(after); validate_state(after); window.state = after
                history.record(after, before, after, label, merge_key)
                return True
        self.w.canvas = Canvas()
        self.editor = PromptEditor(self.w, compact=True)
        self.canvas = guide(self.w.state)['canvas']
        self.editor.set_destination(self.canvas)

    def tearDown(self):
        self.editor.dirty = False; self.editor.close(); self.editor.deleteLater()
        self.w.completion.timer.stop(); self.w.closing = True; self.w.close(); self.app.processEvents()
        self.w.store.close(); self.folder.cleanup()

    def click_material(self, editor, ident, button=None):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        editor.show_library(); editor.resize(1100 if not editor.compact else 420, 800)
        editor.show(); self.app.processEvents()
        entry = next(editor.listing.item(i) for i in range(editor.listing.count())
                     if editor.listing.item(i).data(Qt.ItemDataRole.UserRole) == ident)
        editor.listing.scrollToItem(entry); self.app.processEvents()
        position = editor.listing.visualItemRect(entry).center()
        QTest.mouseClick(editor.listing.viewport(), button or Qt.MouseButton.LeftButton, pos=position)
        self.app.processEvents()
        return position

    def context_action(self, editor, ident, title):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QContextMenuEvent
        from PySide6.QtTest import QTest
        from prompt_calculus_studio.widgets import RoundMenu
        other = next(editor.listing.item(i) for i in range(editor.listing.count())
                     if editor.listing.item(i).data(Qt.ItemDataRole.UserRole) != ident)
        editor.listing.setCurrentItem(other)
        self.assertNotEqual(editor.selected_id(), ident)
        position = self.click_material(editor, ident, Qt.MouseButton.RightButton)
        if not isinstance(self.app.activePopupWidget(), RoundMenu):
            # Offscreen QPA does not synthesize a platform context-menu event.
            event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, position,
                                      editor.listing.viewport().mapToGlobal(position))
            self.app.sendEvent(editor.listing.viewport(), event); self.app.processEvents()
        menu = self.app.activePopupWidget()
        self.assertIsInstance(menu, RoundMenu)
        action = next(action for action in menu.actions() if action.text() == title)
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
        self.app.processEvents()

    def test_full_page_mouse_selection_edit_and_pin_never_add_material(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QPushButton
        from PySide6.QtTest import QTest
        from prompt_calculus_studio.prompt_editor import PromptEditor
        requests = []; self.w.add_material_to_canvas = requests.append
        editor = PromptEditor(self.w)
        try:
            for destination in (None, self.canvas):
                with self.subTest(destination=destination):
                    editor.set_destination(destination)
                    ident = self.w.state['items'][1]['id']; before = copy.deepcopy(self.w.state['uses'])
                    self.click_material(editor, ident)
                    self.assertTrue(editor.browser.isVisible())
                    self.assertEqual(editor.target, ('material', ident))
                    old_pin = materials.item(self.w.state, ident).get('pinned', False)
                    self.context_action(editor, ident, '取消釘選' if old_pin else '釘選素材')
                    self.assertEqual(materials.item(self.w.state, ident)['pinned'], not old_pin)
                    self.assertEqual(self.w.state['uses'], before); self.assertEqual(requests, [])
        finally:
            editor.close(); editor.deleteLater()

    def test_context_menu_edits_and_pins_unselected_material_without_add_or_destination(self):
        from prompt_calculus_studio.prompt_editor import PromptEditor
        requests = []; self.w.add_material_to_canvas = requests.append
        full = PromptEditor(self.w)
        try:
            for editor in (self.editor, full):
                for destination in (None, self.canvas):
                    with self.subTest(compact=editor.compact, destination=destination):
                        editor.set_destination(destination)
                        ident = self.w.state['items'][2]['id']; before = copy.deepcopy(self.w.state['uses'])
                        self.context_action(editor, ident, '編輯素材')
                        self.assertEqual(editor.target, ('material', ident))
                        old_pin = materials.item(self.w.state, ident).get('pinned', False)
                        self.context_action(editor, ident, '取消釘選' if old_pin else '釘選素材')
                        self.assertEqual(materials.item(self.w.state, ident)['pinned'], not old_pin)
                        self.assertEqual(self.w.state['uses'], before); self.assertEqual(requests, [])
        finally:
            full.close(); full.deleteLater()

    def test_sidebar_click_still_takes_material_and_requests_missing_destination(self):
        requests = []; self.w.add_material_to_canvas = requests.append
        ident = self.w.state['items'][1]['id']
        self.editor.set_destination(None); before = copy.deepcopy(self.w.state['uses'])
        self.click_material(self.editor, ident)
        self.assertEqual(requests, [ident]); self.assertEqual(self.w.state['uses'], before)
        self.editor.set_destination(self.canvas); self.click_material(self.editor, ident)
        added = set(self.w.state['uses']) - set(before)
        self.assertEqual(len(added), 1)
        self.assertEqual(self.w.state['uses'][added.pop()]['source_id'], ident)
        self.assertEqual(requests, [ident])

    def test_full_page_saves_original_without_canvas_insertion_controls(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QPushButton
        from PySide6.QtTest import QTest
        from prompt_calculus_studio.prompt_editor import PromptEditor
        editor = PromptEditor(self.w)
        try:
            editor.set_destination(self.canvas); ident = self.w.state['items'][1]['id']
            before = set(self.w.state['uses']); self.click_material(editor, ident)
            self.assertTrue(editor.destination.isHidden()); self.assertTrue(editor.use_button.isHidden())
            self.assertEqual(set(self.w.state['uses']), before)
            editor.new_material(); editor.name.setText('Reusable new'); editor.prompt.setPlainText('smile')
            QTest.mouseClick(editor.save_button, Qt.MouseButton.LeftButton); self.app.processEvents()
            saved = next(v for v in self.w.state['items'] if v['name'] == 'Reusable new')
            self.assertEqual(saved['prompt'], 'smile')
            self.assertEqual(set(self.w.state['uses']), before)
        finally:
            editor.close(); editor.deleteLater()

    def test_new_material_then_canvas_partial_edit_in_shared_editor(self):
        self.editor.new_material()
        self.editor.name.setText('New material'); self.editor.prompt.setPlainText('1girl, red dress,')
        self.editor.save_material(True)
        asset = next(v for v in self.w.state['items'] if v['name'] == 'New material')
        key = next(k for k, v in self.w.state['uses'].items() if v.get('source_id') == asset['id'])
        self.editor.edit_canvas(key); self.editor.prompt.setPlainText('1girl, blue dress,')
        self.assertTrue(self.editor.flush_canvas())
        self.assertEqual(self.w.state['uses'][key]['prompt'], '1girl, blue dress,')
        self.assertEqual(materials.item(self.w.state, asset['id'])['prompt'], '1girl, red dress,')
        self.assertEqual(self.editor.stack.currentIndex(), 1)
        self.editor.show_library(); self.assertEqual(self.editor.stack.currentIndex(), 0)

    def test_workspace_change_rejects_delayed_edit(self):
        key = materials.add_to_canvas(self.w.state, self.w.state['items'][0]['id'], self.canvas)
        self.editor.edit_canvas(key); original = self.w.state['uses'][key]['prompt']
        self.editor.prompt.setPlainText('not applied')
        self.w.state['workspace'] = 'different'
        self.assertFalse(self.editor.flush_canvas())
        self.assertEqual(self.w.state['uses'][key]['prompt'], original)

    def test_shared_full_sidebar_drafts_survive_target_switch_without_saving(self):
        from prompt_calculus_studio.prompt_editor import PromptEditor
        self.editor.new_material(text='unsaved source')
        self.editor.name.setText('Draft name'); self.editor.prompt.setPlainText('draft edited')
        other = PromptEditor(self.w)
        try:
            self.assertEqual(other.name.text(), 'Draft name')
            self.assertEqual(other.prompt.toPlainText(), 'draft edited')
            self.assertTrue(other.dirty)
            other.open_material(self.w.state['items'][0]['id'])
            other.new_material()
            self.assertEqual(other.prompt.toPlainText(), 'draft edited')
            self.assertEqual(len(self.w.state['items']), 7)
            other.dirty = False
        finally:
            other.close(); other.deleteLater()

    def test_cached_lookup_is_immediate_and_stale_result_only_caches(self):
        from prompt_calculus_studio.core import current_token
        token = current_token('smil', 4); service = self.w.completion
        rows = [dict(value='smile', category=0, count=1, source='Danbooru')]
        key = json.dumps(['dictionary', token.query.casefold(), token.artist], ensure_ascii=False)
        self.w.state['settings']['online'] = True
        class Edit:
            def __init__(self): self.shown = []
            def context(self): return token
            def lookup_active(self): return True
            def display(self, token, rows): self.shown.append(rows)
            def lookup_notice(self, text): pass
        edit = Edit(); self.w.store.cache(key, rows)
        service.schedule(edit)
        self.assertEqual(edit.shown, [rows]); self.assertFalse(service.timer.isActive())
        other_key = 'superseded'; serial = service.serial
        service.active = (serial, edit, token, other_key, [])
        service.request_owner = (self.w.store, self.w.state['workspace'])
        service.request_cache_epoch = self.w.store.cache_epoch
        service.serial += 1
        service.finished(serial, rows, '')
        self.assertEqual(self.w.store.cached(other_key), rows)
        self.assertEqual(len(edit.shown), 1)

    def test_flush_uses_latest_target_and_asks_once_for_all_unsaved_originals(self):
        from prompt_calculus_studio.prompt_editor import PromptEditor, flush_editors
        self.editor.new_material(text='draft one'); self.editor.name.setText('one')
        other = PromptEditor(self.w)
        try:
            other.open_material(self.w.state['items'][0]['id'])
            other.prompt.setPlainText('draft two')
            active = self.w._material_editor_session['active']
            self.editor.can_leave()  # stale hidden form must not become active
            self.assertEqual(self.w._material_editor_session['active'], active)
            with patch('prompt_calculus_studio.prompt_editor.ask', return_value=False) as ask:
                self.assertFalse(flush_editors(self.w, closing=True)); ask.assert_called_once()
            self.assertEqual(self.editor.prompt.toPlainText(), 'draft two')
            self.assertNotEqual(self.w.state['items'][0]['prompt'], 'draft two')
        finally:
            other.dirty = False; other.close(); other.deleteLater()

    def test_document_reset_discards_stale_forms_even_when_ids_match(self):
        from prompt_calculus_studio.prompt_editor import PromptEditor, flush_editors, reset_editors
        key = materials.add_to_canvas(self.w.state, self.w.state['items'][0]['id'], self.canvas)
        self.editor.edit_canvas(key); self.editor.prompt.setPlainText('old unsaved')
        other = PromptEditor(self.w)
        try:
            replacement = copy.deepcopy(self.w.state); replacement['uses'][key]['prompt'] = 'imported'
            self.w.state = replacement; reset_editors(self.w)
            self.assertTrue(flush_editors(self.w))
            self.assertEqual(self.w.state['uses'][key]['prompt'], 'imported')
            self.assertTrue(all(v['target'] is None and not v['dirty'] for v in self.w._material_editor_session['drafts'].values()))
            for editor in (self.editor, other):
                self.assertIsNone(editor.target); self.assertEqual(editor.prompt.toPlainText(), '')
                self.assertFalse(editor.timer.isActive())
            self.assertTrue(other.detail.isEnabled())
        finally:
            other.close(); other.deleteLater()

    def test_original_save_preserves_concurrent_usage_pin_and_future_fields(self):
        ident = self.w.state['items'][0]['id']
        self.editor.open_material(ident); self.editor.prompt.setPlainText('edited source')
        material = materials.item(self.w.state, ident)
        material.update(pinned=True, last_used=123, future={'concurrent': True})
        self.editor.save_material()
        saved = materials.item(self.w.state, ident)
        self.assertEqual(saved['prompt'], 'edited source'); self.assertTrue(saved['pinned'])
        self.assertEqual(saved['last_used'], 123); self.assertEqual(saved['future'], {'concurrent': True})

    def test_history_panel_restores_output_without_mutating_submitted_snapshot(self):
        from prompt_calculus_studio.prompt_editor import HistoryPanel
        from prompt_calculus_studio.drafts import edit
        from prompt_calculus_studio.stage_store import StageStore
        key = self.w.state['multi_output']['current_output']
        self.w.canvas.commit(lambda s: edit(s, 'submitted text', key), label='Edit output')
        journal = StageStore(self.w.store.db)
        attempt = journal.add('attempt', self.w.state['workspace'], status='submitted',
                              snapshot_state=copy.deepcopy(self.w.state), images=[])
        self.w.canvas.commit(lambda s: edit(s, '', key), label='Clear text')
        panel = HistoryPanel(self.w, key)
        try:
            panel.listing.setCurrentRow(0)
            self.assertIn('畫布手稿', panel.targets.currentText())
            self.assertIn('空手動草稿', panel.detail.toPlainText())
            panel.restore(True)
            self.assertEqual(self.w.state['draft'], 'submitted text')
            self.assertEqual(journal.read(attempt['id']), attempt)
            self.assertEqual(history.entries(self.w.state)[0]['label'], '還原內容版本')
        finally:
            panel.deleteLater()

    def test_flush_and_reset_ignore_deleted_qt_instances(self):
        from prompt_calculus_studio.prompt_editor import PromptEditor, flush_editors, reset_editors
        from shiboken6 import delete
        old = PromptEditor(self.w); delete(old)
        self.assertTrue(flush_editors(self.w))
        reset_editors(self.w)
        self.assertNotIn(old, self.w._material_editors)

    def test_responsive_editor_keeps_target_and_collapsed_optional_fields(self):
        from prompt_calculus_studio.prompt_editor import PromptEditor
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        other = PromptEditor(self.w)
        try:
            other.resize(1100, 800); other.show(); self.app.processEvents()
            other.open_material(self.w.state['items'][0]['id'])
            self.assertFalse(other.more.isChecked()); self.assertTrue(other.preview.isHidden())
            target = other.target; text = other.prompt.toPlainText()
            other.resize(640, 760); self.app.processEvents()
            self.assertTrue(other.narrow); self.assertTrue(other.browser.isHidden())
            self.assertEqual(other.target, target); self.assertEqual(other.prompt.toPlainText(), text)
            self.assertLessEqual(other.page.width(), other.width())
            other.show_library(); self.assertFalse(other.browser.isHidden())
            self.assertTrue(other.detail.isHidden())
            QTest.mouseClick(other.new_button, Qt.MouseButton.LeftButton); self.app.processEvents()
            self.assertTrue(other.browser.isHidden()); self.assertTrue(other.prompt.hasFocus())
            other.open_material(target[1])
            other.resize(1100, 800); self.app.processEvents()
            self.assertFalse(other.narrow); self.assertEqual(other.target, target)
        finally:
            other.close(); other.deleteLater()

    def test_full_drawer_preserves_material_drafts_cursor_selection_and_scroll(self):
        from prompt_calculus_studio.prompt_editor import PromptEditor
        from PySide6.QtGui import QTextCursor
        full = PromptEditor(self.w)
        try:
            full.resize(1100, 700); full.show(); self.app.processEvents()
            first, second = [v['id'] for v in self.w.state['items'][:2]]
            self.click_material(full, first)
            text = '\n'.join('line ' + str(i) for i in range(80))
            full.prompt.setPlainText(text)
            cursor = full.prompt.textCursor(); cursor.setPosition(12); cursor.setPosition(28, QTextCursor.MoveMode.KeepAnchor)
            full.prompt.setTextCursor(cursor)
            full.prompt.verticalScrollBar().setValue(140)
            expected_scroll = full.prompt.verticalScrollBar().value()
            self.click_material(full, second); self.click_material(full, first)
            self.assertEqual(full.prompt.toPlainText(), text); self.assertTrue(full.dirty)
            self.assertEqual((full.prompt.textCursor().position(), full.prompt.textCursor().anchor()), (28, 12))
            self.assertEqual(full.prompt.verticalScrollBar().value(), expected_scroll)
            self.assertNotEqual(materials.item(self.w.state, first)['prompt'], text)
            full.new_material(); full.name.setText('unsaved new'); full.prompt.setPlainText('new body')
            self.click_material(full, second); full.new_material()
            self.assertEqual(full.name.text(), 'unsaved new'); self.assertEqual(full.prompt.toPlainText(), 'new body')
        finally:
            full.close(); full.deleteLater()

    def test_draft_undo_redo_survives_switch_resize_and_full_compact_transfer(self):
        from prompt_calculus_studio.prompt_editor import PromptEditor, flush_editors
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QTextCursor
        from PySide6.QtTest import QTest
        full = PromptEditor(self.w)
        def key(editor, letter):
            editor.prompt.setFocus()
            QTest.keyClick(editor.prompt, letter, Qt.KeyboardModifier.ControlModifier)
            self.app.processEvents()
        try:
            full.resize(1100, 800); full.show(); self.app.processEvents()
            a, b = [v['id'] for v in self.w.state['items'][:2]]
            full.open_material(a); original = full.prompt.toPlainText()
            full.prompt.moveCursor(QTextCursor.MoveOperation.End)
            QTest.keyClicks(full.prompt, ' tail'); self.app.processEvents()
            full.open_material(b); full.open_material(a)
            key(full, Qt.Key.Key_Z); self.assertEqual(full.prompt.toPlainText(), original)
            key(full, Qt.Key.Key_Y); self.assertEqual(full.prompt.toPlainText(), original + ' tail')
            full.resize(640, 480); self.app.processEvents()
            key(full, Qt.Key.Key_Z); self.assertEqual(full.prompt.toPlainText(), original)
            key(full, Qt.Key.Key_Y); self.assertEqual(full.prompt.toPlainText(), original + ' tail')
            self.editor.transfer_from(full); self.editor.show(); self.app.processEvents()
            self.assertIs(self.editor.prompt.document().parent(), self.w)
            self.assertIsNot(self.editor.prompt.document(), full.prompt.document())
            self.editor.prompt.moveCursor(QTextCursor.MoveOperation.End)
            # Explicit cursor movement breaks the previous native typing edit block.
            cursor = self.editor.prompt.textCursor(); cursor.beginEditBlock()
            cursor.insertText(' rail'); cursor.endEditBlock()
            self.app.processEvents()
            full.transfer_from(self.editor)
            self.assertEqual(full.prompt.toPlainText(), original + ' tail rail')
            self.assertTrue(flush_editors(self.w))
            key(full, Qt.Key.Key_Z); self.assertEqual(full.prompt.toPlainText(), original + ' tail')
            self.editor.transfer_from(full)
            key(self.editor, Qt.Key.Key_Y)
            self.assertEqual(self.editor.prompt.toPlainText(), original + ' tail rail')
            self.assertEqual(materials.item(self.w.state, a)['prompt'], original)
            json.dumps(list(self.w._material_editor_session['drafts'].values()))
        finally:
            full.close(); full.deleteLater()

    def test_draft_history_survives_original_editor_destruction(self):
        from prompt_calculus_studio.prompt_editor import PromptEditor
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QTextCursor
        from PySide6.QtTest import QTest
        from shiboken6 import delete
        full = PromptEditor(self.w)
        full.resize(1100, 800); full.show(); self.app.processEvents()
        ident = self.w.state['items'][0]['id']; full.open_material(ident)
        original = full.prompt.toPlainText()
        full.prompt.moveCursor(QTextCursor.MoveOperation.End); QTest.keyClicks(full.prompt, ' tail')
        full.can_leave(); delete(full)
        self.editor.refresh(); self.editor.show(); self.app.processEvents()
        self.editor.prompt.setFocus()
        QTest.keyClick(self.editor.prompt, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.editor.prompt.toPlainText(), original)
        QTest.keyClick(self.editor.prompt, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.editor.prompt.toPlainText(), original + ' tail')

    def test_full_text_line_spacing_plain_paste_undo_and_completion(self):
        from prompt_calculus_studio.prompt_editor import PromptEditor
        from PySide6.QtCore import Qt, QMimeData
        from PySide6.QtGui import QTextCursor, QInputMethodEvent
        from PySide6.QtTest import QTest
        full = PromptEditor(self.w)
        try:
            full.resize(1100, 700); full.show(); self.app.processEvents()
            full.prompt.setPlainText('one\ntwo\nthree')
            self.app.processEvents()
            y = [full.prompt.cursorRect(QTextCursor(full.prompt.document().findBlockByNumber(i))).y() for i in range(3)]
            ratio = (y[1] - y[0]) / full.prompt.fontMetrics().lineSpacing()
            self.assertGreater(ratio, 1.5); self.assertLess(ratio, 1.8)
            self.assertFalse(full.prompt.acceptRichText())
            full.prompt.setPlainText('base')
            full.prompt.moveCursor(QTextCursor.MoveOperation.End); full.prompt.setFocus()
            QTest.keyClicks(full.prompt, 'X')
            full.prompt.undo(); self.assertEqual(full.prompt.toPlainText(), 'base')
            full.prompt.redo(); self.assertEqual(full.prompt.toPlainText(), 'baseX')
            mime = QMimeData(); mime.setText('<literal>'); mime.setHtml('<b>wrong</b>')
            full.prompt.insertFromMimeData(mime)
            self.assertEqual(full.prompt.toPlainText(), 'baseX<literal>')
            full.prompt.setPlainText('中文😀, sm'); full.prompt.moveCursor(QTextCursor.MoveOperation.End)
            full.lookup.setChecked(True); self.app.processEvents()
            token = full.prompt.context()
            full.prompt.display(token, [dict(value='smile', source='自訂字典', count=1)])
            self.assertTrue(full.prompt.candidates)
            full.prompt.insert_completion(next(iter(full.prompt.candidates)))
            self.assertTrue(full.prompt.toPlainText().startswith('中文😀, '))
            self.assertIn('smile', full.prompt.toPlainText())
            event = QInputMethodEvent('中', [])
            self.app.sendEvent(full.prompt, event)
            self.assertTrue(full.prompt.composing); self.assertFalse(full.prompt.completer.popup().isVisible())
            commit = QInputMethodEvent(); commit.setCommitString('中文')
            self.app.sendEvent(full.prompt, commit)
            self.assertFalse(full.prompt.composing); self.assertIn('中文', full.prompt.toPlainText())
        finally:
            full.close(); full.deleteLater()

    def test_full_save_failure_keeps_draft_and_visible_error(self):
        from prompt_calculus_studio.prompt_editor import PromptEditor
        full = PromptEditor(self.w)
        try:
            full.name.setText('must survive'); full.prompt.setPlainText('pending body')
            before = copy.deepcopy(self.w.state)
            with patch.object(self.w.canvas, 'commit', return_value=False): full.save_material()
            self.assertTrue(full.dirty); self.assertEqual(full.prompt.toPlainText(), 'pending body')
            self.assertFalse(full.status.isHidden()); self.assertIn('保留', full.status.text())
            self.assertEqual(self.w.state, before)
        finally:
            full.close(); full.deleteLater()

    def test_material_cards_wrap_words_and_use_actual_preview_line_count(self):
        from prompt_calculus_studio.material_editor_page import MaterialCardDelegate
        from PySide6.QtGui import QFontMetrics, QIcon, QPixmap
        from PySide6.QtWidgets import QListWidget, QListWidgetItem, QStyleOptionViewItem
        listing = QListWidget(); listing.resize(360, 600); listing.show(); self.app.processEvents()
        delegate = MaterialCardDelegate(listing); option = QStyleOptionViewItem(); option.font = listing.font()
        metrics = QFontMetrics(option.font)
        try:
            lines = delegate.preview_lines('soft morning light', option.font, metrics.horizontalAdvance('soft morning l'))
            self.assertEqual(lines, ['soft morning', 'light'])
            chinese = delegate.preview_lines('文字換行，保留中文與😀符號。', option.font, metrics.horizontalAdvance('文字換行，'))
            self.assertEqual(chinese[0], '文字換行，'); self.assertLessEqual(len(chinese), 2)
            for text in ('One\nsmile', 'Two\n' + 'soft morning light ' * 8): listing.addItem(text)
            heights = [delegate.sizeHint(option, listing.model().index(i, 0)).height() for i in range(2)]
            self.assertEqual(heights[1] - heights[0], metrics.height())
            pixmap = QPixmap(52, 52); pixmap.fill()
            listing.addItem(QListWidgetItem(QIcon(pixmap), 'Image\n'))
            self.assertGreaterEqual(delegate.sizeHint(option, listing.model().index(2, 0)).height(), 82)
        finally: listing.close(); listing.deleteLater()

    def test_information_disclosure_keeps_outer_origin_and_document_scroll(self):
        from prompt_calculus_studio.prompt_editor import PromptEditor
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        full = PromptEditor(self.w)
        try:
            full.resize(1100, 800); full.show(); self.app.processEvents()
            full.prompt.setPlainText('\n'.join('Line ' + str(i) for i in range(80)))
            full.prompt.verticalScrollBar().setValue(120); self.app.processEvents()
            inner = full.prompt.verticalScrollBar().value(); origin = full.scroll.verticalScrollBar().value()
            QTest.mouseClick(full.more, Qt.MouseButton.LeftButton); self.app.processEvents()
            self.assertTrue(full.more.isChecked())
            self.assertEqual(full.scroll.verticalScrollBar().value(), origin)
            self.assertEqual(full.prompt.verticalScrollBar().value(), inner)
            full.scroll.verticalScrollBar().setValue(full.scroll.verticalScrollBar().maximum())
            full.scroll.ensureWidgetVisible(full.more); self.app.processEvents()
            QTest.mouseClick(full.more, Qt.MouseButton.LeftButton); self.app.processEvents()
            self.assertFalse(full.more.isChecked())
            self.assertEqual(full.scroll.verticalScrollBar().value(), min(origin, full.scroll.verticalScrollBar().maximum()))
            self.assertEqual(full.prompt.verticalScrollBar().value(), inner)
        finally: full.close(); full.deleteLater()


if __name__ == '__main__': unittest.main()
