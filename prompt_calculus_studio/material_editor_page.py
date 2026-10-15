"""Focused Prompt Editor presentation with a persistent material browser."""
from PySide6.QtCore import Qt, QSize, QRectF, QTimer, QSignalBlocker, Signal, QStringListModel
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen, QPainter, QShortcut, QKeySequence, QTextCursor, QTextBlockFormat, QTextLayout, QTextOption
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPlainTextEdit, QListWidget, QCheckBox, QScrollArea, QStyledItemDelegate,
    QStyle, QSizePolicy, QPushButton, QTextEdit, QCompleter, QButtonGroup, QStackedWidget)
from .widgets import label, button, row, ComboBox, panel, RoundMenu, ElidedLabel
from .image_drop import ImageDropLabel
from .views import widget_colors


class PagePromptText(QTextEdit):
    """Plain-text storage with native proportional text layout and completion."""
    accepted = Signal(object)

    def __init__(self, owner):
        super().__init__()
        from .widgets import style_completion
        self.owner = owner
        self.service = owner.window.completion
        self.composing = False; self.inserting = False
        self.expected = None; self.candidates = {}
        self.setAcceptRichText(False)
        self.model = QStringListModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setWidget(self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setMaxVisibleItems(10)
        style_completion(self.completer, self, self.service.window)
        self.completer.activated[str].connect(self.insert_completion)
        self.textChanged.connect(self.schedule)
        self.cursorPositionChanged.connect(self.schedule)

    def lookup_active(self):
        return not getattr(self.owner, 'group_mode', False) and self.owner.lookup.isChecked() and self.owner.kind.currentData() == 'tags' and self.hasFocus() and not self.composing

    def schedule(self):
        from .completion import PromptEdit
        if self.lookup_active(): PromptEdit.schedule(self)
        else:
            self.completer.popup().hide(); self.service.cancel(self)

    def claim_history(self):
        from .material_editor_documents import claim_document
        claim_document(self.owner)

    def focusInEvent(self, event):
        self.claim_history()
        super().focusInEvent(event)

    def setPlainText(self, text):
        self.claim_history(); super().setPlainText(text)

    def insertFromMimeData(self, source):
        self.claim_history(); super().insertFromMimeData(source)

    def undo(self):
        self.claim_history(); super().undo()

    def redo(self):
        self.claim_history(); super().redo()

    def inputMethodEvent(self, event):
        self.claim_history()
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)
        if self.composing: self.completer.popup().hide(); self.service.cancel(self)
        else: self.schedule()

    def keyPressEvent(self, event):
        self.claim_history()
        popup = self.completer.popup()
        if popup.isVisible() and not self.composing:
            if event.key() == Qt.Key.Key_Escape: popup.hide(); return
            if event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab):
                index = popup.currentIndex()
                if index.isValid(): self.insert_completion(index.data()); return
        super().keyPressEvent(event)

    # Completion's token, UTF-16 insertion and stale-result checks are shared.
    from .completion import PromptEdit as _Completion
    context = _Completion.context
    lookup_notice = _Completion.lookup_notice
    display = _Completion.display
    insert_completion = _Completion.insert_completion


class MaterialCardDelegate(QStyledItemDelegate):
    @staticmethod
    def preview_lines(text, font, width):
        """Native word/Chinese wrapping, with at most two Unicode-safe lines."""
        if not text: return []
        layout = QTextLayout(text, font)
        option = QTextOption(); option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option); layout.beginLayout()
        first = layout.createLine(); first.setLineWidth(max(20, width))
        end = first.textStart() + first.textLength()
        layout.endLayout()
        encoded = text.encode('utf-16-le')
        result = [encoded[:end * 2].decode('utf-16-le').rstrip()]
        remaining = encoded[end * 2:].decode('utf-16-le').lstrip()
        if remaining: result.append(QFontMetrics(font).elidedText(remaining, Qt.TextElideMode.ElideRight, max(20, width)))
        return result

    def sizeHint(self, option, index):
        line = QFontMetrics(option.font).height()
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        has_image = icon and not icon.isNull()
        width = self.parent().viewport().width() - 27 - (62 if has_image else 0)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or '').partition('\n')[2]
        count = len(self.preview_lines(text, option.font, width))
        return QSize(150, max(line * (count + 1) + 32, 82 if has_image else 0))

    def paint(self, painter, option, index):
        colors = widget_colors(self.parent())
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = QRectF(option.rect).adjusted(1, 3, -2, -3)
        painter.save(); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(colors['accent' if selected else 'line']), 1))
        painter.setBrush(QColor(colors['selected' if selected else 'hover' if hover else 'surface']))
        painter.drawRoundedRect(rect, 8, 8)
        left = rect.left() + 12
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon and not icon.isNull():
            side = min(52, int(rect.height()) - 24)
            icon.paint(painter, QRectF(left, rect.top() + 12, side, side).toRect())
            left += side + 10
        width = max(20, int(rect.right() - left - 12))
        lines = str(index.data(Qt.ItemDataRole.DisplayRole) or '').split('\n', 1)
        font = QFont(option.font); font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font); metrics = QFontMetrics(font); height = metrics.height()
        painter.setPen(QColor(colors['text']))
        painter.drawText(QRectF(left, rect.top() + 10, width, height + 2), Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(lines[0], Qt.TextElideMode.ElideRight, width))
        font.setWeight(QFont.Weight.Normal); painter.setFont(font); metrics = QFontMetrics(font)
        painter.setPen(QColor(colors['secondary']))
        text = lines[1] if len(lines) > 1 else ''
        for i, part in enumerate(self.preview_lines(text, font, width)):
            painter.drawText(QRectF(left, rect.top() + 16 + height * (i + 1), width, height + 2), Qt.AlignmentFlag.AlignVCenter, part)
        painter.restore()


class Disclosure(QPushButton):
    def __init__(self, text):
        super().__init__(text)
        self.setObjectName('Quiet'); self.setCheckable(True)
        self.setStyleSheet('text-align:left; padding-left:0;')
        self.toggled.connect(self.update_arrow); self.update_arrow(False)

    def update_arrow(self, checked):
        from .ui_icons import icon
        self.setIcon(icon('chevron-up' if checked else 'chevron-down'))


def build_browser(e):
    from .prompt_editor import MaterialList
    e.browser = QWidget(); browse = QVBoxLayout(e.browser)
    browse.setContentsMargins(0, 0, 0, 0); browse.setSpacing(12)
    e.library_return = button('返回編輯', e.hide_library, 'Quiet')
    e.new_button = button('新增', e.new_material, 'Quiet')
    e.library_more = button('更多', lambda: library_menu(e), 'Quiet')
    e.library_title = label('素材庫', 'Heading')
    e.browse_header = row(e.library_return, e.library_title, None, e.new_button, e.library_more)
    browse.addLayout(e.browse_header)
    e.search = QLineEdit(); e.search.setPlaceholderText('搜尋名稱、別名或內容'); browse.addWidget(e.search)
    e.all_filter = button('全部', lambda: e.pins.setChecked(False), 'FilterChip')
    e.pin_filter = button('釘選', lambda: e.pins.setChecked(True), 'FilterChip')
    e.filter_group = QButtonGroup(e)
    for widget in (e.all_filter, e.pin_filter): widget.setCheckable(True); e.filter_group.addButton(widget)
    e.all_filter.setChecked(True)
    e.filters = row(e.all_filter, e.pin_filter, None); browse.addLayout(e.filters)
    e.category_label = label('內容分類'); browse.addWidget(e.category_label)
    e.category = ComboBox(); e.category.setAccessibleName('內容分類'); browse.addWidget(e.category)
    e.order = ComboBox(e); e.order.addItem('釘選優先', 'name'); e.order.addItem('最近使用', 'recent'); e.order.hide()
    e.list_hint = label('點選素材以開啟編輯', wrap=True); browse.addWidget(e.list_hint)
    e.listing = MaterialList(); e.listing.setItemDelegate(MaterialCardDelegate(e.listing))
    e.listing.setMouseTracking(True); e.listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    e.listing.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    e.listing.customContextMenuRequested.connect(e.material_menu); browse.addWidget(e.listing, 1)
    e.empty_results = label('沒有符合的素材。換個關鍵字或分類再試試。', wrap=True); browse.addWidget(e.empty_results)
    e.count = label(''); browse.addWidget(e.count)


def build_page(e):
    e._library_collapsed = False; e._library_page = False
    e.page_layout = QHBoxLayout(e); e.page_layout.setContentsMargins(24, 24, 24, 24)
    e.page_layout.addStretch()
    e.page = QWidget(); e.page.setMaximumWidth(1280); e.page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    e.page_layout.addWidget(e.page, 1); e.page_layout.addStretch()
    outer = QVBoxLayout(e.page); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(16)
    e.toolbar = QWidget(); toolbar = QHBoxLayout(e.toolbar); toolbar.setContentsMargins(0, 0, 0, 0)
    e.page_title = label('Prompt Editor', 'DialogTitle'); toolbar.addWidget(e.page_title); toolbar.addStretch()
    e.library_button = button('收合素材庫', e.toggle_library, 'Quiet')
    e.canvas_button = button('前往畫布', lambda: open_canvas(e), 'Quiet')
    toolbar.addWidget(e.library_button); toolbar.addWidget(e.canvas_button); outer.addWidget(e.toolbar)
    e.body_host = QWidget(); e.columns = QHBoxLayout(e.body_host); e.columns.setContentsMargins(0, 0, 0, 0); e.columns.setSpacing(24)
    outer.addWidget(e.body_host, 1)
    e.pins = QCheckBox(e); e.pins.hide()
    build_browser(e)
    e.detail, detail = panel('InsetPanel'); detail.setContentsMargins(24, 24, 24, 24); detail.setSpacing(12)
    e.columns.addWidget(e.browser, 0, Qt.AlignmentFlag.AlignTop)
    e.columns.addWidget(e.detail, 1, Qt.AlignmentFlag.AlignTop)
    e.header = QWidget(); header = QVBoxLayout(e.header); header.setContentsMargins(0, 0, 0, 0); header.setSpacing(8)
    e.name_row = QHBoxLayout(); e.name_row.setSpacing(8)
    e.name_label = label('名稱')
    e.name = QLineEdit(); e.name.setPlaceholderText('未命名 Prompt'); e.name.setAccessibleName('目前 Prompt 名稱')
    e.save_button = button('儲存', e.save_material, 'Primary')
    e.name_row.addWidget(e.name_label); e.name_row.addWidget(e.name, 1); e.name_row.addWidget(e.save_button)
    header.addLayout(e.name_row)
    e.identity = ElidedLabel(); e.identity.setTextFormat(Qt.TextFormat.PlainText)
    e.copy_button = button('複製', lambda: copy_prompt(e), 'Quiet')
    e.actions_button = button('更多', lambda: action_menu(e), 'Quiet')
    e.identity_row = row(e.identity, None, e.copy_button, e.actions_button); header.addLayout(e.identity_row)
    e.status = label('', wrap=True); header.addWidget(e.status)
    detail.addWidget(e.header)
    e.scroll = QScrollArea(); e.scroll.setWidgetResizable(True); e.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    e.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); e.scroll.viewport().setObjectName('ScrollViewport')
    content = QWidget(); content.setObjectName('ScrollContent')
    e.content_layout = QVBoxLayout(content); e.content_layout.setContentsMargins(0, 0, 0, 0); e.content_layout.setSpacing(12)
    e.prompt_label = label('Prompt'); e.content_layout.addWidget(e.prompt_label)
    e.prompt = PagePromptText(e); e.prompt.setObjectName('Prompt'); e.prompt.setPlaceholderText('開始編寫 Prompt…')
    e.content_layout.addWidget(e.prompt)
    e.more = Disclosure('素材資訊'); e.content_layout.addWidget(e.more)
    e.extras = QWidget(); extra = QVBoxLayout(e.extras); extra.setContentsMargins(0, 4, 0, 8); extra.setSpacing(12)
    common = QFormLayout(); common.setSpacing(12); common.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    e.edit_category = ComboBox(); common.addRow('分類', e.edit_category)
    e.aliases = QLineEdit(); common.addRow('別名', e.aliases)
    e.kind = ComboBox()
    for title, key in (('標籤', 'tags'), ('純文字', 'text'), ('Markdown', 'markdown')): e.kind.addItem(title, key)
    common.addRow('內容格式', e.kind); extra.addLayout(common)
    e.advanced_toggle = Disclosure('查詢、備註與停用關係'); extra.addWidget(e.advanced_toggle)
    e.advanced = QWidget(); advanced = QFormLayout(e.advanced); advanced.setContentsMargins(0, 0, 0, 0)
    advanced.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows); advanced.setSpacing(12)
    e.lookup = QCheckBox('標籤查詢（Danbooru／字典）'); advanced.addRow(e.lookup)
    e.lookup_hint = label('輸入標籤並選擇候選，採用後才加入正文。', wrap=True); advanced.addRow(e.lookup_hint); e.lookup_hint.hide()
    e.notes = QPlainTextEdit(); e.notes.setMaximumHeight(90); advanced.addRow('備註', e.notes)
    e.excludes = QPlainTextEdit(); e.excludes.setMaximumHeight(90)
    e.excludes.setPlaceholderText('使用此素材時停用的完整 Tag，每行一項'); advanced.addRow('停用關係', e.excludes)
    extra.addWidget(e.advanced); e.advanced.hide(); e.advanced_toggle.toggled.connect(e.advanced.setVisible)
    e.preview_toggle = Disclosure('預覽圖片'); extra.addWidget(e.preview_toggle)
    e.preview_section = QWidget(); preview_layout = QVBoxLayout(e.preview_section); preview_layout.setContentsMargins(0, 0, 0, 0)
    e.preview = ImageDropLabel(e.window.store); e.preview.setMaximumHeight(140); e.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
    e.preview.pathReady.connect(e.receive_image); e.preview.failed.connect(e.window.notice); preview_layout.addWidget(e.preview)
    preview_layout.addLayout(row(button('選擇預覽圖', e.choose_image, 'Quiet'), button('移除預覽', e.clear_image, 'DangerQuiet'), None))
    extra.addWidget(e.preview_section); e.preview_section.hide(); e.preview_toggle.toggled.connect(e.preview_section.setVisible)
    e.content_layout.addWidget(e.extras); e.extras.hide(); e.content_layout.addStretch()
    e.scroll.setWidget(content); detail.addWidget(e.scroll, 1)
    # Shared data/session helpers retain their compact controls without exposing them here.
    e.heading = label(''); e.scope_hint = label(''); e.back_button = button('返回素材', e.show_library, 'Quiet')
    e.destination = ComboBox(e); e.edit_destination = ComboBox(e)
    e.use_button = button('保存並加入畫布', lambda: e.save_material(True), 'Quiet')
    e.variant_button = button('另存為新素材', e.save_variant, 'Quiet')
    e.history_button = button('內容歷史', e.history, 'Quiet'); e.delete_button = button('刪除', e.delete_material, 'DangerQuiet')
    for widget in (e.heading, e.scope_hint, e.back_button, e.destination, e.edit_destination, e.use_button, e.variant_button, e.history_button, e.delete_button): widget.setParent(e); widget.hide()
    e.more.toggled.connect(lambda opened: toggle_information(e, opened))
    e.more.toggled.connect(lambda _: e._publish(force=True))
    e.lookup.toggled.connect(e.lookup_changed)
    e.search.textChanged.connect(e.refresh_list); e.category.currentIndexChanged.connect(e.refresh_list)
    e.order.currentIndexChanged.connect(e.refresh_list); e.pins.toggled.connect(e.refresh_list)
    e.listing.itemClicked.connect(e.material_clicked); e.listing.itemActivated.connect(lambda _: select_current(e))
    e.destination.currentIndexChanged.connect(lambda: e.destination_changed(e.destination))
    e.edit_destination.currentIndexChanged.connect(lambda: e.destination_changed(e.edit_destination))
    for field in (e.name, e.aliases): field.textChanged.connect(e.edited)
    for field in (e.prompt, e.notes, e.excludes): field.textChanged.connect(e.edited)
    e.kind.currentIndexChanged.connect(e.edited); e.edit_category.currentIndexChanged.connect(e.edited)
    e.prompt.textChanged.connect(lambda: apply_line_spacing(e))
    resize_page(e)


def toggle_information(e, opened):
    outer = e.scroll.verticalScrollBar()
    if opened: e._information_origin = outer.value()
    e.extras.setVisible(opened)
    if not opened:
        # Return to the pre-disclosure reading position. The document's own
        # scroll remains untouched; only the shorter outer range is clamped.
        origin = getattr(e, '_information_origin', outer.value())
        QTimer.singleShot(0, lambda: outer.setValue(min(origin, outer.maximum())))


def select_current(e):
    e.open_selected()
    if e.narrow: e.hide_library()


def open_canvas(e):
    if e.can_leave() and hasattr(e.window, 'enter_canvas'): e.window.enter_canvas()


def copy_prompt(e):
    if hasattr(e.window, 'copy_text'): e.window.copy_text(e.prompt.toPlainText())


def action_menu(e):
    menu = RoundMenu(e)
    menu.addAction('新增 Prompt', e.new_material)
    if e._page_compact:
        menu.addAction('複製正文', lambda: copy_prompt(e)); menu.addAction('前往畫布', lambda: open_canvas(e))
    if e.target:
        menu.addAction('另存為新素材', e.save_variant); menu.addAction('內容歷史', e.history)
        if e.target[0] == 'material':
            from .materials import item
            ident = e.target[1]; pinned = item(e.window.state, ident).get('pinned', False)
            menu.addAction('取消釘選' if pinned else '釘選素材', lambda: e.pin_material(ident))
            menu.addSeparator(); menu.add_destructive_action('刪除', e.delete_material)
    menu.open_at(e.actions_button.mapToGlobal(e.actions_button.rect().bottomLeft()))


def library_menu(e):
    menu = RoundMenu(e)
    for index in range(e.order.count()):
        action = menu.addAction(e.order.itemText(index), lambda checked=False, i=index: e.order.setCurrentIndex(i))
        action.setCheckable(True); action.setChecked(index == e.order.currentIndex())
    menu.addSeparator(); menu.addAction('新增分類', e.new_category)
    menu.addAction('內容與操作歷史', lambda: history(e)); menu.addAction('取回舊清單內容', e.legacy_content)
    menu.open_at(e.library_more.mapToGlobal(e.library_more.rect().bottomLeft()))


def history(e):
    from .prompt_editor import show_history
    show_history(e.window)


def update_page(e):
    if not hasattr(e, 'identity'): return
    kind = e.target[0] if e.target else 'new'
    identity = '新稿' if kind == 'new' else '編輯素材原件' if kind == 'material' else e.heading.text()
    status = '未儲存' if e.dirty or kind == 'new' else '已儲存' if kind == 'material' else '即時同步'
    if kind == 'canvas': status = '同步中' if e.dirty else '即時同步'
    e.identity.setText(identity + ' · ' + status); e.identity.setToolTip(e.scope_hint.text())
    e.more.setText('素材資訊 · ' + (e.edit_category.currentText() or '未分類') + ' · ' + e.kind.currentText())
    routine = ('', '尚未保存', '完成後可直接保存並加入畫布。', '正在編輯畫布副本…')
    e.status.setVisible(e.status.text() not in routine)
    e.use_button.hide(); e.edit_destination.hide(); e.save_button.setVisible(kind != 'canvas')
    e.copy_button.setEnabled(bool(e.prompt.toPlainText()))
    name = e.name.text() or '未命名 Prompt'
    e.library_return.setText('返回編輯 ' + e.library_return.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, max(70, e.browser.width() - 350)))
    e.library_return.setToolTip('返回編輯 ' + name)
    apply_line_spacing(e)


def apply_line_spacing(e):
    if getattr(e, '_spacing', False): return
    e._spacing = True
    try:
        document = e.prompt.document(); block = document.begin(); blocks = []
        while block.isValid():
            if block.blockFormat().lineHeight() != 165: blocks.append(block)
            block = block.next()
        if blocks:
            with QSignalBlocker(e.prompt):
                cursor = QTextCursor(document); cursor.joinPreviousEditBlock()
                fmt = QTextBlockFormat(); fmt.setLineHeight(165, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
                for block in blocks:
                    cursor.setPosition(block.position()); cursor.mergeBlockFormat(fmt)
                cursor.endEditBlock()
    finally: e._spacing = False


def sync_prompt_style(e):
    from .theme import font_pixels
    colors = widget_colors(e); size = e.window.state['settings']['prompt_size']
    style = "QTextEdit#Prompt { font-family:'Cascadia Mono','Consolas','Microsoft JhengHei'; font-size:%dpx; background:%s; border:0; border-radius:8px; padding:10px; }" % (font_pixels(size), colors['field'])
    if e.prompt.property('modulePrompt'):
        from .module_chrome import dialog_colors
        colors=dialog_colors(colors)
        style += "QTextEdit#Prompt {background:%s;border:1px solid %s;} QTextEdit#Prompt:focus {border-color:%s;}" % (colors['surface'],colors['line'],colors['accent'])
    if e.prompt.styleSheet() != style: e.prompt.setStyleSheet(style)
    e.prompt.ensurePolished()
    e.prompt.document().setDefaultFont(e.prompt.font())


def resize_page(e):
    if not hasattr(e, 'identity'): return
    sync_prompt_style(e)
    tight = e.height() < 580; e._page_compact = tight
    margin = 12 if tight else 16 if e.width() < 760 else 24
    e.page_layout.setContentsMargins(margin, margin, margin, margin)
    e.detail.layout().setContentsMargins(margin, margin, margin, margin)
    available = min(1280, e.width() - margin * 2)
    scale = max(1, e.name.fontMetrics().height() / 19)
    minimum_library = max(300, round(300 * scale))
    minimum_editor = max(520, round(520 * scale))
    was_narrow = e.narrow
    e.narrow = available < minimum_library + minimum_editor + 24
    if e.narrow and not was_narrow: e._library_page = False
    e.page.layout().setSpacing(8 if tight else 16); e.detail.layout().setSpacing(6 if tight else 12)
    e.toolbar.setVisible(not tight)
    if tight and e.library_button.parentWidget() is not e.header:
        e.identity_row.insertWidget(0, e.library_button)
    elif not tight and e.library_button.parentWidget() is not e.toolbar:
        e.toolbar.layout().insertWidget(2, e.library_button)
    e.library_button.setText('素材庫' if e.narrow or e._library_collapsed else '收合素材庫')
    e.library_return.setVisible(e.narrow)
    e.library_title.setVisible(not e.narrow)
    e.list_hint.setVisible(not tight)
    e.category_label.setVisible(not tight)
    if tight:
        if e.filters.indexOf(e.category) < 0: e.filters.addWidget(e.category, 1)
        e.browser.layout().setSpacing(8)
    else:
        if e.filters.indexOf(e.category) >= 0:
            e.filters.removeWidget(e.category); e.browser.layout().insertWidget(4, e.category)
        e.browser.layout().setSpacing(12)
    e.copy_button.setVisible(not tight)
    if e.narrow:
        e.browser.setMinimumWidth(0); e.browser.setMaximumWidth(16777215)
        e.browser.setVisible(e._library_page); e.detail.setVisible(not e._library_page)
    else:
        e.browser.setFixedWidth(max(300, min(360, available - minimum_editor - 24)))
        e.browser.setVisible(not e._library_collapsed); e.detail.show()
    # A bounded document, not a viewport-filling empty frame. Keep header outside its scroll.
    header_height = e.header.sizeHint().height()
    toolbar_height = 0 if tight else e.toolbar.sizeHint().height() + e.page.layout().spacing()
    panel_available = max(200, e.height() - margin * 2 - toolbar_height)
    label_height = e.prompt_label.sizeHint().height()
    line = e.prompt.fontMetrics().lineSpacing(); minimum_prompt = int(line * 3 * 1.65) + 24
    desired = min(400, max(minimum_prompt, panel_available - header_height - label_height - margin * 2 - 74))
    e.prompt.setFixedHeight(desired)
    natural = header_height + label_height + 400 + e.more.sizeHint().height() + margin * 2 + 72
    panel_height = min(panel_available, natural)
    e.detail.setFixedHeight(panel_height); e.browser.setFixedHeight(panel_height)
    height = max(36, e.name.fontMetrics().height() + 16)
    for widget in (e.name, e.save_button, e.copy_button, e.actions_button, e.library_button, e.canvas_button, e.new_button): widget.setMinimumHeight(height)
    update_page(e)
