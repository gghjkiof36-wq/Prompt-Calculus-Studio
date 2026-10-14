"""One material/canvas editor shared by the page and collapsible canvas rail."""
import copy
import datetime
import json
import weakref
from shiboken6 import isValid
from PySide6.QtCore import Qt, QMimeData, QTimer, QSize, Signal
from PySide6.QtGui import QDrag, QPainter, QPen, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPlainTextEdit, QListWidget, QListWidgetItem, QStackedWidget, QCheckBox, QSplitter,
    QScrollArea, QDialog, QSizePolicy)
from . import materials, composition, content_history
from .core import uid
from .completion import PromptEdit
from .widgets import label, button, row, ComboBox, StudioDialog, RoundMenu, ask, image_path, set_preview, thumb_icon, InputDialog
from .image_drop import ImageDropLabel
from .media import thumbnail

MIME_TYPE = materials.MIME_TYPE


def _live_editors(window):
    registry = getattr(window, '_material_editors', ())
    for editor in list(registry):
        if not isValid(editor) or not isValid(editor.timer):
            registry.discard(editor)
            continue
        yield editor


def reset_editors(window):
    """Drop old-document forms after the caller has confirmed and replaced it."""
    page = getattr(window, 'prompt_editor', None)
    if page is not None and isValid(page) and hasattr(page, 'reset'): page.reset()
    serial = getattr(window, '_material_editor_session', {}).get('serial', 0) + 1
    window._material_editor_session = dict(serial=serial, active=None, drafts={})
    from .material_editor_documents import reset_documents
    reset_documents(window)
    for editor in _live_editors(window):
        editor.session = window._material_editor_session
        editor.timer.stop()
        window.completion.cancel(editor.prompt)
        editor.prompt.completer.popup().hide()
        editor.loading = True
        editor.target = None
        editor.base = None
        editor.dirty = False
        editor.edit_workspace = None
        editor.destination_id = None
        editor.preview_path = ''
        editor.session_serial = serial
        editor.name.clear(); editor.prompt.clear(); editor.aliases.clear()
        editor.notes.clear(); editor.excludes.clear()
        editor.heading.setText('文字素材')
        editor.scope_hint.setText('選擇素材或畫布副本開始編輯。')
        editor.status.clear()
        editor.show_preview()
        editor.detail.setEnabled(False)
        if editor.compact and editor.narrow: editor.stack.setCurrentIndex(0)
        if not editor.compact: editor._library_page = False
        editor.loading = False
        editor.refresh()
        if not editor.compact: editor.new_material()
    canvas = getattr(window, 'canvas', None)
    if canvas is not None and hasattr(canvas, 'view'): canvas.view.viewport().update()


def flush_editors(window, closing=False):
    """Flush shared, identity-bound drafts once, without reviving hidden forms."""
    session = getattr(window, '_material_editor_session', None)
    if session is None: return True
    for key, saved in list(session['drafts'].items()):
        target = saved['target']
        if not saved['dirty'] or target is None or target[0] != 'canvas': continue
        if saved['workspace'] != window.state['workspace']:
            window.notice('仍有其他工作區尚未套用的文字，請切回檢查。')
            return False
        changes = {name: value for name, value in saved['fields'].items() if value != saved['base'].get(name)}
        expected = copy.deepcopy(saved['base'])
        def apply(state, target=target, changes=changes, expected=expected):
            current = materials.canvas_node(state, target[1])
            for field in changes:
                if current.get(field) != expected.get(field) and current.get(field) != changes[field]:
                    raise ValueError('文字已在另一處更新，保留未套用草稿，請重新檢查。')
            materials.edit_canvas(state, target[1], changes)
        if changes and not window.canvas.commit(apply, label='編輯畫布文字', merge_key='editor:' + target[1]): return False
        saved['base'] = copy.deepcopy(materials.canvas_node(window.state, target[1]))
        saved['dirty'] = False
    session['serial'] += 1
    for editor in _live_editors(window):
        editor.timer.stop()
        active = session['drafts'].get(session['active'])
        if active: editor._restore_session(active, session['serial'])
    if closing:
        pending = any(v['dirty'] and (v['target'] is None or v['target'][0] != 'canvas') for v in session['drafts'].values())
        if pending and not ask(window, '未保存的素材', '仍有未保存的素材草稿，確定關閉並捨棄？'): return False
    return True


class ReorderableList(QListWidget):
    """Same-view ordering is a transaction; external drags never move records."""
    reorderRequested = Signal(object, object)
    dragStarted = Signal()
    ORDER_MIME = 'application/x-pcs-library-order'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.reorder_enabled = False
        self._drop_row = None
        self._drag_position = None
        self._suppress_release = False
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(60)
        self._scroll_timer.timeout.connect(self._scroll_drag)

    def enable_reordering(self):
        self.reorder_enabled = True
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)

    def order_ids(self):
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]

    def mousePressEvent(self, event):
        self._suppress_release = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._suppress_release:
            self._suppress_release = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def drag_mime(self, current):
        mime = QMimeData()
        if self.reorder_enabled:
            payload = dict(view=str(id(self)), ident=current.data(Qt.ItemDataRole.UserRole), order=self.order_ids())
            mime.setData(self.ORDER_MIME, json.dumps(payload).encode('utf-8'))
        return mime

    def startDrag(self, actions):
        current = self.currentItem()
        if current is None or not current.flags() & Qt.ItemFlag.ItemIsDragEnabled: return
        drag = QDrag(self); drag.setMimeData(self.drag_mime(current))
        if not current.icon().isNull(): drag.setPixmap(current.icon().pixmap(64, 64))
        external = drag.mimeData().hasFormat(MIME_TYPE)
        allowed = Qt.DropAction.CopyAction if external else Qt.DropAction.MoveAction
        if self.reorder_enabled: allowed |= Qt.DropAction.MoveAction
        self._suppress_release = True; self.dragStarted.emit()
        try:
            drag.exec(allowed, Qt.DropAction.CopyAction if external else Qt.DropAction.MoveAction)
        finally:
            self._clear_drop()
            drag.deleteLater()

    def _own_drag(self, event):
        if not self.reorder_enabled or event.source() is not self: return None
        try:
            payload = json.loads(bytes(event.mimeData().data(self.ORDER_MIME)))
            if payload['view'] != str(id(self)) or payload['order'] != self.order_ids(): return None
            entry = self.item(payload['order'].index(payload['ident']))
            return payload if entry.flags() & Qt.ItemFlag.ItemIsDragEnabled else None
        except (ValueError, KeyError, TypeError, AttributeError):
            return None

    def _insertion_row(self, position):
        if not self.viewport().rect().contains(position): return None
        movable = [i for i in range(self.count()) if self.item(i).flags() & Qt.ItemFlag.ItemIsDragEnabled]
        if not movable: return None
        # itemAt() misses the spacing and the sides of a row. Those are still
        # the same vertical insertion slot, rather than the end of the list.
        for row in movable:
            if position.y() <= self.visualItemRect(self.item(row)).center().y(): return row
        return movable[-1]+1

    def dragEnterEvent(self, event):
        if self._own_drag(event):
            event.setDropAction(Qt.DropAction.MoveAction); event.accept()
        else: event.ignore()

    def dragMoveEvent(self, event):
        if not self._own_drag(event):
            self._clear_drop(); event.ignore(); return
        self._drag_position = event.position().toPoint()
        self._drop_row = self._insertion_row(self._drag_position)
        if self._drop_row is None:
            self._clear_drop(); event.ignore(); return
        self._scroll_timer.start(); self.viewport().update()
        event.setDropAction(Qt.DropAction.MoveAction); event.accept()

    def _scroll_drag(self):
        if self._drag_position is None: return
        y = self._drag_position.y(); height = self.viewport().height()
        direction = -1 if y < 24 else 1 if y > height-24 else 0
        if direction:
            bar = self.verticalScrollBar(); bar.setValue(bar.value()+direction*max(8, self.fontMetrics().height()))
            self._drop_row = self._insertion_row(self._drag_position); self.viewport().update()

    def _clear_drop(self):
        self._scroll_timer.stop(); self._drop_row = None; self._drag_position = None
        self.viewport().update()

    def dragLeaveEvent(self, event):
        self._clear_drop(); event.accept()

    def dropEvent(self, event):
        payload = self._own_drag(event)
        row = self._insertion_row(event.position().toPoint()) if payload else None
        self._clear_drop()
        if row is None: event.ignore(); return
        before = payload['order']; after = list(before); source = after.index(payload['ident'])
        after.insert(row-(source < row), after.pop(source))
        event.setDropAction(Qt.DropAction.MoveAction); event.accept()
        if after != before: self.reorderRequested.emit(before, after)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_row is None or not self.count(): return
        from .views import widget_colors
        end = self._drop_row == self.count()
        rect = self.visualItemRect(self.item(self.count()-1 if end else self._drop_row))
        y = rect.bottom() if end else rect.top()
        painter = QPainter(self.viewport()); painter.setPen(QPen(QColor(widget_colors(self)['accent']), 2))
        painter.drawLine(rect.left()+4, y, rect.right()-4, y)


class MaterialList(ReorderableList):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setWordWrap(False)
        self.setIconSize(QSize(64, 64))
        self.setSpacing(4)
        self.pressed_button = Qt.MouseButton.NoButton

    def mousePressEvent(self, event):
        self.pressed_button = event.button()
        super().mousePressEvent(event)

    def drag_mime(self, current):
        mime = super().drag_mime(current)
        mime.setData(MIME_TYPE, current.data(Qt.ItemDataRole.UserRole).encode('utf-8'))
        return mime


class MaterialText(PromptEdit):
    def __init__(self, owner):
        super().__init__(owner.window.completion)
        self.owner = owner

    def lookup_active(self):
        return not getattr(self.owner,'group_mode',False) and self.owner.lookup.isChecked() and self.owner.kind.currentData() == 'tags' and super().lookup_active()

    def schedule(self):
        if self.lookup_active(): super().schedule()
        else:
            self.completer.popup().hide()
            self.service.cancel(self)


class HistoryPanel(QWidget):
    def __init__(self, window, target=None, parent=None):
        super().__init__(parent)
        self.window, self.target = window, target
        self.rows = []
        body = QVBoxLayout(self);body.setContentsMargins(0,0,0,0);body.setSpacing(12)
        self.filter=ComboBox();self.filter.addItem('內容版本',True);self.filter.addItem('所有操作',False)
        self.filter.setCurrentIndex(0 if target is not None else 1);self.filter.setAccessibleName('歷史範圍')
        from .quiet_splitter import QuietSplitter
        self.split=QuietSplitter();self.split.setChildrenCollapsible(False);body.addWidget(self.split,1)
        sidebar=QWidget();left=QVBoxLayout(sidebar);left.setContentsMargins(0,0,12,0);left.setSpacing(12)
        left.addWidget(self.filter);self.split.addWidget(sidebar)
        self.listing = QListWidget()
        self.listing.setObjectName('HistoryEntries');self.listing.setSpacing(4);self.listing.setMinimumWidth(210)
        left.addWidget(self.listing,1)
        self.empty=label('尚無紀錄','Subtle');left.addWidget(self.empty)
        content=QWidget();right=QVBoxLayout(content);right.setContentsMargins(12,0,0,0);right.setSpacing(12);self.split.addWidget(content)
        self.targets = ComboBox();self.targets.setAccessibleName('歷史項目');right.addWidget(self.targets)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumSize(300,260);right.addWidget(self.detail,1)
        self.restore_before = button('還原修改前內容', lambda: self.restore(True), 'Quiet')
        self.restore_after = button('還原此版本內容', lambda: self.restore(False), 'Quiet')
        right.addLayout(row(self.restore_before,self.restore_after,None))
        self.split.setSizes([250,520])
        self.listing.currentRowChanged.connect(self.select)
        self.filter.currentIndexChanged.connect(self.refresh)
        self.targets.currentIndexChanged.connect(self.show_detail)
        self.refresh()

    def refresh(self):
        self.rows = content_history.entries(self.window.state, target=self.target,
                    workspace=self.window.state['workspace'] if self.target is None else None,
                    content_only=self.filter.currentData())
        self.listing.clear()
        for value in self.rows:
            stamp = datetime.datetime.fromtimestamp(value['time']).strftime('%m/%d %H:%M:%S')
            item=QListWidgetItem(value['label']+'\n'+stamp)
            item.setSizeHint(QSize(210,max(64,self.fontMetrics().height()*2+24)));self.listing.addItem(item)
        self.empty.setVisible(not self.rows)
        if self.rows:self.listing.setCurrentRow(0)
        else:self.select(-1)

    def select(self, index):
        self.targets.blockSignals(True)
        self.targets.clear()
        if 0 <= index < len(self.rows):
            for change in self.rows[index]['changes']:
                value = change.get('after') or change.get('before') or {}
                suffix = (' · 舊清單手稿' if change.get('scope') == 'list' else ' · 畫布手稿') if change['kind'] == 'output' else ''
                self.targets.addItem(value.get('name', change['target']) + suffix, (change['target'], change.get('scope')))
        self.targets.blockSignals(False)
        self.targets.setVisible(self.targets.count()>1)
        self.show_detail()

    def show_detail(self, *_):
        index = self.listing.currentRow()
        value = self.rows[index] if 0 <= index < len(self.rows) else None
        selected = self.targets.currentData()
        target, scope = selected if selected else (None, None)
        change = next((v for v in value['changes'] if v['target'] == target and v.get('scope') == scope), None) if value else None
        self.restore_before.setEnabled(bool(change and change.get('before')))
        self.restore_after.setEnabled(bool(change and change.get('after')))
        if change:
            def describe(data):
                if data is None: return '（不存在）'
                if change['kind'] == 'output':
                    draft = data.get('draft')
                    text = '自動組合（沒有手動草稿）' if draft is None else '（空手動草稿）' if draft == '' else draft
                    return text + '\n\n當時的草稿基準：\n' + data.get('draft_base', '')
                root = data.get('composition', data)
                if root.get('sequence'):
                    blocks=[]
                    for group in root['sequence']['groups']:
                        preview=copy.deepcopy(root);preview['sequence']['selected']=group['id']
                        blocks.append(group['name']+f" · {group['repeat']} 次\n"+composition.render(preview))
                    return data.get('name','')+'\n\n'+'\n\n'.join(blocks)
                return data.get('name', '') + '\n\n' + root.get('prompt', '') + ('\n\n子項：\n' +
                    json.dumps(root['children'], ensure_ascii=False, indent=2) if root.get('children') else '')
            self.detail.setPlainText('修改前\n' + describe(change['before']) + '\n\n────────\n修改後\n' + describe(change['after']))
        else:
            self.detail.setPlainText('此操作未改變文字內容。' if value else '')

    def restore(self, previous):
        index = self.listing.currentRow()
        if not 0 <= index < len(self.rows) or not self.targets.currentData(): return
        entry_id = self.rows[index]['id']
        target, scope = self.targets.currentData()
        if self.window.canvas.commit(lambda state: content_history.restore(state, entry_id, target, previous=previous, scope=scope), label='還原內容版本'):
            self.refresh()


def show_history(window, target=None):
    dialog = StudioDialog(window)
    from .module_chrome import apply_prompt_dialog,prompt_divider,dialog_colors
    from .theme import visual_tokens
    apply_prompt_dialog(dialog,window);colors=dialog_colors(visual_tokens(window.state['settings']))
    dialog.setStyleSheet(dialog.styleSheet()+f'''
        QListWidget#HistoryEntries {{background:transparent;border:0;outline:0;}}
        QListWidget#HistoryEntries::item {{border:1px solid transparent;border-radius:8px;padding:8px;}}
        QListWidget#HistoryEntries::item:selected {{background:{colors['selected']};border-color:{colors['accent']};color:{colors['text']};}}
    ''')
    dialog.setWindowTitle('內容與操作歷史')
    dialog.resize(min(880,window.width()-24),min(580,window.height()-24))
    dialog.body.addWidget(HistoryPanel(window, target), 1)
    dialog.body.addWidget(prompt_divider());dialog.body.addLayout(row(None,button('關閉',dialog.accept,'Quiet')))
    dialog.exec()


class MaterialEditorForm(QWidget):
    """`target` identifies a material original or a workspace-local node copy."""
    def __init__(self, window, compact=False, isolated=False):
        super().__init__()
        self.isolated = isolated
        self.document_scope = uid() if isolated else None
        self.window, self.compact = window, compact
        self.narrow = compact
        self.target = None
        self.base = None
        self.loading = False
        self.dirty = False
        self.preview_path = ''
        self.destination_id = None
        self.edit_workspace = None
        self.session_serial = 0
        self.importing_session = False
        if not hasattr(window, '_material_editor_session'):
            window._material_editor_session = dict(serial=0, active=None, drafts={})
        if not hasattr(window, '_material_editors'): window._material_editors = weakref.WeakSet()
        if not isolated: window._material_editors.add(self)
        self.session = (copy.deepcopy(window._material_editor_session) if isolated else window._material_editor_session)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.flush_canvas)
        if not compact:
            from .material_editor_page import build_page
            build_page(self)
            self.refresh()
            if self.base is None: self.new_material()
            return
        body = QVBoxLayout(self)
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(10)
        if not compact:
            body.addLayout(row(button('返回畫布', lambda: window.enter_canvas(), 'Quiet'), None))
        self.browser = QWidget()
        library = QVBoxLayout(self.browser)
        library.setContentsMargins(0, 0, 0, 0)
        library.addLayout(row(label('文字素材', 'Heading'), None, button('新增', self.new_material, 'Primary')))
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜尋名稱、別名或內容')
        library.addWidget(self.search)
        self.category = ComboBox()
        self.order = ComboBox()
        self.order.addItem('釘選優先', 'name')
        self.order.addItem('最近使用', 'recent')
        library.addLayout(row(self.category, self.order))
        self.pins = QCheckBox('只看釘選')
        library.addWidget(self.pins)
        self.list_hint = label('點選加入畫布；右鍵可直接編輯或釘選。' if compact else
                               '點選素材後編輯或釘選；取用請按「加入畫布」。', 'Subtle', True)
        library.addWidget(self.list_hint)
        self.listing = MaterialList()
        self.listing.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listing.customContextMenuRequested.connect(self.material_menu)
        library.addWidget(self.listing, 1)
        self.count = label('', 'Subtle')
        library.addWidget(self.count)
        self.destination = ComboBox()
        self.destination.setAccessibleName('加入的畫布')
        library.addWidget(self.destination)
        library.addLayout(row(button('加入畫布', self.add_selected, 'Primary'), button('編輯素材', self.open_selected, 'Quiet')))
        library.addLayout(row(button('釘選／取消', self.toggle_pin, 'Quiet'), button('新增分類', self.new_category, 'Quiet'), button('歷史', lambda: show_history(window), 'Quiet')))
        library.addWidget(button('取回舊清單內容', self.legacy_content, 'Quiet'))

        self.detail = QWidget()
        detail = QVBoxLayout(self.detail)
        detail.setContentsMargins(0, 0, 0, 0)
        self.back_button = button('返回素材', self.show_library, 'Quiet')
        self.heading = label('建立或選擇素材', 'Heading')
        detail.addLayout(row(self.back_button, self.heading))
        self.back_button.setVisible(compact)
        self.scope_hint = label('', 'Subtle', True)
        detail.addWidget(self.scope_hint)
        form = QFormLayout()
        self.name = QLineEdit()
        self.kind = ComboBox()
        for title, key in (('標籤', 'tags'), ('純文字', 'text'), ('Markdown', 'markdown')):
            self.kind.addItem(title, key)
        self.edit_category = ComboBox()
        form.addRow('名稱', self.name)
        form.addRow('內容格式', self.kind)
        detail.addLayout(form)
        self.lookup = QCheckBox('展開標籤查詢（Danbooru／字典）')
        detail.addWidget(self.lookup)
        self.lookup_hint = label('輸入標籤並選擇候選；採用後才加入文字。', 'Subtle', True)
        self.lookup_hint.hide()
        detail.addWidget(self.lookup_hint)
        from .material_editor_page import PagePromptText
        from .material_editor_documents import ModalPromptText
        self.prompt = ModalPromptText(self) if self.isolated else PagePromptText(self)
        self.prompt.setObjectName('Prompt')
        self.prompt.setPlaceholderText('輸入文字內容')
        self.prompt.setMinimumHeight(150)
        detail.addWidget(self.prompt, 1)
        self.more = QCheckBox('分類資訊、關係與預覽')
        detail.addWidget(self.more)
        self.extras = QWidget()
        extra = QFormLayout(self.extras)
        extra.setContentsMargins(0, 0, 0, 0)
        extra.addRow('素材分類', self.edit_category)
        self.aliases = QLineEdit()
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(70)
        self.excludes = QPlainTextEdit()
        self.excludes.setMaximumHeight(70)
        self.excludes.setPlaceholderText('使用此卡時停用的完整 Tag，每行一項')
        extra.addRow('別名', self.aliases)
        extra.addRow('備註', self.notes)
        extra.addRow('停用關係', self.excludes)
        self.preview = ImageDropLabel(window.store)
        self.preview.setMinimumHeight(80)
        self.preview.setMaximumHeight(140)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.pathReady.connect(self.receive_image)
        self.preview.failed.connect(window.notice)
        extra.addRow(self.preview)
        extra.addRow(row(button('選擇預覽圖', self.choose_image, 'Quiet'), button('移除預覽', self.clear_image, 'Quiet')))
        detail.addWidget(self.extras)
        self.extras.hide()
        self.status = label('', 'Subtle', True)
        detail.addWidget(self.status)
        self.edit_destination = ComboBox()
        self.edit_destination.setAccessibleName('新素材加入的畫布')
        detail.addWidget(self.edit_destination)
        self.save_button = button('保存素材', self.save_material, 'Primary')
        self.use_button = button('保存並加入畫布', lambda: self.save_material(True), 'Quiet')
        self.variant_button = button('另存為新素材', self.save_variant, 'Quiet')
        self.history_button = button('內容歷史', self.history, 'Quiet')
        self.delete_button = button('刪除素材', self.delete_material, 'Quiet')
        detail.addLayout(row(self.save_button, self.use_button))
        detail.addLayout(row(self.variant_button, self.history_button, self.delete_button))

        self.stack = QStackedWidget()
        self.split = QSplitter()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setWidget(self.detail)
        self.browser.setMinimumWidth(260)
        self.detail.setMinimumWidth(300)
        body.addWidget(self.stack, 1)
        body.addWidget(self.split, 1)
        self._set_narrow(compact or self.width() < 850)
        self.more.toggled.connect(self.extras.setVisible)
        self.more.toggled.connect(lambda _: self._publish(force=True))
        self.lookup.toggled.connect(self.lookup_changed)
        self.search.textChanged.connect(self.refresh_list)
        self.category.currentIndexChanged.connect(self.refresh_list)
        self.order.currentIndexChanged.connect(self.refresh_list)
        self.pins.toggled.connect(self.refresh_list)
        self.listing.itemClicked.connect(self.material_clicked)
        self.destination.currentIndexChanged.connect(lambda: self.destination_changed(self.destination))
        self.edit_destination.currentIndexChanged.connect(lambda: self.destination_changed(self.edit_destination))
        for field in (self.name, self.aliases): field.textChanged.connect(self.edited)
        for field in (self.prompt, self.notes, self.excludes): field.textChanged.connect(self.edited)
        self.kind.currentIndexChanged.connect(self.edited)
        self.edit_category.currentIndexChanged.connect(self.edited)
        self.detail.setEnabled(False)
        self.refresh()

    def _set_narrow(self, narrow):
        if not self.compact: return
        showing_detail = self.stack.currentIndex() == 1 if self.narrow else self.base is not None
        self.narrow = narrow
        if narrow:
            self.stack.addWidget(self.browser); self.stack.addWidget(self.scroll)
            self.stack.setCurrentIndex(1 if showing_detail else 0)
        else:
            self.split.addWidget(self.browser); self.split.addWidget(self.scroll)
            self.split.setStretchFactor(0, 1); self.split.setStretchFactor(1, 2)
            self.split.setSizes([340, max(480, self.width() - 380)])
        self.stack.setVisible(narrow); self.split.setVisible(not narrow)
        if not narrow: self.browser.show(); self.scroll.show()
        self.back_button.setVisible(narrow)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.compact:
            from .material_editor_page import resize_page
            resize_page(self)
            return
        narrow = self.compact or event.size().width() < 850
        if narrow != self.narrow: self._set_narrow(narrow)
        if hasattr(self, 'prompt'):
            from .material_editor_page import sync_prompt_style
            sync_prompt_style(self)

    def lookup_changed(self, enabled):
        self.lookup_hint.setVisible(enabled)
        if enabled:
            self.prompt.setFocus()
            self.prompt.schedule()
        else:
            self.window.completion.cancel(self.prompt)
            self.prompt.completer.popup().hide()

    def refresh(self):
        self.loading = True
        try:
            for combo, all_categories in ((self.category, True), (self.edit_category, False)):
                selected = combo.currentData()
                combo.blockSignals(True)
                combo.clear()
                if all_categories:
                    combo.addItem('全部素材' if self.compact else '所有分類', None)
                    if not self.compact:
                        combo.addItem('未分類', '__uncategorized__')
                else: combo.addItem('未分類', None)
                for value in self.window.state['modules']:
                    if all_categories and not self.compact and value['name'] == '未分類': continue
                    combo.addItem(value['name'], value['id'])
                combo.setCurrentIndex(max(0, combo.findData(selected)))
                combo.blockSignals(False)
            destination = self.destination_id
            self.destination.blockSignals(True)
            self.destination.clear()
            self.destination.addItem('選擇畫布', None)
            canvases = self.window.state.get('multi_output', {}).get('canvases', {})
            for key, value in canvases.items(): self.destination.addItem(value['name'], key)
            self.destination.setCurrentIndex(max(0, self.destination.findData(destination)))
            self.destination_id = self.destination.currentData()
            self.destination.blockSignals(False)
            self.edit_destination.blockSignals(True)
            self.edit_destination.clear()
            for index in range(self.destination.count()):
                self.edit_destination.addItem(self.destination.itemText(index), self.destination.itemData(index))
            self.edit_destination.setCurrentIndex(self.destination.currentIndex())
            self.edit_destination.blockSignals(False)
            self.refresh_list()
        finally:
            self.loading = False
        session = self.session
        if not self.isolated and session['serial'] != self.session_serial and session['active'] is not None:
            saved = session['drafts'].get(session['active'])
            if saved and (saved['target'] is None or saved['target'][0] != 'canvas' or saved['workspace'] == self.window.state['workspace']):
                self._restore_session(saved, session['serial'])
        if self.target and not self.dirty:
            kind, key = self.target
            if kind == 'canvas' and self.edit_workspace != self.window.state['workspace']:
                self.target = None
                self.detail.setEnabled(False)
                self.show_library()
                return
            try:
                value = materials.canvas_node(self.window.state, key) if kind == 'canvas' else materials.item(self.window.state, key)
                if value != self.base: self.load_value(value, kind)
            except ValueError:
                self.target = None
                self.detail.setEnabled(False)
                self.status.setText('原項目已移除。')
        if not self.compact:
            from .material_editor_page import update_page, resize_page
            update_page(self); resize_page(self)
        else:
            from .material_editor_page import sync_prompt_style
            sync_prompt_style(self)

    def refresh_list(self, *_):
        selected = self.listing.currentItem()
        ident = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        if not self.compact and self.target and self.target[0] == 'material': ident = self.target[1]
        scroll = self.listing.verticalScrollBar().value()
        self.listing.blockSignals(True)
        self.listing.clear()
        category = self.category.currentData()
        rows = materials.search(self.window.state, self.search.text(), None if category in ('__pinned__', '__uncategorized__') else category,
                                pinned=self.pins.isChecked() or category == '__pinned__', order=self.order.currentData())
        if category == '__uncategorized__':
            uncategorized = {v['id'] for v in self.window.state['modules'] if v['name'] == '未分類'}
            rows = [v for v in rows if v['module'] in uncategorized]
        for value in rows:
            text = ('◆ ' if self.compact and value.get('pinned') else '') + value['name'] + '\n' + value['prompt'].replace('\n', ' ')[:180]
            entry = QListWidgetItem(thumb_icon(self.window.store, value.get('preview'), 64), text)
            entry.setData(Qt.ItemDataRole.UserRole, value['id'])
            entry.setToolTip(value['name'] + '\n' + value['prompt'])
            self.listing.addItem(entry)
            if value['id'] == ident: self.listing.setCurrentItem(entry)
        self.listing.blockSignals(False)
        self.listing.verticalScrollBar().setValue(scroll)
        self.count.setText(f'{len(rows)} 個素材')
        if not self.compact:
            self.empty_results.setVisible(not rows)
            (self.pin_filter if self.pins.isChecked() else self.all_filter).setChecked(True)

    def material_clicked(self, entry):
        if self.listing.pressed_button != Qt.MouseButton.LeftButton: return
        if self.compact: self.add_selected()
        else:
            self.open_material(entry.data(Qt.ItemDataRole.UserRole))
            if self.narrow: self.hide_library()

    def material_menu(self, position):
        entry = self.listing.itemAt(position)
        if entry is None: return
        ident = entry.data(Qt.ItemDataRole.UserRole)
        try: value = materials.item(self.window.state, ident)
        except ValueError: return
        self.listing.setCurrentItem(entry)
        menu = RoundMenu(self)
        menu.addAction('編輯素材', lambda: self.open_material(ident))
        menu.addAction('取消釘選' if value.get('pinned') else '釘選素材', lambda: self.pin_material(ident))
        menu.open_at(self.listing.viewport().mapToGlobal(position))

    def show_library(self):
        if not self.compact:
            if not self.can_leave(): return
            self._library_collapsed = False; self._library_page = True
            from .material_editor_page import resize_page
            resize_page(self); self.refresh_list(); self.search.setFocus()
            return
        self.flush_canvas()
        if self.narrow: self.stack.setCurrentIndex(0)
        self.refresh_list()
        self._publish()

    def hide_library(self):
        if self.compact:
            if self.base is not None: self.stack.setCurrentIndex(1)
        else:
            if self.narrow: self._library_page = False
            else: self._library_collapsed = True
            from .material_editor_page import resize_page
            resize_page(self)
        self.prompt.setFocus()

    def toggle_library(self):
        if self.browser.isVisible(): self.hide_library()
        else: self.show_library()

    def set_destination(self, canvas_id):
        self.destination_id = canvas_id
        self.destination.setCurrentIndex(max(0, self.destination.findData(canvas_id)))
        self.edit_destination.setCurrentIndex(max(0, self.edit_destination.findData(canvas_id)))

    def destination_changed(self, source):
        self.destination_id = source.currentData()
        other = self.edit_destination if source is self.destination else self.destination
        other.blockSignals(True)
        other.setCurrentIndex(max(0, other.findData(self.destination_id)))
        other.blockSignals(False)
        if not self.loading: self._publish()

    def export_state(self):
        if self.base is None: return None
        return dict(target=self.target, base=copy.deepcopy(self.base), dirty=self.dirty,
                    fields=self.form_values(), aliases=self.aliases.text(), notes=self.notes.toPlainText(),
                    category=self.edit_category.currentData(), destination=self.destination_id,
                    workspace=self.edit_workspace, extras=self.more.isChecked(),
                    cursor=self.prompt.textCursor().position(), anchor=self.prompt.textCursor().anchor(),
                    scroll=self.prompt.verticalScrollBar().value(), horizontal_scroll=self.prompt.horizontalScrollBar().value())

    def _publish(self, force=False):
        if self.loading or self.importing_session: return
        if not self.isolated and self.base:
            from .material_editor_documents import _key
            if _key(self) in getattr(self.window, '_material_native_transactions', {}): return
        value = self.export_state()
        if value is None: return
        session = self.session
        if not force and self.session_serial < session['serial']: return
        key = self.target if self.target is not None else ('new', '')
        if self.target and self.target[0] == 'canvas': key += (self.edit_workspace,)
        session['drafts'][key] = copy.deepcopy(value)
        session['active'] = key
        session['serial'] += 1
        self.session_serial = session['serial']
        canvas = getattr(self.window, 'canvas', None)
        if canvas is not None and hasattr(canvas, 'view'): canvas.view.viewport().update()

    def _restore_session(self, value, serial=None):
        self.importing_session = True
        self.target = value['target']
        self.edit_workspace = value['workspace']
        mode = self.target[0] if self.target else 'new'
        self.load_value(value['base'], mode, prompt_text=value['fields']['prompt'])
        self.loading = True
        fields = value['fields']
        self.name.setText(fields['name'])
        self.kind.setCurrentIndex(max(0, self.kind.findData(fields['content_kind'])))
        self.excludes.setPlainText('\n'.join(fields['excludes']))
        self.preview_path = fields['preview']; self.show_preview()
        self.aliases.setText(value['aliases']); self.notes.setPlainText(value['notes'])
        self.edit_category.setCurrentIndex(max(0, self.edit_category.findData(value['category'])))
        self.set_destination(value['destination'])
        self.more.setChecked(value['extras'])
        cursor = self.prompt.textCursor(); limit = self.prompt.document().characterCount() - 1
        cursor.setPosition(min(value.get('anchor', value['cursor']), limit))
        from PySide6.QtGui import QTextCursor
        cursor.setPosition(min(value['cursor'], limit), QTextCursor.MoveMode.KeepAnchor)
        self.prompt.setTextCursor(cursor)
        self.prompt.verticalScrollBar().setValue(value.get('scroll', 0))
        self.prompt.horizontalScrollBar().setValue(value.get('horizontal_scroll', 0))
        self.dirty = value['dirty']
        self.status.setText('尚未保存' if self.dirty and mode != 'canvas' else '')
        if not self.compact:
            from .material_editor_page import update_page
            update_page(self)
        self.loading = False
        self.importing_session = False
        if serial is not None: self.session_serial = serial

    def transfer_from(self, editor):
        editor.flush_canvas()
        value = editor.export_state()
        if value is not None:
            self._restore_session(value)
            self._publish(force=True)

    def selected_id(self):
        selected = self.listing.currentItem()
        return selected.data(Qt.ItemDataRole.UserRole) if selected else None

    def can_leave(self, closing=False):
        if self.target and self.target[0] == 'canvas' and self.flush_canvas() is False: return False
        self._publish()
        if closing:
            pending = any(v['dirty'] and (v['target'] is None or v['target'][0] != 'canvas')
                          for v in self.session['drafts'].values())
            if pending and not ask(self.window, '未保存的素材', '仍有未保存的素材草稿，確定關閉並捨棄？'):
                return False
        return True

    def new_material(self, add_to_canvas=False, text=''):
        if not self.can_leave(): return
        saved = self.session['drafts'].get(('new', ''))
        if saved and saved['dirty'] and not text:
            self._restore_session(saved)
            self._publish(force=True)
            if not self.compact and self.narrow: self.hide_library()
            return
        self.target = None
        value = dict(id=uid(), name='', prompt=text, aliases=[], notes='', excludes=[], preview='',
                     module=self.category.currentData(), content_kind='tags')
        self.load_value(value, 'new')
        self.status.setText('完成後可直接保存並加入畫布。')
        if not self.compact and self.narrow: self.hide_library()
        (self.name if self.compact else self.prompt).setFocus()

    def open_selected(self):
        ident = self.selected_id()
        if ident: self.open_material(ident)

    def open_material(self, ident):
        if not self.can_leave(): return
        try: value = materials.item(self.window.state, ident)
        except ValueError as exc: self.window.notice(str(exc)); return
        for index in range(self.listing.count()):
            entry = self.listing.item(index)
            if entry.data(Qt.ItemDataRole.UserRole) == ident:
                if entry is not self.listing.currentItem():
                    self.listing.setCurrentItem(entry); self.listing.scrollToItem(entry)
                break
        self.target = ('material', ident)
        saved = self.session['drafts'].get(self.target)
        if saved and (saved['dirty'] or saved['base'] == value):
            self._restore_session(saved)
            self._publish(force=True)
            if not self.compact and self.narrow: self.hide_library()
            return
        self.load_value(value, 'material')
        if not self.compact and self.narrow: self.hide_library()

    def edit_canvas(self, key):
        if not self.can_leave(): return
        try: value = materials.canvas_node(self.window.state, key)
        except ValueError as exc: self.window.notice(str(exc)); return
        self.target = ('canvas', key)
        self.edit_workspace = self.window.state['workspace']
        from .multi_output import owner
        destination = owner(self.window.state, key.partition(':')[0])
        if destination: self.set_destination(destination)
        self.load_value(value, 'canvas')

    def load_value(self, value, mode, prompt_text=None):
        self.loading = True
        self.timer.stop()
        self.base = copy.deepcopy(value)
        root = value.get('composition', value)
        self.name.setText(value.get('name', ''))
        from .material_editor_documents import activate_document
        activate_document(self, root.get('prompt', '') if prompt_text is None else prompt_text)
        self.kind.setCurrentIndex(max(0, self.kind.findData(value.get('content_kind', root.get('content_kind', 'tags')))))
        self.aliases.setText('、'.join(value.get('aliases', [])))
        self.notes.setPlainText(value.get('notes', ''))
        self.excludes.setPlainText('\n'.join(root.get('excludes', [])))
        self.edit_category.setCurrentIndex(max(0, self.edit_category.findData(value.get('module', value.get('source_module')))))
        self.preview_path = value.get('preview', '')
        self.show_preview()
        self.more.setChecked(False)
        is_canvas = mode == 'canvas'
        self.heading.setText('畫布文字副本' if is_canvas else '新建文字素材' if mode == 'new' else '素材原件')
        if is_canvas and self.destination_id:
            canvas = self.window.state.get('multi_output', {}).get('canvases', {}).get(self.destination_id, {})
            self.heading.setText('畫布副本 · ' + canvas.get('name', ''))
        self.scope_hint.setText('修改立即套用目前畫布；素材原件與其他副本保持原樣。' if is_canvas
                              else '保存供下次使用；既有畫布副本保持原樣。')
        self.aliases.setEnabled(not is_canvas)
        self.notes.setEnabled(not is_canvas)
        self.edit_category.setEnabled(not is_canvas)
        self.save_button.setVisible(not is_canvas)
        self.use_button.setVisible(self.compact and not is_canvas)
        self.edit_destination.setVisible(self.compact and not is_canvas)
        self.variant_button.setVisible(self.compact and mode != 'new')
        self.delete_button.setVisible(self.compact and mode == 'material')
        self.history_button.setVisible(self.compact and mode != 'new')
        self.status.setText('')
        self.detail.setEnabled(True)
        self.dirty = False
        self.loading = False
        if self.compact:
            if self.narrow: self.stack.setCurrentIndex(1)
        else:
            from .material_editor_page import update_page
            update_page(self)
        history = getattr(self.prompt, 'history', None)
        if history is not None and history.identity != self.base['id']: history.bind()
        self._publish(force=True)

    def form_values(self):
        return dict(name=self.name.text().strip(), prompt=self.prompt.toPlainText(),
                    content_kind=self.kind.currentData(), preview=self.preview_path,
                    excludes=[v.strip() for v in self.excludes.toPlainText().splitlines() if v.strip()])

    def edited(self, *_):
        if self.loading: return
        self.dirty = True
        if self.target and self.target[0] == 'canvas':
            self.timer.start()
            self.status.setText('正在編輯畫布副本…')
        else: self.status.setText('尚未保存')
        self._publish(force=True)
        if not self.compact:
            from .material_editor_page import update_page
            update_page(self)

    def flush_canvas(self):
        self.timer.stop()
        if not self.dirty or not self.target or self.target[0] != 'canvas': return True
        if self.edit_workspace != self.window.state['workspace']:
            self.status.setText('工作區已切換，未將編輯套用到其他畫布。')
            return False
        values = self.form_values()
        if not values['name']:
            self.status.setText('請填寫名稱；已保存的內容仍保留。')
            return False
        key = self.target[1]
        expected = copy.deepcopy(self.base)
        changed = {name: value for name, value in values.items() if value != expected.get(name, '' if name in ('preview', 'prompt') else None)}
        def apply(state):
            current = materials.canvas_node(state, key)
            for field in changed:
                if current.get(field) != expected.get(field) and current.get(field) != changed[field]:
                    raise ValueError('此文字已在其他位置修改，請重新開啟後繼續編輯。')
            materials.edit_canvas(state, key, changed)
        if not changed:
            self.dirty = False
            return True
        # refresh() can be called during commit; keep this form's pending text.
        if self.window.canvas.commit(apply, label='編輯畫布文字', merge_key='editor:' + key):
            self.base = copy.deepcopy(materials.canvas_node(self.window.state, key))
            self.dirty = False
            self.status.setText('已同步畫布')
            self._publish()
            if not self.compact:
                from .material_editor_page import update_page
                update_page(self)
            return True
        self.status.setText('尚未套用，原內容保留。')
        return False

    def target_canvas(self):
        key = self.destination.currentData()
        if key is None:
            self.window.notice('請先選擇要加入的畫布。')
        return key

    def save_material(self, add=False):
        if isinstance(add, bool): add_to_canvas = add
        else: add_to_canvas = False
        target = self.target_canvas() if add_to_canvas else None
        if add_to_canvas and target is None: return
        value = copy.deepcopy(self.base or {})
        fields = self.form_values()
        if 'composition' in value:
            value['composition'].update(fields)
        value.update(fields, module=self.edit_category.currentData(), notes=self.notes.toPlainText(),
                     aliases=list(dict.fromkeys(v.strip() for v in self.aliases.text().replace(',', '、').replace('，', '、').split('、') if v.strip())))
        old = copy.deepcopy(self.base)
        ident = value.get('id') or uid()
        value['id'] = ident
        def apply(state):
            if self.target and self.target[0] == 'material':
                current = materials.item(state, ident)
                changed = {key: field for key, field in value.items() if field != old.get(key)}
                for key, field in changed.items():
                    if current.get(key) != old.get(key) and current.get(key) != field:
                        raise ValueError('素材已在其他位置修改，請重新開啟後再保存。')
                materials.save(state, dict(changed, id=ident))
            else: materials.save(state, value)
            if add_to_canvas: materials.add_to_canvas(state, ident, target)
        if self.window.canvas.commit(apply, label='保存素材並加入畫布' if add_to_canvas else '保存文字素材'):
            self.session['drafts'].pop(('new', ''), None)
            self.dirty = False
            self.target = ('material', ident)
            self.load_value(materials.item(self.window.state, ident), 'material')
            self.refresh_list()
            self.status.setText('已保存並加入畫布' if add_to_canvas else '素材已保存')
        else:
            self.status.setText('未能儲存，編輯內容已保留；請檢查名稱與正文。')
        if not self.compact:
            from .material_editor_page import update_page
            update_page(self)

    def add_selected(self):
        ident, target = self.selected_id(), self.destination.currentData()
        if not ident: return
        if target is None and hasattr(self.window, 'add_material_to_canvas'):
            self.window.add_material_to_canvas(ident)
            self.refresh_list()
            return
        if target is None:
            self.target_canvas()
            return
        if self.window.canvas.commit(lambda state: materials.add_to_canvas(state, ident, target), label='加入文字素材'):
            self.refresh_list()

    def toggle_pin(self):
        ident = self.selected_id()
        if ident: self.pin_material(ident)

    def pin_material(self, ident):
        def apply(state):
            value = materials.item(state, ident)
            value['pinned'] = not value.get('pinned', False)
        if self.window.canvas.commit(apply, label='變更素材釘選'): self.refresh_list()

    def new_category(self):
        name, ok = InputDialog.getText(self.window, '新增分類', '分類名稱')
        if not ok or not name.strip(): return
        added = []
        if self.window.canvas.commit(lambda state: added.append(materials.add_category(state, name)), label='新增素材分類'):
            self.refresh()
            self.category.setCurrentIndex(self.category.findData(added[0]))
            self.edit_category.setCurrentIndex(self.edit_category.findData(added[0]))

    def save_variant(self):
        if not self.can_leave() or not self.base: return
        if self.target and self.target[0] == 'canvas':
            value = materials.from_canvas(self.window.state, self.target[1], self.edit_category.currentData())
        else:
            value = copy.deepcopy(self.base)
            fields = self.form_values()
            if 'composition' in value:
                value['composition'].update(fields)
            value.update(fields, module=self.edit_category.currentData(), notes=self.notes.toPlainText(),
                         aliases=list(dict.fromkeys(v.strip() for v in self.aliases.text().replace(',', '、').replace('，', '、').split('、') if v.strip())))
            value['id'] = uid()
            value.pop('last_used', None)
            value['pinned'] = False
        self.target = None
        self.load_value(value, 'new')
        self.edited()
        self.name.selectAll()
        self.name.setFocus()
        self.status.setText('為變體命名後保存；原件保持原樣。')

    def delete_material(self):
        if not self.target or self.target[0] != 'material': return
        ident = self.target[1]
        if not ask(self.window, '刪除素材', '刪除此素材原件？畫布中的獨立副本會保留。'): return
        if self.window.canvas.commit(lambda state: materials.remove(state, ident), label='刪除文字素材'):
            self.target = None
            self.dirty = False
            self.base = None
            self.detail.setEnabled(False)
            if self.compact: self.show_library()
            else: self.new_material()

    def history(self):
        if self.target: show_history(self.window, self.target[1].partition(':')[0])

    def legacy_content(self):
        from .editor_legacy import documents
        values = documents(self.window.state)
        if not values:
            self.window.notice('沒有保存的舊清單內容。')
            return
        dialog = StudioDialog(self.window)
        dialog.setWindowTitle('取回舊清單內容')
        dialog.resize(720, 600)
        selector = ComboBox()
        for value in values: selector.addItem(value['name'])
        mode = ComboBox()
        mode.addItem('自動組合', 'generated'); mode.addItem('手動草稿', 'manual'); mode.addItem('原始草稿基準', 'draft_base')
        body = QPlainTextEdit(); body.setReadOnly(True)
        info = label('', 'Subtle', True)
        dialog.body.addLayout(row(selector, mode)); dialog.body.addWidget(info); dialog.body.addWidget(body, 1)
        def refresh():
            value = values[selector.currentIndex()][mode.currentData()]
            body.setPlainText(value if value is not None else '')
            info.setText('沒有手動草稿，當時使用自動組合。' if value is None else
                         '這是保留的空手動草稿。' if value == '' and mode.currentData() == 'manual' else '內容保留在原文件；複製或製作素材不會替換目前畫布。')
        def create():
            text = body.toPlainText()
            dialog.accept()
            self.new_material(text=text)
        selector.currentIndexChanged.connect(refresh); mode.currentIndexChanged.connect(refresh)
        dialog.body.addLayout(row(button('複製內容', lambda: self.window.copy_text(body.toPlainText()), 'Quiet'),
                                   button('製作新素材', create, 'Primary'), button('關閉', dialog.accept, 'Quiet')))
        refresh(); dialog.exec()

    def choose_image(self):
        path = image_path(self)
        if path: self.receive_image(path)

    def receive_image(self, path):
        try: self.preview_path = thumbnail(path, self.window.store.directory)
        except Exception as exc: self.window.notice(str(exc)); return
        self.show_preview()
        self.edited()

    def clear_image(self):
        self.preview_path = ''
        self.show_preview()
        self.edited()

    def show_preview(self):
        if hasattr(self.preview,'set_source'):
            self.preview.set_source(self.preview_path);return
        set_preview(self.preview, self.window.store, self.preview_path, 130)
        self.preview.setVisible(bool(self.preview_path))
        if not self.preview_path: self.preview.setText('')

    def showEvent(self, event):
        super().showEvent(event)
        if not self.compact and self.target is None: self.prompt.setFocus()

    def hideEvent(self, event):
        self.flush_canvas()
        self._publish()
        super().hideEvent(event)


# Full-page browsing and modal editing share the same material form semantics.
from .material_editor_browser import PromptEditor
