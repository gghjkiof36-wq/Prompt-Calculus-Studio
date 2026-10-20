"""Import a saved Prompt management page into the current Canvas library."""
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QFileDialog

from .local_documents import library, paths
from .widgets import RoundMenu


def choose(palette):
    if not palette.ready():
        return
    workspace = getattr(palette.canvas.window, 'workspace_owner', None)
    menu = RoundMenu(palette)
    menu.setToolTipsVisible(True)
    menu.setStyleSheet('QMenu { menu-scrollable: 1; }')
    choices = workspace.material_transfer.candidates('category') if workspace else []
    counts = Counter(path.stem.casefold() for path in choices)
    for path in choices:
        title = path.stem
        if counts[title.casefold()] > 1:
            title += ' · ' + str(path.parent)
        action = menu.addAction(menu.fontMetrics().elidedText(title, Qt.TextElideMode.ElideMiddle, 420),
                                lambda checked=False, p=path: palette.import_category(p))
        action.setData(str(path))
        action.setToolTip(str(path))
    if choices:
        menu.addSeparator()
    def browse():
        start = str(workspace.locations.current or '') if workspace else ''
        path, _ = QFileDialog.getOpenFileName(palette, '匯入 Prompt 管理頁', start,
                                              'Prompt 管理頁 (*.pcs-category)')
        if path:
            palette.import_category(Path(path))
    menu.addAction('選擇其他管理頁…', browse)
    palette.import_menu = menu
    menu.open_at(palette.new_button.mapToGlobal(QPoint(0, palette.new_button.height())))


def import_category(palette, path):
    if not palette.ready():
        return None
    palette.busy = True
    palette.update_destination()
    palette.status.hide()
    host = palette.canvas.window
    created = []
    committed = False
    try:
        payload, attachments = library.prepare(path)
        for relative, raw in attachments.items():
            target = paths.checked(host.store.directory, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target = paths.checked(host.store.directory, relative)
            if target.exists():
                if target.read_bytes() != raw:
                    raise ValueError('附件目的地內容不同，原件未覆寫。')
            else:
                with target.open('xb') as stream:
                    created.append(target)
                    stream.write(raw)
        committed = bool(palette.canvas.commit(lambda state: library.add(state, payload),
                                               label='匯入 Prompt 管理頁'))
        if not committed:
            raise ValueError('未能匯入管理頁，請重試。')
        ident = payload['category']['id']
        palette.search.clear()
        palette.populate_categories()
        palette.set_browser_category(ident)
        # The ordinary document autosaver persists the library as one edit.
        return ident
    except (ValueError, OSError, KeyError) as error:
        palette.show_error('無法匯入：' + str(error))
        return None
    finally:
        if not committed:
            for target in created:
                try:
                    target.unlink(missing_ok=True)
                except OSError as error:
                    palette.show_error('匯入未完成，附件清理失敗：' + str(error))
        palette.busy = False
        palette.update_destination()
