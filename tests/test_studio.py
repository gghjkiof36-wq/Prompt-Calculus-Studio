import copy
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib
import threading
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/"vendor"))
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QImage, QTextCursor, QInputMethodEvent
from PySide6.QtCore import Qt, QTimer, QModelIndex
from prompt_calculus_studio.core import initial_state, build_prompt, apply_workspace, validate_state, current_token, insertion, Storage, reorder_output, output_groups, TEMPORARY_GROUP
from prompt_calculus_studio.media import checked_model, scan_models, copy_model, png_metadata, import_image, Catalog
from prompt_calculus_studio.validation import validate_resources
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.dialogs import WorkspaceDialog, SettingsDialog, ClearDraftDialog

APP=QApplication.instance() or QApplication([])
QFontDatabase.addApplicationFont("C:/Windows/Fonts/msjh.ttc")

class CoreTests(unittest.TestCase):
    def test_appearance_preferences_validate_and_load_old_data(self):
        s=initial_state(); s["settings"].pop("acrylic_transparency"); s["settings"].pop("confirm_clear_draft")
        with tempfile.TemporaryDirectory(dir=ROOT/"qa") as directory:
            store=Storage(directory); store.save(s); loaded=store.load(); store.close()
        self.assertEqual(loaded["settings"]["acrylic_transparency"],61)
        self.assertTrue(loaded["settings"]["confirm_clear_draft"])
        for key,value in (("acrylic_transparency",-1),("acrylic_transparency",101),
                          ("acrylic_transparency",True),("confirm_clear_draft","false")):
            broken=copy.deepcopy(loaded); broken["settings"][key]=value
            with self.assertRaises(ValueError): validate_state(broken)

    def test_group_reorder_preserves_empty_modules_and_roundtrips(self):
        s=initial_state(); modules=s["modules"]; first=s["items"][0]; pose=s["items"][2]
        s["selections"]={first["module"]:[first["id"]],pose["module"]:[pose["id"]]}; s["temporary"]=["white wall"]
        reorder_output(s,("group",TEMPORARY_GROUP),("group",first["module"]))
        self.assertTrue(build_prompt(s).startswith("white wall,\n1girl"))
        reorder_output(s,("group",pose["module"]),("group",first["module"]))
        self.assertEqual(build_prompt(s),"white wall,\nstanding, hands behind back,\n1girl, red dress")
        self.assertEqual(s["modules"],modules)
        with tempfile.TemporaryDirectory(dir=ROOT/"qa") as directory:
            store=Storage(directory); store.save(s); restored=store.load(); store.close()
            self.assertEqual(output_groups(restored),output_groups(s)); self.assertEqual(build_prompt(restored),build_prompt(s))

    def test_fragment_and_item_order_rejects_cross_module_changes(self):
        s=initial_state(); a,b=s["items"][4:6]; other=s["items"][0]
        s["selections"]={a["module"]:[a["id"],b["id"]],other["module"]:[other["id"]]}
        reorder_output(s,("item",b["id"]),("item",a["id"]))
        self.assertEqual(s["selections"][a["module"]],[b["id"],a["id"]])
        s["temporary"]=["same","middle","same"]
        self.assertEqual(reorder_output(s,("temporary",2),("temporary",1)),("temporary",1))
        self.assertEqual(s["temporary"],["same","same","middle"])
        before=copy.deepcopy(s)
        with self.assertRaises(ValueError): reorder_output(s,("item",a["id"]),("item",other["id"]))
        self.assertEqual(s,before)

    def test_workspace_restores_fixed_and_keeps_dynamic_and_draft(self):
        s=initial_state(); a,b=s["modules"][2:4]; first=s["items"][0]; pose=s["items"][2]
        s["selections"]={a["id"]:[first["id"]],b["id"]:[pose["id"]]}; s["draft"]="custom"
        w=s["workspaces"][0]; w["fixed"]=[a["id"]]; w["picks"]={a["id"]:[]}
        apply_workspace(s,w["id"])
        self.assertEqual(s["selections"][a["id"]],[]); self.assertEqual(s["selections"][b["id"]],[pose["id"]]); self.assertEqual(s["draft"],"custom")

    def test_order_and_temporary(self):
        s=initial_state(); first,pose=s["items"][0],s["items"][2]
        s["selections"]={first["module"]:[first["id"]],pose["module"]:[pose["id"]]}; s["modules"].reverse(); s["temporary"]=["white wall"]
        self.assertEqual(build_prompt(s),pose["prompt"]+",\n"+first["prompt"]+",\nwhite wall")

    def test_validate_rejects_cross_module_and_duplicate(self):
        s=initial_state(); s["selections"]={s["modules"][0]["id"]:[s["items"][0]["id"]]}
        with self.assertRaises(ValueError): validate_state(s)
        s=initial_state(); s["items"].append(copy.deepcopy(s["items"][0]))
        with self.assertRaises(ValueError): validate_state(s)

    def test_insert_and_preserve_weight_syntax(self):
        text="1girl, simp, night"; token=current_token(text,11)
        start,end,value=insertion(text,token,"simple background")
        self.assertEqual(text[:start]+value+text[end:],"1girl, simple background, night")
        self.assertIsNone(current_token("(red dress:1.2)",9))

    def test_storage_roundtrip_backup(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"qa") as directory:
            store=Storage(directory); s=initial_state(); store.save(s); self.assertEqual(store.load()["items"],s["items"])
            self.assertTrue(store.backup().is_file()); store.cache("a",[{"value":"apple"}]); self.assertEqual(store.cached("a")[0]["value"],"apple"); store.close()

class FileTests(unittest.TestCase):
    def test_cancelled_scan_does_not_return_partial_catalog(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"qa") as directory:
            root=Path(directory); (root/"loras").mkdir(); (root/"loras"/"a.safetensors").write_bytes(b"test")
            cancel=threading.Event(); cancel.set()
            with self.assertRaisesRegex(ValueError,"已取消"): scan_models(root,cancel)

    def test_zip_backup_and_owned_original_relocation(self):
        from prompt_calculus_studio.backup import archive_data
        with tempfile.TemporaryDirectory(dir=ROOT/"qa") as directory:
            root=Path(directory); path=root/"image.png"; image=QImage(64,64,QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.red); image.save(str(path))
            store=Storage(root/"data"); store.save(initial_state()); catalog=Catalog(store)
            record=import_image(path,store.directory,"album",copy_original=True); catalog.put("album",dict(id="album",name="Album")); catalog.put("image",record,"album")
            snapshot=store.backup(); archive=archive_data(store.directory,snapshot,root/"backup.zip")
            with zipfile.ZipFile(archive) as bundle:
                self.assertIn("data/studio.sqlite3",bundle.namelist()); self.assertTrue(any(n.startswith("data/originals/") for n in bundle.namelist()))
            relocated=root/"relocated"; relocated.mkdir()
            with zipfile.ZipFile(archive) as bundle: bundle.extractall(relocated)
            restored=Storage(relocated/"data"); restored_catalog=Catalog(restored); moved=restored_catalog.get(record["id"])
            self.assertTrue(Path(moved["path"]).is_relative_to(relocated)); self.assertTrue(Path(moved["path"]).is_file())
            self.assertNotIn("metadata",restored_catalog.rows("image","album")[0]); restored.close(); store.close()

    def test_familiar_artist_is_available_without_network(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"qa") as directory:
            store=Storage(directory)
            store.use("w","apple_caramel",dict(value="apple_caramel",category=1,source="Danbooru"))
            self.assertEqual(store.familiar("w","ap",True)[0]["value"],"apple_caramel")
            self.assertEqual(store.familiar("different_workspace","ap",True),[]); store.close()

    def test_safe_scan_copy_and_unchanged_fingerprint(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"qa") as directory:
            root=Path(directory)/"models"; (root/"loras").mkdir(parents=True)
            source=Path(directory)/"a.safetensors"; source.write_bytes(b"test-model")
            target=Path(copy_model(source,root,"loras")); rows=scan_models(root)
            self.assertEqual(len(rows),1); self.assertEqual(source.read_bytes(),target.read_bytes())
            with self.assertRaises(ValueError): copy_model(source,root,"loras")
            with self.assertRaises(ValueError): checked_model(root,source)
            target.write_bytes(b"changed")
            with self.assertRaises(ValueError): checked_model(root,target,rows[0])

    def test_metadata_link_preview_and_manual_provenance(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"qa") as directory:
            path=Path(directory)/"test.png"; img=QImage(640,480,QImage.Format.Format_RGB32); img.fill(Qt.GlobalColor.darkGray)
            graph={"3":{"class_type":"KSampler","inputs":{"cfg":5.5,"steps":22,"seed":12,"positive":["6",0]}},"6":{"class_type":"CLIPTextEncode","inputs":{"text":"1girl, white wall"}}}
            img.setText("prompt",json.dumps(graph)); img.save(str(path))
            meta=png_metadata(path); self.assertEqual(meta["source"],"embedded"); self.assertEqual(meta["raw"]["prompt"],graph)
            record=import_image(path,Path(directory)/"data","album")
            self.assertFalse(record["owned"]); self.assertEqual(record["path"],str(path.resolve())); self.assertTrue(path.exists())
            preview=QImage(str(Path(directory)/"data"/record["thumb"])); self.assertLessEqual(max(preview.width(),preview.height()),512)
            store=Storage(Path(directory)/"data"); catalog=Catalog(store); snap=catalog.snapshot(initial_state())
            self.assertEqual(snap["source"],"manual_recommendation"); catalog.put("image",record,"album"); self.assertEqual(catalog.count("image","album"),1); store.close()

    def test_manifest_rejects_orphan_image(self):
        row=dict(id="i",kind="image",parent="missing",name="image",body=dict(id="i",name="image",album="missing",path="x.png"))
        with self.assertRaises(ValueError): validate_resources([row])

class UiTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(dir=ROOT/"qa")
        self.window=Window(self.directory.name); self.window.state["settings"]["online"]=False; self.window.show(); APP.processEvents()
        self.errors=[]; self.window.error=self.errors.append

    def tearDown(self):
        self.window.completion.timer.stop(); self.window.close(); APP.processEvents(); self.directory.cleanup()

    def test_selection_copy_manual_draft(self):
        w=self.window; w.current_module=w.state["modules"][2]["id"]; w.refresh_library(); w.toggle_item(w.library.item(0))
        self.assertEqual(w.final.toPlainText(),"1girl, red dress")
        w.final.setPlainText("hand edited"); w.add_temporary("white wall")
        self.assertEqual(w.final.toPlainText(),"hand edited")
        w.copy_module(w.current_module); self.assertEqual(APP.clipboard().text(),"1girl, red dress")
        with patch("prompt_calculus_studio.window.ClearDraftDialog.exec",return_value=1): w.regenerate()
        self.assertIn("white wall",w.final.toPlainText()); self.assertIsNone(w.state["draft"])

    def test_display_refresh_preserves_draft_cursor_preferences_and_collapsed_panels(self):
        from PySide6.QtTest import QTest
        from prompt_calculus_studio.theme import font_pixels
        w=self.window; w.add_temporary("retained"); w.final.setPlainText("manual prompt")
        cursor=w.final.textCursor(); cursor.setPosition(4); w.final.setTextCursor(cursor)
        w.module_fold.set_expanded(False,animated=False)
        saved=copy.deepcopy(w.state); recovery=w.display_recovery
        recovery.native_notification(0x218,0x12)
        QTest.qWait(450)
        self.assertEqual(w.state,saved); self.assertEqual(w.final.textCursor().position(),4)
        self.assertTrue(w.module_panel.isHidden()); self.assertFalse(w.selected.isEnabled())
        self.assertEqual(w.final.font().pixelSize(),font_pixels(w.state["settings"]["prompt_size"]))
        self.assertGreater(recovery.refresh_count,0)
        recovery.stop(); self.assertFalse(recovery.timer.isActive()); self.assertFalse(recovery.settle.isActive())

    def test_manual_version_clear_cancel_and_empty_draft(self):
        from PySide6.QtTest import QTest
        w=self.window; w.add_temporary("white wall")
        self.assertFalse(w.clear_draft.isEnabled()); self.assertTrue(w.selected.isEnabled())
        w.final.setPlainText("")  # Empty is still a deliberately edited version.
        QTest.qWait(220)
        self.assertTrue(w.clear_draft.isEnabled()); self.assertFalse(w.selected.isEnabled())
        self.assertAlmostEqual(w.selected.activity,0.42,places=2)
        self.assertIn("正在使用手動版本",w.draft_status.text())
        selections=copy.deepcopy(w.state["selections"])
        with patch("prompt_calculus_studio.window.ClearDraftDialog.exec",return_value=0): w.clear_draft.click()
        self.assertEqual(w.state["draft"],""); self.assertEqual(w.final.toPlainText(),"")
        with patch("prompt_calculus_studio.window.ClearDraftDialog.exec",return_value=1): w.clear_draft.click()
        QTest.qWait(220)
        self.assertIsNone(w.state["draft"]); self.assertEqual(w.final.toPlainText(),"white wall")
        self.assertEqual(w.state["selections"],selections); self.assertEqual(w.state["temporary"],["white wall"])
        self.assertTrue(w.selected.isEnabled()); self.assertFalse(w.clear_draft.isEnabled())
        self.assertAlmostEqual(w.selected.activity,1.0,places=2)

    def test_clear_draft_remember_only_on_confirmation_and_restore_prompt(self):
        w=self.window; w.add_temporary("white wall"); w.final.setPlainText("keep this draft")
        def respond(accepted):
            dialog=APP.activeModalWidget()
            dialog.dont_ask_again.setChecked(True)
            dialog.accept() if accepted else dialog.reject()
        QTimer.singleShot(40,lambda:respond(False)); w.regenerate()
        self.assertEqual(w.state["draft"],"keep this draft"); self.assertTrue(w.state["settings"]["confirm_clear_draft"])
        QTimer.singleShot(40,lambda:respond(True)); w.regenerate()
        self.assertEqual(w.final.toPlainText(),"white wall"); self.assertFalse(w.state["settings"]["confirm_clear_draft"])
        w.persist(); self.assertFalse(w.store.load()["settings"]["confirm_clear_draft"])
        w.final.setPlainText("another draft")
        with patch("prompt_calculus_studio.window.ClearDraftDialog",side_effect=AssertionError("Unexpected confirmation")):
            w.regenerate()
        self.assertEqual(w.final.toPlainText(),"white wall"); self.assertEqual(w.state["temporary"],["white wall"])
        settings=SettingsDialog(w); settings.confirm_clear_draft.setChecked(True); settings.save()
        w.final.setPlainText("ask again")
        with patch("prompt_calculus_studio.window.ClearDraftDialog.exec",return_value=0) as ask_again: w.regenerate()
        ask_again.assert_called_once(); self.assertEqual(w.state["draft"],"ask again")
        settings.deleteLater()

    def test_acrylic_slider_visibility_preview_cancel_and_persistence(self):
        w=self.window; before=copy.deepcopy(w.state["settings"])
        # Native compositing is unavailable in offscreen tests; this checks the
        # same application stylesheet and controls with a supported backdrop.
        with patch("prompt_calculus_studio.window.apply_backdrop",return_value=True):
            w.apply_theme(); original=w.styleSheet()
            dialog=SettingsDialog(w); dialog.show(); APP.processEvents()
            self.assertTrue(dialog.material_form.isRowVisible(dialog.transparency_row))
            dialog.material.setCurrentIndex(dialog.material.findData("acrylic"))
            self.assertTrue(dialog.material_form.isRowVisible(dialog.transparency_row))
            dialog.transparency.setValue(0); self.assertEqual(dialog.transparency_value.value(),0)
            self.assertIn("background:rgba(14,14,14,255)",w.styleSheet())
            dialog.transparency_value.setValue(100); self.assertEqual(dialog.transparency.value(),100)
            self.assertIn("background:rgba(14,14,14,0)",w.styleSheet())
            self.assertIn("QFrame#WorkspaceSurface { background:#111111",w.styleSheet())
            dialog.material.setCurrentIndex(dialog.material.findData("mica"))
            self.assertTrue(dialog.material_form.isRowVisible(dialog.transparency_row))
            self.assertEqual(dialog.transparency.value(),61)
            self.assertIn("background:rgba(14,14,14,99)",w.styleSheet())
            dialog.transparency.setValue(45)
            dialog.material.setCurrentIndex(dialog.material.findData('acrylic'))
            self.assertEqual(dialog.transparency.value(),100)
            dialog.material.setCurrentIndex(dialog.material.findData('mica'))
            self.assertEqual(dialog.transparency.value(),45)
            self.assertEqual(w.state["settings"],before)
            dialog.reject(); self.assertEqual(w.styleSheet(),original); dialog.deleteLater()
            saved=SettingsDialog(w); saved.material.setCurrentIndex(saved.material.findData("acrylic"))
            saved.transparency_value.setValue(37); saved.save(); w.persist()
            self.assertEqual(w.store.load()["settings"]["acrylic_transparency"],37)
            reopened=SettingsDialog(w)
            self.assertEqual(reopened.transparency.value(),37)
            self.assertTrue(reopened.material_form.isRowVisible(reopened.transparency_row))
            saved.deleteLater(); reopened.reject(); reopened.deleteLater()

    def test_copy_feedback_repeated_click_and_edit_reset(self):
        from PySide6.QtTest import QTest
        w=self.window; w.add_temporary("white wall"); w.copy_button.click()
        self.assertEqual(APP.clipboard().text(),"white wall"); self.assertIn("已複製",w.copy_button.text())
        w.copy_button.click(); self.assertTrue(w.copy_timer.isActive())
        w.final.setPlainText("red wall"); self.assertFalse(w.copy_timer.isActive())
        self.assertEqual(w.copy_button.text(),"複製完整 Prompt")
        w.copy_button.click(); QTest.qWait(1500)
        self.assertEqual(APP.clipboard().text(),"red wall"); self.assertFalse(w.copy_button.property("feedback"))
        w.copy_button.click(); w.add_temporary("soft lighting")
        self.assertFalse(w.copy_button.property("feedback"))

    def test_folds_reverse_without_losing_content_or_staying_active(self):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import QAbstractAnimation
        w=self.window; w.add_temporary("retained")
        for fold in (w.module_fold,w.builder_fold):
            fold.set_expanded(False); QTest.qWait(230)
            self.assertTrue(fold.widget.isHidden())
            fold.set_expanded(True); QTest.qWait(70); fold.set_expanded(False); QTest.qWait(70)
            fold.set_expanded(True); QTest.qWait(250)
            self.assertFalse(fold.widget.isHidden()); self.assertTrue(fold.expanded)
            self.assertEqual(fold.animation.state(),QAbstractAnimation.State.Stopped)
            self.assertGreater(fold.splitter.sizes()[fold.splitter.indexOf(fold.widget)],60)
        self.assertEqual(w.final.toPlainText(),"retained")

    def test_module_list_move_updates_canonical_order(self):
        w=self.window; prior=[m["id"] for m in w.state["modules"]]
        self.assertTrue(w.module_list.model().moveRow(QModelIndex(),4,QModelIndex(),1))
        expected=list(prior); expected.insert(1,expected.pop(4))
        self.assertEqual([m["id"] for m in w.state["modules"]],expected)

    def test_output_drag_keeps_sidebar_and_new_groups_use_sidebar_positions(self):
        w=self.window; modules=copy.deepcopy(w.state["modules"])
        lora,artist=modules[:2]; pose=w.state["items"][2]; expression=w.state["items"][4]
        for module in (lora,artist):
            item=copy.deepcopy(pose); item.update(id=module["id"]+"-item",module=module["id"],name=module["name"],prompt=module["name"])
            w.state["items"].append(item)
        w.state["selections"]={pose["module"]:[pose["id"]],expression["module"]:[expression["id"]]}
        w.refresh_builder(); w.reorder_builder(("group",expression["module"]),("group",pose["module"]))
        self.assertEqual(w.state["modules"],modules)
        self.assertEqual([w.module_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(len(modules))],[m["id"] for m in modules])
        self.assertEqual(output_groups(w.state),[expression["module"],pose["module"]])
        for module in (lora,artist):
            w.current_module=module["id"]; w.refresh_library(); w.toggle_item(w.library.item(0))
        expected=[lora["id"],artist["id"],expression["module"],pose["module"]]
        self.assertEqual(output_groups(w.state),expected)
        w.persist(); self.assertEqual(output_groups(w.store.load()),expected)
        w.reorder_builder(("group",lora["id"]),("group",pose["module"]),True)
        w.current_module=lora["id"]; w.refresh_library(); w.toggle_item(w.library.item(0)); w.toggle_item(w.library.item(0))
        self.assertEqual(output_groups(w.state),expected)
        self.assertEqual(w.state["modules"],modules)

    def test_output_group_validation(self):
        for order in (None,["missing"],[self.window.state["modules"][0]["id"]]*2):
            s=copy.deepcopy(self.window.state); s["output_order"]=order
            with self.assertRaises(ValueError): validate_state(s)

    def test_custom_name_dialog_returns_text_before_cleanup(self):
        from prompt_calculus_studio.widgets import InputDialog
        from PySide6.QtWidgets import QLineEdit
        def accept():
            dialog=APP.activeModalWidget(); dialog.findChild(QLineEdit).setText("Anima 版本 2"); dialog.accept()
        QTimer.singleShot(0,accept)
        text,ok=InputDialog.getText(self.window,"新增工作區","工作區名稱")
        self.assertTrue(ok); self.assertEqual(text,"Anima 版本 2")
        QTimer.singleShot(0,lambda:APP.activeModalWidget().reject())
        text,ok=InputDialog.getText(self.window,"新增工作區","工作區名稱")
        self.assertFalse(ok)

    def test_builder_reorder_updates_output_and_keeps_manual_draft(self):
        w=self.window; a,b=w.state["items"][4:6]; mid=a["module"]
        w.state["selections"]={mid:[a["id"],b["id"]]}; w.state["temporary"]=["first","second"]
        w.refresh_builder(); w.reorder_builder(("temporary",1),("temporary",0))
        self.assertTrue(w.final.toPlainText().endswith("second,\nfirst"))
        w.reorder_builder(("group",TEMPORARY_GROUP),("group",mid))
        self.assertTrue(w.final.toPlainText().startswith("second,\nfirst"))
        w.final.setPlainText("keep this draft")
        w.reorder_builder(("item",b["id"]),("item",a["id"]))
        self.assertEqual(w.final.toPlainText(),"keep this draft")
        self.assertIn("looking at viewer, smile",build_prompt(w.state)); self.assertFalse(self.errors)

    def test_builder_drop_routes_to_state_and_rejects_external_group(self):
        from PySide6.QtCore import QPointF
        w=self.window; item=w.state["items"][0]; w.state["selections"]={item["module"]:[item["id"]]}; w.state["temporary"]=["one","two"]
        w.refresh_builder(); tree=w.selected; tree.show(); APP.processEvents()
        temp=tree.topLevelItem(1); source=temp.child(1); target=temp.child(0); tree.setCurrentItem(source)
        tree._drag_source=tree.key(source)
        pos=QPointF(tree.visualItemRect(target).topLeft()); pos.setX(pos.x()+40); pos.setY(pos.y()+4)
        class Drop:
            accepted=False
            def source(self): return tree
            def position(self): return pos
            def acceptProposedAction(self): self.accepted=True
            def ignore(self): self.accepted=False
        event=Drop(); tree.dropEvent(event)
        self.assertTrue(event.accepted); self.assertEqual(w.state["temporary"],["two","one"])
        temp=tree.topLevelItem(1); tree.setCurrentItem(temp.child(0)); tree._drag_source=tree.key(temp.child(0))
        self.assertIsNone(tree.drop_target(tree.visualItemRect(tree.topLevelItem(0).child(0)).center()))

    def test_chinese_completion_acceptance_and_stale_result(self):
        w=self.window; editor=w.quick; editor.setFocus(); editor.setPlainText("白色的牆壁"); cursor=editor.textCursor(); cursor.movePosition(QTextCursor.MoveOperation.End); editor.setTextCursor(cursor); APP.processEvents()
        token=editor.context(); editor.display(token,[dict(value="white wall",category=None,source="自訂字典")])
        editor.insert_completion(next(iter(editor.candidates)))
        self.assertEqual(w.state["temporary"],["white wall"]); self.assertIsNone(w.state["draft"]); self.assertEqual(editor.toPlainText(),"")
        editor.setPlainText("simple"); cursor=editor.textCursor(); cursor.movePosition(QTextCursor.MoveOperation.End); editor.setTextCursor(cursor)
        editor.display(editor.context(),[dict(value="simple_background",category=0,source="Danbooru")]); old=next(iter(editor.candidates)); editor.setPlainText("new")
        editor.insert_completion(old); self.assertEqual(editor.toPlainText(),"new")

    def test_ime_preedit_does_not_translate(self):
        editor=self.window.quick; editor.setFocus(); event=QInputMethodEvent("白",[]); APP.sendEvent(editor,event)
        self.assertTrue(editor.composing); self.assertEqual(editor.toPlainText(),""); self.assertFalse(self.window.completion.timer.isActive())

    def test_workspace_history_save_restore(self):
        dialog=WorkspaceDialog(self.window); dialog.fields["cfg"].setText("5.5"); dialog.version_note.setText("first"); dialog.save()
        dialog=WorkspaceDialog(self.window); dialog.fields["cfg"].setText("6"); dialog.version_note.setText("second"); dialog.save()
        w=self.window.state["workspaces"][0]; self.assertEqual(len(w["history"]),2); self.assertEqual(w["history"][0]["parameters"]["cfg"],"5.5")
        dialog=WorkspaceDialog(self.window); dialog.history.setCurrentRow(1); dialog.restore(); dialog.save()
        self.assertEqual(self.window.state["workspaces"][0]["parameters"]["cfg"],"5.5")

    def test_model_cancel_delete_never_calls_recycle(self):
        directory=Path(self.directory.name)/"models"; (directory/"loras").mkdir(parents=True)
        file=directory/"loras"/"test.safetensors"; file.write_bytes(b"only-fixture")
        rows=scan_models(directory); self.window.catalog.merge_models(rows,directory)
        page=self.window.models; page.root.setText(str(directory)); page.refresh(); page.list.setCurrentRow(0)
        with patch("prompt_calculus_studio.pages.ask",return_value=False),patch("prompt_calculus_studio.pages.QFile.moveToTrash") as recycle:
            page.recycle(); recycle.assert_not_called()
        self.assertTrue(file.exists()); self.assertFalse(self.errors)

    def test_failed_recycle_never_permanently_deletes(self):
        directory=Path(self.directory.name)/"models"; (directory/"loras").mkdir(parents=True)
        file=directory/"loras"/"test.safetensors"; file.write_bytes(b"only-fixture")
        self.window.catalog.merge_models(scan_models(directory),directory)
        page=self.window.models; page.root.setText(str(directory)); page.refresh(); page.list.setCurrentRow(0)
        with patch("prompt_calculus_studio.pages.ask",return_value=True),patch("prompt_calculus_studio.pages.QFile.moveToTrash",return_value=(False,"")):
            page.recycle()
        self.assertTrue(file.exists()); self.assertEqual(len(self.errors),1)

if __name__=="__main__":
    (ROOT/"qa").mkdir(exist_ok=True)
    unittest.main(verbosity=2)
