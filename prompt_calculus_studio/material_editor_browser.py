"""Read-only material library and transactional original-edit dialog (rev9)."""
import copy
import json
import math
from PySide6.QtCore import Qt, QTimer, QSize, QRectF, QPoint, Signal, QEvent
from PySide6.QtGui import QFont, QColor, QPen, QFontMetrics, QTextCursor, QTextBlockFormat, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QLineEdit,
    QListWidget, QListWidgetItem, QCheckBox, QScrollArea, QTextEdit, QLabel,
    QStyledItemDelegate, QStyleOptionViewItem, QStyle, QApplication, QDialog, QSizePolicy, QPushButton)
from . import materials, material_navigation as navigation, prompt_materials as pm
from .core import uid
from .widgets import label, button, row, ComboBox, StudioDialog, RoundMenu, preview_path, set_preview, InputDialog, ask
from .views import widget_colors
from .material_editor_page import Disclosure, sync_prompt_style, apply_line_spacing
from .management_reading import READING_CONTENT_WIDTH, READING_MARGIN, READING_NARROW_MARGIN

from .material_scope import TYPE_SHORTCUTS, MaterialScope, populate


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
        actions = self.parent().row_actions
        action_row = actions.ident == index.data(Qt.ItemDataRole.UserRole)
        pinned = bool(index.data(Qt.ItemDataRole.UserRole+1))
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver) or action_row
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        depth = getattr(self.parent(), 'stack_depth', lambda _: 0)(index)
        if depth and not (selected or hover or focused):
            from .color_roles import mix
            colors = dict(colors)
            for key in ('surface', 'line'):
                colors[key] = mix(colors[key], colors['base'], min(.65, depth*.3))
        rect = QRectF(option.rect).adjusted(1, 6, -1, -6)
        painter.save(); painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(colors['accent' if selected or focused else 'line']), 2 if focused else 1))
        painter.setBrush(QColor(colors['selected' if selected else 'hover' if hover else 'surface']))
        painter.drawRoundedRect(rect, 9, 9)
        # Back cards are depth cues, not overlapping scraps of text. Their
        # content is revealed continuously as scrolling brings them forward.
        if depth:
            painter.setOpacity(max(0., 1-depth/.4))
        left = rect.left() + 16
        width = max(10, int(rect.right()-left-16)-(actions.width()+8 if action_row else 40 if pinned else 0))
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
        if pinned and not action_row:
            from .ui_icons import icon
            icon('pin-filled', colors['secondary']).paint(painter,
                QRectF(rect.right()-35, rect.center().y()-9, 18, 18).toRect())
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
        from .module_chrome import suppress_prompt_tooltips
        suppress_prompt_tooltips(self)
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
        self.category_add = button('', self.category_add_menu, 'Quiet')
        self.category_add.setIcon(icon('plus')); self.category_add.setIconSize(QSize(18, 18))
        self.category_add.setAccessibleName('新增資料夾或分類'); self.category_add.setToolTip('新增資料夾或分類'); self.category_add.setFixedSize(28, 28)
        self.category_add.setCursor(Qt.CursorShape.PointingHandCursor); self.category_add.setStyleSheet('padding:0;')
        self.category_title = QWidget()
        self.category_title_row = row(self.category_heading, None, self.category_add)
        self.category_title_row.setContentsMargins(0, 0, 0, 0); self.category_title.setLayout(self.category_title_row)
        category_layout.addWidget(self.category_title)
        from .material_category_drag import EditorCategoryNavigation
        self.categories = EditorCategoryNavigation(self); self.categories.setObjectName('SidebarNavigation')
        self.categories.reorderRequested.connect(self._reorder_categories)
        self.categories.toggleRequested.connect(self.toggle_folder)
        self.categories.menuRequested.connect(self.category_row_menu)
        self.categories.moveRequested.connect(self.move_category)
        canvas=getattr(self.window,'canvas',None)
        if canvas is not None:
            self.categories.undoRequested.connect(canvas.undo)
            self.categories.redoRequested.connect(canvas.redo)
        self.categories.setFrameShape(QListWidget.Shape.NoFrame); self.categories.setSpacing(3)
        self.categories.setResizeMode(QListWidget.ResizeMode.Adjust); category_layout.addWidget(self.categories, 1)
        self.category = ComboBox(self); self.category.hide(); self.category.installEventFilter(self)
        self.category_controls = QWidget(); self.category_controls.hide()
        controls = QHBoxLayout(self.category_controls); controls.setContentsMargins(0, 0, 0, 0); controls.setSpacing(12)
        controls.addWidget(self.category, 1)
        self.category_more = button('', self.selected_category_menu, 'Quiet')
        self.category_more.setIcon(icon('more-horizontal')); self.category_more.setFixedSize(28, 28)
        self.category_more.setAccessibleName('資料夾或分類選單'); self.category_more.setToolTip('資料夾或分類選單')
        self.category_more.setStyleSheet('padding:0;'); controls.addWidget(self.category_more)
        category_layout.addWidget(self.category_controls)
        self.browser = QWidget(self.shell); content = QVBoxLayout(self.browser)
        content.setContentsMargins(0, 0, 0, 0); content.setSpacing(14)
        self.browser_header = QWidget()
        self.header_layout = QVBoxLayout(self.browser_header)
        self.header_layout.setContentsMargins(1, 0, 13, 0); self.header_layout.setSpacing(14)
        content.addWidget(self.browser_header)
        self.title = label('文字模塊', 'DialogTitle', True); self.count = label('', 'Subtle')
        self.new_button = button('新增', self.new_material, 'Primary')
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
        self.type_filter=ComboBox();self.type_filter.setAccessibleName('素材類型篩選');self.type_filter.addItem('全部類型',None)
        for key,name in pm.TYPES.items():self.type_filter.addItem(name,key)
        self.filter_controls = QWidget(self.browser_header)
        filters = row(self.type_filter,self.pins, self.order); filters.setContentsMargins(0, 0, 0, 0)
        self.filter_controls.setLayout(filters)
        self.search_grid = QGridLayout(); self.search_grid.setContentsMargins(0, 0, 0, 0)
        self.search_grid.setHorizontalSpacing(8); self.search_grid.setVerticalSpacing(10)
        self.header_layout.addLayout(self.search_grid)
        self._header_modes = None
        self._arrange_browser_controls(READING_CONTENT_WIDTH-14)
        from .material_editor_motion import EditorMaterialList
        self.listing = EditorMaterialList(self); self.listing.setFrameShape(QListWidget.Shape.NoFrame)
        self.listing.enable_reordering(); self.listing.reorderRequested.connect(self._reorder_materials)
        self.listing.setItemDelegate(LibraryDelegate(self.listing)); self.listing.setSpacing(0)
        self.listing.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.listing.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listing.customContextMenuRequested.connect(self.material_menu)
        content.addWidget(self.listing, 1)
        self.empty_results = label('這個分類還沒有素材，可按「新增」開始。', 'Subtle', True)
        content.addWidget(self.empty_results)
        self.preview_card = QFrame(self.shell); self.preview_card.setObjectName('InsetPanel')
        preview = QVBoxLayout(self.preview_card); preview.setContentsMargins(18, 18, 18, 18); preview.setSpacing(12)
        preview.addLayout(row(label('素材預覽', 'Subtle'), None, button('×', self.close_preview, 'Quiet')))
        self.preview_name = label('', '', True); preview.addWidget(self.preview_name)
        from .prompt_group_editor import tool
        self.preview_variant_id = None
        self.variant_previous = tool('chevron-left', '上一個變體', lambda: self.move_preview_variant(-1))
        self.variant_next = tool('chevron-right', '下一個變體', lambda: self.move_preview_variant(1))
        self.variant_position = label('', 'Subtle', True)
        self.variant_position.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.variant_navigation = QWidget()
        variant_row = QHBoxLayout(self.variant_navigation); variant_row.setContentsMargins(0, 0, 0, 0)
        variant_row.addWidget(self.variant_previous); variant_row.addWidget(self.variant_position, 1); variant_row.addWidget(self.variant_next)
        self.variant_navigation.hide()
        self.preview_text = QTextEdit(); self.preview_text.setReadOnly(True); self.preview_text.setAcceptRichText(False)
        self.preview_text.setObjectName('MaterialPreviewPrompt'); self.preview_text.setUndoRedoEnabled(False)
        self.preview_text.setFrameShape(QTextEdit.Shape.NoFrame)
        self.preview_text.document().setDocumentMargin(0)
        self.preview_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_image = PreviewImage(); self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._preview_pixmap = QPixmap()
        self.preview_image.setCursor(Qt.CursorShape.PointingHandCursor); self.preview_image.clicked.connect(self.enlarge_preview)
        preview.addWidget(self.preview_image)
        preview.addWidget(self.preview_text); preview.addWidget(self.variant_navigation)
        self.edit_button = button('編輯', lambda: self.edit_material(self.preview_id))
        self.edit_button.setIcon(icon('pencil')); preview.addWidget(self.edit_button)
        if getattr(window, 'document', None) is not None and getattr(window, 'workspace_owner', None) is not None:
            self.add_canvas_button = button('加入Canvas…', self.add_canvas_menu, 'Quiet')
            self.add_canvas_button.setIcon(icon('plus'))
            preview.addWidget(self.add_canvas_button)
        self.preview_card.hide()
        self.scroll.verticalScrollBar().rangeChanged.connect(self._queue_layout)
        self.listing.verticalScrollBar().rangeChanged.connect(self._queue_layout)
        from .material_editor_interaction import PreviewClickBridge
        self.preview_click = PreviewClickBridge(self)
        self.listing.dragStarted.connect(self.preview_click.cancel)
        self.categories.dragStarted.connect(self.preview_click.cancel)
        self.listing.itemClicked.connect(self._mouse_preview)
        self.listing.itemDoubleClicked.connect(lambda entry: self.edit_material(entry.data(Qt.ItemDataRole.UserRole)))
        self.listing.currentItemChanged.connect(self._selection_changed)
        self.f2 = QShortcut(QKeySequence(Qt.Key.Key_F2), self); self.f2.activated.connect(self.open_selected)
        self.search.textChanged.connect(self.refresh_list); self.pins.toggled.connect(self.refresh_list)
        self.type_filter.currentIndexChanged.connect(self.refresh_list)
        self.order.currentIndexChanged.connect(self._order_changed)
        self.category.currentIndexChanged.connect(self._category_changed)
        self.categories.currentItemChanged.connect(self._category_selected)
        self.refresh()
        self.set_browser_category(getattr(window, '_material_browser_category', None))

    def eventFilter(self, watched, event):
        if watched is getattr(self, 'category', None) and event.type() == QEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.Undo): self.window.canvas.undo(); return True
            if event.matches(QKeySequence.StandardKey.Redo): self.window.canvas.redo(); return True
        if event.type() == QEvent.Type.Resize and hasattr(self, 'scroll') and watched is self.scroll.viewport():
            self._queue_layout()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event); self._queue_layout()

    def _queue_layout(self, *_):
        if not hasattr(self, '_layout_timer'):
            self._layout_timer = QTimer(self)
            self._layout_timer.setSingleShot(True)
            self._layout_timer.timeout.connect(self._layout)
        if not self._layout_timer.isActive():
            self._layout_timer.start(0)

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

    def _column_minimums(self):
        browser = max(280, self.heading_caption.minimumSizeHint().width()+14,
                      self.heading_actions.minimumSizeHint().width()+14,
                      self.filter_controls.minimumSizeHint().width()+14)
        detail = max(260, self.edit_button.minimumSizeHint().width()+38)
        return browser, detail

    def _layout(self):
        if hasattr(self, '_layout_timer'):
            self._layout_timer.stop()
        from shiboken6 import isValid
        if not isValid(self) or not hasattr(self, 'preview_text') or not isValid(self.preview_text): return
        if not hasattr(self, 'browser'): return
        self._sync_type()
        self._reserve_scroll_gutter(self.scroll)
        viewport_width = self.scroll.viewport().width(); height = self.scroll.viewport().height()
        minimum_browser, minimum_detail = self._column_minimums()
        minimum_pair = minimum_browser+24+minimum_detail
        self.narrow = viewport_width < max(800, 240+2*READING_MARGIN+minimum_pair)
        # Center the task column within the workspace after navigation, using
        # the same balanced gutters as the settings reading pages.
        width = viewport_width
        self.workspace_bar.refresh()
        if getattr(self, 'document_mode', False):
            self.category_panel.hide()
            self.workspace_header.hide()
            start = READING_NARROW_MARGIN if self.narrow else READING_MARGIN
            area_x = start; area_width = max(0, width-2*start)
        elif self.narrow:
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
        # Refresh also runs while an ancestor page is hidden. Reserve the
        # detail column until the preview itself is explicitly closed.
        has_preview = bool(self.preview_id and not self.preview_card.isHidden())
        stacked_preview = has_preview and area_width < minimum_pair
        if has_preview and not stacked_preview:
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
        # Use the viewport's remaining height rather than a fixed 580px list.
        # Rows keep their delegate height; only overflowing content scrolls.
        self.listing.setMinimumHeight(110); self.listing.setMaximumHeight(16777215)
        browser_layout = self.browser.layout(); browser_layout.invalidate()
        browser_height = max(browser_layout.totalMinimumSize().height(), browser_layout.totalHeightForWidth(central))
        if self.listing.count():
            if stacked_preview:
                rows = self.listing.sizeHintForRow(0)*self.listing.count()
                browser_height = max(browser_height,self.browser_header.sizeHint().height()+14+max(110,min(rows,round(height*.35))))
            else:browser_height = max(browser_height, height-start-24)
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
            card_width = central if stacked_preview else detail_width
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
            px, py = (x, bottom) if stacked_preview else (x+central+24, self.search.mapTo(self.shell, QPoint()).y())
            self.preview_card.setGeometry(px, py, card_width, card_height)
            layout.activate()
            bottom = max(bottom, py+card_height+24)
        self.shell.setMinimumHeight(max(height, bottom))
        self.shell.setMinimumWidth(area_x+area_width+(READING_NARROW_MARGIN if self.narrow else READING_MARGIN))

    def refresh_visual_theme(self):
        # Palette changes need not resize the browser. Refresh the preview's
        # local text style even when this document is hidden behind Settings.
        self._sync_type()
        self.listing.viewport().update()

    def _sync_type(self):
        from .theme import font_pixels,window_appearance
        settings = window_appearance(self.window)
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
        ident = getattr(self, '_category_type_aliases', {}).get(ident, ident)
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
        self.category_more.setEnabled(self.get_browser_category() not in TYPE_SHORTCUTS and self.get_browser_category() != '@uncategorized' and self.category.currentText() != '未分類')
        self._sync_type_filter()
        self.title.setText(self.category.currentText()); self.refresh_list()

    def _sync_type_filter(self):
        fixed = self.get_browser_category() in TYPE_SHORTCUTS
        if fixed:
            self.type_filter.blockSignals(True); self.type_filter.setCurrentIndex(0); self.type_filter.blockSignals(False)
        self.type_filter.setVisible(not fixed)

    def _order_changed(self):
        if self._refreshing or self.window.state['settings'].get('material_library_order', 'name') == self.order.currentData():return
        self.window.state['settings']['material_library_order'] = self.order.currentData()
        self.refresh_list()
        changed = getattr(self.window, 'changed', None)
        if changed: changed('settings', refresh=False)

    def _reorder_materials(self, before, after):
        if before == after or before != self.listing.order_ids(): return
        def apply(state):
            _replace_visible_order(state, 'items', before, after)
            state['settings']['material_library_order'] = 'manual'
        if self.window.canvas.commit(apply, label='調整素材順序'): self.refresh()

    def _reorder_categories(self, before, after):
        if before != self.categories.order_ids(): return
        root = navigation.view(self.window.state)['order']
        original = [ident for ident in before if ident in root]
        reordered = [ident for ident in after if ident in root]
        # Dedicated type categories can be hidden here; retain their stored slots.
        visible = set(original); replacement = iter(reordered)
        complete = [next(replacement) if ident in visible else ident for ident in root]
        def apply(state): navigation.reorder_root(state, root, complete)
        self._commit_navigation(apply, '調整分類順序')

    def _commit_navigation(self, operation, label):
        # Prepare only library metadata; no-op moves/renames must not enter the
        # Canvas normalization path or create an Undo entry.
        state = self.window.state; value = dict(modules=state['modules'])
        if navigation.FIELD in state: value[navigation.FIELD] = copy.deepcopy(state[navigation.FIELD])
        try: operation(value)
        except ValueError as exc: self.window.notice(str(exc)); return False
        if state.get(navigation.FIELD) == value.get(navigation.FIELD): return False
        return self.window.canvas.commit(lambda target: target.update({navigation.FIELD: value[navigation.FIELD]}), label=label)

    def toggle_folder(self, ident):
        collapsed = set(self.window.state['settings'].get('material_folder_collapsed', []))
        if ident in collapsed: collapsed.remove(ident)
        else: collapsed.add(ident)
        self.window.state['settings']['material_folder_collapsed'] = sorted(collapsed)
        self._refresh_categories()
        self.window.changed('settings', refresh=False)

    def move_category(self, ident, destination=None, before=None):
        return self._commit_navigation(lambda state: navigation.move(state, ident, destination, before), '移動分類收納')

    def category_add_menu(self):
        menu = RoundMenu(self)
        menu.addAction('新增資料夾', self.new_folder); menu.addAction('新增分類', self.new_category)
        menu.open_for(self.category_add)
        return menu

    def category_row_menu(self, ident, position):
        if ident in TYPE_SHORTCUTS: return
        menu = RoundMenu(self)
        if navigation.is_folder(ident):
            menu.addAction('重新命名', lambda: self.rename_folder(ident))
            menu.addSeparator()
            menu.add_destructive_action('刪除資料夾', lambda: self.remove_folder(ident))
        else:
            materials.category(self.window.state, ident)
            menu.addAction('重新命名', lambda: self.rename_category(ident))
            move = menu.addMenu('移至資料夾')
            parent = navigation.parent(self.window.state, ident)
            value = navigation.view(self.window.state); folders = {v['id']: v for v in value['folders']}
            for fid in value['order']:
                if fid not in folders: continue
                action = move.addAction(folders[fid]['name'], lambda checked=False, target=fid: self.move_category(ident, target))
                action.setCheckable(True); action.setChecked(parent == fid)
                action.setEnabled(parent != fid)
            if folders: move.addSeparator()
            action = move.addAction('移回外層', lambda: self.move_category(ident))
            action.setEnabled(parent is not None)
            menu.addSeparator()
            menu.add_destructive_action('刪除分類', lambda: self.remove_category(ident))
        menu.open_at(position)
        return menu

    def selected_category_menu(self):
        return self.category_row_menu(self.get_browser_category(), self.category_more.mapToGlobal(self.category_more.rect().bottomLeft()))

    def new_folder(self):
        name, ok = InputDialog.getText(self.window, '新增資料夾', '資料夾名稱')
        if not ok: return
        added = []
        if self._commit_navigation(lambda state: added.append(navigation.add_folder(state, name)), '新增資料夾'):
            self.set_browser_category(added[0])

    def rename_folder(self, ident):
        folder = navigation.folder(self.window.state, ident)
        if folder is None: return
        name, ok = InputDialog.getText(self.window, '重新命名資料夾', '資料夾名稱', text=folder['name'])
        if ok: self._commit_navigation(lambda state: navigation.rename_folder(state, ident, name), '重新命名資料夾')

    def remove_folder(self, ident):
        folder = navigation.folder(self.window.state, ident)
        if folder is None or not ask(self.window, '刪除資料夾', f'刪除「{folder["name"]}」資料夾？裡面的分類會移回外層，素材保留。'): return
        self._commit_navigation(lambda state: navigation.remove_folder(state, ident), '刪除資料夾')

    def rename_category(self, ident):
        value = materials.category(self.window.state, ident)
        name, ok = InputDialog.getText(self.window, '重新命名分類', '分類名稱', text=value['name'])
        if ok and self.window.canvas.commit(lambda state: materials.rename_category(state, ident, name), label='重新命名分類'):
            self.refresh()

    def remove_category(self, ident):
        value = materials.category(self.window.state, ident)
        if not ask(self.window, '刪除分類', f'刪除「{value["name"]}」分類？其中的素材會移到「未分類」保留。'): return
        destination = []
        selected = self.get_browser_category()
        if self.window.canvas.commit(lambda state: destination.append(materials.remove_category(state, ident)), label='刪除分類'):
            self.refresh()
            if selected == ident: self.set_browser_category(destination[0])

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
        self._refresh_categories()
        self._refreshing = False
        self._sync_type_filter()
        self.title.setText(self.category.currentText()); self.refresh_list()
        if self.preview_id: self.preview_material(self.preview_id)

    def _refresh_categories(self):
        selected = self.get_browser_category(); state = self.window.state
        scope=MaterialScope(state)
        if getattr(self, '_category_rows', None) == scope.rows:
            return
        self._category_rows=copy.deepcopy(scope.rows)
        self.category.blockSignals(True); self.categories.blockSignals(True)
        self.category.clear(); self.categories.clear()
        self._category_type_aliases=scope.aliases
        populate(scope,self.categories,self.category)
        selected=scope.normalize(selected)
        self.category.setCurrentIndex(max(0, self.category.findData(selected)))
        selected = self.category.currentData()
        for index in range(self.categories.count()):
            if self.categories.item(index).data(Qt.ItemDataRole.UserRole) == selected:
                self.categories.setCurrentRow(index); break
        self.category.blockSignals(False); self.categories.blockSignals(False)
        self.window._material_browser_category = self.category.currentData()
        self.category_more.setEnabled(selected not in TYPE_SHORTCUTS and selected != '@uncategorized' and self.category.currentText() != '未分類')

    def refresh_list(self, *_):
        self.preview_click.cancel()
        self.listing.row_actions.clear()
        self.listing.stop_scroll()
        selected = self.selected_id(); scroll = self.listing.verticalScrollBar().value()
        category = self.get_browser_category()
        folder = navigation.folder(self.window.state, category) if navigation.is_folder(category) else None
        material_type = TYPE_SHORTCUTS.get(category, self.type_filter.currentData())
        self.search.setPlaceholderText('搜尋'+pm.TYPES[material_type]+'的名稱、別名或內容' if category in TYPE_SHORTCUTS else '搜尋此資料夾的名稱、別名或內容' if folder else '搜尋目前分類的名稱、別名或內容')
        rows = [] if category == '@uncategorized' else materials.search(self.window.state, self.search.text(), None if folder or category in TYPE_SHORTCUTS else category, pinned=self.pins.isChecked(), order=self.order.currentData(),material_type=material_type)
        if folder: rows = [v for v in rows if v['module'] in folder['categories']]
        if self.order.currentData() == 'manual':
            visible = {value['id'] for value in rows}
            rows = [value for value in self.window.state['items'] if value['id'] in visible]
        # Preserve row objects and scroll geometry for metadata-only edits.
        # In particular, pinning must not briefly turn this into an empty list.
        listing=self.listing; listing.blockSignals(True); listing.setUpdatesEnabled(False)
        try:
            changed=listing.order_ids()!=[value['id'] for value in rows]
            wanted={value['id'] for value in rows}
            for index in range(listing.count()-1,-1,-1):
                if listing.item(index).data(Qt.ItemDataRole.UserRole) not in wanted:
                    listing.takeItem(index)
            entries={listing.item(i).data(Qt.ItemDataRole.UserRole):listing.item(i) for i in range(listing.count())}
            for position,value in enumerate(rows):
                ident=value['id'];entry=entries.get(ident)
                if entry is None:
                    entry=QListWidgetItem();entry.setData(Qt.ItemDataRole.UserRole,ident)
                    listing.insertItem(position,entry)
                elif listing.row(entry)!=position:
                    listing.insertItem(position,listing.takeItem(listing.row(entry)))
                text=value['name']+'\n'+pm.summary(value).replace('\n',' ')
                if entry.text()!=text:entry.setText(text)
                entry.setData(Qt.ItemDataRole.UserRole+1,bool(value.get('pinned')))
            if selected in entries:listing.setCurrentItem(entries[selected])
            if changed:listing.doItemsLayout()
            listing.verticalScrollBar().setValue(scroll)
        finally:
            listing.blockSignals(False); listing.setUpdatesEnabled(True)
        self.listing.setVisible(bool(rows))
        self.count.setText(f'{len(rows)} 個素材'); self.empty_results.setVisible(not rows)
        self.empty_results.setText('找不到符合搜尋的素材。' if self.search.text() else '還沒有'+pm.TYPES[material_type]+'，可按「新增」開始。' if category in TYPE_SHORTCUTS else '這個資料夾還沒有素材，可先移入分類。' if folder else '這個分類還沒有素材，可按「新增」開始。')
        self._layout()

    def selected_id(self):
        entry = self.listing.currentItem()
        return entry.data(Qt.ItemDataRole.UserRole) if entry else None

    def _mouse_preview(self, entry):
        if self.active_dialog or self.listing.pressed_button != Qt.MouseButton.LeftButton: return
        ident = entry.data(Qt.ItemDataRole.UserRole)
        if not self.preview_card.isVisible():
            # Show immediately while preserving the first click's identity if
            # the new column moves the row away from the second click.
            self.preview_click.arm(ident, self.listing.release_global)
        self.preview_material(ident)

    def _selection_changed(self, entry, previous):
        if entry and QApplication.mouseButtons() == Qt.MouseButton.NoButton and not self._refreshing:
            self.preview_click.cancel(); self.preview_material(entry.data(Qt.ItemDataRole.UserRole))

    def preview_material(self, ident):
        try: value = materials.item(self.window.state, ident)
        except ValueError: self.close_preview(); return
        self.listing.blockSignals(True)
        for i in range(self.listing.count()):
            if self.listing.item(i).data(Qt.ItemDataRole.UserRole) == ident: self.listing.setCurrentRow(i); break
        self.listing.blockSignals(False)
        if self.preview_id != ident: self.preview_variant_id = None
        self.preview_id = ident; self.preview_name.setText(value['name'])
        if hasattr(self, 'add_canvas_button'):
            self.add_canvas_button.setVisible(pm.kind(value) == 'text')
        self.refresh_preview_content(value)
        image_path = preview_path(self.window.store, value.get('preview'))
        has_image = bool(image_path)
        self.preview_image.setVisible(has_image)
        self._preview_pixmap = QPixmap(str(image_path)) if has_image else QPixmap()
        self.preview_card.show(); self._layout()

    def refresh_preview_content(self, value):
        """Browse detached contents without changing execution selection."""
        from .composition import render, prototype
        root = prototype(value)
        seq = pm.sequence(root)
        self.variant_navigation.setVisible(bool(seq))
        if not seq:
            self.preview_variant_id = None
            text=render(root)
            if self.preview_text.toPlainText()!=text:self.preview_text.setPlainText(text)
            return
        rows = seq['groups']; ids = [v['id'] for v in rows]
        if self.preview_variant_id not in ids:
            self.preview_variant_id = seq['selected'] if seq['selected'] in ids else ids[0]
        at = ids.index(self.preview_variant_id)
        self.variant_position.setText(f"{at+1} / {len(rows)}")
        self.variant_position.setToolTip(rows[at]['name'])
        noun='組合' if root.get('sequence') else '變體'
        for control,direction in ((self.variant_previous,'上一個'),(self.variant_next,'下一個')):
            control.setAccessibleName(direction+noun);control.setToolTip(direction+noun)
        self.variant_previous.setEnabled(at > 0); self.variant_next.setEnabled(at+1 < len(rows))
        preview = copy.deepcopy(root); pm.sequence(preview)['selected'] = self.preview_variant_id
        text=render(preview)
        if self.preview_text.toPlainText()!=text:self.preview_text.setPlainText(text)

    def move_preview_variant(self, step):
        if not self.preview_id: return
        try: value = materials.item(self.window.state, self.preview_id)
        except ValueError: self.close_preview(); return
        seq = pm.sequence(value.get('composition', {}))
        if not seq: self.refresh_preview_content(value); return
        ids = [v['id'] for v in seq['groups']]
        at = ids.index(self.preview_variant_id) if self.preview_variant_id in ids else 0
        if 0 <= at+step < len(ids): self.preview_variant_id = ids[at+step]
        self.refresh_preview_content(value); self._layout()

    def close_preview(self):
        self.preview_click.cancel()
        self.preview_id = None; self.preview_variant_id = None; self.preview_card.hide(); self._layout()

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
        material_type = TYPE_SHORTCUTS.get(self.get_browser_category())
        if material_type in ('combination', 'flow') and not text:
            category = next((ident for ident, shortcut in self._category_type_aliases.items()
                             if shortcut == self.get_browser_category()), None)
            value = dict(id=uid(), name='', prompt=text, aliases=[], notes='', excludes=[], preview='',
                         module=category, content_kind='tags', material_type=material_type,
                         composition=pm.new(material_type, ''))
            return self._open_dialog(None, text, initial_value=value)
        return self._open_dialog(None, text)

    def edit_material(self, ident): return self._open_dialog(ident)
    open_material = edit_material

    def open_selected(self):
        ident = self.selected_id() or self.preview_id
        if ident: return self.edit_material(ident)

    def _open_dialog(self, ident, text='', initial_value=None):
        if self.active_dialog:
            self.active_dialog.raise_(); return self.active_dialog
        dialog = MaterialEditDialog(self, ident, text, initial_value=initial_value)
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
        self.material_menu_at(entry.data(Qt.ItemDataRole.UserRole), self.listing.viewport().mapToGlobal(position))

    def material_menu_at(self, ident, position):
        self.preview_click.cancel()
        value = materials.item(self.window.state, ident)
        menu = RoundMenu(self); menu.addAction('編輯素材', lambda: self.edit_material(ident))
        from .workspace_material_menu import add_entry
        add_entry(self, menu, ident)
        menu.addAction('取消釘選' if value.get('pinned') else '釘選素材', lambda: self.pin_material(ident))
        menu.addAction('另存為新素材', lambda: self.duplicate_material(ident))
        from .prompt_editor import show_history
        menu.addAction('內容歷史', lambda: show_history(self.window, ident))
        menu.add_destructive_action('刪除', lambda: self.delete_material(ident))
        menu.open_at(position)

    def add_canvas_menu(self):
        from .workspace_material_menu import open_menu
        return open_menu(self, self.preview_id,
                         self.add_canvas_button.mapToGlobal(self.add_canvas_button.rect().bottomLeft()))

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
        dialog = self._open_dialog(None, initial_value=value)
        dialog.form.edited()
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
        selected = self.get_browser_category()
        def apply(state):
            if any(v['name'] == name.strip() for v in state['modules']): raise ValueError('已有同名分類，請使用其他名稱。')
            added.append(materials.add_category(state, name))
            if navigation.is_folder(selected): navigation.move(state, added[0], selected)
        if self.window.canvas.commit(apply, label='新增素材分類'):
            self.refresh(); self.set_browser_category(added[0])


class MaterialEditDialog(StudioDialog):
    """Save publishes; dismissal keeps a local draft without blocking the app."""
    def __init__(self, page, ident, text='', initial_value=None):
        super().__init__(page.window,defer_style=True)
        from .module_chrome import apply_prompt_dialog, prompt_divider
        apply_prompt_dialog(self,page.window)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.set_outside_dismiss(True)
        self._dismissed = False
        from .draft_cache import DraftCache
        self.draft_cache = DraftCache(page.window.store.directory)
        self.draft_key = 'material:'+str(ident or '@new')
        self.draft_memory = getattr(page.window, '_pending_material_drafts', {})
        page.window._pending_material_drafts = self.draft_memory
        self.restored_draft = (self.draft_memory.get(self.draft_key) or self.draft_cache.read(self.draft_key)) if initial_value is None else None
        # Assemble the empty shell first. Moving a populated, styled widget
        # tree between parents synchronously repolishes every descendant.
        from .material_editor_panels import MaterialPanels
        self.panels=MaterialPanels(self,None,lambda:self.pages.close_group_details())
        from .prompt_editor import MaterialEditorForm
        self.page = page; self.target_id = ident; self.busy = False; self.receipt = None; self.committed = False; self.was_new = ident is None
        self.form = MaterialEditorForm(page.window, compact=True, isolated=True, parent=self); self.form.hide()
        f = self.form
        f.set_destination(page.destination_id)
        f.category.setCurrentIndex(max(0, f.category.findData(page.get_browser_category())))
        if ident: f.open_material(ident)
        else: f.new_material(text=text)
        if initial_value is not None:
            f.target = None
            f.load_value(copy.deepcopy(initial_value), 'new')
        if self.restored_draft:
            saved = copy.deepcopy(self.restored_draft['form'])
            if saved['target'] is not None:saved['target'] = tuple(saved['target'])
            f._restore_session(saved)
        self.initial_id = f.base['id']
        self.name, self.prompt = f.name, f.prompt
        self.prompt.setProperty('modulePrompt',True)
        self.setWindowTitle('編輯素材' if ident else '新增素材')
        self.resize(580, 660); self.body.setSpacing(10)
        from .prompt_group_editor import MaterialPages
        self.pages = MaterialPages(self)
        self.form_scroll = QScrollArea(); self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form_scroll.setStyleSheet('QScrollArea { background:transparent; border:0; }')
        self.form_scroll.viewport().setAutoFillBackground(False)
        self.form_scroll.setWidget(self.pages); self.body.addWidget(self.form_scroll, 1)
        self.pages.setAutoFillBackground(False)
        self.error = StatusLabel(); f.status = self.error
        self.destination_label = label('', 'Subtle', True)
        self.cancel_button = button('捨棄修改', self.discard, 'Quiet')
        self.save_button = button('保存', lambda: self.save(bool(self.form.destination_id) and not self.target_id), 'Primary')
        self.add_button = button('保存並加入畫布', lambda: self.save(True), 'Quiet')
        self.identity = label('', 'Subtle')
        from .prompt_group_editor import tool
        self.back_button=tool('back','返回',self.pages.back);self.heading_row.insertWidget(0,self.back_button);self.back_button.hide()
        self.done_button=button('完成',self.pages.back,'Primary');self.done_button.hide()
        self.body.addWidget(self.destination_label)
        self.body.addWidget(prompt_divider())
        footer=QWidget();details=QVBoxLayout(footer);details.setContentsMargins(8,0,0,0);details.setSpacing(4);details.addWidget(self.error);details.addWidget(self.identity)
        self.body.addLayout(row(footer, None, self.cancel_button, self.save_button, self.add_button,self.done_button))
        self.panels.content=self.pages;self.panels.form_scroll=self.form_scroll
        self.panels.set_content(self.pages.details_container,'管理',self.pages.close_group_details)
        self.set_destination(page.destination_id)
        self.prompt.textChanged.connect(lambda: apply_line_spacing(f)); sync_prompt_style(f); apply_line_spacing(f)
        self.error.setText('')
        bounds = page.window.size()
        if bounds.width() > 0 and bounds.height() > 0:
            self.resize(min(760 if self.pages.is_group else 580, max(340, bounds.width()-24)), min(660, max(400, bounds.height()-24)))
        from .material_editor_documents import ModalNativeHistory
        self.native_history = ModalNativeHistory(f)
        # QDialog otherwise chooses the header's close button for Return.
        # Let text editors and completion popups consume their own keys first.
        for control in self.findChildren(QPushButton): control.setAutoDefault(False)
        self.save_button.setDefault(True)
        self.pages.page_changed(0)

    def draft_snapshot(self):
        return dict(form=self.form.export_state(), type=self.pages.material_type,
                    groups={key: copy.deepcopy(editor.sequence) for key, editor in self.pages.group_editors.items()},
                    variants=copy.deepcopy(self.pages.variant_editor.sequence) if self.pages.variant_editor else None)

    def reject(self):
        if self._dismissed:return
        self._dismissed = True
        if self.form.dirty or self.restored_draft:
            self.draft_memory[self.draft_key] = self.draft_snapshot()
            try:self.draft_cache.write(self.draft_key, self.draft_memory[self.draft_key])
            except (OSError, ValueError) as error:
                self.page.window.notice(str(error))
        super().reject()

    def discard(self):
        self.draft_cache.clear(self.draft_key)
        self.draft_memory.pop(self.draft_key, None)
        self._dismissed = True
        super().reject()

    def contains_global(self, position):
        for surface in (self.panels.main, self.panels.side):
            if surface.isVisible() and surface.rect().contains(surface.mapFromGlobal(position)):return True
        return False

    def fit_page(self):
        self.panels.fit_content()

    def showEvent(self,event):
        # The first geometry pass gives nested group pages their final width;
        # their resize handlers then update toolbar and list minimum heights.
        # Settle those constraints before the first paint, not with a later
        # timer that would resize the visible native window and flash.
        for _ in range(2):self.panels.fit_content(before_show=True)
        super().showEvent(event)

    def set_destination(self, canvas_id):
        self.form.set_destination(canvas_id)
        value = self.page.window.state.get('multi_output', {}).get('canvases', {}).get(canvas_id)
        self.add_button.setVisible(value is not None and self.target_id is not None)
        self.save_button.setText('保存並加入畫布' if value and not self.target_id else '保存')
        self.destination_label.setVisible(value is not None)
        self.destination_label.setText('加入畫布：'+value['name'] if value else '')
        if self.receipt and canvas_id != self.receipt['destination']:
            self.error.setText('加入目的地已改變；再次保存並加入會使用新的目的地。')

    def _value(self):
        f = self.form; value = copy.deepcopy(f.base or {}); fields = f.form_values()
        if 'composition' in value: value['composition'].update(fields)
        value.update(fields, module=f.edit_category.currentData(), notes=f.notes.toPlainText(),
            aliases=list(dict.fromkeys(v.strip() for v in f.aliases.text().replace(',', '、').replace('，', '、').split('、') if v.strip())))
        return self.pages.value(value)

    def save(self, add=False):
        if self.busy: return False
        add = bool(add); f = self.form; window = self.page.window
        if self.require_category and f.edit_category.currentData() not in {v['id'] for v in window.state['modules']}:
            self.error.setText('請選擇素材所屬的分類。'); f.edit_category.setFocus(); return False
        destination = f.destination_id if add else None
        if add and destination is None: self.error.setText('請先選擇要加入的畫布。'); return False
        try:
            value = self._value(); signature = json.dumps(value, sort_keys=True, ensure_ascii=False)
        except ValueError as exc:
            self.error.setText(str(exc)); return False
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
        if result == QDialog.DialogCode.Accepted:
            self.draft_cache.clear(self.draft_key)
            self.draft_memory.pop(self.draft_key, None)
        self.set_outside_dismiss(False)
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
