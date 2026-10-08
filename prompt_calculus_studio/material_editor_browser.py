"""Read-only material library and transactional original-edit dialog (rev9)."""
import copy
import json
from PySide6.QtCore import Qt, QTimer, QSize, QRectF, Signal, QEvent
from PySide6.QtGui import QFont, QColor, QPen, QFontMetrics, QTextCursor, QTextBlockFormat, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QListWidget, QListWidgetItem, QCheckBox, QScrollArea, QTextEdit, QLabel,
    QStyledItemDelegate, QStyle, QApplication, QDialog, QSizePolicy)
from . import materials
from .core import uid
from .widgets import label, button, row, ComboBox, StudioDialog, RoundMenu, preview_path, set_preview, InputDialog, ask
from .views import widget_colors
from .material_editor_page import Disclosure, sync_prompt_style, apply_line_spacing
from .management_reading import PREFERRED_CONTENT_WIDTH


class LibraryDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(180, QFontMetrics(option.font).height() * 2 + 34)

    def paint(self, painter, option, index):
        colors = widget_colors(self.parent())
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = QRectF(option.rect).adjusted(1, 3, -2, -3)
        painter.save(); painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(colors['accent' if selected else 'line']), 1))
        painter.setBrush(QColor(colors['selected' if selected else 'hover' if hover else 'surface']))
        painter.drawRoundedRect(rect, 9, 9)
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QPen(QColor(colors['text']), 1, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 6, 6)
        left = rect.left() + 16
        width = max(10, int(rect.right()-left-32))
        name, _, prompt = str(index.data(Qt.ItemDataRole.DisplayRole)).partition('\n')
        font = QFont(option.font); font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(font); height = metrics.height()
        painter.setFont(font); painter.setPen(QColor(colors['text']))
        painter.drawText(QRectF(left, rect.top()+10, width, height+2), Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(name, Qt.TextElideMode.ElideRight, width))
        font.setWeight(QFont.Weight.Normal); metrics = QFontMetrics(font); painter.setFont(font)
        painter.setPen(QColor(colors['secondary']))
        painter.drawText(QRectF(left, rect.top()+16+height, width, height+2), Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(prompt, Qt.TextElideMode.ElideRight, width))
        painter.drawText(QRectF(rect.right()-28, rect.top(), 20, rect.height()), Qt.AlignmentFlag.AlignCenter, '›')
        painter.restore()


class StatusLabel(QLabel):
    def __init__(self):
        super().__init__(); self.setObjectName('Subtle'); self.setWordWrap(True); self.hide()
    def setText(self, text):
        super().setText(text); self.setVisible(bool(text))


class PreviewImage(QLabel):
    clicked = Signal()
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton: self.clicked.emit()
        super().mouseReleaseEvent(event)


class PromptEditor(QWidget):
    """Full-page library; compact callers retain the existing copy editor."""
    def __new__(cls, window, compact=False):
        if compact:
            from .prompt_editor import MaterialEditorForm
            return MaterialEditorForm(window, compact=True)
        return super().__new__(cls)

    def __init__(self, window, compact=False):
        super().__init__()
        self.window = window; self.compact = False; self.destination_id = None
        self.active_dialog = None; self.preview_id = None; self._refreshing = False
        self.narrow = False; self._keyboard_selection = False
        if not hasattr(window, '_material_editor_session'):
            window._material_editor_session = dict(serial=0, active=None, drafts={})
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        from .workspace_navigation import WorkspaceModeSwitch
        self.workspace_switch = WorkspaceModeSwitch(window, active='list')
        workbar = QHBoxLayout(); workbar.setContentsMargins(24, 16, 24, 0)
        self.workspace_name = label('', 'Subtle'); self.workspace_name.setMaximumWidth(180)
        workbar.addWidget(self.workspace_name); workbar.addWidget(self.workspace_switch); workbar.addStretch(); outer.addLayout(workbar)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame); outer.addWidget(self.scroll)
        self.scroll.viewport().installEventFilter(self)
        self.shell = QWidget(); self.scroll.setWidget(self.shell)
        self.category_panel = QWidget(self.shell); category_layout = QVBoxLayout(self.category_panel)
        category_layout.setContentsMargins(16, 24, 16, 16); category_layout.setSpacing(12)
        self.category_heading = label('內容分類', 'Subtle'); category_layout.addWidget(self.category_heading)
        self.categories = QListWidget(); self.categories.setObjectName('CategoryList')
        self.categories.setFrameShape(QListWidget.Shape.NoFrame); self.categories.setSpacing(3)
        self.categories.setResizeMode(QListWidget.ResizeMode.Adjust); category_layout.addWidget(self.categories, 1)
        self.category = ComboBox(self); self.category.hide()
        self.category_controls = QWidget(); self.category_controls.hide()
        controls = QHBoxLayout(self.category_controls); controls.setContentsMargins(0, 0, 0, 0); controls.setSpacing(12)
        controls.addWidget(self.category, 1)
        category_layout.addWidget(self.category_controls)
        self.manage_button = button('管理分類', self.manage_categories, 'Quiet'); category_layout.addWidget(self.manage_button)
        self.browser = QWidget(self.shell); content = QVBoxLayout(self.browser)
        content.setContentsMargins(0, 0, 0, 0); content.setSpacing(14)
        self.title = label('全部模組', 'Heading'); self.count = label('', 'Subtle')
        self.new_button = button('新增模組', self.new_material, 'Primary')
        from .ui_icons import icon
        self.new_button.setIcon(icon('plus'))
        self.library_more = button('更多', self.library_menu, 'Quiet'); self.library_more.setIcon(icon('more-horizontal'))
        content.addLayout(row(self.title, self.count, None, self.new_button, self.library_more))
        self.search = QLineEdit(); self.search.setPlaceholderText('搜尋目前分類的名稱、別名或內容')
        self.pins = QCheckBox('釘選'); self.order = ComboBox(); self.order.addItem('名稱', 'name'); self.order.addItem('最近使用', 'recent')
        content.addLayout(row(self.search, self.pins, self.order))
        self.list_hint = label('單擊預覽 · 雙擊編輯', 'Subtle'); content.addWidget(self.list_hint)
        from .prompt_editor import MaterialList
        self.listing = MaterialList(); self.listing.setFrameShape(QListWidget.Shape.NoFrame)
        self.listing.setItemDelegate(LibraryDelegate(self.listing)); self.listing.setSpacing(3)
        self.listing.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listing.customContextMenuRequested.connect(self.material_menu)
        content.addWidget(self.listing, 1)
        self.empty_results = label('這個分類還沒有模組，可按「新增模組」開始。', 'Subtle', True)
        content.addWidget(self.empty_results)
        self.preview_card = QFrame(self.shell); self.preview_card.setObjectName('InsetPanel')
        preview = QVBoxLayout(self.preview_card); preview.setContentsMargins(18, 18, 18, 18); preview.setSpacing(12)
        preview.addLayout(row(label('素材預覽', 'Subtle'), None, button('×', self.close_preview, 'Quiet')))
        self.preview_name = label('', '', True); preview.addWidget(self.preview_name)
        self.preview_text = QTextEdit(); self.preview_text.setReadOnly(True); self.preview_text.setAcceptRichText(False)
        self.preview_text.setObjectName('MaterialPreviewPrompt'); self.preview_text.setUndoRedoEnabled(False)
        self.preview_text.setFrameShape(QTextEdit.Shape.NoFrame)
        self.preview_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        preview.addWidget(self.preview_text)
        self.preview_image = PreviewImage(); self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.setCursor(Qt.CursorShape.PointingHandCursor); self.preview_image.clicked.connect(self.enlarge_preview)
        preview.addWidget(self.preview_image)
        self.edit_button = button('編輯素材', lambda: self.edit_material(self.preview_id)); preview.addWidget(self.edit_button)
        self.preview_card.hide()
        self.preview_timer = QTimer(self); self.preview_timer.setTimerType(Qt.TimerType.PreciseTimer); self.preview_timer.setSingleShot(True); self.preview_timer.timeout.connect(self._show_selected_preview)
        self.listing.itemClicked.connect(self._mouse_preview)
        self.listing.itemDoubleClicked.connect(lambda entry: self.edit_material(entry.data(Qt.ItemDataRole.UserRole)))
        self.listing.currentItemChanged.connect(self._selection_changed)
        self.f2 = QShortcut(QKeySequence(Qt.Key.Key_F2), self); self.f2.activated.connect(self.open_selected)
        self.search.textChanged.connect(self.refresh_list); self.pins.toggled.connect(self.refresh_list)
        self.order.currentIndexChanged.connect(self.refresh_list)
        self.category.currentIndexChanged.connect(self._category_changed)
        self.categories.currentItemChanged.connect(self._category_selected)
        self.refresh()
        self.set_browser_category(getattr(window, '_material_browser_category', None))

    def eventFilter(self, watched, event):
        if hasattr(self, 'scroll') and watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._layout)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event); QTimer.singleShot(0, self._layout)

    def _layout(self):
        if not hasattr(self, 'browser'): return
        self._sync_type()
        viewport_width = self.scroll.viewport().width(); height = self.scroll.viewport().height()
        minimum_browser = max(520, self.browser.minimumSizeHint().width())
        self.narrow = viewport_width < max(800, 264+minimum_browser+24)
        # Use the established management content column. Detail is additional:
        # opening it must not change the main list's bounds.
        width = viewport_width if self.narrow else self.scroll.width()-2*self.scroll.frameWidth()
        self.workspace_name.setVisible(width >= 600)
        if self.narrow:
            self.category_heading.hide(); self.categories.hide()
            self.category.show(); self.category_controls.show()
            if self.manage_button.parentWidget() is not self.category_controls:
                self.category_controls.layout().addWidget(self.manage_button)
            self.category_panel.setGeometry(0, 0, width, 100)
            panel_height = self.category_panel.layout().sizeHint().height()
            self.category_panel.setFixedHeight(panel_height)
            start = panel_height+16; area_x = 16; area_width = max(0, width-32)
        else:
            self.category_heading.show(); self.categories.show()
            self.category.hide(); self.category_controls.hide()
            if self.manage_button.parentWidget() is not self.category_panel:
                self.category_panel.layout().addWidget(self.manage_button)
            self.categories.setFlow(QListWidget.Flow.TopToBottom); self.categories.setWrapping(False)
            self.categories.setMinimumHeight(0); self.categories.setMaximumHeight(16777215)
            self.categories.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            for i in range(self.categories.count()): self.categories.item(i).setSizeHint(QSize())
            self.category_panel.setFixedHeight(height)
            self.category_panel.setGeometry(0, 0, 240, height)
            start = 24; area_x = 264; area_width = max(0, width-area_x-24)
        has_preview = bool(self.preview_id and self.preview_card.isVisible())
        detail_width = min(380, max(340, round(area_width*.22)))
        central = min(PREFERRED_CONTENT_WIDTH, area_width)
        below = area_width < central+24+detail_width
        x = area_x
        row_height = self.listing.fontMetrics().height()*2 + 40
        list_height = max(110, min(580, self.listing.count()*row_height + 12))
        self.listing.setMinimumHeight(list_height); self.listing.setMaximumHeight(list_height)
        browser_height = self.browser.sizeHint().height()
        self.browser.setGeometry(x, start, central, browser_height)
        bottom = start + browser_height + 24
        if has_preview:
            card_width = detail_width if not below else central
            self.preview_card.ensurePolished(); self.preview_card.setFixedWidth(card_width)
            layout = self.preview_card.layout(); margins = layout.contentsMargins()
            frame = self.preview_card.contentsMargins()
            available = max(1, card_width-margins.left()-margins.right()-frame.left()-frame.right())
            # QLabel's unconstrained sizeHint describes fewer wrapped lines.
            # Fix its actual height at this card width before asking the layout
            # for its minimum/height-for-width; fixed text and image then cannot
            # be squeezed into each other's geometry by a too-short card.
            self.preview_name.setMinimumHeight(0); self.preview_name.setMaximumHeight(16777215)
            name_height = max(1, self.preview_name.heightForWidth(available))
            self.preview_name.setFixedHeight(name_height)
            self.preview_text.setFixedWidth(available)
            text_height = int(self.preview_text.document().size().height())+16
            self.preview_text.setFixedHeight(max(70, min(300, text_height)))
            layout.invalidate()
            card_height = max(layout.totalMinimumSize().height(), layout.totalHeightForWidth(card_width))
            px, py = (x, bottom) if below else (x+central+24, start)
            self.preview_card.setGeometry(px, py, card_width, card_height)
            layout.activate()
            bottom = max(bottom, py+card_height+24)
        self.shell.setMinimumHeight(max(height, bottom))
        self.shell.setMinimumWidth(0)

    def _sync_type(self):
        from .theme import font_pixels
        settings = dict(self.window.state['settings']); settings.update(getattr(self.window, 'appearance_preview', {}))
        title_style = 'font-size:%dpx; font-weight:600;' % font_pixels(settings['ui_size']+5)
        if self.title.styleSheet() != title_style: self.title.setStyleSheet(title_style)
        colors = widget_colors(self)
        preview_style = ("QTextEdit#MaterialPreviewPrompt { font-family:'Cascadia Mono','Consolas','Microsoft JhengHei'; "
                         "font-size:%dpx; color:%s; background:%s; border:0; padding:4px; }") % (
                             font_pixels(settings['prompt_size']), colors['text'], colors['field'])
        if self.preview_text.styleSheet() != preview_style: self.preview_text.setStyleSheet(preview_style)
        self.preview_text.ensurePolished(); self.title.ensurePolished()
        self.preview_text.document().setDefaultFont(self.preview_text.font())
        block = self.preview_text.document().begin()
        if block.isValid() and block.blockFormat().lineHeight() != 160:
            cursor = QTextCursor(self.preview_text.document()); cursor.select(QTextCursor.SelectionType.Document)
            fmt = QTextBlockFormat(); fmt.setLineHeight(160, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            cursor.mergeBlockFormat(fmt)

    def get_browser_category(self): return self.category.currentData()

    def set_browser_category(self, ident):
        if ident == '@uncategorized':
            ident = next((v['id'] for v in self.window.state['modules'] if v['name']=='未分類'), ident)
        index = self.category.findData(ident)
        self.category.setCurrentIndex(max(0, index))
        self.window._material_browser_category = self.category.currentData()
        for i in range(self.categories.count()):
            if self.categories.item(i).data(Qt.ItemDataRole.UserRole) == self.category.currentData():
                self.categories.setCurrentRow(i); break

    def _category_selected(self, entry, previous=None):
        if entry and not self._refreshing: self.set_browser_category(entry.data(Qt.ItemDataRole.UserRole))

    def _category_changed(self):
        self.window._material_browser_category = self.category.currentData()
        self.title.setText(self.category.currentText()); self.refresh_list()

    def refresh(self):
        self._refreshing = True
        workspace = next((v for v in self.window.state['workspaces'] if v['id']==self.window.state['workspace']), {})
        self.workspace_name.setText(workspace.get('name', ''))
        selected = self.get_browser_category()
        self.category.blockSignals(True); self.categories.blockSignals(True)
        self.category.clear(); self.categories.clear()
        values = [('全部模組', None)] + [(v['name'], v['id']) for v in self.window.state['modules']]
        unclassified = next((v['id'] for v in self.window.state['modules'] if v['name']=='未分類'), None)
        if unclassified is None: values.append(('未分類', '@uncategorized'))
        elif selected == '@uncategorized': selected = unclassified
        for name, ident in values:
            count = sum(1 for v in self.window.state['items'] if ident is None or v['module'] == ident)
            self.category.addItem(name, ident)
            entry = QListWidgetItem(f'{name}  {count}'); entry.setData(Qt.ItemDataRole.UserRole, ident)
            self.categories.addItem(entry)
        index = max(0, self.category.findData(selected)); self.category.setCurrentIndex(index); self.categories.setCurrentRow(index)
        self.category.blockSignals(False); self.categories.blockSignals(False); self._refreshing = False
        self.window._material_browser_category = self.category.currentData()
        self.title.setText(self.category.currentText()); self.refresh_list()
        if self.preview_id: self.preview_material(self.preview_id)

    def refresh_list(self, *_):
        selected = self.selected_id(); scroll = self.listing.verticalScrollBar().value()
        self.listing.blockSignals(True); self.listing.clear()
        category = self.get_browser_category()
        rows = [] if category == '@uncategorized' else materials.search(self.window.state, self.search.text(), category, pinned=self.pins.isChecked(), order=self.order.currentData())
        for value in rows:
            entry = QListWidgetItem(value['name']+'\n'+value['prompt'].replace('\n', ' '))
            entry.setData(Qt.ItemDataRole.UserRole, value['id']); self.listing.addItem(entry)
            if value['id'] == selected: self.listing.setCurrentItem(entry)
        self.listing.blockSignals(False); self.listing.verticalScrollBar().setValue(scroll)
        self.listing.setVisible(bool(rows))
        self.count.setText(f'{len(rows)} 個模組'); self.empty_results.setVisible(not rows)
        self.empty_results.setText('找不到符合搜尋的模組。' if self.search.text() else '這個分類還沒有模組，可按「新增模組」開始。')
        self._layout()

    def selected_id(self):
        entry = self.listing.currentItem()
        return entry.data(Qt.ItemDataRole.UserRole) if entry else None

    def _mouse_preview(self, entry):
        # A single click may introduce the preview below the list. Never relayout
        # until Qt's actual double-click interval has elapsed.
        if self.active_dialog or self.listing.pressed_button != Qt.MouseButton.LeftButton: return
        self.preview_timer.start(QApplication.doubleClickInterval())

    def _selection_changed(self, entry, previous):
        if entry and QApplication.mouseButtons() == Qt.MouseButton.NoButton and not self._refreshing:
            self.preview_timer.stop(); self.preview_material(entry.data(Qt.ItemDataRole.UserRole))

    def _show_selected_preview(self):
        if self.selected_id(): self.preview_material(self.selected_id())

    def preview_material(self, ident):
        try: value = materials.item(self.window.state, ident)
        except ValueError: self.close_preview(); return
        self.listing.blockSignals(True)
        for i in range(self.listing.count()):
            if self.listing.item(i).data(Qt.ItemDataRole.UserRole) == ident: self.listing.setCurrentRow(i); break
        self.listing.blockSignals(False)
        self.preview_id = ident; self.preview_name.setText(value['name']); self.preview_text.setPlainText(value['prompt'])
        has_image = bool(preview_path(self.window.store, value.get('preview')))
        self.preview_image.setVisible(has_image)
        if has_image: set_preview(self.preview_image, self.window.store, value.get('preview'), 250)
        self.preview_card.show(); self._layout()

    def close_preview(self):
        self.preview_timer.stop(); self.preview_id = None; self.preview_card.hide(); self._layout()

    def enlarge_preview(self):
        if not self.preview_id: return
        value = materials.item(self.window.state, self.preview_id); path = preview_path(self.window.store, value.get('preview'))
        if not path: return
        dialog = StudioDialog(self.window); dialog.setWindowTitle(value['name']); dialog.resize(800, 700)
        image = QLabel(); image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setPixmap(QPixmap(str(path)).scaled(720, 560, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        dialog.body.addWidget(image)
        def finished(_):
            if getattr(self, 'image_dialog', None) is dialog: self.image_dialog = None
            dialog.deleteLater()
        dialog.finished.connect(finished)
        self.image_dialog = dialog; dialog.show()

    def set_destination(self, canvas_id):
        self.destination_id = canvas_id
        if self.active_dialog: self.active_dialog.set_destination(canvas_id)

    def new_material(self, add_to_canvas=False, text=''):
        return self._open_dialog(None, text)

    def edit_material(self, ident): return self._open_dialog(ident)
    open_material = edit_material

    def open_selected(self):
        ident = self.selected_id() or self.preview_id
        if ident: return self.edit_material(ident)

    def _open_dialog(self, ident, text=''):
        self.preview_timer.stop()
        if self.active_dialog:
            self.active_dialog.raise_(); return self.active_dialog
        dialog = MaterialEditDialog(self, ident, text)
        self.active_dialog = dialog
        dialog.finished.connect(lambda _: self._dialog_finished(dialog))
        dialog.show(); dialog.prompt.setFocus()
        return dialog

    def _dialog_finished(self, dialog):
        self.active_dialog = None; self.refresh(); self.listing.setFocus()
        dialog.deleteLater()

    def export_state(self):
        return self.active_dialog.form.export_state() if self.active_dialog else None

    def reset(self):
        if self.active_dialog: self.active_dialog.reject()
        self.close_preview(); self.refresh()

    def can_leave(self, closing=False): return True
    def flush_canvas(self): return True
    def show_library(self): self.show(); self.search.setFocus()
    def hide_library(self): pass

    def material_menu(self, position):
        entry = self.listing.itemAt(position)
        if not entry: return
        ident = entry.data(Qt.ItemDataRole.UserRole); value = materials.item(self.window.state, ident)
        menu = RoundMenu(self); menu.addAction('編輯素材', lambda: self.edit_material(ident))
        menu.addAction('取消釘選' if value.get('pinned') else '釘選素材', lambda: self.pin_material(ident))
        menu.addAction('另存為新素材', lambda: self.duplicate_material(ident))
        from .prompt_editor import show_history
        menu.addAction('內容歷史', lambda: show_history(self.window, ident))
        menu.addAction('刪除素材', lambda: self.delete_material(ident))
        menu.open_at(self.listing.viewport().mapToGlobal(position))

    def pin_material(self, ident):
        def apply(state):
            value = materials.item(state, ident); value['pinned'] = not value.get('pinned', False)
        if self.window.canvas.commit(apply, label='變更素材釘選'): self.refresh()

    def library_menu(self):
        menu = RoundMenu(self); menu.addAction('新增分類', self.new_category)
        from .prompt_editor import MaterialEditorForm
        menu.addAction('取回舊清單內容', lambda: MaterialEditorForm.legacy_content(self))
        menu.open_at(self.library_more.mapToGlobal(self.library_more.rect().bottomLeft()))

    def duplicate_material(self, ident):
        value = copy.deepcopy(materials.item(self.window.state, ident)); value['id'] = uid()
        value.pop('last_used', None); value['pinned'] = False
        dialog = self.new_material(); dialog.form.target = None
        dialog.form.load_value(value, 'new'); dialog.initial_id = value['id']; dialog.form.edited()
        dialog.name.selectAll(); dialog.name.setFocus()
        return dialog

    def delete_material(self, ident):
        if not ask(self.window, '刪除素材', '刪除此素材原件？畫布中的獨立副本會保留。'): return
        if self.window.canvas.commit(lambda state: materials.remove(state, ident), label='刪除文字素材'):
            if self.preview_id == ident: self.close_preview()
            self.refresh()

    def manage_categories(self):
        menu = RoundMenu(self); menu.addAction('新增分類', self.new_category)
        menu.open_at(self.category_panel.mapToGlobal(self.category_panel.rect().bottomLeft()))

    def new_category(self):
        name, ok = InputDialog.getText(self.window, '新增分類', '分類名稱')
        if not ok or not name.strip(): return
        added = []
        if self.window.canvas.commit(lambda state: added.append(materials.add_category(state, name)), label='新增素材分類'):
            self.refresh(); self.set_browser_category(added[0])


class MaterialEditDialog(StudioDialog):
    """Only explicit save crosses from this dialog's draft to canonical data."""
    def __init__(self, page, ident, text=''):
        super().__init__(page.window)
        from .prompt_editor import MaterialEditorForm
        self.page = page; self.target_id = ident; self.busy = False; self.receipt = None; self.committed = False; self.was_new = ident is None
        self.form = MaterialEditorForm(page.window, compact=True, isolated=True); self.form.setParent(self); self.form.hide()
        f = self.form
        f.set_destination(page.destination_id)
        f.category.setCurrentIndex(max(0, f.category.findData(page.get_browser_category())))
        if ident: f.open_material(ident)
        else: f.new_material(text=text)
        self.initial_id = f.base['id']
        self.name, self.prompt = f.name, f.prompt
        self.setWindowTitle('編輯素材原件' if ident else '新增文字素材'); self.resize(600, 610)
        self.body.setSpacing(10)
        self.intro = label('保存供下次使用；既有畫布副本保持原樣。', 'Subtle', True); self.body.addWidget(self.intro)
        self.name_label = label('名稱', 'Subtle'); self.body.addWidget(self.name_label); self.body.addWidget(self.name)
        self.name.setPlaceholderText('素材名稱'); self.name.setAccessibleName('素材名稱')
        expand = button('展開長文', self.expand_text, 'Quiet')
        self.body.addLayout(row(label('Prompt', 'Subtle'), None, expand))
        self.prompt.setMinimumHeight(100); self.prompt.setFixedHeight(170); self.body.addWidget(self.prompt)
        self.advanced_toggle = Disclosure('進階功能 · 4 項'); self.body.addWidget(self.advanced_toggle)
        self.advanced_scroll = QScrollArea(); self.advanced_scroll.setWidgetResizable(True)
        self.advanced_scroll.setFrameShape(QScrollArea.Shape.NoFrame); self.advanced_scroll.setMinimumHeight(100)
        self.advanced = QWidget(); self.advanced_scroll.setWidget(self.advanced)
        advanced_layout = QVBoxLayout(self.advanced); advanced_layout.setContentsMargins(0, 0, 0, 0); advanced_layout.setSpacing(8)
        self.body.addWidget(self.advanced_scroll); self.advanced_scroll.hide()
        self.advanced_toggle.toggled.connect(self.advanced_scroll.setVisible)
        def group(title):
            toggle = Disclosure(title); panel = QWidget(); layout = QVBoxLayout(panel); layout.setContentsMargins(8, 4, 8, 10)
            advanced_layout.addWidget(toggle); advanced_layout.addWidget(panel); panel.hide(); toggle.toggled.connect(panel.setVisible)
            return toggle, layout
        self.info_toggle, info = group('素材資訊')
        fields = QFormLayout(); fields.addRow('分類', f.edit_category); fields.addRow('格式', f.kind); fields.addRow('別名', f.aliases); fields.addRow('備註', f.notes); info.addLayout(fields)
        self.lookup_toggle, lookup = group('標籤查詢'); lookup.addWidget(f.lookup); lookup.addWidget(f.lookup_hint)
        self.relation_toggle, relation = group('關係與預覽'); relation.addWidget(label('停用的完整 Tag，每行一項', 'Subtle')); relation.addWidget(f.excludes); relation.addWidget(f.preview)
        relation.addLayout(row(button('選擇預覽圖', f.choose_image, 'Quiet'), button('移除預覽', f.clear_image, 'Quiet')))
        self.history_toggle, history = group('內容歷史'); history.addWidget(button('查看內容歷史', f.history, 'Quiet'))
        self.error = StatusLabel(); f.status = self.error; self.body.addWidget(self.error)
        self.destination_label = label('', 'Subtle', True); self.body.addWidget(self.destination_label)
        self.cancel_button = button('取消', self.reject, 'Quiet')
        self.save_button = button('保存', lambda: self.save(bool(self.form.destination_id) and not self.target_id), 'Primary')
        self.add_button = button('保存並加入畫布', lambda: self.save(True), 'Quiet')
        self.identity = label('素材原件' if ident else '新素材', 'Subtle')
        self.body.addLayout(row(self.identity, None, self.cancel_button, self.save_button, self.add_button))
        self.form_scroll = QScrollArea(); self.form_scroll.setWidgetResizable(True); self.form_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.form_scroll.setMinimumHeight(0)
        form_content = QWidget(); form_content.setObjectName('MaterialDialogBody')
        self.form_scroll.setStyleSheet('QScrollArea { background:transparent; border:0; } QWidget#MaterialDialogBody { background:transparent; }')
        self.form_scroll.viewport().setObjectName('MaterialDialogViewport')
        self.form_scroll.viewport().setStyleSheet('QWidget#MaterialDialogViewport { background:transparent; }')
        form_layout = QVBoxLayout(form_content); form_layout.setContentsMargins(0, 0, 0, 0); form_layout.setSpacing(10)
        while self.body.count() > 1:
            item = self.body.takeAt(0)
            if item.widget(): form_layout.addWidget(item.widget())
            elif item.layout(): form_layout.addLayout(item.layout())
        form_layout.addStretch(); self.form_scroll.setWidget(form_content); self.body.insertWidget(0, self.form_scroll, 1)
        self.set_destination(page.destination_id)
        self.prompt.textChanged.connect(lambda: apply_line_spacing(f)); sync_prompt_style(f); apply_line_spacing(f)
        self.error.setText('')
        bounds = page.window.size()
        if bounds.width() > 0 and bounds.height() > 0:
            self.resize(min(600, max(340, bounds.width()-24)), min(610, max(400, bounds.height()-24)))
        if self.height() < 560:
            surface = self.layout().itemAt(0).widget().layout()
            surface.setContentsMargins(16, 12, 16, 12); surface.setSpacing(8); self.body.setSpacing(6)
            self.intro.hide(); self.name_label.hide(); form_layout.setSpacing(6)
            expand.setStyleSheet('min-height:0;padding:2px;'); expand.setFixedHeight(34)
            self.advanced_toggle.setStyleSheet('text-align:left;min-height:0;padding:2px;'); self.advanced_toggle.setFixedHeight(40)
            if self.width()<500: self.identity.hide()
            self.prompt.setFixedHeight(max(140, int(self.prompt.fontMetrics().lineSpacing()*3*1.65)+20))

    def set_destination(self, canvas_id):
        self.form.set_destination(canvas_id)
        value = self.page.window.state.get('multi_output', {}).get('canvases', {}).get(canvas_id)
        self.add_button.setVisible(value is not None and self.target_id is not None)
        self.save_button.setText('保存並加入畫布' if value and not self.target_id else '保存')
        self.destination_label.setVisible(value is not None)
        self.destination_label.setText('加入文字畫布：'+value['name'] if value else '')
        if self.receipt and canvas_id != self.receipt['destination']:
            self.error.setText('加入目的地已改變；再次保存並加入會使用新的目的地。')

    def expand_text(self):
        expanded = self.prompt.height() < 300
        self.prompt.setFixedHeight(340 if expanded else 170); self.resize(self.width(), 780 if expanded else 610)

    def _value(self):
        f = self.form; value = copy.deepcopy(f.base or {}); fields = f.form_values()
        if 'composition' in value: value['composition'].update(fields)
        value.update(fields, module=f.edit_category.currentData(), notes=f.notes.toPlainText(),
            aliases=list(dict.fromkeys(v.strip() for v in f.aliases.text().replace(',', '、').replace('，', '、').split('、') if v.strip())))
        return value

    def save(self, add=False):
        if self.busy: return False
        add = bool(add); f = self.form; window = self.page.window
        destination = f.destination_id if add else None
        if add and destination is None: self.error.setText('請先選擇要加入的文字畫布。'); return False
        value = self._value(); signature = json.dumps(value, sort_keys=True, ensure_ascii=False)
        receipt = self.receipt
        retry = bool(add and receipt and signature == receipt['signature'] and destination == receipt['destination'])
        if retry and receipt.get('added'): self.accept(); return True
        self.busy = True
        try:
            if not retry:
                old = copy.deepcopy(f.base); existed = f.target is not None and f.target[0] == 'material'
                ident = value['id']
                def apply(state):
                    if existed:
                        current = materials.item(state, ident)
                        changes = {key: val for key, val in value.items() if val != old.get(key)}
                        for key, val in changes.items():
                            if current.get(key) != old.get(key) and current.get(key) != val:
                                raise ValueError('素材已在其他位置修改，請重新開啟後再保存。')
                        materials.save(state, dict(changes, id=ident))
                    else: materials.save(state, value)
                if not self._commit(apply, '保存文字素材'): return False
                self.target_id = ident; f.target = ('material', ident)
                canonical = copy.deepcopy(materials.item(window.state, ident))
                position, anchor = f.prompt.textCursor().position(), f.prompt.textCursor().anchor()
                scroll = f.prompt.verticalScrollBar().value()
                f.base = canonical; f.dirty = False
                f.refresh(); f.load_value(canonical, 'material')
                cursor = f.prompt.textCursor(); limit = f.prompt.document().characterCount()-1
                cursor.setPosition(min(anchor, limit)); cursor.setPosition(min(position, limit), QTextCursor.MoveMode.KeepAnchor)
                f.prompt.setTextCursor(cursor); f.prompt.verticalScrollBar().setValue(scroll)
                self._publish_saved()
                signature = json.dumps(self._value(), sort_keys=True, ensure_ascii=False)
                self.receipt = receipt = dict(identity=ident, signature=signature,
                    version=materials.fingerprint(f.base), destination=destination, added=None)
            if add:
                current = materials.item(window.state, receipt['identity'])
                if materials.fingerprint(current) != receipt['version']:
                    self.receipt = None; self.error.setText('素材已變更，請確認內容後再次保存並加入。'); return False
                added = []
                if not self._commit(lambda state: added.append(materials.add_to_canvas(state, receipt['identity'], destination)), '加入文字素材', adding=True):
                    return False
                receipt['added'] = added[0]
            self.committed = True; self.accept()
            if add and hasattr(window, 'return_after_material_add'): window.return_after_material_add(destination)
            return True
        finally: self.busy = False

    def _commit(self, operation, label, adding=False):
        try: success = self.page.window.canvas.commit(operation, label=label)
        except (ValueError, KeyError) as exc:
            self.error.setText(('素材已保存，但未加入畫布。可重試加入。\n' if adding else '') + str(exc)); return False
        if not success:
            self.error.setText('素材已保存，但未加入畫布。按「保存並加入畫布」重試只會加入。' if adding else '未能保存，請檢查名稱與正文；編輯內容仍保留。')
        return bool(success)

    def _publish_saved(self):
        """Commit text into the prior native document as one undoable save.

        The dialog's working document never replaces the old draft's history on
        cancel. Other editor surfaces therefore retain their actual Qt document.
        """
        f = self.form; window = self.page.window
        from .material_editor_documents import _document
        cache = getattr(window, '_material_editor_documents', {})
        key = ('source', self.target_id); document = cache.get(key)
        if document is None: document = cache[key] = f.prompt.document()
        elif document.toPlainText() != f.prompt.toPlainText():
            cursor = QTextCursor(document); cursor.beginEditBlock(); cursor.select(QTextCursor.SelectionType.Document)
            cursor.insertText(f.prompt.toPlainText()); cursor.endEditBlock()
        saved = f.export_state(); saved.update(base=copy.deepcopy(f.base), dirty=False)
        session = window._material_editor_session
        previous_new = session['drafts'].get(('new', ''))
        if self.was_new and previous_new and previous_new['base'].get('id') == self.initial_id:
            session['drafts'].pop(('new', ''), None)
        session['drafts'][f.target] = saved
        session['active'] = f.target; session['serial'] += 1

    def done(self, result):
        self.form.timer.stop(); self.page.window.completion.cancel(self.prompt); self.prompt.completer.popup().hide()
        cache = getattr(self.page.window, '_material_editor_documents', {})
        scope = self.form.document_scope
        documents = []
        for key in list(cache):
            if key[:2] == ('dialog', scope):
                document = cache.pop(key)
                if document not in documents: documents.append(document)
        # A duplicate can change identity during this transaction. Release every
        # private document, but retain any document adopted by the source cache.
        for document in documents:
            if document not in cache.values():
                self.destroyed.connect(document.deleteLater)
        super().done(result)
