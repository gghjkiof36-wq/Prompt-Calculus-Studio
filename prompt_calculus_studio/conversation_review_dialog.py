"""Conversation browsing, manual ChatGPT exchange and source-linked review."""
from copy import deepcopy
from pathlib import Path
import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor, QActionGroup
from PySide6.QtWidgets import (QApplication, QFileDialog, QLineEdit, QListWidget, QListWidgetItem,
                              QTabWidget, QWidget, QVBoxLayout, QSpinBox, QSplitter, QPushButton,
                              QStackedWidget, QSizePolicy, QProgressBar, QLabel, QListView, QFormLayout, QLayout)
from . import conversation_review as review
from .retained_dialog import RetainedSheet, plain_editor
from .retained_files import load_record, read_local
from .widgets import ComboBox, ElidedLabel, ActionHeader, RoundMenu, label, button, row


ROLE_NAMES = {'user': '你', 'assistant': 'ChatGPT', 'system': '系統', 'tool': '工具'}


def pane():
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    return widget, layout


def combo(name, values=()):
    control = ComboBox()
    control.addItems(values)
    control.setAccessibleName(name)
    control.setMinimumContentsLength(6)
    control.setSizeAdjustPolicy(ComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    return control


def editor(name, readonly=False):
    control = plain_editor(readonly)
    control.setAccessibleName(name)
    control.setMinimumSize(120, 72)
    control.document().setDocumentMargin(4)
    control.setStyleSheet('QPlainTextEdit { padding: 8px; }')
    return control


class StatusLabel(QLabel):
    def clear(self):
        self.setText('')

    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))


class PageStack(QStackedWidget):
    """An inactive editor must not impose its size on the source picker."""
    def __init__(self):
        super().__init__()
        self.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

    def minimumSizeHint(self):
        return self.currentWidget().minimumSizeHint() if self.currentWidget() else super().minimumSizeHint()

    def sizeHint(self):
        return self.currentWidget().sizeHint() if self.currentWidget() else super().sizeHint()


class ReviewSheet(RetainedSheet):
    def __init__(self, surface):
        super().__init__(surface, '對話整理')
        self._owns_backdrop = True  # Match the nonmodal conversation import sheet.
        self.resize(1040, 760)
        self.setMinimumSize(480, 460)
        self.source_data = None
        self.request = None
        self.items = []
        self.received = set()
        self.pins = set()
        self.previous = None
        self.search_results = []
        self.result_page = 0
        self.loading_item = False
        self.record = None
        self.has_output = False
        self.generated_text = ''
        self.pending_output = None
        self.chunks = None
        self.reply_drafts = {}
        self.active_chunk = -1
        self.splits = []
        self.status.deleteLater()
        self.status = StatusLabel()
        self.status.setObjectName('Subtle')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide()
        self.pages = PageStack()
        self.body.addWidget(self.pages, 1)
        self.library_ui()
        self.workspace_ui()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        self.body.addWidget(self.progress)
        self.body.addWidget(self.status)
        self.body.addLayout(row(None, self.cancel_button))
        self.cancel_button.hide()
        self.branches.currentIndexChanged.connect(self.branch_changed)
        self.tabs.currentChanged.connect(self.tab_changed)
        candidate = None
        for parent in self.document.parents:
            if not parent.is_relative_to(self.root):
                break
            if (parent / '原始對話.json').is_file():
                candidate = parent / '原始對話.json'
                break
        if candidate:
            self.load_source(candidate)
        else:
            self.resize(640, 480)
            self.refresh_library()

    def library_ui(self):
        page, layout = pane()
        self.pages.addWidget(page)
        self.library_query = QLineEdit()
        self.library_query.setPlaceholderText('搜尋對話')
        self.library_query.setAccessibleName('搜尋對話')
        self.library_query.textChanged.connect(self.filter_library)
        self.library_import = button('匯入對話…', self.import_source)
        layout.addWidget(ActionHeader(label('選擇對話', 'Heading'),
                                      button('開啟檔案…', self.choose_source, 'Quiet'),
                                      self.library_import))
        layout.addWidget(self.library_query)
        self.library_stack = QStackedWidget()
        self.library = QListWidget()
        self.library.setProperty('moduleChoices', True)
        self.library.setAccessibleName('已匯入的對話')
        self.library.setWordWrap(True)
        self.library.setSpacing(4)
        self.library_stack.addWidget(self.library)
        empty, box = pane()
        box.addStretch()
        box.addWidget(label('尚無對話', 'Heading'), 0, Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(button('匯入對話…', self.import_source, 'Primary'), 0, Qt.AlignmentFlag.AlignHCenter)
        box.addStretch()
        self.library_stack.addWidget(empty)
        self.library_empty = label('找不到對話', 'Muted')
        self.library_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.library_stack.addWidget(self.library_empty)
        layout.addWidget(self.library_stack, 1)
        self.open_source_button = button('開啟對話', self.open_library_item, 'Primary')
        self.open_source_button.setEnabled(False)
        layout.addLayout(row(None, self.open_source_button))
        self.library.currentRowChanged.connect(self.library_selection)
        self.library.itemActivated.connect(self.open_library_item)

    def workspace_ui(self):
        page, layout = pane()
        self.pages.addWidget(page)
        self.source_title = ElidedLabel('')
        self.source_title.setObjectName('Heading')
        self.source_title.setTextFormat(Qt.TextFormat.PlainText)
        self.source_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.source_button = button('更換對話', self.show_library, 'Quiet')
        self.source_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.branches = combo('對話分支')
        self.branches.setMaximumWidth(200)
        source_row = row(self.source_title, self.branches, self.source_button)
        source_row.setStretch(0, 1)
        layout.addLayout(source_row)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        layout.addWidget(self.tabs, 1)
        self.lookup_ui()
        self.review_ui()
        self.continuation_ui()
        self.tabs.setTabEnabled(2, False)

    def tab(self, name):
        widget, layout = pane()
        layout.setContentsMargins(0, 12, 0, 0)
        self.tabs.addTab(widget, name)
        return layout

    def split(self, layout, left, right, sizes):
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes(sizes)
        self.splits.append((split, sizes))
        layout.addWidget(split, 1)
        return split

    def lookup_ui(self):
        layout = self.tab('原對話')
        self.query = QLineEdit()
        self.query.setPlaceholderText('搜尋訊息')
        self.query.setAccessibleName('搜尋訊息')
        self.query.setClearButtonEnabled(True)
        self.find_button = button('搜尋', self.search)
        self.filter_button = button('篩選', lambda: self.filters.setVisible(not self.filters.isVisible()), 'Quiet')
        layout.addLayout(row(self.query, self.find_button, self.filter_button))
        self.filters, fields = pane()
        self.role_filter = combo('發話者')
        for text, value in [('所有角色', ''), ('你', 'user'), ('ChatGPT', 'assistant'), ('工具', 'tool')]:
            self.role_filter.addItem(text, value)
        self.scope = combo('搜尋範圍', ['目前分支', '全部分支'])
        self.since = QLineEdit()
        self.until = QLineEdit()
        for control, name in ((self.since, '起日'), (self.until, '迄日')):
            control.setPlaceholderText('YYYY-MM-DD')
            control.setAccessibleName(name + '（UTC）')
        fields.addLayout(row(self.scope, self.role_filter))
        fields.addLayout(row(label('日期（UTC）'), self.since, label('至'), self.until))
        layout.addWidget(self.filters)
        self.filters.hide()
        left, left_box = pane()
        right, right_box = pane()
        self.results = QListWidget()
        self.results.setProperty('moduleChoices', True)
        self.results.setAccessibleName('訊息清單')
        self.results.setWordWrap(True)
        self.results.setSpacing(3)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setResizeMode(QListView.ResizeMode.Adjust)
        self.results.setMinimumSize(160, 90)
        self.result_stack = QStackedWidget()
        self.result_stack.addWidget(self.results)
        no_results = label('找不到訊息', 'Muted')
        no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_stack.addWidget(no_results)
        left_box.addWidget(self.result_stack, 1)
        self.result_count = label('', 'Muted')
        self.previous_page = button('上一頁', lambda: self.page_results(-1), 'Quiet')
        self.next_page = button('下一頁', lambda: self.page_results(1), 'Quiet')
        left_box.addLayout(row(self.result_count, None, self.previous_page, self.next_page))
        self.context_branch = combo('訊息所在分支')
        self.pin_button = button('釘選原文', self.pin)
        right_box.addLayout(row(label('原始問答', 'Heading'), None, self.pin_button))
        right_box.addWidget(self.context_branch)
        self.original = editor('原始問答', True)
        right_box.addWidget(self.original, 1)
        self.lookup_split = self.split(layout, left, right, [300, 600])
        layout.addLayout(row(None, button('交給 ChatGPT 整理', lambda: self.tabs.setCurrentIndex(1), 'Primary')))
        self.results.currentRowChanged.connect(self.show_context)
        self.context_branch.currentIndexChanged.connect(self.render_context)
        self.query.returnPressed.connect(self.search)

    def review_ui(self):
        layout = self.tab('ChatGPT 整理')
        self.exchange_pages = QStackedWidget()
        layout.addWidget(self.exchange_pages, 1)
        setup, box = pane()
        self.exchange_pages.addWidget(setup)
        box.addSpacing(24)
        box.addWidget(label('整理這段對話', 'Heading'))
        self.purpose = combo('整理用途', ['接續工作', '閱讀整理'])
        self.length = combo('內容長度', ['精簡', '標準', '詳盡'])
        self.length.setCurrentIndex(1)
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(16)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow('用途', self.purpose)
        form.addRow('篇幅', self.length)
        box.addLayout(form)
        self.pin_label = label('', 'Muted')
        self.clear_pin_button = button('清除釘選', self.clear_pins, 'Quiet')
        box.addLayout(row(self.pin_label, None, self.clear_pin_button))
        self.chunk_options, options = pane()
        self.capacity = QSpinBox()
        self.capacity.setRange(1000, 50000)
        self.capacity.setValue(16000)
        self.capacity.setSuffix(' 字元')
        self.capacity.setAccessibleName('每段上限')
        options.addLayout(row(label('每段上限'), self.capacity))
        box.addWidget(button('分段設定', lambda: self.chunk_options.setVisible(not self.chunk_options.isVisible()), 'Quiet'),
                      0, Qt.AlignmentFlag.AlignLeft)
        box.addWidget(self.chunk_options)
        self.chunk_options.hide()
        box.addStretch(2)
        self.packet_button = button('準備整理', self.prepare_packets, 'Primary')
        self.load_button = button('開啟整理稿…', self.choose_candidate, 'Quiet')
        box.addLayout(row(self.load_button, None, self.packet_button))
        exchange, exchange_box = pane()
        self.exchange_pages.addWidget(exchange)
        self.chunk = combo('整理段落')
        self.chunk.currentIndexChanged.connect(self.show_packet)
        self.segment_count = label('', 'Muted')
        exchange_box.addLayout(row(self.chunk, None, self.segment_count))
        outgoing, out_box = pane()
        incoming, in_box = pane()
        out_box.addWidget(label('1. 複製到 ChatGPT', 'Heading'))
        self.packet_preview = editor('本段對話', True)
        out_box.addWidget(self.packet_preview, 1)
        self.prompt = editor('整理請求', True)
        self.prompt.setParent(self)
        self.prompt.hide()
        self.copy_packet_button = button('複製內容', self.copy_packet, 'Primary')
        out_box.addLayout(row(button('開啟 ChatGPT', lambda: QDesktopServices.openUrl(QUrl('https://chatgpt.com/')), 'Quiet'),
                              None, self.copy_packet_button))
        in_box.addWidget(label('2. 貼回結果', 'Heading'))
        self.reply = editor('ChatGPT 回覆')
        self.reply.setPlaceholderText('ChatGPT 回覆')
        in_box.addWidget(self.reply, 1)
        self.accept_button = button('加入整理稿', self.accept_reply, 'Primary')
        self.accept_button.setEnabled(False)
        self.reply.textChanged.connect(self.update_reply_action)
        in_box.addLayout(row(button('貼上', self.paste_reply), None, self.accept_button))
        self.exchange_split = self.split(exchange_box, outgoing, incoming, [450, 450])
        self.review_button = button('查看整理稿', lambda: self.tabs.setCurrentIndex(2))
        self.review_button.hide()
        exchange_box.addLayout(row(None, self.review_button))
        self.update_pins()

    def continuation_ui(self):
        layout = self.tab('整理稿')
        self.document_pages = QStackedWidget()
        layout.addWidget(self.document_pages, 1)
        checking, box = pane()
        self.document_pages.addWidget(checking)
        self.compact_items = combo('整理項目')
        self.compact_items.hide()
        box.addWidget(self.compact_items)
        self.compact_items.currentIndexChanged.connect(lambda index: self.candidates.setCurrentRow(index))
        left, list_box = pane()
        right, detail_box = pane()
        self.item_count = label('', 'Muted')
        list_box.addWidget(self.item_count)
        self.candidates = QListWidget()
        self.candidates.setProperty('moduleChoices', True)
        self.candidates.setWordWrap(True)
        self.candidates.setSpacing(4)
        self.candidates.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.candidates.setResizeMode(QListView.ResizeMode.Adjust)
        self.candidates.setMinimumSize(150, 100)
        self.candidates.setAccessibleName('整理項目')
        list_box.addWidget(self.candidates, 1)
        self.source_toggle = button('查看原文', self.toggle_source, 'Quiet')
        self.source_toggle.setCheckable(True)
        self.item_options = QPushButton('項目選項')
        self.item_options.setObjectName('Quiet')
        menu = self.options_menu = RoundMenu(self.item_options, persistent=True)
        categories = menu.addMenu('分類')
        self.category_menu = categories
        self.category_group = QActionGroup(self)
        self.category_actions = {}
        for category in review.CATEGORIES:
            action = categories.addAction(review.CATEGORY_LABELS[category])
            action.setCheckable(True)
            self.category_group.addAction(action)
            action.triggered.connect(lambda _=False, value=category: self.change_category(value))
            self.category_actions[category] = action
        menu.addSeparator()
        self.checked = menu.addAction('已對照原文')
        self.checked.setCheckable(True)
        self.item_options.clicked.connect(lambda: menu.open_for(self.item_options, align_right=True))
        detail_box.addLayout(row(label('內容', 'Heading'), None, self.source_toggle, self.item_options))
        self.item_text = editor('整理內容')
        detail_box.addWidget(self.item_text, 1)
        self.source_panel, source_panel_box = pane()
        self.citation_button = button('選擇引用', self.choose_citations, 'Quiet')
        source_panel_box.addLayout(row(label('原文', 'Heading'), None, self.citation_button))
        self.citation_pages = QStackedWidget()
        self.citation_text = editor('引用原文', True)
        self.citation_pages.addWidget(self.citation_text)
        source_picker, source_box = pane()
        self.citation_choices = QListWidget()
        self.citation_choices.setWordWrap(True)
        self.citation_choices.setAccessibleName('引用訊息')
        source_box.addWidget(self.citation_choices, 1)
        source_box.addLayout(row(button('取消', lambda: self.citation_pages.setCurrentIndex(0), 'Quiet'),
                                 None, button('套用引用', self.apply_citations)))
        self.citation_pages.addWidget(source_picker)
        source_panel_box.addWidget(self.citation_pages, 1)
        detail_box.addWidget(self.source_panel, 1)
        self.source_panel.hide()
        self.review_split = self.split(box, left, right, [280, 620])
        self.maximum = QSpinBox()
        self.maximum.setRange(1000, 1000000)
        self.maximum.setValue(12000)
        self.maximum.setSuffix(' 字元')
        self.maximum.setAccessibleName('整理稿上限')
        self.compose_button = button('預覽整理稿', self.compose, 'Primary')
        self.output_settings, settings_box = pane()
        settings_box.addLayout(row(label('字數上限'), self.maximum))
        self.output_settings.hide()
        box.addWidget(self.output_settings)
        box.addLayout(row(button('輸出設定', lambda: self.output_settings.setVisible(not self.output_settings.isVisible()), 'Quiet'),
                          None, self.compose_button))
        self.candidates.currentRowChanged.connect(self.show_candidate)
        self.item_text.textChanged.connect(self.edit_candidate)
        self.checked.toggled.connect(self.mark_checked)
        document, doc_box = pane()
        self.document_pages.addWidget(document)
        doc_box.addWidget(ActionHeader(label('整理稿', 'Heading'),
                                       button('編輯項目', lambda: self.document_pages.setCurrentIndex(0), 'Quiet')))
        self.output = editor('整理稿')
        self.update_row, update_box = pane()
        self.keep_draft_button = button('保留編輯', lambda: self.resolve_output(False))
        self.rebuild_button = button('重新產生', lambda: self.resolve_output(True))
        update_box.addWidget(ActionHeader(label('整理項目已變更', 'Muted'), self.keep_draft_button, self.rebuild_button))
        doc_box.addWidget(self.update_row)
        self.update_row.hide()
        doc_box.addWidget(self.output, 1)
        self.output_count = label('', 'Muted')
        self.output.textChanged.connect(lambda: self.output_count.setText(f'{len(self.output.toPlainText()):,} 字元'))
        doc_box.addWidget(self.output_count)
        self.copy_output_button = button('複製', self.copy_output)
        self.save_button = button('儲存新版本', self.save, 'Primary')
        self.export_button = button('另存 Markdown…', self.export_md)
        doc_box.addWidget(ActionHeader(self.copy_output_button,
                                       self.export_button, self.save_button))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        orientation = Qt.Orientation.Vertical if self.width() < 780 else Qt.Orientation.Horizontal
        for split, sizes in getattr(self, 'splits', []):
            if split.orientation() != orientation:
                split.setOrientation(orientation)
                split.setSizes(([220, 220] if split is getattr(self, 'exchange_split', None) else [150, 270])
                               if orientation == Qt.Orientation.Vertical else sizes)
        if hasattr(self, 'review_split'):
            compact = orientation == Qt.Orientation.Vertical
            self.review_split.widget(0).setVisible(not compact)
            self.compact_items.setVisible(compact)
        for control in (getattr(self, 'results', None), getattr(self, 'candidates', None)):
            if control is not None and control.currentItem():
                control.scrollToItem(control.currentItem())

    def set_busy(self, busy):
        self.pages.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy)
        if not busy:
            self.status.clear()

    def tab_changed(self, index):
        self.status.clear()
        if index == 0 and self.source_data is not None:
            self.search()

    def show_library(self):
        if self.request:
            return
        self.pages.setCurrentIndex(0)
        self.refresh_library()

    def refresh_library(self):
        root = self.root
        def scan(cancel, progress):
            paths = []
            for count, (folder, dirs, files) in enumerate(os.walk(root, followlinks=False)):
                if cancel.is_set() or count >= 4000 or len(paths) >= 250:
                    break
                dirs[:] = [name for name in dirs if not name.startswith('.') and name not in ('assets', '整理稿')
                           and not (Path(folder) / name).is_symlink() and not (Path(folder) / name).is_junction()]
                if '原始對話.json' in files:
                    paths.append(Path(folder) / '原始對話.json')
            return sorted(paths, key=lambda path: str(path).casefold())
        self.run(scan, self.library_loaded)

    def library_loaded(self, paths):
        self.library.clear()
        for path in paths:
            item = QListWidgetItem(path.parent.name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(str(path.parent))
            self.library.addItem(item)
        self.library_query.setVisible(bool(paths))
        self.library_import.setVisible(bool(paths))
        self.open_source_button.setVisible(bool(paths))
        self.filter_library()
        self.library.setCurrentRow(0)

    def filter_library(self, *_):
        query = self.library_query.text().casefold().strip()
        count = 0
        for index in range(self.library.count()):
            item = self.library.item(index)
            item.setHidden(query not in item.text().casefold())
            count += not item.isHidden()
        self.library_stack.setCurrentIndex(0 if count else 2 if self.library.count() else 1)
        self.library_selection()

    def library_selection(self, *_):
        item = self.library.currentItem()
        self.open_source_button.setEnabled(item is not None and not item.isHidden())

    def open_library_item(self, *_):
        item = self.library.currentItem()
        if item and not item.isHidden():
            self.load_source(item.data(Qt.ItemDataRole.UserRole))

    def import_source(self):
        from .conversation_dialog import ConversationImportDialog
        dialog = ConversationImportDialog(self.workspace, self.root)
        self.import_dialog = dialog
        def returned(*_):
            from shiboken6 import isValid
            if isValid(self):
                self.show()
                self.refresh_library()
        dialog.finished.connect(returned)
        self.hide()
        dialog.show()

    def choose_source(self):
        path, _ = QFileDialog.getOpenFileName(self, '開啟對話', str(self.document.parent), '對話 (*.json)')
        if path:
            self.load_source(Path(path))

    def load_source(self, path):
        if self.request:
            return
        if not Path(path).is_relative_to(self.root):
            self.status.setText('請先匯入這份對話。')
            return
        self.run(lambda cancel, progress: review.Source.load(path, cancel), self.source_loaded)

    def source_loaded(self, source):
        self.source_data = source
        self.source_title.setText(source.item.title.replace('\n', ' '))
        self.source_title.setToolTip(source.item.title)
        self.branches.blockSignals(True)
        self.branches.clear()
        for number, leaf in enumerate(source.item.leaves, 1):
            self.branches.addItem(f'分支 {number}', leaf)
        selected = self.branches.findData(source.item.current)
        import re
        match = re.match(r'分支-(\d+)-', self.document.name)
        if match and 1 <= int(match[1]) <= len(source.item.leaves):
            selected = int(match[1]) - 1
        self.branches.setCurrentIndex(max(0, selected))
        self.branches.blockSignals(False)
        self.branches.setVisible(self.branches.count() > 1)
        if self.pages.currentIndex() == 0:
            bounds = self.screen().availableGeometry()
            self.resize(min(1040, bounds.width() - 40), min(760, bounds.height() - 40))
        self.pages.setCurrentIndex(1)
        self.branch_changed()

    def branch_changed(self, *_):
        self.pins.clear()
        self.update_pins()
        self.search()

    def search(self):
        if not self.source_data:
            return
        source = self.source_data
        args = dict(query=self.query.text(), branch=self.branches.currentData() if self.scope.currentIndex() == 0 else None,
                    role_filter=self.role_filter.currentData(), since=self.since.text().strip(), until=self.until.text().strip())
        def find(cancel, progress):
            source.verify()
            return source.search(**args, cancel=cancel)
        self.run(find, self.searched)

    def searched(self, results):
        self.search_results = results
        self.result_page = 0
        self.render_results()

    def page_results(self, delta):
        self.result_page = max(0, min((max(1, len(self.search_results)) - 1) // 200, self.result_page + delta))
        self.render_results()

    def render_results(self):
        self.results.clear()
        for hit in self.search_results[self.result_page * 200:(self.result_page + 1) * 200]:
            item = QListWidgetItem(ROLE_NAMES.get(hit['role'], hit['role']) + '\n' + hit['body'][:90].replace('\n', ' '))
            item.setData(Qt.ItemDataRole.UserRole, hit)
            self.results.addItem(item)
        count = len(self.search_results)
        self.result_count.setText(f'{count} 則訊息')
        self.result_stack.setCurrentIndex(0 if count else 1)
        self.previous_page.setVisible(count > 200)
        self.next_page.setVisible(count > 200)
        self.previous_page.setEnabled(self.result_page > 0)
        self.next_page.setEnabled((self.result_page + 1) * 200 < count)
        self.original.setVisible(bool(count))
        self.pin_button.setVisible(bool(count))
        self.results.setCurrentRow(0)

    def show_context(self, *_):
        item = self.results.currentItem()
        self.context_branch.blockSignals(True)
        self.context_branch.clear()
        if item:
            for branch in item.data(Qt.ItemDataRole.UserRole)['branches']:
                number = self.source_data.item.leaves.index(branch) + 1
                self.context_branch.addItem(f'分支 {number}', branch)
            index = self.context_branch.findData(self.branches.currentData())
            self.context_branch.setCurrentIndex(max(index, 0))
        self.context_branch.blockSignals(False)
        self.context_branch.setVisible(self.context_branch.count() > 1)
        self.render_context()

    def context_text(self, key, branch):
        keys = self.source_data.scope(branch)
        index = keys.index(key)
        start, end = index, index + 1
        mapping = self.source_data.item.raw['mapping']
        while start > 0 and review.role(mapping[keys[start]]) != 'user':
            start -= 1
        while end < len(keys) and review.role(mapping[keys[end]]) != 'user':
            end += 1
        return '\n\n'.join(ROLE_NAMES.get(review.role(mapping[k]), review.role(mapping[k])) + '\n' + review.message_body(mapping[k])
                           for k in keys[start:end] if mapping[k].get('message'))

    def render_context(self, *_):
        item = self.results.currentItem()
        if not item or self.context_branch.currentData() is None:
            self.original.clear()
            return
        hit = item.data(Qt.ItemDataRole.UserRole)
        self.original.setPlainText(self.context_text(hit['node'], self.context_branch.currentData())[:2000000])
        self.pin_button.setText('取消釘選' if hit['node'] in self.pins else '釘選原文')

    def pin(self):
        item = self.results.currentItem()
        if not item:
            return
        key = item.data(Qt.ItemDataRole.UserRole)['node']
        if key not in self.source_data.scope(self.branches.currentData()):
            self.status.setText('請先切換到這則訊息的分支。')
            return
        if key in self.pins:
            self.pins.remove(key)
        else:
            self.pins.add(key)
        self.update_pins()
        self.render_context()

    def update_pins(self):
        self.pin_label.setText(f'已釘選 {len(self.pins)} 則原文' if self.pins else '')
        self.clear_pin_button.setVisible(bool(self.pins))

    def clear_pins(self):
        self.pins.clear()
        self.update_pins()
        self.render_context()

    def prepare_packets(self):
        if not self.source_data or self.request:
            return
        source, branch = self.source_data, self.branches.currentData()
        args = (source, branch, self.purpose.currentText(), self.length.currentText(), tuple(sorted(self.pins)), self.capacity.value())
        self.run(lambda cancel, progress: review.packets(*args), self.packets_ready)

    def packets_ready(self, result):
        self.request, self.chunks, self.packet_texts = result
        self.source_button.hide()
        self.branches.setEnabled(False)
        self.pin_button.setEnabled(False)
        self.chunk.clear()
        for index in range(len(self.packet_texts)):
            self.chunk.addItem(f'第 {index + 1} 段', index)
        self.exchange_pages.setCurrentIndex(1)
        self.update_segments()
        self.copy_packet_button.setFocus()

    def show_packet(self, *_):
        if not self.request or self.chunk.currentIndex() < 0:
            return
        if self.active_chunk >= 0:
            self.reply_drafts[self.active_chunk] = self.reply.toPlainText()
        self.active_chunk = self.chunk.currentIndex()
        self.prompt.setPlainText(self.packet_texts[self.active_chunk])
        self.packet_preview.setPlainText('\n\n'.join(ROLE_NAMES.get(part['role'], part['role']) + '\n' + part['text']
                                                    for part in self.chunks[self.active_chunk]))
        self.reply.setPlainText(self.reply_drafts.get(self.active_chunk, ''))
        self.copy_packet_button.setText('複製內容')
        self.update_reply_action()

    def copy_packet(self):
        if self.prompt.toPlainText():
            QApplication.clipboard().setText(self.prompt.toPlainText())
            self.copy_packet_button.setText('已複製')

    def paste_reply(self):
        self.reply.setPlainText(QApplication.clipboard().text())
        self.reply.setFocus()

    def update_reply_action(self):
        received = self.active_chunk + 1 in self.received
        self.accept_button.setText('已加入' if received else '加入整理稿')
        self.accept_button.setEnabled(bool(self.reply.toPlainText().strip()) and not received)

    def update_segments(self):
        total = self.request['chunk_count']
        self.segment_count.setText(f'已完成 {len(self.received)} / {total} 段')
        for index in range(self.chunk.count()):
            self.chunk.setItemText(index, f'第 {index + 1} 段' + (' · 已加入' if index + 1 in self.received else ''))
        self.review_button.setVisible(bool(self.received))
        self.tabs.setTabEnabled(2, bool(self.received))
        self.update_reply_action()

    def accept_reply(self):
        try:
            if not self.request or self.chunks is None:
                raise ValueError('請先準備整理。')
            chunk, items = review.accept_reply(self.reply.toPlainText(), self.request, self.chunks, self.source_data)
            if chunk in self.received:
                raise ValueError('這一段已加入整理稿。')
            self.received.add(chunk)
            self.items.extend(items)
            self.record = None
            self.render_candidates()
            self.update_segments()
            self.document_pages.setCurrentIndex(0)
            remaining = [i for i in range(self.chunk.count()) if i + 1 not in self.received]
            if remaining:
                self.chunk.setCurrentIndex(remaining[0])
                self.status.setText(f'第 {chunk} 段已加入。')
            else:
                self.tabs.setCurrentIndex(2)
                self.candidates.setFocus()
        except (ValueError, OSError, KeyError) as error:
            self.status.setText(str(error))

    def candidate_label(self, item):
        return item['text'][:120].replace('\n', ' ')

    def compact_label(self, index, item):
        return f'{index + 1} / {len(self.items)} · {item["text"][:60].replace(chr(10), " ")}'

    def render_candidates(self):
        self.candidates.blockSignals(True)
        self.compact_items.blockSignals(True)
        selected = self.candidates.currentRow()
        self.candidates.clear()
        self.compact_items.clear()
        for index, item in enumerate(self.items, 1):
            self.candidates.addItem(self.candidate_label(item))
            self.compact_items.addItem(self.compact_label(index - 1, item))
        self.candidates.blockSignals(False)
        self.compact_items.blockSignals(False)
        self.candidates.setCurrentRow(max(0, min(selected, len(self.items) - 1)))
        self.update_review_count()
        self.show_candidate()

    def update_review_count(self):
        self.item_count.setText(f'{len(self.items)} 項內容')

    def toggle_source(self):
        self.source_panel.setVisible(self.source_toggle.isChecked())
        self.source_toggle.setText('收起原文' if self.source_toggle.isChecked() else '查看原文')

    def change_category(self, category):
        if self.loading_item or self.candidates.currentRow() < 0:
            return
        self.items[self.candidates.currentRow()]['category'] = category
        self.edit_candidate()

    def show_candidate(self, *_):
        index = self.candidates.currentRow()
        self.loading_item = True
        for control in (self.item_options, self.source_toggle, self.item_text, self.checked, self.citation_button):
            control.setEnabled(index >= 0)
        self.citation_pages.setCurrentIndex(0)
        if index >= 0:
            item = self.items[index]
            self.compact_items.blockSignals(True)
            self.compact_items.setCurrentIndex(index)
            self.compact_items.blockSignals(False)
            self.category_actions[item['category']].setChecked(True)
            self.item_text.setPlainText(item['text'])
            self.checked.setChecked(bool(item.get('checked')))
            try:
                texts = list(dict.fromkeys(self.context_text(key, self.request['branch']) for key in item['sources']))
                self.citation_text.setPlainText('\n\n'.join(texts))
            except (ValueError, KeyError):
                self.citation_text.setPlainText('找不到引用原文')
        else:
            self.item_text.clear()
            self.citation_text.clear()
        self.loading_item = False

    def edit_candidate(self, *_):
        if self.loading_item or self.candidates.currentRow() < 0:
            return
        index = self.candidates.currentRow()
        item = self.items[index]
        item.update(text=self.item_text.toPlainText(), edited=True, checked=False)
        self.record = None
        self.checked.blockSignals(True)
        self.checked.setChecked(False)
        self.checked.blockSignals(False)
        self.candidates.item(index).setText(self.candidate_label(item))
        self.compact_items.setItemText(index, self.compact_label(index, item))
        self.update_review_count()

    def mark_checked(self, value):
        if not self.loading_item and self.candidates.currentRow() >= 0:
            index = self.candidates.currentRow()
            self.items[index]['checked'] = value
            # This is saved metadata; it does not alter the generated document.

    def choose_citations(self):
        index = self.candidates.currentRow()
        if index < 0:
            return
        self.source_toggle.setChecked(True)
        self.toggle_source()
        selected = self.items[index]['sources']
        self.citation_choices.clear()
        for key in self.source_data.scope(self.request['branch']):
            node = self.source_data.item.raw['mapping'][key]
            if not node.get('message'):
                continue
            item = QListWidgetItem(ROLE_NAMES.get(review.role(node), review.role(node)) + ' · ' + review.message_body(node)[:120].replace('\n', ' '))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if key in selected else Qt.CheckState.Unchecked)
            self.citation_choices.addItem(item)
        self.citation_pages.setCurrentIndex(1)

    def apply_citations(self):
        refs = [self.citation_choices.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.citation_choices.count())
                if self.citation_choices.item(i).checkState() == Qt.CheckState.Checked]
        if not refs:
            self.status.setText('請選擇至少一則原文。')
            return
        index = self.candidates.currentRow()
        self.items[index].update(sources=refs, checked=False, edited=True)
        self.record = None
        self.candidates.item(index).setText(self.candidate_label(self.items[index]))
        self.show_candidate()
        self.update_review_count()

    def build_record(self):
        if not self.request:
            raise ValueError('尚無整理稿。')
        if any(item['category'] not in review.CATEGORIES or not item['text'].strip() or not item['sources'] for item in self.items):
            raise ValueError('整理項目的內容或引用尚未完成。')
        return review.candidate(self.request, self.items, self.received, self.previous)

    def compose(self):
        try:
            if self.record is not None and self.has_output:
                self.document_pages.setCurrentIndex(1)
                return
            record = self.build_record()
            text = review.continuation(record, self.source_data, self.maximum.value())
            self.pending_output = (record, text)
            if self.has_output and self.output.toPlainText() != self.generated_text:
                self.update_row.show()
                for control in (self.copy_output_button, self.export_button, self.save_button):
                    control.setEnabled(False)
            else:
                self.resolve_output(True)
            self.document_pages.setCurrentIndex(1)
            self.status.clear()
        except (ValueError, OSError, KeyError) as error:
            self.status.setText(str(error))

    def resolve_output(self, rebuild):
        if self.pending_output is None:
            return
        self.record, self.generated_text = self.pending_output
        if rebuild:
            if self.has_output:
                cursor = self.output.textCursor()
                cursor.beginEditBlock()
                cursor.select(QTextCursor.SelectionType.Document)
                cursor.insertText(self.generated_text)
                cursor.endEditBlock()
            else:
                self.output.setPlainText(self.generated_text)
        self.has_output = True
        self.pending_output = None
        self.update_row.hide()
        for control in (self.copy_output_button, self.export_button, self.save_button):
            control.setEnabled(True)

    def checked_output(self):
        if self.record is None:
            raise ValueError('內容已變更，請重新組成整理稿。')
        record = self.build_record()
        review.continuation(record, self.source_data, self.maximum.value())
        text = self.output.toPlainText()
        if not text.strip():
            raise ValueError('整理稿是空白的。')
        if len(text) > self.maximum.value():
            raise ValueError('整理稿超過字元上限。')
        for key in record['request'].get('pins', []):
            if review.message_body(self.source_data.item.raw['mapping'][key]) not in text:
                raise ValueError('整理稿缺少釘選原文，請返回項目並重新組成。')
        return record, text

    def copy_output(self):
        try:
            _, text = self.checked_output()
            QApplication.clipboard().setText(text)
            self.status.setText('已複製')
        except (ValueError, OSError, KeyError) as error:
            self.status.setText(str(error))

    def save(self):
        try:
            record, text = self.checked_output()
            source = self.source_data
            self.run(lambda cancel, progress: review.save_candidate(source, record, text, cancel, progress), self.saved)
        except (ValueError, OSError, KeyError) as error:
            self.status.setText(str(error))

    def saved(self, folder):
        self.status.setText('已儲存新版本')
        self.status.setToolTip(str(folder / '整理.md'))

    def choose_candidate(self):
        if not self.source_data or self.request:
            return
        path, _ = QFileDialog.getOpenFileName(self, '開啟整理稿', str(self.source_data.path.parent), '整理稿 (整理.json)')
        if path:
            def load(cancel, progress):
                record = load_record(Path(path), 'conversation-summary')
                review.validate_candidate(record)
                text = read_local(Path(path).parent / '整理.md', 8 * 1024 * 1024, cancel).decode('utf-8-sig')
                return record, text
            self.run(load, self.candidate_loaded)

    def candidate_loaded(self, result):
        record, text = result
        if record['request']['conversation_id'] != self.source_data.item.key:
            raise ValueError('這份整理稿屬於另一段對話。')
        branch = self.branches.findData(record['request']['branch'])
        if branch >= 0:
            self.branches.blockSignals(True)
            self.branches.setCurrentIndex(branch)
            self.branches.blockSignals(False)
        self.previous = deepcopy(record)
        self.request = record['request']
        self.items = deepcopy(record['items'])
        self.received = set(record['received'])
        self.record = deepcopy(record)
        self.has_output = True
        # Loaded Markdown is user-owned. Any subsequent structural change must
        # offer preservation even if no keystroke has occurred in this session.
        self.generated_text = None
        self.output.setPlainText(text)
        self.source_button.hide()
        self.branches.setEnabled(False)
        self.pin_button.setEnabled(False)
        self.tabs.setTabEnabled(2, True)
        self.tabs.setTabEnabled(1, False)
        self.render_candidates()
        self.document_pages.setCurrentIndex(1)
        self.tabs.setCurrentIndex(2)
        state = review.freshness(record, self.source_data, self.branches.currentData())
        if state.startswith(('不同', '來源已更新')):
            self.status.setText(state)

    def export_md(self):
        try:
            _, text = self.checked_output()
        except (ValueError, OSError, KeyError) as error:
            self.status.setText(str(error))
            return
        path, _ = QFileDialog.getSaveFileName(self, '另存 Markdown', str(self.document.parent / '整理.md'), 'Markdown (*.md)')
        if not path:
            return
        try:
            from .local_documents.directory_pin import pinned_target
            from .retained_files import _write
            destination = Path(path)
            with pinned_target(destination.parent, destination.name) as target:
                _write(target, text.encode('utf-8'))
            self.status.setText('已另存 ' + destination.name)
        except (ValueError, OSError) as error:
            self.status.setText(str(error))


def open_review(surface):
    sheet = ReviewSheet(surface)
    surface.review_sheet = sheet
    sheet.show()
    return sheet
