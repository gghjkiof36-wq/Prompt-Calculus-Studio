"""Exercise and capture synthetic local-workspace Qt surfaces in a fresh area."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback
import shutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(root, out):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QFontDatabase, QFont
    from prompt_calculus_studio import materials, composition
    from prompt_calculus_studio.local_documents import io
    from prompt_calculus_studio.workspace_shell import WorkspaceShell
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(root, out/'files')
    root = out/'files'
    errors = []
    def error(kind, value, trace):
        errors.append(''.join(traceback.format_exception(kind, value, trace)))
        print(errors[-1], file=sys.stderr)
    sys.excepthook = error
    app = QApplication([])
    # The offscreen font database otherwise lacks Windows' native CJK fallback.
    for filename in ('msjh.ttc', 'msjhbd.ttc'):
        font = Path('C:/Windows/Fonts')/filename
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont('Microsoft JhengHei UI', 10))
    window = WorkspaceShell(root, out/'data')
    window.report_error = errors.append
    checks = []
    def events():
        for _ in range(8):
            app.processEvents()
            time.sleep(.03)
    try:
        window.show()
        events()
        category = window.open_path(root/'場景.pcs-category')
        events()
        assert category is not None, errors
        window.grab().save(str(out/'category.png'))
        canvas = window.open_path(root/'街景.pcs-workflow', True)
        events()
        assert canvas is not None, errors
        canvas.document.host.canvas.fit()
        events()
        window.grab().save(str(out/'canvas.png'))
        window.tab_strip.bar.setCurrentIndex(window.index_of(category.key))
        events()
        page = category.document.host.prompt_editor
        page.preview_material(category.document.host.state['items'][0]['id'])
        events()
        window.grab().save(str(out/'preview.png'))
        checks.append('Top tabs, real category preview and Canvas are visible; opening is clean.')
        host = category.document.host
        ident = host.state['items'][0]['id']
        original = materials.item(host.state, ident)['prompt']
        file_before = category.path.read_bytes()
        page.search.setText('場景文字')
        events()
        entry = next(page.listing.item(i) for i in range(page.listing.count())
                     if page.listing.item(i).data(Qt.ItemDataRole.UserRole) == ident)
        pos = page.listing.visualItemRect(entry).center()
        QTest.mouseClick(page.listing.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        QTest.qWait(QApplication.doubleClickInterval()+30)
        events()
        QTest.mouseClick(page.edit_button, Qt.MouseButton.LeftButton)
        events()
        dialog = page.active_dialog
        assert dialog is not None
        prompt = dialog.pages.variant_editor.stack.currentWidget() if dialog.pages.variant_editor else dialog.prompt
        QTest.keyClicks(prompt, ' cancelled')
        QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
        events()
        assert category.path.read_bytes() == file_before
        assert materials.item(host.state, ident)['prompt'] == original
        checks.append('Search, preview edit button and cancel preserve both source file and canonical content.')
        page.preview_material(ident)
        events()
        QTest.mouseClick(page.edit_button, Qt.MouseButton.LeftButton)
        events()
        dialog = page.active_dialog
        prompt = dialog.pages.variant_editor.stack.currentWidget() if dialog.pages.variant_editor else dialog.prompt
        prompt.moveCursor(prompt.textCursor().MoveOperation.End)
        QTest.keyClicks(prompt, ', review edit')
        events()
        dialog.grab().save(str(out/'material-editor.png'))
        QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        events()
        assert category.document.dirty and category.retained
        assert category.path.read_bytes() == file_before
        window.grab().save(str(out/'dirty.png'))
        def cancel_close():
            choice = app.activeModalWidget()
            try:
                assert isinstance(choice, QMessageBox)
                choice.grab().save(str(out/'close-cancel.png'))
                QTest.mouseClick(next(b for b in choice.buttons() if b.text() == '取消'), Qt.MouseButton.LeftButton)
            finally:
                if choice.isVisible():
                    choice.reject()
        QTimer.singleShot(150, cancel_close)
        assert not window.close_tab(window.index_of(category.key))
        assert category.document.dirty
        checks.append('Editing retains the tab; Cancel in the native dirty-close question keeps the page and content.')
        QTest.mouseClick(category.save_button, Qt.MouseButton.LeftButton)
        events()
        assert not category.document.dirty, category.document.error
        expected = original+', review edit'
        saved = io.load(root, category.path.name).document
        assert saved['payload']['items'][0]['prompt'] == expected
        cache = dict(host._material_editor_documents)
        window.tab_strip.bar.setCurrentIndex(window.index_of(canvas.key))
        events()
        window.tab_strip.bar.setCurrentIndex(window.index_of(category.key))
        events()
        assert dict(host._material_editor_documents) == cache
        host.canvas.undo()
        assert materials.item(host.state, ident)['prompt'] == original
        host.canvas.redo()
        assert materials.item(host.state, ident)['prompt'] == expected
        assert category.document.save(), category.document.error
        checks.append('Save targets the category file; switching preserves native text documents and real Undo/Redo.')
        page.search.clear()
        for kind in range(1, page.type_filter.count()):
            page.type_filter.setCurrentIndex(kind)
            events()
            assert page.listing.count() == 1
        page.type_filter.setCurrentIndex(0)
        originally_pinned = materials.item(host.state, ident).get('pinned', False)
        page.pin_material(ident)
        page.pins.setChecked(True)
        assert any(page.listing.item(i).data(Qt.ItemDataRole.UserRole) == ident
                   for i in range(page.listing.count())) == (not originally_pinned)
        page.pins.setChecked(False)
        host.canvas.undo()
        assert category.document.save(), category.document.error
        checks.append('All three material types and pin filtering use the existing central manager.')
        window.tab_strip.bar.setCurrentIndex(window.index_of(canvas.key))
        events()
        canvas_host = canvas.document.host
        graph = canvas_host.canvas
        editable = next(k for k in graph.entries if graph.entries[k][0] in canvas_host.state['uses'])
        use_id = graph.entries[editable][0]
        before_use = composition.render(canvas_host.state['uses'][use_id])
        def edit_node():
            editor = app.activeModalWidget()
            try:
                editor.prompt.setFocus()
                editor.prompt.selectAll()
                QTest.keyClicks(editor.prompt, 'native canvas review')
                editor.grab().save(str(out/'canvas-editor.png'))
                editor.save()
            finally:
                if editor.isVisible():
                    editor.reject()
        QTimer.singleShot(150, edit_node)
        assert graph.edit(editable)
        events()
        assert canvas.document.dirty
        assert composition.render(canvas_host.state['uses'][use_id]) == 'native canvas review'
        assert canvas.document.save(), canvas.document.error
        graph.undo()
        assert composition.render(canvas_host.state['uses'][use_id]) == before_use
        graph.redo()
        assert composition.render(canvas_host.state['uses'][use_id]) == 'native canvas review'
        assert canvas.document.save(), canvas.document.error
        checks.append('The actual Canvas node dialog edits and saves its workflow; real Undo/Redo remains available.')
        for entry in window.entries:
            if entry.kind == 'markdown':
                window.open_path(root/entry.relative, True)
        window.tab_strip.bar.setCurrentIndex(window.index_of(category.key))
        window.resize(960, 700)
        events()
        window.grab().save(str(out/'narrow.png'))
        window.toggle_sidebar()
        events()
        window.grab().save(str(out/'collapsed.png'))
        assert window.tab_strip.plus.geometry().right() <= window.tab_strip.width()
        checks.append('A narrow window and collapsed sidebar keep the tab strip and adjacent plus accessible.')
        report = dict(errors=errors, tabs=window.tab_strip.bar.count(),
                      checks=checks, platform=app.platformName(),
                      dpi=window.devicePixelRatioF(),
                      dirty=[tab.document.dirty for tab in window.tabs_by_key.values() if tab.document])
        (out/'REPORT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
        assert not errors, errors
    finally:
        while window.tab_strip.bar.count():
            if not window.close_tab(0, decision='discard', make_home=False):
                break
        window.close()
        events()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run(args.root.absolute(), args.out.absolute())
