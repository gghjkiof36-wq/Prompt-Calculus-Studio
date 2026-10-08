"""Read-only material library and transactional original-edit dialog (rev9)."""
import copy
import json
import math
from PySide6.QtCore import Qt, QTimer, QSize, QRectF, QPoint, Signal, QEvent
from PySide6.QtGui import QFont, QColor, QPen, QFontMetrics, QTextCursor, QTextBlockFormat, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QLineEdit,
    QListWidget, QListWidgetItem, QCheckBox, QScrollArea, QTextEdit, QLabel,
    QStyledItemDelegate, QStyleOptionViewItem, QStyle, QApplication, QDialog, QSizePolicy)
from . import materials
from .core import uid, output_groups
from .widgets import label, button, row, ComboBox, StudioDialog, RoundMenu, preview_path, set_preview, InputDialog, ask
from .views import widget_colors
from .material_editor_page import Disclosure, sync_prompt_style, apply_line_spacing
from .management_reading import READING_CONTENT_WIDTH, READING_MARGIN, READING_NARROW_MARGIN


def _replace_visible_order(state, field, before, after):
    """Keep hidden records in their slots, with every record and field intact."""
    mapping = {value['id']: value for value in state[field]}
    visible = set(before)
    if len(visible) != len(before) or len(after) != len(before) or set(after) != visible or not visible <= mapping.keys():
        raise ValueError('清單已更新，請重新拖曳排序。')
    ordered = iter(after)
    state[field] = [mapping[next(ordered)] if value['id'] in visible else value for value in state[field]]


class LibraryDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(180, QFontMetrics(option.font).height() * 2 + 40)

    def paint(self, painter, option, index):
        colors = widget_colors(self.parent())
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        rect = QRectF(option.rect).adjusted(1, 6, -1, -6)
        painter.save(); painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(colors['accent' if selected or focused else 'line']), 2 if focused else 1))
        painter.setBrush(QColor(colors['selected' if selected else 'hover' if hover else 'surface']))
        painter.drawRoundedRect(rect, 9, 9)
        left = rect.left() + 16
        width = max(10, int(rect.right()-left-16))
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
        painter.restore()


class CategoryNavigationDelegate(QStyledItemDelegate):
    """Separate the fixed all-items shortcut without changing reorder IDs."""
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if index.data(Qt.ItemDataRole.UserRole) is None: size.setHeight(size.height()+16)
        return size

    def paint(self, painter, option, index):
        if index.data(Qt.ItemDataRole.UserRole) is not None:
            return super().paint(painter, option, index)
        entry = QStyleOptionViewItem(option); entry.rect.adjust(0, 0, 0, -16)
        super().paint(painter, entry, index)
        colors = widget_colors(self.parent())
        painter.save(); painter.setPen(QPen(QColor(colors.get('divider', colors['line'])), 1))
        painter.drawLine(option.rect.left()+12, option.rect.bottom()-8, option.rect.right()-12, option.rect.bottom()-8)
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
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        from .recent_sidebar import CanvasWorkspaceCombo, CanvasWorkspaceBar
        self.workspace_header = QWidget(); self.workspace_header.setObjectName('EditorWorkspaceHeader')
        workbar = QHBoxLayout(self.workspace_header); workbar.setContentsMargins(24, 16, 24, 8); workbar.setSpacing(0)
        self.workspace_combo = CanvasWorkspaceCombo(); self.workspace_combo.setFixedWidth(156)
        self.workspace_combo.setEnabled(callable(getattr(window, 'change_workspace', None)))
        self.workspace_combo.currentIndexChanged.connect(self._workspace_changed)
        self.workspace_bar = CanvasWorkspaceBar(window, self.workspace_combo, active='list', header=self.workspace_header, embedded=True)
        self.workspace_switch = self.workspace_bar.workspace_switch
        workbar.addWidget(self.workspace_bar); workbar.addStretch(); outer.addWidget(self.workspace_header)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setProperty('pcsReadingPage', True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame); outer.addWidget(self.scroll)
        self.scroll.viewport().installEventFilter(self)
        self.shell = QWidget(); self.scroll.setWidget(self.shell)
        self.category_panel = QWidget(self.scroll.viewport()); self.category_panel.setObjectName('ContextSidebar')
        self.category_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        category_layout = QVBoxLayout(self.category_panel)
        category_layout.setContentsMargins(12, 24, 12, 12); category_layout.setSpacing(8)
        self.category_heading = label('內容分類', 'SidebarHeading')
        from .ui_icons import icon
        self.category_add = button('', self.new_category, 'Quiet')
        self.category_add.setIcon(icon('plus')); self.category_add.setIconSize(QSize(18, 18))
        self.category_add.setAccessibleName('新增分類'); self.category_add.setFixedSize(28, 28)
        self.category_add.setCursor(Qt.CursorShape.PointingHandCursor); self.category_add.setStyleSheet('padding:0;')
        self.category_title = QWidget()
        self.category_title_row = row(self.category_heading, None, self.category_add)
        self.category_title_row.setContentsMargins(0, 0, 0, 0); self.category_title.setLayout(self.category_title_row)
        category_layout.addWidget(self.category_title)
        from .prompt_editor import ReorderableList
        self.categories = ReorderableList(); self.categories.setObjectName('SidebarNavigation')
        self.categories.setItemDelegate(CategoryNavigationDelegate(self.categories))
        self.categories.enable_reordering(); self.categories.reorderRequested.connect(self._reorder_categories)
        self.categories.setFrameShape(QListWidget.Shape.NoFrame); self.categories.setSpacing(3)
        self.categories.setResizeMode(QListWidget.ResizeMode.Adjust); category_layout.addWidget(self.categories, 1)
        self.category = ComboBox(self); self.category.hide()
        self.category_controls = QWidget(); self.category_controls.hide()
        controls = QHBoxLayout(self.category_controls); controls.setContentsMargins(0, 0, 0, 0); controls.setSpacing(12)
        controls.addWidget(self.category, 1)
        category_layout.addWidget(self.category_controls)
        self.browser = QWidget(self.shell); content = QVBoxLayout(self.browser)
        content.setContentsMargins(0, 0, 0, 0); content.setSpacing(14)
        self.browser_header = QWidget()
        self.header_layout = QVBoxLayout(self.browser_header)
        self.header_layout.setContentsMargins(1, 0, 13, 0); self.header_layout.setSpacing(14)
        content.addWidget(self.browser_header)
        self.title = label('全部模組', 'DialogTitle', True); self.count = label('', 'Subtle')
        self.new_button = button('新增模組', self.new_material, 'Primary')
        self.new_button.setCursor(Qt.CursorShape.PointingHandCursor)
        from .ui_icons import icon
        self.new_button.setIcon(icon('plus', 'on-accent'))
        self.library_more = button('更多', self.library_menu, 'Quiet'); self.library_more.setIcon(icon('more-horizontal'))
        self.heading_caption = QWidget(self.browser_header)
        title_row = row(self.title, self.count, None); title_row.setContentsMargins(0, 0, 0, 0)
        self.heading_caption.setLayout(title_row)
        for text in (self.title, self.count):
            text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
            title_row.setAlignment(text, Qt.AlignmentFlag.AlignBottom)
        self.heading_actions = QWidget(self.browser_header)
        actions = row(self.new_button, self.library_more); actions.setContentsMargins(0, 0, 0, 0)
        self.heading_actions.setLayout(actions)
        self.heading_grid = QGridLayout(); self.heading_grid.setContentsMargins(0, 0, 0, 0)
        self.heading_grid.setHorizontalSpacing(8); self.heading_grid.setVerticalSpacing(10)
        self.header_layout.addLayout(self.heading_grid)
        self.search = QLineEdit(); self.search.setPlaceholderText('搜尋目前分類的名稱、別名或內容')
        self.pins = QCheckBox('釘選'); self.order = ComboBox(); self.order.addItem('名稱', 'name'); self.order.addItem('最近使用', 'recent')
        self.order.addItem('手動排序', 'manual')
        self.filter_controls = QWidget(self.browser_header)
        filters = row(self.pins, self.order); filters.setContentsMargins(0, 0, 0, 0)
        self.filter_controls.setLayout(filters)
        self.search_grid = QGridLayout(); self.search_grid.setContentsMargins(0, 0, 0, 0)
        self.search_grid.setHorizontalSpacing(8); self.search_grid.setVerticalSpacing(10)
        self.header_layout.addLayout(self.search_grid)
        self._header_modes = None
        self._arrange_browser_controls(READING_CONTENT_WIDTH-14)
        self.list_hint = label('單擊預覽 · 雙擊編輯', 'Subtle'); self.header_layout.addWidget(self.list_hint)
        from .prompt_editor import MaterialList
        self.listing = MaterialList(); self.listing.setFrameShape(QListWidget.Shape.NoFrame)
        self.listing.enable_reordering(); self.listing.reorderRequested.connect(self._reorder_materials)
        self.listing.setItemDelegate(LibraryDelegate(self.listing)); self.listing.setSpacing(0)
        self.listing.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
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
        self.preview_text.document().setDocumentMargin(0)
        self.preview_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        preview.addWidget(self.preview_text)
        self.preview_image = PreviewImage(); self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._preview_pixmap = QPixmap()
        self.preview_image.setCursor(Qt.CursorShape.PointingHandCursor); self.preview_image.clicked.connect(self.enlarge_preview)
        preview.addWidget(self.preview_image)
        self.edit_button = button('編輯素材', lambda: self.edit_material(self.preview_id)); preview.addWidget(self.edit_button)
        self.preview_card.hide()
        self.scroll.verticalScrollBar().rangeChanged.connect(self._queue_layout)
        self.listing.verticalScrollBar().rangeChanged.connect(self._queue_layout)
        self.preview_timer = QTimer(self); self.preview_timer.setTimerType(Qt.TimerType.PreciseTimer); self.preview_timer.setSingleShot(True); self.preview_timer.timeout.connect(self._show_selected_preview)
        self.listing.dragStarted.connect(self.preview_timer.stop)
        self.categories.dragStarted.connect(self.preview_timer.stop)
        self.listing.itemClicked.connect(self._mouse_preview)
        self.listing.itemDoubleClicked.connect(lambda entry: self.edit_material(entry.data(Qt.ItemDataRole.UserRole)))
        self.listing.currentItemChanged.connect(self._selection_changed)
        self.f2 = QShortcut(QKeySequence(Qt.Key.Key_F2), self); self.f2.activated.connect(self.open_selected)
        self.search.textChanged.connect(self.refresh_list); self.pins.toggled.connect(self.refresh_list)
        self.order.currentIndexChanged.connect(self._order_changed)
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

    def _queue_layout(self, *_):
        QTimer.singleShot(0, self._layout)

    @staticmethod
    def _reserve_scroll_gutter(area):
        # Keep the column stable without showing a scrollbar that cannot move.
        bar = area.verticalScrollBar()
        reserve = bar.sizeHint().width() if bar.maximum() <= bar.minimum() else 0
        if area.viewportMargins().right() != reserve:
            area.setViewportMargins(0, 0, reserve, 0)

    def _arrange_browser_controls(self, width):
        # Reflow controls inside their own column before shrinking the list.
        # A single unbroken toolbar must not decide where the preview belongs.
        heading_below = width < self.heading_caption.sizeHint().width()+self.heading_actions.sizeHint().width()+8
        filters_below = width < 220+self.filter_controls.sizeHint().width()+8
        modes = (heading_below, filters_below)
        if modes == self._header_modes: return
        self._header_modes = modes
        for grid, first, second, below in (
                (self.heading_grid, self.heading_caption, self.heading_actions, heading_below),
                (self.search_grid, self.search, self.filter_controls, filters_below)):
            grid.removeWidget(first); grid.removeWidget(second)
            grid.addWidget(first, 0, 0, 1, 2 if below else 1)
            grid.addWidget(second, 1 if below else 0, 0 if below else 1, 1, 2 if below else 1,
                           Qt.AlignmentFlag.AlignRight)
            grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 0)
        self.header_layout.invalidate()

    def _layout(self):
        if not hasattr(self, 'browser'): return
        self._sync_type()
        self._reserve_scroll_gutter(self.scroll)
        viewport_width = self.scroll.viewport().width(); height = self.scroll.viewport().height()
        minimum_browser = max(280, self.heading_caption.minimumSizeHint().width()+14,
                              self.heading_actions.minimumSizeHint().width()+14,
                              self.filter_controls.minimumSizeHint().width()+14)
        minimum_detail = max(260, self.edit_button.minimumSizeHint().width()+38)
        minimum_pair = minimum_browser+24+minimum_detail
        self.narrow = viewport_width < max(800, 240+2*READING_MARGIN+minimum_pair)
        # Center the task column within the workspace after navigation, using
        # the same balanced gutters as the settings reading pages.
        width = viewport_width
        self.workspace_bar.refresh()
        if self.narrow:
            self.category_title.hide(); self.categories.hide()
            self.category.show(); self.category_controls.show()
            if self.category_add.parentWidget() is not self.category_controls:
                self.category_controls.layout().addWidget(self.category_add)
            self.category_panel.setGeometry(0, 0, width, 100)
            panel_height = self.category_panel.layout().sizeHint().height()
            self.category_panel.setFixedHeight(panel_height)
            start = panel_height+READING_NARROW_MARGIN
            area_x = READING_NARROW_MARGIN; area_width = max(0, width-2*READING_NARROW_MARGIN)
        else:
            self.category_title.show(); self.categories.show()
            self.category.hide(); self.category_controls.hide()
            if self.category_add.parentWidget() is not self.category_title:
                self.category_title_row.addWidget(self.category_add)
            self.categories.setFlow(QListWidget.Flow.TopToBottom); self.categories.setWrapping(False)
            self.categories.setMinimumHeight(0); self.categories.setMaximumHeight(16777215)
            self.categories.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            for i in range(self.categories.count()): self.categories.item(i).setSizeHint(QSize())
            self.category_panel.setFixedHeight(height)
            self.category_panel.setGeometry(0, 0, 240, height)
            start = READING_MARGIN; area_x = 240+READING_MARGIN
            area_width = max(0, width-area_x-READING_MARGIN)
        has_preview = bool(self.preview_id and self.preview_card.isVisible())
        if has_preview:
            # The detail remains a right-hand column at every width. At desktop
            # sizes both columns contract; only an exceptionally small viewport
            # scrolls horizontally instead of moving the detail below the list.
            area_width = max(area_width, minimum_pair)
            detail_width = min(380, max(minimum_detail, round(area_width*.32)))
            detail_width = min(detail_width, area_width-minimum_browser-24)
            central = min(READING_CONTENT_WIDTH, area_width-24-detail_width)
            group_width = central+24+detail_width
        else:
            area_width = max(area_width, minimum_browser)
            central = min(READING_CONTENT_WIDTH, area_width)
            group_width = central
        x = area_x + (area_width-group_width)//2
        self._arrange_browser_controls(central-14)
        row_height = self.listing.fontMetrics().height()*2 + 40
        list_height = max(110, min(580, self.listing.count()*row_height + 12))
        self.listing.setMinimumHeight(list_height); self.listing.setMaximumHeight(list_height)
        browser_layout = self.browser.layout(); browser_layout.invalidate()
        browser_height = max(browser_layout.totalMinimumSize().height(), browser_layout.totalHeightForWidth(central))
        self.browser.setGeometry(x, start, central, browser_height)
        browser_layout.activate()
        self._reserve_scroll_gutter(self.listing)
        # Reserve the same scroll gutter for the header and rows, so controls
        # align with the visible card edges even when the list needs scrolling.
        gutter = max(0, self.listing.width()-self.listing.viewport().width())
        self.header_layout.setContentsMargins(1, 0, gutter+1, 0)
        self.header_layout.activate()
        self.category_panel.raise_()
        bottom = start + browser_height + 24
        if has_preview:
            card_width = detail_width
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
            self.preview_text.document().setTextWidth(self.preview_text.viewport().width())
            text_insets = self.preview_text.height()-self.preview_text.viewport().height()
            text_height = math.ceil(self.preview_text.document().size().height())+text_insets
            self.preview_text.setFixedHeight(max(36, min(300, text_height)))
            if not self.preview_image.isHidden():
                image = self._preview_pixmap.scaled(min(250, available), 250,
                    Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self.preview_image.setPixmap(image); self.preview_image.setFixedHeight(image.height())
            layout.invalidate()
            card_height = max(layout.totalMinimumSize().height(), layout.totalHeightForWidth(card_width))
            px, py = x+central+24, self.search.mapTo(self.shell, QPoint()).y()
            self.preview_card.setGeometry(px, py, card_width, card_height)
            layout.activate()
            bottom = max(bottom, py+card_height+24)
        self.shell.setMinimumHeight(max(height, bottom))
        self.shell.setMinimumWidth(area_x+area_width+(READING_NARROW_MARGIN if self.narrow else READING_MARGIN))

    def _sync_type(self):
        from .theme import font_pixels
        settings = dict(self.window.state['settings']); settings.update(getattr(self.window, 'appearance_preview', {}))
        colors = widget_colors(self)
        preview_style = ("QTextEdit#MaterialPreviewPrompt { font-family:'Cascadia Mono','Consolas','Microsoft JhengHei'; "
                         "font-size:%dpx; font-weight:400; color:%s; background:transparent; border:0; padding:4px; }") % (
                             font_pixels(settings['prompt_size']), colors['text'])
        if self.preview_text.styleSheet() != preview_style: self.preview_text.setStyleSheet(preview_style)
        self.preview_text.ensurePolished()
        # Font descenders leave different empty space beneath large Latin/CJK
        # headings and their smaller count. Align the visible glyph bottoms,
        # rather than their widget boxes or the layout's nominal baseline.
        padding = []
        for text in (self.title, self.count):
            text.ensurePolished()
            metrics = text.fontMetrics()
            padding.append(metrics.descent()-metrics.tightBoundingRect(text.text()).bottom())
        for text, inset in zip((self.title, self.count), padding):
            bottom = max(padding)-inset
            if text.contentsMargins().bottom() != bottom:
                text.setContentsMargins(0, 0, 0, bottom)
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

    def _order_changed(self):
        self.window.state['settings']['material_library_order'] = self.order.currentData()
        self.refresh_list()
        changed = getattr(self.window, 'changed', None)
        if changed: changed('settings', refresh=False)

    def _reorder_materials(self, before, after):
        if before != self.listing.order_ids(): return
        def apply(state):
            _replace_visible_order(state, 'items', before, after)
            state['settings']['material_library_order'] = 'manual'
        if self.window.canvas.commit(apply, label='調整素材順序'): self.refresh()

    def _reorder_categories(self, before, after):
        if before != self.categories.order_ids(): return
        # The all-items shortcut and virtual empty category are navigation,
        # not stored categories. A real uncategorized category can be moved.
        original = [ident for ident in before if ident not in (None, '@uncategorized')]
        reordered = [ident for ident in after if ident not in (None, '@uncategorized')]
        def apply(state):
            if 'output_order' not in state:
                state['output_order'] = output_groups(state, include_hidden=True)
            _replace_visible_order(state, 'modules', original, reordered)
        if self.window.canvas.commit(apply, label='調整分類順序'): self.refresh()

    def refresh_workspace(self):
        self.workspace_combo.refresh(self.window.state)
        self.workspace_bar.refresh()

    def _workspace_changed(self):
        target = self.workspace_combo.currentData()
        if target is None or target == self.window.state['workspace']: return
        change = getattr(self.window, 'change_workspace', None)
        if callable(change): change(target)
        # A rejected draft flush leaves the actual workspace unchanged.
        self.refresh()

    def refresh(self):
        self._refreshing = True
        order = self.window.state['settings'].get('material_library_order', 'name')
        self.order.blockSignals(True); self.order.setCurrentIndex(max(0, self.order.findData(order))); self.order.blockSignals(False)
        self.refresh_workspace()
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
            if ident in (None, '@uncategorized'): entry.setFlags(entry.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
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
        if self.order.currentData() == 'manual':
            visible = {value['id'] for value in rows}
            rows = [value for value in self.window.state['items'] if value['id'] in visible]
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
        # A single click may shrink the list to make room for its preview. Never relayout
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
        image_path = preview_path(self.window.store, value.get('preview'))
        has_image = bool(image_path)
        self.preview_image.setVisible(has_image)
        self._preview_pixmap = QPixmap(str(image_path)) if has_image else QPixmap()
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
        self.advanced = QWidget(); self.advanced.setObjectName('MaterialDialogAdvanced')
        advanced_layout = QVBoxLayout(self.advanced); advanced_layout.setContentsMargins(0, 0, 0, 0); advanced_layout.setSpacing(8)
        self.body.addWidget(self.advanced); self.advanced.hide()
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        def group(title):
            toggle = Disclosure(title); panel = QWidget(); layout = QVBoxLayout(panel); layout.setContentsMargins(8, 4, 8, 10)
            advanced_layout.addWidget(toggle); advanced_layout.addWidget(panel); panel.hide(); toggle.toggled.connect(panel.setVisible)
            return toggle, layout
        self.info_toggle, info = group('素材資訊')
        fields = QFormLayout(); fields.addRow('分類', f.edit_category); fields.addRow('格式', f.kind); fields.addRow('別名', f.aliases); fields.addRow('備註', f.notes); info.addLayout(fields)
        self.lookup_toggle, lookup = group('標籤查詢'); lookup.addWidget(f.lookup); lookup.addWidget(f.lookup_hint)
        self.relation_toggle, relation = group('關係與預覽'); relation.addWidget(label('停用的完整 Tag，每行一項', 'Subtle')); relation.addWidget(f.excludes); relation.addWidget(f.preview)
        relation.addLayout(row(button('選擇預覽圖', f.choose_image, 'Quiet'), button('移除預覽', f.clear_image, 'Quiet')))
        self.history_toggle, history = group('內容歷史')
        self.history_button = button('查看內容歷史', f.history, 'Quiet'); history.addWidget(self.history_button)
        self.error = StatusLabel(); f.status = self.error
        self.destination_label = label('', 'Subtle', True)
        self.cancel_button = button('取消', self.reject, 'Quiet')
        self.save_button = button('保存', lambda: self.save(bool(self.form.destination_id) and not self.target_id), 'Primary')
        self.add_button = button('保存並加入畫布', lambda: self.save(True), 'Quiet')
        self.identity = label('素材原件' if ident else '新素材', 'Subtle')
        self.body.addLayout(row(self.identity, None, self.cancel_button, self.save_button, self.add_button))
        self.form_scroll = QScrollArea(); self.form_scroll.setWidgetResizable(True); self.form_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.form_scroll.setMinimumHeight(0)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        form_content = QWidget(); form_content.setObjectName('MaterialDialogBody')
        self.form_scroll.setStyleSheet('QScrollArea { background:transparent; border:0; } QWidget#MaterialDialogBody, QWidget#MaterialDialogAdvanced { background:transparent; }')
        self.form_scroll.viewport().setObjectName('MaterialDialogViewport')
        self.form_scroll.viewport().setStyleSheet('QWidget#MaterialDialogViewport { background:transparent; }')
        form_layout = QVBoxLayout(form_content); form_layout.setContentsMargins(0, 0, 0, 0); form_layout.setSpacing(10)
        # The single outer viewport follows every disclosure's natural height.
        # A second scroll area here compressed expanded fields into its own
        # viewport, leaving two competing scrollbars and clipped form rows.
        form_layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        while self.body.count() > 1:
            item = self.body.takeAt(0)
            if item.widget(): form_layout.addWidget(item.widget())
            elif item.layout(): form_layout.addLayout(item.layout())
        form_layout.addStretch(); self.form_scroll.setWidget(form_content); self.body.insertWidget(0, self.form_scroll, 1)
        self.body.insertWidget(1, self.error); self.body.insertWidget(2, self.destination_label)
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
        from .material_editor_documents import ModalNativeHistory
        self.native_history = ModalNativeHistory(f)

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
        self.prompt.setFixedHeight(340 if expanded else 170)
        available = max(400, self.page.window.height()-24)
        if self.screen(): available = min(available, self.screen().availableGeometry().height()-40)
        self.resize(self.width(), min(780 if expanded else 610, available))

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
        """Advance the native source checkpoint only after canonical Save."""
        f = self.form; window = self.page.window
        self.native_history.save_checkpoint()
        saved = f.export_state(); saved.update(base=copy.deepcopy(f.base), dirty=False)
        session = window._material_editor_session
        previous_new = session['drafts'].get(('new', ''))
        if self.was_new and previous_new and previous_new['base'].get('id') == self.initial_id:
            session['drafts'].pop(('new', ''), None)
        session['drafts'][f.target] = saved
        session['active'] = f.target; session['serial'] += 1

    def done(self, result):
        self.native_history.rollback()
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
