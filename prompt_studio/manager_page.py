"""Responsive Manager page backed by real capability and operation records."""
from math import ceil
from PySide6.QtCore import Qt, QTimer, QUrl, QSize, QPointF
from PySide6.QtGui import QDesktopServices, QPainter, QTextLayout, QTextOption
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QBoxLayout, QStackedWidget,
    QLineEdit, QCheckBox, QButtonGroup, QListWidgetItem, QSizePolicy, QLabel)

from .manager_gallery import PackageGallery, DATA_ROLE, package_status
from .manager_client import ManagerClient
from .manager_protocol import is_manager_package
from .widgets import (label, button, row, panel, scrolling, ActionHeader, StudioDialog,
                      ComboBox as QComboBox)


STATE_NAMES = {'not_sent': '未送出', 'submitting': '送出中', 'queued': '已排入工作',
               'running': '更新中', 'unknown': '結果待確認', 'pending_restart': '已更新 · 待重啟',
               'skipped': '無需更新', 'failed': '更新失敗'}


def decorated(text, callback, symbol, role=None):
    control = button(text, callback, role)
    from .ui_icons import icon
    control.setIcon(icon(symbol, color='on-accent' if role in ('Primary', 'Run') else None))
    return control


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


class PackageDetailText(QLabel):
    """Wrap package IDs and hashes without changing their original text."""
    def __init__(self, text, role=None):
        super().__init__(text)
        if role:
            self.setObjectName(role)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setMinimumWidth(0)
        policy = self.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        self.setSizePolicy(policy)
        self.setToolTip(text)
        self.setAccessibleName(text)

    def text_layout(self, width):
        layout = QTextLayout(self.text().replace('\n', '\u2028'), self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        height = 0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, width))
            line.setPosition(QPointF(0, height))
            height += line.height()
        layout.endLayout()
        return layout, ceil(height)

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height())

    def heightForWidth(self, width):
        margins = self.contentsMargins()
        return self.text_layout(width - margins.left() - margins.right())[1] + margins.top() + margins.bottom()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setClipRect(self.contentsRect())
        self.text_layout(self.contentsRect().width())[0].draw(painter, QPointF(self.contentsRect().topLeft()))


class ManagerPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.client = ManagerClient(window.store.directory, self, activity=self.generation_busy)
        self.selected = set()
        self.detail_id = None
        self.view = 'packages'
        self.rendering = False
        self.setObjectName('ManagerPage')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 16)
        outer.setSpacing(14)
        self.location = label('', 'Subtle')
        self.location.hide()
        self.refresh_button = decorated('', self.refresh, 'refresh', 'Quiet')
        self.refresh_button.setAccessibleName('重新整理套件')
        self.refresh_button.setToolTip('重新整理套件')
        self.refresh_button.setFixedWidth(36)
        self.heading = label('管理與更新', 'DialogTitle')
        # The existing confirmation dialog owns the core/package update scope.
        self.summary = QWidget(self)
        self.summary.hide()
        self.summary_text = label('', 'Subtle')
        self.summary_text.setParent(self.summary)
        self.scope = QComboBox(self.summary)
        self.scope.addItem('已啟用節點', False)
        self.scope.addItem('ComfyUI＋已啟用節點', True)
        self.scope.hide()
        self.update_all = decorated('更新全部', self.confirm_all, 'refresh', 'Primary')
        self.update_all.setMinimumHeight(36)
        self.header = ActionHeader(self.heading, self.refresh_button, self.update_all)
        outer.addWidget(self.header)

        self.tabs = QButtonGroup(self)
        nav = QHBoxLayout()
        for key, text, symbol in (('packages', '節點套件', 'package'), ('extension', 'PCS 擴充', 'workflow'), ('automatic', '自動更新', 'clock'),
                                  ('tools', '工具', 'sliders'), ('history', '紀錄', 'history')):
            control = decorated(text, lambda _=False, key=key: self.show_view(key), symbol, 'SideNavigation')
            control.setCheckable(True)
            control.setChecked(key == self.view)
            self.tabs.addButton(control)
            control.setProperty('managerView', key)
            nav.addWidget(control)
        nav.addStretch()
        self.tab_widget = QWidget()
        self.tab_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.tab_widget.setMinimumWidth(0)
        self.tab_widget.setLayout(nav)
        nav.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.tab_widget)
        self.compact_tabs = QComboBox()
        for key, title in (('packages', '節點套件'), ('extension', 'PCS 擴充'), ('automatic', '自動更新'), ('tools', '工具'), ('history', '操作紀錄')):
            self.compact_tabs.addItem(title, key)
        self.compact_tabs.setAccessibleName('Manager 頁面')
        self.compact_tabs.currentIndexChanged.connect(lambda: self.show_view(self.compact_tabs.currentData()))
        self.compact_tabs.hide()
        outer.addWidget(self.compact_tabs)
        self.message = label('', 'Subtle', True)
        outer.addWidget(self.message)
        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)
        self.package_page = self.make_packages()
        self.pages.addWidget(self.package_page)
        from .extension_page import ExtensionPage
        self.extension = ExtensionPage(window)
        self.pages.addWidget(scrolling(self.extension))
        self.auto_page = self.make_auto()
        self.pages.addWidget(self.auto_page)
        self.tools_page = self.make_tools()
        self.pages.addWidget(self.tools_page)
        self.history_content = QWidget()
        self.history_layout = QVBoxLayout(self.history_content)
        self.history_page = scrolling(self.history_content)
        self.pages.addWidget(self.history_page)
        self.client.changed.connect(self.render)
        window.comfy.stateChanged.connect(self.connection_changed)
        self.connection_changed()

    def make_packages(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.filter = QComboBox(page)
        self.filter.hide()
        self.categories = QButtonGroup(self)
        self.category_bar = QWidget()
        categories = QHBoxLayout(self.category_bar)
        categories.setContentsMargins(0, 0, 0, 0)
        categories.setSpacing(6)
        for index, (text, key) in enumerate((('全部', 'all'), ('已啟用', 'enabled'), ('已停用', 'disabled'), ('固定版本', 'pinned'))):
            self.filter.addItem(text, key)
            control = button(text, lambda _=False, i=index: self.filter.setCurrentIndex(i), 'FilterChip')
            control.setCheckable(True)
            control.setChecked(index == 0)
            self.categories.addButton(control, index)
            categories.addWidget(control)
        categories.addStretch()
        self.filter.currentIndexChanged.connect(self.render_packages)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜尋套件名稱或作者')
        self.search.setAccessibleName('搜尋已安裝套件')
        from .ui_icons import icon
        self.search.addAction(icon('search'), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.render_packages)
        self.grid_button = decorated('', lambda: self.set_package_view(True), 'grid', 'IconButton')
        self.grid_button.setCheckable(True)
        self.grid_button.setChecked(True)
        self.grid_button.setToolTip('卡片檢視')
        self.grid_button.setAccessibleName('卡片檢視')
        self.grid_button.setFixedSize(36, 36)
        self.list_button = decorated('', lambda: self.set_package_view(False), 'list', 'IconButton')
        self.list_button.setCheckable(True)
        self.list_button.setToolTip('清單檢視')
        self.list_button.setAccessibleName('清單檢視')
        self.list_button.setFixedSize(36, 36)
        self.batch_button = button('多選', self.toggle_batch, 'Quiet')
        self.batch_button.setCheckable(True)
        self.filter_bar = QWidget()
        self.filters = QHBoxLayout(self.filter_bar)
        self.filters.setContentsMargins(0, 0, 0, 0)
        self.filters.addWidget(self.search, 1)
        self.count = label('', 'Subtle')
        self.filters.addWidget(self.count)
        self.filters.addWidget(self.batch_button)
        self.filters.addWidget(self.grid_button)
        self.filters.addWidget(self.list_button)
        layout.addWidget(self.filter_bar)
        layout.addWidget(self.category_bar)
        self.selection_bar = QWidget()
        self.selection_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.selection_bar)
        self.selection_layout.setContentsMargins(0, 0, 0, 0)
        self.selection_text = label('')
        self.selection_layout.addWidget(self.selection_text)
        self.selection_layout.addStretch()
        self.selected_update = decorated('更新所選', self.confirm_selected, 'refresh', 'Primary')
        self.selection_layout.addWidget(self.selected_update)
        self.selection_layout.addWidget(button('取消選取', self.clear_selection, 'Quiet'))
        layout.addWidget(self.selection_bar)
        self.content = QHBoxLayout()
        self.content.setSpacing(18)
        layout.addLayout(self.content, 1)
        self.list_stack = QStackedWidget()
        self.content.addWidget(self.list_stack, 1)
        self.gallery = PackageGallery()
        self.gallery.openRequested.connect(self.open_detail)
        self.gallery.checkRequested.connect(self.select)
        self.list_stack.addWidget(self.gallery)
        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(16, 24, 16, 0)
        self.empty_title = label('尚未讀取套件', 'Heading', True)
        self.empty_description = label('連接 ComfyUI 後，按「重新整理」。', 'Subtle', True)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_description)
        self.open_manager = decorated('連線與工作流', lambda: self.window.settings('workflows'), 'workflow', 'Primary')
        empty_layout.addWidget(self.open_manager, 0, Qt.AlignmentFlag.AlignLeft)
        empty_layout.addStretch()
        self.list_stack.addWidget(self.empty)
        self.detail, self.detail_layout = panel('InsetPanel')
        self.detail.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.detail_layout.setContentsMargins(20, 18, 20, 18)
        self.detail_layout.setSpacing(14)
        detail_container = QWidget()
        detail_container_layout = QVBoxLayout(detail_container)
        # Align the card surface with the gallery delegate's four-pixel inset.
        detail_container_layout.setContentsMargins(0, 4, 0, 12)
        detail_container_layout.setSpacing(0)
        detail_container_layout.addWidget(self.detail, 0, Qt.AlignmentFlag.AlignTop)
        detail_container_layout.addStretch()
        self.detail_scroll = scrolling(detail_container)
        self.detail_scroll.setMinimumWidth(0)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content.addWidget(self.detail_scroll)
        self.detail_scroll.hide()
        return page

    def set_package_view(self, grid):
        self.gallery.set_mode(grid)
        self.grid_button.setChecked(grid)
        self.list_button.setChecked(not grid)

    def toggle_batch(self):
        self.gallery.batch_mode = self.batch_button.isChecked()
        self.gallery.viewport().update()

    def make_auto(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        frame, body = panel('SettingsGroup')
        body.addWidget(label('其他節點套件', 'Heading'))
        body.addWidget(label('排程尚未提供', 'Subtle'))
        body.addWidget(label('目前可手動更新全部或所選套件。固定版本會排除於更新範圍。', None, True))
        body.addWidget(button('管理套件', lambda: self.show_view('packages'), 'Quiet'), 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(button('PCS 配套擴充的自動更新', lambda: self.show_view('extension'), 'Quiet'), 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(frame)
        layout.addStretch()
        return scrolling(content)

    def make_tools(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        frame, body = panel('SettingsGroup')
        frame.setMaximumWidth(920)
        body.addWidget(label('在 ComfyUI 管理環境','DialogTitle'))
        for title, description in (
            ('安裝與修復', '缺少節點 · Git 套件 · Python 套件'),
            ('版本與環境', '重新啟動 · 環境快照 · 主程式版本')):
            body.addWidget(label(title, 'Heading'))
            body.addWidget(label(description, 'Subtle', True))
        body.addWidget(decorated('開啟 ComfyUI', self.open_comfy, 'external-link','Primary'), 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(frame)
        layout.addStretch()
        return scrolling(content)

    def connection_changed(self):
        self.client.set_server(self.window.comfy.url, bool(self.window.comfy.enabled))
        self.location.setText('ComfyUI · ' + (self.client.server or '未連線'))
        self.render()

    def refresh(self):
        self.connection_changed()
        self.client.refresh()

    def show_view(self, key):
        self.view = key
        self.compact_tabs.blockSignals(True)
        self.compact_tabs.setCurrentIndex(self.compact_tabs.findData(key))
        self.compact_tabs.blockSignals(False)
        for control in self.tabs.buttons():
            control.setChecked(control.property('managerView') == key)
        self.pages.setCurrentIndex(('packages', 'extension', 'automatic', 'tools', 'history').index(key))
        self.update_all.setVisible(key == 'packages' and bool(self.client.available or self.client.packages))
        if key == 'history':
            self.render_history()

    def open_comfy(self):
        if self.client.server:
            QDesktopServices.openUrl(QUrl(self.client.server))
        else:
            self.window.notice('請先設定 ComfyUI 連線')

    def render(self):
        if self.rendering:
            return
        self.rendering = True
        self.message.setText(self.client.message)
        repeated = {'連接 ComfyUI 後，查看已安裝套件', '請先連接 ComfyUI', '按「重新整理」讀取套件',
                    f'{len(self.client.packages)} 個已安裝套件'}
        self.message.setVisible(bool(self.client.message) and self.client.message not in repeated)
        self.refresh_button.setEnabled(bool(self.client.server) and not self.client.busy)
        unresolved = bool(self.client.journal.unresolved(self.client.server))
        enabled = self.client.can_update and not self.client.busy and not unresolved
        self.update_all.setEnabled(enabled)
        self.scope.setEnabled(enabled)
        self.selected_update.setEnabled(enabled)
        active = sum(1 for pack in self.client.packages if pack['enabled'])
        pins = self.client.journal.pins(self.client.server)
        self.summary_text.setText((f'{active} 個已啟用套件' + (f' · {len(pins)} 項固定版本' if pins else ''))
                                 if self.client.available else '尚未連線' if not self.client.server else '尚未讀取套件')
        self.update_all.setVisible(self.view == 'packages' and bool(self.client.available or self.client.packages))
        self.render_packages()
        if self.detail_id:
            self.render_detail()
        if self.view == 'history':
            self.render_history()
        self.rendering = False

    def visible_packages(self):
        text = self.search.text().strip().casefold()
        mode = self.filter.currentData()
        pins = self.client.journal.pins(self.client.server)
        return [pack for pack in self.client.packages
                if (not text or text in (pack['name'] + ' ' + pack['id'] + ' ' + pack['author']).casefold())
                and (mode == 'all' or mode == 'enabled' and pack['enabled'] or mode == 'disabled' and not pack['enabled']
                     or mode == 'pinned' and pack['id'] in pins)]

    def can_select(self, pack):
        return (self.client.can_update and pack['enabled'] and pack['id'] not in self.client.journal.pins(self.client.server)
                and bool(pack['cnr_id'] or pack['aux_id']) and not is_manager_package(pack))

    def pack_action(self, pack):
        control = button('更新', lambda: self.confirm_ids([pack['id']]))
        control.setEnabled(self.can_select(pack) and not self.client.busy and not self.client.journal.unresolved(self.client.server))
        if not self.can_select(pack):
            control.setToolTip(self.exclusion(pack))
        return control

    def exclusion(self, pack):
        if pack['id'] in self.client.journal.pins(self.client.server):
            return '此套件已固定版本'
        if not pack['enabled']:
            return '套件已停用'
        if not pack['cnr_id'] and not pack['aux_id']:
            return '無法確認安裝來源'
        if is_manager_package(pack):
            return '請從 ComfyUI 更新 Manager'
        return '此 Manager 版本的更新請在 ComfyUI 操作'

    def render_packages(self, *_):
        packages = self.visible_packages()
        self.selected.intersection_update({pack['id'] for pack in self.client.packages if self.can_select(pack)})
        self.categories.button(self.filter.currentIndex()).setChecked(True)
        pins = self.client.journal.pins(self.client.server)
        current = self.gallery.currentItem()
        current_id = current.data(DATA_ROLE)['pack']['id'] if current else self.detail_id
        position = self.gallery.verticalScrollBar().value()
        self.gallery.blockSignals(True)
        self.gallery.clear()
        locked = self.client.busy or bool(self.client.journal.unresolved(self.client.server))
        for pack in packages:
            item = QListWidgetItem(pack['name'])
            selectable = self.can_select(pack) and not locked
            item.setData(DATA_ROLE, dict(pack=pack, pinned=pack['id'] in pins, selectable=selectable))
            if not selectable:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            status, markers = package_status(pack, pack['id'] in pins)
            item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, ' · '.join((pack['version'], status, *markers)))
            source = pack['aux_id'] or pack['cnr_id']
            item.setToolTip('\n'.join(value for value in (pack['name'], pack['version'], pack['author'], source,
                                                          ' · '.join((status, *markers))) if value))
            item.setCheckState(Qt.CheckState.Checked if pack['id'] in self.selected else Qt.CheckState.Unchecked)
            self.gallery.addItem(item)
            if pack['id'] == current_id:
                self.gallery.setCurrentItem(item)
        self.gallery.blockSignals(False)
        self.gallery.verticalScrollBar().setValue(position)
        self.refresh_selection()
        self.count.setText(f'{len(packages)} 個套件' + (f' / 共 {len(self.client.packages)} 個' if len(packages) != len(self.client.packages) else ''))
        available = bool(self.client.available or self.client.packages)
        self.count.setVisible(available)
        self.filter_bar.setVisible(available)
        self.category_bar.setVisible(available)
        if not packages:
            self.empty_title.setText('找不到符合的套件' if self.client.packages else '尚未安裝節點套件' if self.client.available else '尚無套件清單')
            self.empty_description.setText('試試其他名稱或篩選條件。' if self.client.packages else
                                          '可在「工具」開啟 ComfyUI Manager 尋找套件。' if self.client.available else
                                          '連接 ComfyUI 後，按「重新整理」。')
        self.arrange()

    def refresh_selection(self):
        self.selection_bar.setVisible(bool(self.selected))
        self.selection_text.setText(f'已選 {len(self.selected)} 項')
        self.selected_update.setText(f'更新所選（{len(self.selected)}）')
        role = 'Quiet' if self.selected else 'Primary'
        if self.update_all.objectName() != role:
            from .ui_icons import icon
            self.update_all.setObjectName(role)
            self.update_all.setIcon(icon('refresh', color='on-accent' if role == 'Primary' else None))
            self.update_all.style().unpolish(self.update_all)
            self.update_all.style().polish(self.update_all)
            self.update_all.update()

    def select(self, ident, checked):
        pack = next((pack for pack in self.client.packages if pack['id'] == ident), None)
        if checked and (not pack or not self.can_select(pack) or self.client.busy or self.client.journal.unresolved(self.client.server)):
            checked = False
        (self.selected.add if checked else self.selected.discard)(ident)
        for index in range(self.gallery.count()):
            item = self.gallery.item(index)
            if item.data(DATA_ROLE)['pack']['id'] == ident:
                self.gallery.blockSignals(True)
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                self.gallery.blockSignals(False)
                break
        self.refresh_selection()

    def clear_selection(self):
        self.selected.clear()
        self.render_packages()

    def open_detail(self, ident):
        self.detail_id = ident
        for index in range(self.gallery.count()):
            item = self.gallery.item(index)
            if item.data(DATA_ROLE)['pack']['id'] == ident:
                self.gallery.setCurrentItem(item)
                break
        self.render_detail()
        self.arrange()

    def close_detail(self):
        self.detail_id = None
        self.arrange()

    def render_detail(self):
        clear_layout(self.detail_layout)
        pack = next((item for item in self.client.packages if item['id'] == self.detail_id), None)
        if not pack:
            self.detail_id = None
            self.arrange()
            return
        self.detail_back = decorated('返回套件', self.close_detail, 'arrow-left', 'Quiet')
        self.detail_layout.addWidget(self.detail_back, 0, Qt.AlignmentFlag.AlignLeft)
        self.detail_layout.addWidget(PackageDetailText(pack['name'], 'DialogTitle'))
        status, markers = package_status(pack, pack['id'] in self.client.journal.pins(self.client.server))
        self.detail_layout.addWidget(label(' · '.join((status, *markers)), 'Subtle', True))
        if pack['description']:
            self.detail_layout.addWidget(PackageDetailText(pack['description']))
        action = self.pack_action(pack)
        action.setText('更新此套件')
        action.setObjectName('Primary')
        self.detail_layout.addWidget(action)
        if not self.can_select(pack):
            self.detail_layout.addWidget(label(self.exclusion(pack), 'Subtle', True))
        self.detail_layout.addSpacing(8)
        self.detail_layout.addWidget(label('套件資訊', 'Heading'))
        for title, value in (('目前版本', pack['version']), ('作者', pack['author']),
                             ('安裝來源', 'Git · ' + pack['aux_id'] if pack['aux_id'] else
                              'Comfy Registry · ' + pack['cnr_id'] if pack['cnr_id'] else '無法確認安裝來源')):
            if value:
                field = label(title, 'Subtle')
                field.setFixedWidth(max(100, field.fontMetrics().horizontalAdvance(title)))
                field.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                content = PackageDetailText(value)
                metadata = QHBoxLayout()
                metadata.setSpacing(12)
                metadata.addWidget(field)
                metadata.addWidget(content, 1)
                self.detail_layout.addLayout(metadata)
        self.detail_layout.addSpacing(8)
        pin = QCheckBox('固定此版本')
        pin.setChecked(pack['id'] in self.client.journal.pins(self.client.server))
        pin.setEnabled(not self.client.busy and not self.client.journal.unresolved(self.client.server) and not self.client.journal.error)
        pin.toggled.connect(lambda value: self.set_pin(pack['id'], value))
        self.detail_layout.addWidget(pin)

    def set_pin(self, ident, enabled):
        try:
            self.client.journal.pin(self.client.server, ident, enabled)
        except (ValueError, OSError) as exc:
            self.window.notice(str(exc))
        QTimer.singleShot(0, self.render)

    def confirm_all(self):
        self.confirm_ids(None, self.scope.currentData())

    def confirm_selected(self):
        self.confirm_ids(sorted(self.selected))

    def confirm_ids(self, selected, include_core=False):
        if self.generation_busy():
            self.window.notice('請等目前的生成工作結束再更新')
            return
        try:
            plan = self.client.plan(selected, include_core)
        except ValueError as exc:
            if selected is not None:
                self.window.notice(str(exc))
                return
            plan = []
        dialog = StudioDialog(self.window)
        dialog.setWindowTitle('更新範圍')
        dialog.resize(530, min(650, 250 + len(plan) * 32))
        title = label(f'更新 {len(plan)} 項', 'DialogTitle')
        dialog.body.addWidget(title)
        scope = QCheckBox('同時更新 ComfyUI（穩定版）') if selected is None else None
        if scope:
            scope.setChecked(bool(include_core))
            dialog.body.addWidget(scope)
        names = QWidget()
        body = QVBoxLayout(names)
        dialog.body.addWidget(scrolling(names), 1)
        dialog.body.addWidget(label('完成後需重新啟動 ComfyUI。', 'Subtle', True))
        dialog.body.addWidget(label('使用本機 Manager 共用佇列。', 'Subtle', True))
        confirm = button('開始更新', dialog.accept, 'Primary')
        dialog.body.addLayout(row(None, button('取消', dialog.reject, 'Quiet'), confirm))

        def scope_changed():
            nonlocal plan
            try:
                plan = self.client.plan(selected, scope.isChecked() if scope else include_core)
            except ValueError:
                plan = []
            clear_layout(body)
            title.setText(f'更新 {len(plan)} 項')
            confirm.setEnabled(bool(plan))
            if not plan:
                body.addWidget(label('沒有可更新的已啟用套件', 'Subtle', True))
            for task in plan:
                body.addWidget(label(task['name'] + (' · ' + task['before'] if task['before'] else ' · 穩定版'), None, True))
        if scope:
            scope.toggled.connect(scope_changed)
        scope_changed()
        if dialog.exec():
            if self.generation_busy():
                self.window.notice('請等目前的生成工作結束再更新')
                return
            if scope:
                self.scope.setCurrentIndex(1 if scope.isChecked() else 0)
            self.client.submit(plan)
            self.show_view('history')

    def generation_busy(self):
        comfy = self.window.comfy
        if getattr(comfy, 'running', 0) or getattr(comfy, 'pending', 0) or getattr(comfy, 'run_id', ''):
            return True
        queue = getattr(comfy, 'queue', None)
        return bool(queue and queue.busy())

    def render_history(self):
        clear_layout(self.history_layout)
        ops = self.client.journal.operations(self.client.server)
        unresolved = self.client.journal.unresolved(self.client.server)
        if unresolved:
            self.history_layout.addWidget(ActionHeader(label('操作結果', 'Heading'),
                decorated('重新查詢', self.client.poll, 'refresh'),
                button('啟動本機 Manager 佇列', self.client.resume_queue)))
        if not ops:
            self.history_layout.addWidget(label('尚無操作紀錄', 'Heading'))
        for operation in reversed(ops):
            frame, body = panel('SettingsGroup')
            for task in operation['tasks']:
                body.addWidget(label(task['name'] + ' · ' + STATE_NAMES[task['state']], 'Heading', True))
                if task.get('message'):
                    body.addWidget(label(task['message'], 'Subtle', True))
            self.history_layout.addWidget(frame)
        if any(task['state'] == 'pending_restart' for op in ops for task in op['tasks']):
            self.history_layout.addWidget(decorated('開啟 ComfyUI 重新啟動', self.open_comfy, 'external-link'), 0, Qt.AlignmentFlag.AlignLeft)
        self.history_layout.addStretch()

    def arrange(self):
        if not hasattr(self, 'list_stack'):
            return
        compact = self.width() < 1100 or self.height() < 680
        self.location.hide()
        self.heading.setText('管理與更新')
        self.layout().setSpacing(14)
        self.layout().setContentsMargins(14 if compact else 24, 16 if compact else 24, 14 if compact else 24, 12)
        tabs_width = sum(control.sizeHint().width() for control in self.tabs.buttons()) + 8 * (len(self.tabs.buttons()) - 1) + 48
        compact_navigation = self.width() < max(540, tabs_width)
        self.tab_widget.setVisible(not compact_navigation)
        self.compact_tabs.setVisible(compact_navigation)
        self.selection_layout.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 540 else QBoxLayout.Direction.LeftToRight)
        self.list_stack.setCurrentWidget(self.empty if not self.visible_packages() else self.gallery)
        self.detail_scroll.setVisible(bool(self.detail_id))
        detail_only = bool(self.detail_id) and self.width() < 1040
        if self.detail_id and hasattr(self, 'detail_back'):
            from .ui_icons import icon
            self.detail_back.setText('返回套件' if detail_only else '關閉詳情')
            self.detail_back.setIcon(icon('arrow-left' if detail_only else 'close'))
        self.detail_layout.setContentsMargins(16 if detail_only else 20, 14 if detail_only else 18,
                                             16 if detail_only else 20, 14 if detail_only else 18)
        self.detail_layout.setSpacing(10 if detail_only else 14)
        self.list_stack.setVisible(not detail_only)
        available = bool(self.client.available or self.client.packages)
        self.filter_bar.setVisible(available and not detail_only)
        self.category_bar.setVisible(available and not detail_only)
        self.count.setVisible(available and not detail_only)
        self.detail_scroll.setMaximumWidth(16777215 if detail_only else 340)
        self.detail_scroll.setMinimumWidth(0 if detail_only else 310 if self.detail_id else 0)
        self.gallery.set_compact(self.width() < 1040 or self.height() < 680)
        self.gallery.reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange()

    def shutdown(self):
        self.extension.shutdown()
        self.client.shutdown()
