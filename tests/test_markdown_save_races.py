"""089-002: actual displaced bytes, post-replace races and transaction callers."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from PySide6.QtCore import QSaveFile, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListWidget, QPlainTextEdit
from prompt_calculus_studio.local_documents import windows
from prompt_calculus_studio.file_publication import checked_replace, PublicationError
from prompt_calculus_studio.notebook_composer import Composition
from prompt_calculus_studio.markdown_relocation import checked_write
from prompt_calculus_studio.widgets import RoundMenu
import test_workspace_panes as fixture


NATIVE_REPLACE = windows.replace
BASE = b'\xef\xbb\xbf# base\r\nline\n'
LOCAL = BASE + b'local\r\n'
EXTERNAL = b'\xef\xbb\xbf# external unique\r\n'
LATER = b'# later external unique\n'


def external_replace(path, raw):
    source = path.with_name(path.name + '.other-writer-' + uuid.uuid4().hex)
    source.write_bytes(raw)
    # MoveFileEx/os.replace can be denied by the pinned reader. ReplaceFileW
    # can still replace this name and is the adversarial native boundary.
    NATIVE_REPLACE(path, source, source.with_name(source.name + '.previous'))


def retained(result):
    return [Path(p).read_bytes() for p in result.recovery_paths if Path(p).is_file()]


@unittest.skipUnless(os.name == 'nt', 'Windows actual-displacement contract')
class MarkdownReplacementTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-md-race-')
        self.addCleanup(self.area.cleanup)
        self.path = Path(self.area.name)/'note.md'
        self.path.write_bytes(BASE)

    def save(self):
        return checked_replace(self.path, BASE, LOCAL, QSaveFile)

    def test_success_exact_bytes_without_recovery_accumulation(self):
        self.assertEqual(self.save().status, 'saved')
        self.assertEqual(self.path.read_bytes(), LOCAL)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_actual_replace_boundary_retains_external_and_candidate(self):
        def race(destination, temporary, backup):
            external_replace(destination, EXTERNAL)
            NATIVE_REPLACE(destination, temporary, backup)
        with patch.object(windows, 'replace', side_effect=race):
            with self.assertRaises(PublicationError) as caught:self.save()
        result = caught.exception.result
        self.assertTrue(result.uncertain)
        self.assertEqual(result.status, 'conflict')
        self.assertIn(EXTERNAL, retained(result))
        self.assertIn(LOCAL, retained(result))
        self.assertEqual(self.path.read_bytes(), LOCAL)

    def test_post_replace_writer_is_not_rolled_back(self):
        def race(destination, temporary, backup):
            NATIVE_REPLACE(destination, temporary, backup)
            external_replace(destination, LATER)
        with patch.object(windows, 'replace', side_effect=race):
            with self.assertRaises(PublicationError) as caught:self.save()
        self.assertEqual(self.path.read_bytes(), LATER)
        self.assertIn(LOCAL, retained(caught.exception.result))
        self.assertIn(BASE, retained(caught.exception.result))

    def test_both_races_keep_first_external_and_leave_latest_in_place(self):
        def race(destination, temporary, backup):
            external_replace(destination, EXTERNAL)
            NATIVE_REPLACE(destination, temporary, backup)
            external_replace(destination, LATER)
        with patch.object(windows, 'replace', side_effect=race):
            with self.assertRaises(PublicationError) as caught:self.save()
        self.assertEqual(self.path.read_bytes(), LATER)
        self.assertIn(EXTERNAL, retained(caught.exception.result))
        self.assertIn(LOCAL, retained(caught.exception.result))

    def test_in_place_writer_is_denied_during_publication(self):
        denied = []
        def race(destination, temporary, backup):
            try:destination.write_bytes(EXTERNAL)
            except PermissionError:denied.append(True)
            NATIVE_REPLACE(destination, temporary, backup)
        with patch.object(windows, 'replace', side_effect=race):self.save()
        self.assertEqual(denied, [True])
        self.assertEqual(self.path.read_bytes(), LOCAL)

    def test_prepublication_failure_keeps_original_and_all_available_bytes(self):
        with patch.object(windows, 'replace', side_effect=OSError('native denied')):
            with self.assertRaises(PublicationError) as caught:self.save()
        self.assertEqual(self.path.read_bytes(), BASE)
        self.assertIn(LOCAL, retained(caught.exception.result))
        self.assertIn(BASE, retained(caught.exception.result))
        self.assertEqual(self.save().status, 'saved')  # Explicit safe retry.

    def test_native_partial_move_failure_does_not_claim_original_unchanged(self):
        def partial(destination, temporary, backup):
            os.replace(destination, backup)
            raise OSError('native failed after moving original')
        with patch.object(windows, 'replace', side_effect=partial):
            with self.assertRaises(PublicationError) as caught:self.save()
        self.assertFalse(self.path.exists())
        self.assertTrue(caught.exception.result.uncertain)
        self.assertIn(BASE, retained(caught.exception.result))
        self.assertIn(LOCAL, retained(caught.exception.result))

    def test_native_error_after_real_replacement_keeps_both_versions(self):
        def partial(destination, temporary, backup):
            NATIVE_REPLACE(destination, temporary, backup)
            raise OSError('failed after replace')
        with patch.object(windows, 'replace', side_effect=partial):
            with self.assertRaises(PublicationError) as caught:self.save()
        self.assertEqual(self.path.read_bytes(), LOCAL)
        self.assertIn(BASE, retained(caught.exception.result))
        self.assertIn(LOCAL, retained(caught.exception.result))


@unittest.skipUnless(os.name == 'nt', 'Windows actual-displacement contract')
class MarkdownRaceWorkspaceTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    open = fixture.WorkspacePaneTests.open
    events = fixture.WorkspacePaneTests.events

    def test_shared_views_stay_dirty_stop_queued_autosave_and_allow_save_as(self):
        (self.root/'Notes.md').write_bytes(BASE)
        left = self.open('Notes.md', retained=True)
        right = self.w.duplicate_view(left, True)
        left.widget.insertPlainText('本機稿')
        candidate = left.widget.encoded()
        def race(destination, temporary, backup):
            external_replace(destination, EXTERNAL)
            NATIVE_REPLACE(destination, temporary, backup)
        with patch.object(windows, 'replace', side_effect=race):
            self.assertFalse(self.w.save_tab(right))
        self.assertTrue(left.dirty and right.dirty)
        self.assertEqual(left.widget.baseline, BASE)
        self.assertIs(left.widget.last_result, right.widget.last_result)
        self.assertIn(candidate, retained(right.widget.last_result))
        self.assertIn(EXTERNAL, retained(left.widget.last_result))
        copies = tuple(self.root.glob('*.recovery-*'))
        with patch.object(left.widget, 'save', wraps=left.widget.save) as saves:
            self.w.autosave.flush(left)  # A timeout already queued before failure.
            self.w.autosave.schedule(right)
            QTest.qWait(1100)
            self.assertEqual(saves.call_count, 0)
        self.assertEqual(copies, tuple(self.root.glob('*.recovery-*')))
        self.assertEqual(right.widget.encoded(), candidate)
        self.assertTrue(left.widget.document().isUndoAvailable())
        context = self.w.commands.context('tab', tab=right)
        rescue = self.w.commands.files.run('save_as', context, name='Rescued', directory=self.root)
        self.assertIsNotNone(rescue, self.errors)
        self.assertEqual(rescue.read_bytes(), candidate)
        self.assertFalse(left.dirty or right.dirty)
        self.assertFalse(left.widget.error)
        self.assertIsNone(right.widget.last_result)
        self.assertTrue(all(p.exists() for p in copies))

    def test_reference_conflict_marks_recovery_and_preserves_both_views(self):
        reference = self.root/'Incoming.md';reference.write_bytes(b'[[Notes]]\n')
        source = self.open('Notes.md', retained=True)
        other = self.open('Incoming.md', retained=True, new_tab=True)
        duplicate = self.w.duplicate_view(other, True)
        other.widget.insertPlainText('draft ')
        draft = other.widget.encoded()
        def race(destination, temporary, backup):
            if destination == reference:external_replace(destination, EXTERNAL)
            NATIVE_REPLACE(destination, temporary, backup)
        with patch.object(windows, 'replace', side_effect=race):
            result = self.w.commands.files.run('rename', self.w.commands.context('tab', tab=source), name='Renamed')
        self.assertIsNone(result)
        self.assertTrue(other.widget.operation_error and duplicate.widget.operation_error)
        self.assertTrue(source.widget.operation_error)
        self.assertEqual(other.widget.encoded(), draft)
        self.assertIn(EXTERNAL, retained(other.widget.last_result))
        self.assertFalse(other.widget.save())
        journal = next((self.w.data/'relation-recovery').glob('*/operation.json'))
        self.assertEqual(json.loads(journal.read_text(encoding='utf-8'))['state'], 'recovery-needed')
        self.assertFalse(self.w.autosave.paused)

    def test_tab_recovery_action_displays_both_preserved_versions(self):
        tab = self.open('Notes.md', retained=True)
        tab.widget.insertPlainText('local draft ')
        candidate = tab.widget.encoded().decode('utf-8-sig')
        def race(destination, temporary, backup):
            external_replace(destination, EXTERNAL)
            NATIVE_REPLACE(destination, temporary, backup)
        with patch.object(windows, 'replace', side_effect=race):
            self.assertFalse(self.w.save_tab(tab))
        pane = self.w.pane_for(tab)
        previous = {id(menu) for menu in self.w.findChildren(RoundMenu)}
        self.w.tab_menu(pane.strip.bar.tabRect(self.w.index_of(tab.key)).center(), pane)
        menu = next(menu for menu in self.w.findChildren(RoundMenu) if id(menu) not in previous)
        action = next(action for action in menu.actions() if action.text() == '檢視回復版本')
        menu.close()
        shown = []
        def inspect():
            dialog = self.app.activeModalWidget()
            try:
                choices = dialog.findChild(QListWidget)
                viewer = dialog.findChild(QPlainTextEdit)
                for row in range(choices.count()):
                    choices.setCurrentRow(row)
                    shown.append(viewer.toPlainText())
            finally:dialog.accept()
        QTimer.singleShot(0, inspect)
        action.trigger()
        self.assertIn(candidate.replace('\r\n','\n'), shown)
        self.assertIn(EXTERNAL.decode('utf-8-sig').replace('\r\n','\n'), shown)

    def composition(self, extract=False):
        (self.root/'Notes.md').write_bytes(BASE)
        target = self.root/'Target.md';target.write_bytes(b'# target\n')
        tab = self.open('Notes.md', retained=True)
        if extract:
            cursor = tab.widget.md_surface.editor.textCursor()
            cursor.select(cursor.SelectionType.Document)
            tab.widget.md_surface.editor.setTextCursor(cursor)
        return Composition(self.w.notebook.operations(), tab.widget.md_surface, target, extract), tab, target

    def rollback_race(self):
        calls = []
        def write(path, before, after):
            calls.append(path)
            if len(calls) == 2:raise OSError('second file refused before publication')
            if len(calls) == 3:
                def race(destination, temporary, backup):
                    external_replace(destination, EXTERNAL)
                    NATIVE_REPLACE(destination, temporary, backup)
                    external_replace(destination, LATER)
                with patch.object(windows, 'replace', side_effect=race):
                    return checked_write(path, before, after)
            return checked_write(path, before, after)
        return calls, write

    def test_reference_rollback_conflict_keeps_newer_external(self):
        for name in ('First.md','Second.md'):(self.root/name).write_bytes(b'[[Notes]]\n')
        source = self.open('Notes.md', retained=True)
        reference = self.open('First.md', retained=True, new_tab=True)
        calls, write = self.rollback_race()
        with patch('prompt_calculus_studio.markdown_relocation.checked_write', side_effect=write):
            result = self.w.commands.files.run('rename', self.w.commands.context('tab',tab=source), name='Moved')
        self.assertIsNone(result)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0].read_bytes(), LATER)
        self.assertTrue(reference.widget.operation_error)
        self.assertIn(EXTERNAL, retained(reference.widget.last_result))

    def test_composition_rollback_conflict_keeps_newer_external(self):
        (self.root/'Ref.md').write_bytes(b'[[Notes]]\n')
        plan, source, target = self.composition()
        calls, write = self.rollback_race()
        with patch('prompt_calculus_studio.notebook_composer.checked_write', side_effect=write):
            with self.assertRaises(ValueError):plan.commit()
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0].read_bytes(), LATER)
        self.assertTrue(plan.recovery_needed)
        self.assertTrue(source.widget.operation_error)
        self.assertIn(EXTERNAL, retained(source.widget.last_result))

    def test_merge_conflict_before_apply_blocks_retry_and_preserves_source(self):
        plan, source, target = self.composition()
        def race(destination, temporary, backup):
            if destination == target:external_replace(destination, EXTERNAL)
            NATIVE_REPLACE(destination, temporary, backup)
        with patch.object(windows, 'replace', side_effect=race):
            with self.assertRaises(ValueError):plan.commit()
        self.assertEqual(source.path.read_bytes(), BASE)
        self.assertFalse(plan.committed)
        self.assertTrue(source.widget.operation_error)
        self.assertIn(EXTERNAL, retained(source.widget.last_result))
        self.assertEqual(json.loads((plan.journal/'operation.json').read_text(encoding='utf-8'))['state'], 'recovery-needed')
        with self.assertRaises(ValueError):plan.commit()
        self.assertFalse(self.w.autosave.paused)

    def test_extract_undo_conflict_is_not_reported_as_complete(self):
        plan, source, target = self.composition(True)
        plan.commit()
        def race(destination, temporary, backup):
            if destination == target:
                NATIVE_REPLACE(destination, temporary, backup)
                external_replace(destination, LATER)
            else:NATIVE_REPLACE(destination, temporary, backup)
        with patch.object(windows, 'replace', side_effect=race):
            with self.assertRaises(ValueError):plan.undo()
        self.assertEqual(target.read_bytes(), LATER)
        self.assertTrue(plan.recovery_needed)
        self.assertEqual(json.loads((plan.journal/'operation.json').read_text(encoding='utf-8'))['state'], 'recovery-needed')
        with self.assertRaises(ValueError):plan.undo()
        self.assertFalse(self.w.autosave.paused)
