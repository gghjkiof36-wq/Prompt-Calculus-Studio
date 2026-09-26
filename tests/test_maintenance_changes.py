"""Protect side-effect boundaries and actual saved geometry, not implementation shape."""
import os,tempfile,unittest,copy
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from prompt_studio.window import Window
from prompt_studio.multi_output import compiled_outputs
from prompt_studio.media import scan_models
APP=QApplication.instance() or QApplication([])

class ChangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        with patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1'}):self.w=Window(self.tmp.name)
        self.w.state['settings'].update(online=False,material='solid'); self.w.show(); self.w.set_interface_mode('canvas'); QTest.qWait(50)
    def tearDown(self):self.w.close(); APP.processEvents(); self.tmp.cleanup()
    def test_burst_prompt_updates_coalesce_and_never_generate(self):
        with patch.object(self.w.canvas,'update_output',wraps=self.w.canvas.update_output) as paint,patch.object(self.w.comfy,'schedule_sync') as sync,patch.object(self.w.comfy,'run') as run:
            for _ in range(20):self.w.changed()
            APP.processEvents(); self.assertEqual(paint.call_count,1); self.assertEqual(sync.call_count,1); run.assert_not_called()
    def test_workflow_panel_does_not_duplicate_canvas_or_sync(self):
        with patch.object(self.w.canvas,'update_output') as paint,patch.object(self.w.comfy,'schedule_sync') as sync:
            for _ in range(3):self.w.generation_panel.changed()
            APP.processEvents(); self.assertEqual(paint.call_count,1); self.assertEqual(sync.call_count,1)
    def test_settings_and_collection_destination_do_not_recompile_prompt(self):
        self.w.settings('completion'); QTest.qWait(30)
        with patch.object(self.w,'refresh_builder') as compile,patch.object(self.w.comfy,'schedule_sync') as sync:
            self.w.settings_page.preferences.online.setChecked(False); self.w.settings_page.flush()
            self.w.run_controls.count.setValue(4); self.w.recent.destination_changed(); APP.processEvents()
            compile.assert_not_called(); sync.assert_not_called(); self.assertEqual(self.w.state['settings']['comfy_count'],4)
    def test_model_notes_preserve_source_without_prompt_work(self):
        root=Path(self.tmp.name)/'models'; (root/'loras').mkdir(parents=True); (root/'loras/m.safetensors').write_bytes(b'fixture')
        m=self.w.models; m.root.setText(str(root)); self.w.catalog.merge_models(scan_models(root),root); m.refresh(); m.list.setCurrentRow(0); APP.processEvents()
        before=compiled_outputs(self.w.state)
        with patch.object(self.w.canvas,'update_output') as compile,patch.object(self.w.comfy,'schedule_sync') as sync:
            m.notes.setPlainText('personal note'); m.save(); APP.processEvents(); compile.assert_not_called(); sync.assert_not_called()
        self.assertEqual(self.w.catalog.get(m.record['id'])['notes'],'personal note'); self.assertEqual(compiled_outputs(self.w.state),before)
    def test_geometry_and_undo_preserve_text_and_resolution_without_sync(self):
        c=self.w.canvas; key=next(iter(c.data()['outputs'])); old=copy.deepcopy(self.w.state.get('text_positions',{})); text=compiled_outputs(self.w.state); position=c.outputs[key].pos()
        with patch.object(self.w.comfy,'schedule_sync') as sync,patch.object(c,'update_output') as compile:
            c.move_cards({key:[40,65]}); APP.processEvents(); self.assertEqual([c.outputs[key].x(),c.outputs[key].y()],[40,65])
            c.undo(); APP.processEvents(); self.assertEqual(self.w.state.get('text_positions',{}),old)
            self.assertEqual(c.outputs[key].pos(),position)
            c.redo(); APP.processEvents(); self.assertEqual(self.w.state['text_positions'][key],[40,65]); sync.assert_not_called(); compile.assert_not_called()
        self.assertEqual(compiled_outputs(self.w.state),text)
    def test_invalid_change_scope_cannot_silently_skip_required_work(self):
        with self.assertRaises(ValueError):self.w.changed('mistyped_scope')

if __name__=='__main__':unittest.main()
