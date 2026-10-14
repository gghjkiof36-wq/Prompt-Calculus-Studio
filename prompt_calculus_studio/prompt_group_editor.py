"""One transactional material draft: overview, group, and library picker pages."""
import copy
from PySide6.QtCore import Qt, Signal, QSize, QRect, QTimer, QPoint, QEvent
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QFontMetrics
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPlainTextEdit, QStackedWidget, QSplitter, QListWidgetItem, QStyledItemDelegate,
    QStyle, QStyleOptionViewItem, QSpinBox, QCheckBox, QTabWidget, QTabBar, QScrollArea, QSizePolicy, QDialog)
from . import composition as comp, prompt_groups as groups, materials, material_navigation as nav
from .widgets import button, label, row, ComboBox, RoundMenu, InputDialog
from .material_editor_page import Disclosure
from .material_folder_navigation import FolderNavigation
from .prompt_editor import ReorderableList
from .ui_icons import icon, MODE_ICONS
from .views import widget_colors
from .canvas_weight import submitted_weight
from .module_chrome import dialog_colors
from .theme import visual_tokens
from .quiet_splitter import QuietSplitter
from .palette_cards import PickerCardDelegate
from .stacked_card_list import StackedCardList

DATA = Qt.ItemDataRole.UserRole + 2


def tool(name, caption, callback):
    result = button('', callback, 'Quiet')
    result.setIcon(icon(name)); result.setFixedWidth(34)
    result.setAccessibleName(caption); result.setToolTip(caption)
    return result


def page():
    widget = QWidget(); layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(12)
    return widget, layout


def field(title,control):
    widget,layout=page();layout.setSpacing(8)
    control.setMinimumHeight(max(38,control.sizeHint().height()))
    widget.setMaximumWidth(control.maximumWidth())
    layout.addWidget(label(title,'Subtle'));layout.addWidget(control)
    return widget


def mode_options(combo):
    for key,name in groups.MODES.items():combo.addItem(icon(MODE_ICONS[key]),name,key)


class PageStack(QStackedWidget):
    """Hidden editing pages must not impose their size on the current page."""
    def __init__(self):
        super().__init__(); self.currentChanged.connect(self.updateGeometry)

    def sizeHint(self):
        widget = self.currentWidget()
        return widget.sizeHint() if widget else QSize(0, 0)

    def minimumSizeHint(self):
        widget = self.currentWidget()
        return QSize(0, max(0, widget.minimumSizeHint().height())) if widget else QSize(0, 0)


class WeightField(QLineEdit):
    committed = Signal(int)

    def __init__(self, value=10):
        super().__init__(); self.saved = value; self.composing = False
        self.setText(f'{value / 10:.1f}'); self.setMaximumWidth(82); self.setAccessibleName('權重')
        self.editingFinished.connect(self.submit)

    def submit(self):
        value = submitted_weight(self.text()); self.setText(f'{value / 10:.1f}')
        if value != self.saved: self.saved = value; self.committed.emit(value)

    def value(self):return self.saved / 10
    def setValue(self,value):
        self.setText(str(value));self.submit()

    def inputMethodEvent(self, event):
        self.composing = bool(event.preeditString()); super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.setText(f'{self.saved / 10:.1f}'); event.accept(); return
        if not self.composing and event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):
            self.submit();self.editingFinished.emit();event.accept();return
        if not self.composing and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            value = max(-1000, min(1000, submitted_weight(self.text()) + (1 if event.key() == Qt.Key.Key_Up else -1)))
            self.setText(f'{value / 10:.1f}'); self.submit(); event.accept(); return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        # Scrolling a page must never change a stored weight.
        event.ignore()


class EntryDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(220, 42 + option.fontMetrics.height() * 2)

    def paint(self, painter, option, index):
        owner = self.parent(); data = index.data(DATA); colors = dialog_colors(widget_colors(owner))
        rect=option.rect.adjusted(1,0,-1,-8)
        painter.save();painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active=bool(option.state & QStyle.StateFlag.State_Selected);hover=bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setPen(QPen(QColor(colors['accent' if active else 'line']),1));painter.setBrush(QColor(colors['selected' if active else 'hover' if hover else 'surface']))
        painter.drawRoundedRect(rect,8,8)
        painter.setPen(QColor(colors['text'])); painter.setFont(option.font)
        regions = owner.regions(rect)
        if owner.kind != 'picker' and not owner.readonly:
            grip = regions['grip']; icon('grip-vertical', colors['secondary']).paint(painter, QRect(grip.center().x()-7, grip.center().y()-9, 14, 18))
        name = data['name']; subtitle = data.get('subtitle', '')
        rect = regions['name']; font=QFont(option.font); font.setWeight(QFont.Weight.DemiBold); painter.setFont(font); metrics=QFontMetrics(font)
        height=metrics.height()
        painter.drawText(QRect(rect.left(), rect.top()+10, rect.width(), height+2), Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(name, Qt.TextElideMode.ElideRight, rect.width()))
        painter.setFont(option.font); metrics=option.fontMetrics
        painter.setPen(QColor(colors['secondary']))
        painter.drawText(QRect(rect.left(), rect.top()+16+height, rect.width(), height+2), Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(subtitle, Qt.TextElideMode.ElideRight, rect.width()))
        if owner.kind == 'groups':
            painter.drawText(regions['value'], Qt.AlignmentFlag.AlignCenter, f"{data['repeat']} 次")
        elif owner.kind == 'modules':
            painter.drawText(regions['value'], Qt.AlignmentFlag.AlignCenter, f"{data['weight']/10:.1f}")
        target=regions['open']; icon('chevron-right', colors['secondary']).paint(painter, QRect(target.center().x()-9,target.center().y()-9,18,18))
        if owner.kind != 'picker' and not owner.readonly:
            target=regions['more']; icon('more-horizontal', colors['secondary']).paint(painter, QRect(target.center().x()-9,target.center().y()-9,18,18))
        painter.restore()


class EntryList(ReorderableList):
    activatedRow = Signal(str)
    menuRow = Signal(str, object)
    valueRow = Signal(str, object)

    def __init__(self, kind, *, readonly=False):
        super().__init__(); self.kind = kind; self.readonly=readonly; self._handle = False;self._skip_release=False;self.setObjectName('PromptEntryList')
        self.setMouseTracking(True)
        self.setItemDelegate(EntryDelegate(self)); self.setMinimumHeight(90); self.setSpacing(0)
        self.setVerticalScrollMode(self.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if kind != 'picker' and not readonly: self.enable_reordering()
        self.setAccessibleName({'groups': '組合清單', 'modules': '本組模組', 'picker': '可加入的素材'}[kind])

    def regions(self, rect):
        right = rect.right()+1; more = 34 if self.kind != 'picker' and not self.readonly else 0
        value = 72 if self.kind != 'picker' else 0
        grip = 36 if self.kind != 'picker' and not self.readonly else 14
        return dict(grip=QRect(rect.left(), rect.top(), grip, rect.height()),
            name=QRect(rect.left()+grip, rect.top(), max(1, rect.width()-grip-value-more-42), rect.height()),
            value=QRect(right-more-34-value, rect.top(), value, rect.height()),
            open=QRect(right-more-34, rect.top(), 34, rect.height()),
            more=QRect(right-more, rect.top(), more, rect.height()))

    def startDrag(self, actions):
        if self._handle: super().startDrag(actions)

    def mousePressEvent(self, event):
        self._skip_release=False
        item = self.itemAt(event.position().toPoint())
        self._handle = bool(not self.readonly and item and self.regions(self.visualItemRect(item))['grip'].contains(event.position().toPoint()))
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._skip_release:
            self._skip_release=False;event.accept();return
        suppressed = self._suppress_release
        super().mouseReleaseEvent(event)
        if suppressed or event.button() != Qt.MouseButton.LeftButton or self._handle: return
        item = self.itemAt(event.position().toPoint())
        if item is None: return
        ident = item.data(Qt.ItemDataRole.UserRole); regions = self.regions(self.visualItemRect(item))
        if self.kind != 'picker' and not self.readonly and regions['more'].contains(event.position().toPoint()):
            self.menuRow.emit(ident, self.viewport().mapToGlobal(regions['more'].bottomLeft()))
        elif self.kind != 'picker' and not self.readonly and regions['value'].contains(event.position().toPoint()):
            self.valueRow.emit(ident, regions['value'])
        elif self.kind!='modules' or regions['open'].contains(event.position().toPoint()):self.activatedRow.emit(ident)

    def mouseDoubleClickEvent(self,event):
        item=self.itemAt(event.position().toPoint())
        if self.kind=='modules' and item and event.button()==Qt.MouseButton.LeftButton:
            regions=self.regions(self.visualItemRect(item))
            if regions['name'].contains(event.position().toPoint()):
                self._skip_release=True;event.accept()
                self.activatedRow.emit(item.data(Qt.ItemDataRole.UserRole));return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        item = self.currentItem()
        if item and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activatedRow.emit(item.data(Qt.ItemDataRole.UserRole)); event.accept(); return
        if item and event.key() == Qt.Key.Key_Menu:
            self.menuRow.emit(item.data(Qt.ItemDataRole.UserRole), self.mapToGlobal(self.rect().center())); return
        super().keyPressEvent(event)

    def fill(self, rows):
        current = self.currentItem().data(Qt.ItemDataRole.UserRole) if self.currentItem() else None
        scroll = self.verticalScrollBar().value(); self.clear()
        for data in rows:
            item = QListWidgetItem(data['name']); item.setData(Qt.ItemDataRole.UserRole, data['id']); item.setData(DATA, data)
            item.setToolTip(data.get('subtitle', '')); self.addItem(item)
            if data['id'] == current: self.setCurrentItem(item)
        self.verticalScrollBar().setValue(scroll)
        if self.kind!='picker':
            self.setMinimumHeight(min(3, max(1,self.count())) * self.row_height()+8)
            self.updateGeometry()

    def row_height(self):
        return 42 + self.fontMetrics().height()*2

    def sizeHint(self):
        return QSize(420, min(6,max(1,self.count())) * self.row_height()+8)

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange):
            self.setMinimumHeight(min(3,max(1,self.count())) * self.row_height()+8)
            self.updateGeometry()


class GroupEditor(QWidget):
    changed = Signal()
    pageChanged = Signal(int)

    def __init__(self, window, sequence):
        super().__init__(); self.host = window; self.sequence = copy.deepcopy(sequence)
        self.current_group = None; self.current_module = None; self.loading = False
        self.category = getattr(window,'_material_browser_category',None)
        self.collapsed = set(window.state['settings'].get('material_folder_collapsed',[]))
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.stack = PageStack(); layout.addWidget(self.stack)
        self.stack.currentChanged.connect(self.pageChanged)
        self._overview(); self._group(); self._picker(); self.refresh_overview()

    def _overview(self):
        widget, layout = page(); self.stack.addWidget(widget)
        self.mode = ComboBox()
        mode_options(self.mode)
        self.start = ComboBox(); self.start.setMinimumWidth(100)
        layout.addLayout(row(field('變量',self.mode),None,field('起始組合',self.start)))
        self.mode.currentIndexChanged.connect(self.mode_changed); self.start.currentIndexChanged.connect(self.start_changed)
        layout.addLayout(row(label('組合', 'SectionTitle'), None, button('新增組合', self.add_group, 'Quiet')))
        self.listing = EntryList('groups'); layout.addWidget(self.listing, 1)
        self.listing.activatedRow.connect(self.edit_group); self.listing.menuRow.connect(self.group_menu)
        self.listing.valueRow.connect(self.repeat_inline)
        self.listing.reorderRequested.connect(lambda before, after: self.reorder(self.sequence['groups'], before, after))
        self.overview_preview_toggle = Disclosure('Prompt 預覽'); layout.addWidget(self.overview_preview_toggle)
        self.overview_preview = QPlainTextEdit(); self.overview_preview.setReadOnly(True); self.overview_preview.setMaximumHeight(140)
        layout.addWidget(self.overview_preview); self.overview_preview.hide()
        self.overview_preview_toggle.toggled.connect(self.overview_preview.setVisible)
        self.overview_options=row();layout.addLayout(self.overview_options)

    def refresh_overview(self):
        self.loading = True
        self.mode.setCurrentIndex(self.mode.findData(self.sequence['mode'])); self.start.clear()
        rows = []
        for group in self.sequence['groups']:
            self.start.addItem(group['name'], group['id'])
            subtitle = f"{len(group['modules'])} 個模組" if group['input'] == 'modules' else '整段 Prompt'
            if group['id'] == self.sequence['start']: subtitle += ' · 起始'
            rows.append(dict(group, subtitle=subtitle))
        self.start.setCurrentIndex(self.start.findData(self.sequence['start'])); self.listing.fill(rows)
        self.overview_preview.setPlainText(groups.render_group(groups.selected(self.sequence)))
        self.loading = False

    def mode_changed(self):
        if not self.loading: self.sequence['mode'] = self.mode.currentData(); self.changed.emit()

    def start_changed(self):
        if not self.loading and self.start.currentData():
            self.sequence['start'] = self.start.currentData(); self.refresh_overview(); self.changed.emit()

    def reorder(self, rows, before, after):
        if [g['id'] for g in rows] != before or set(before) != set(after): return
        mapping = {g['id']: g for g in rows}; rows[:] = [mapping[k] for k in after]
        self.refresh_overview(); self.refresh_modules(); self.changed.emit()

    def add_group(self):
        group = groups.new_group(f'組合 {len(self.sequence["groups"])+1}')
        self.sequence['groups'].append(group); self.refresh_overview(); self.edit_group(group['id']); self.changed.emit()

    def repeat_inline(self, ident, rect):
        group = next(g for g in self.sequence['groups'] if g['id'] == ident)
        edit = QSpinBox(self.listing.viewport()); edit.setRange(1, groups.MAX_REPEAT); edit.setValue(group['repeat'])
        edit.setGeometry(rect); edit.setAccessibleName(group['name']+'重複次數'); edit.show(); edit.setFocus(); edit.selectAll()
        finished = [False]
        def finish():
            if finished[0]: return
            finished[0] = True; group['repeat'] = edit.value(); edit.hide(); edit.deleteLater()
            self.refresh_overview(); self.changed.emit()
        edit.editingFinished.connect(finish)

    def group_menu(self, ident, position):
        menu = RoundMenu(self); rows = self.sequence['groups']; at = next(i for i, g in enumerate(rows) if g['id'] == ident)
        def move(delta):
            rows[at], rows[at+delta] = rows[at+delta], rows[at]; self.refresh_overview(); self.changed.emit()
        menu.addAction('上移', lambda: move(-1)).setEnabled(at > 0)
        menu.addAction('下移', lambda: move(1)).setEnabled(at+1 < len(rows))
        menu.addAction('複製', lambda: self.copy_group(ident))
        menu.addAction('設為起始組合', lambda: self.set_start(ident))
        menu.addAction('刪除', lambda: self.delete_group(ident)).setEnabled(len(rows) > 1)
        menu.exec(position)

    def copy_group(self, ident):
        groups.duplicate(self.sequence, ident); self.refresh_overview(); self.changed.emit()

    def set_start(self, ident):
        self.sequence['start'] = ident; self.refresh_overview(); self.changed.emit()

    def delete_group(self, ident):
        replacement = None
        if ident == self.sequence['start']:
            from .widgets import StudioDialog
            dialog = StudioDialog(self); dialog.setWindowTitle('選擇新的起始組合'); combo = ComboBox()
            for group in self.sequence['groups']:
                if group['id'] != ident: combo.addItem(group['name'], group['id'])
            combo.setCurrentIndex(-1); dialog.body.addWidget(combo)
            confirm = button('刪除並替換', dialog.accept, 'Primary'); confirm.setEnabled(False)
            combo.currentIndexChanged.connect(lambda: confirm.setEnabled(combo.currentData() is not None))
            dialog.body.addLayout(row(None, button('取消', dialog.reject, 'Quiet'), confirm))
            if not dialog.exec(): return
            replacement = combo.currentData()
        groups.remove(self.sequence, ident, replacement); self.refresh_overview(); self.changed.emit()

    def _group(self):
        widget, layout = page(); self.stack.addWidget(widget)
        self.group_title = label('組合', 'SectionTitle')
        self.group_navigation,gl=page();gl.addLayout(row(tool('back', '返回組合總覽', self.back), self.group_title, None, button('完成', self.back, 'Quiet')));layout.addWidget(self.group_navigation)
        self.group_name = QLineEdit(); self.group_name.setPlaceholderText('組合名稱')
        self.repeat = QSpinBox(); self.repeat.setRange(1, groups.MAX_REPEAT); self.repeat.setSuffix(' 次'); self.repeat.setMaximumWidth(112)
        layout.addLayout(row(field('組合名稱',self.group_name),field('重複次數',self.repeat)))
        self.group_name.textChanged.connect(self.update_group); self.repeat.valueChanged.connect(self.update_group)
        # Editing owns the full width; the optional rendered Prompt stays below.
        self.input_tabs = QTabBar(); self.input_tabs.setExpanding(False); self.input_tabs.setDrawBase(False)
        self.input_tabs.addTab('模組組合'); self.input_tabs.addTab('直接輸入'); layout.addWidget(self.input_tabs)
        self.input_pages = PageStack()
        modules, ml = page(); direct, dl = page(); self.input_pages.addWidget(modules); self.input_pages.addWidget(direct)
        self.module_toolbar,toolbar=page()
        toolbar.addLayout(row(label('使用的模組','Subtle'),None,button('加入素材',self.open_picker,'Quiet'),button('新增空白',self.add_empty,'Quiet')))
        ml.addWidget(self.module_toolbar)
        self.modules = EntryList('modules'); ml.addWidget(self.modules,1)
        self.modules.activatedRow.connect(self.open_module); self.modules.menuRow.connect(self.module_menu)
        self.modules.valueRow.connect(self.module_weight)
        self.modules.reorderRequested.connect(lambda before, after: self.reorder(self.current_group['modules'], before, after))
        self.inspector, self.inspector_layout = page(); ml.addWidget(self.inspector); self.inspector.hide()
        self.module_path = ComboBox(); self.inspector_layout.addWidget(self.module_path); self.module_path.currentIndexChanged.connect(self.select_part)
        self.module_name = QLineEdit(); self.module_weight_edit = WeightField()
        self.inspector_layout.addLayout(row(self.module_name, self.module_weight_edit))
        self.module_text = QPlainTextEdit(); self.module_text.setMaximumHeight(115); self.inspector_layout.addWidget(self.module_text)
        self.local_toggle = Disclosure('Tag 與排除'); self.inspector_layout.addWidget(self.local_toggle)
        self.local_panel, local = page(); self.inspector_layout.addWidget(self.local_panel); self.local_panel.hide()
        self.local_toggle.toggled.connect(self.local_panel.setVisible)
        self.tag_list = ReorderableList(); self.tag_list.setMaximumHeight(100); self.tag_list.itemChanged.connect(self.tags_changed); local.addWidget(self.tag_list)
        self.excludes = QPlainTextEdit(); self.excludes.setPlaceholderText('排除 Tag，每行一項'); self.excludes.setMaximumHeight(80); local.addWidget(self.excludes)
        self.inspector_layout.addLayout(row(button('新增子項', self.add_child, 'Quiet'), None, tool('chevron-up', '收起編輯', self.close_inspector)))
        self.nested_button=button('編輯子組合',self.edit_nested,'Quiet');self.inspector_layout.addWidget(self.nested_button);self.nested_button.hide()
        for signal in (self.module_name.textChanged, self.module_text.textChanged, self.excludes.textChanged): signal.connect(self.update_module)
        self.module_weight_edit.committed.connect(self.update_module)
        self.direct_kind = ComboBox()
        for name, kind in [('Tag', 'tags'), ('一般文字', 'text'), ('Markdown', 'markdown')]: self.direct_kind.addItem(name, kind)
        self.direct_toolbar,dt=page();dt.addLayout(row(label('內容','Subtle'),None,self.direct_kind));dl.addWidget(self.direct_toolbar)
        self.direct_text = QPlainTextEdit(); self.direct_text.setMinimumHeight(192); dl.addWidget(self.direct_text, 1)
        self.direct_kind.currentIndexChanged.connect(self.update_group); self.direct_text.textChanged.connect(self.update_group)
        self.input_tabs.currentChanged.connect(self.input_pages.setCurrentIndex)
        self.input_tabs.currentChanged.connect(self.update_group)
        layout.addWidget(self.input_pages,1)
        self.preview_toggle = Disclosure('Prompt 預覽');layout.addWidget(self.preview_toggle)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True); self.preview.setAccessibleName('本組實際 Prompt')
        self.preview.setMinimumHeight(120);self.preview.setMaximumHeight(200);layout.addWidget(self.preview);self.preview.hide()
        self.preview_toggle.toggled.connect(self.preview.setVisible)
        self.preview_toggle.toggled.connect(self.fit_content); self.local_toggle.toggled.connect(self.fit_content)

    def edit_group(self, ident):
        self.loading = True; self.current_group = next(g for g in self.sequence['groups'] if g['id'] == ident)
        group = self.current_group; self.sequence['selected'] = ident
        self.group_title.setText(group['name']); self.group_name.setText(group['name']); self.repeat.setValue(group['repeat'])
        self.direct_kind.setCurrentIndex(self.direct_kind.findData(group['content_kind'])); self.direct_text.setPlainText(group['text'])
        self.input_tabs.setCurrentIndex(0 if group['input'] == 'modules' else 1)
        self.current_module = None; self.inspector.hide(); self.refresh_modules(); self.loading = False
        self.preview.setPlainText(groups.render_group(group)); self.stack.setCurrentIndex(1)

    def update_group(self, *_):
        if self.loading or self.current_group is None: return
        self.current_group.update(name=self.group_name.text(), repeat=self.repeat.value(),
            input='modules' if self.input_tabs.currentIndex() == 0 else 'text', text=self.direct_text.toPlainText(), content_kind=self.direct_kind.currentData())
        self.preview.setPlainText(groups.render_group(self.current_group)); self.changed.emit()
        self.fit_content()

    def refresh_modules(self):
        if self.current_group is None: return
        rows = [dict(m, subtitle=('已停用 · ' if not m['enabled'] else '')+comp.render(m).replace('\n', ' ')) for m in self.current_group['modules']]
        self.modules.fill(rows)
        if hasattr(self, 'preview'): self.preview.setPlainText(groups.render_group(self.current_group))
        if hasattr(self,'preview_toggle'):self.fit_content()

    def edit_module(self, ident):
        root = next(m for m in self.current_group['modules'] if m['id'] == ident)
        self.module_path.blockSignals(True); self.module_path.clear()
        for part in comp.walk(root): self.module_path.addItem(part['name'], part)
        self.module_path.blockSignals(False); self.inspector.show(); self.select_part()

    def open_module(self, ident):
        from .text_canvas import NodeDialog
        part=next(m for m in self.current_group['modules'] if m['id']==ident)
        dialog=NodeDialog(self.host,part,title='編輯文字模組');self.active_node_dialog=dialog
        try:accepted=dialog.exec()==QDialog.DialogCode.Accepted
        finally:self.active_node_dialog=None
        if accepted:
            part.update(dialog.values());self.inspector.hide();self.current_module=None
            self.refresh_modules();self.changed.emit()

    def select_part(self):
        part = self.module_path.currentData()
        # QVariant copies dictionaries; resolve identity back into the draft.
        if not part: return
        self.current_module = next(n for m in self.current_group['modules'] for n in comp.walk(m) if n['id'] == part['id'])
        part = self.current_module; self.loading = True
        self.module_name.setText(part['name']); self.module_text.setPlainText(part['prompt'])
        self.module_text.setReadOnly(bool(part.get('sequence')))
        self.nested_button.setVisible(bool(part.get('sequence')))
        self.module_weight_edit.saved = part['weight']; self.module_weight_edit.setText(f"{part['weight']/10:.1f}")
        self.excludes.setPlainText('\n'.join(part['excludes']))
        self.refresh_tags(); self.loading = False
        self.fit_content()

    def refresh_tags(self):
        from .prompt_tags import tokens
        part = self.current_module; self.tag_list.blockSignals(True); self.tag_list.clear()
        if groups.output_kind(part) == 'tags':
            raw = copy.deepcopy(part); raw.pop('disabled_tags', None);raw.update(weight=10,grouped=False,enabled=True)
            for text in dict.fromkeys(tokens(comp.render(raw))):
                item = QListWidgetItem(text); item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked if text in part.get('disabled_tags', []) else Qt.CheckState.Checked); self.tag_list.addItem(item)
        self.tag_list.blockSignals(False)

    def tags_changed(self):
        if self.loading or self.current_module is None: return
        self.current_module['disabled_tags'] = [self.tag_list.item(i).text() for i in range(self.tag_list.count()) if self.tag_list.item(i).checkState() == Qt.CheckState.Unchecked]
        self.refresh_modules(); self.changed.emit()

    def update_module(self, *_):
        if self.loading or self.current_module is None: return
        part = self.current_module; part.update(name=self.module_name.text(),
            weight=self.module_weight_edit.saved, excludes=[v.strip() for v in self.excludes.toPlainText().splitlines() if v.strip()])
        if not part.get('sequence'): part['prompt'] = self.module_text.toPlainText()
        self.refresh_modules(); self.changed.emit()

    def close_inspector(self):
        self.inspector.hide(); self.fit_content()

    def fit_content(self,*_):
        field_height=max(self.group_name.sizeHint().height(),self.repeat.sizeHint().height(),38)
        self.group_name.setFixedHeight(field_height);self.repeat.setFixedHeight(field_height)
        header=max(self.module_toolbar.layout().sizeHint().height(),self.direct_toolbar.layout().sizeHint().height())
        for toolbar in (self.module_toolbar,self.direct_toolbar):toolbar.setFixedHeight(header)
        modules=self.input_tabs.currentIndex()==0
        body=(self.modules.minimumHeight() if modules else self.direct_text.minimumHeight())+header+12
        if modules and not self.inspector.isHidden():body+=self.inspector.minimumSizeHint().height()+16
        self.input_pages.setMinimumHeight(body)
        self.stack.updateGeometry(); self.updateGeometry()

    def module_weight(self, ident, rect):
        part=next(m for m in self.current_group['modules'] if m['id']==ident)
        edit=WeightField(part['weight']);edit.setParent(self.modules.viewport());edit.setGeometry(rect)
        edit.show();edit.setFocus();edit.selectAll();finished=[False]
        def finish():
            if finished[0]:return
            finished[0]=True;edit.submit();part['weight']=edit.saved;edit.hide();edit.deleteLater()
            self.refresh_modules();self.changed.emit()
        edit.editingFinished.connect(finish)

    def add_child(self):
        if self.current_module is None: return
        self.current_module['children'].append(comp.node('子項'))
        root = next(m for m in self.current_group['modules'] if comp.find(m, self.current_module['id']))
        self.edit_module(root['id']); self.module_path.setCurrentIndex(self.module_path.count()-1); self.refresh_modules(); self.changed.emit()

    def edit_nested(self):
        if not self.current_module or not self.current_module.get('sequence'):return
        target=self.current_module;widget,layout=page()
        nested=GroupEditor(self.host,target['sequence'])
        def finish():
            target['sequence']=copy.deepcopy(nested.sequence);self.stack.setCurrentIndex(1)
            self.stack.removeWidget(widget);widget.deleteLater();self.refresh_modules();self.changed.emit()
        layout.addLayout(row(tool('back','返回父組合',finish),label(target['name'],'SectionTitle'),None,button('完成',finish,'Quiet')))
        layout.addWidget(nested,1);self.stack.addWidget(widget);self.stack.setCurrentWidget(widget)

    def add_empty(self):
        node = comp.node('新模組'); self.current_group['modules'].append(node)
        self.refresh_modules(); self.edit_module(node['id']); self.changed.emit()

    def module_menu(self, ident, position):
        rows = self.current_group['modules']; at = next(i for i, m in enumerate(rows) if m['id'] == ident); target = rows[at]
        menu = RoundMenu(self)
        def change(action):
            if action == 'enable': target['enabled'] = not target['enabled']
            elif action == 'duplicate': rows.insert(at+1, comp.clone_node(target))
            elif action == 'remove': rows.pop(at); self.inspector.hide(); self.current_module = None
            elif action in (-1, 1): rows[at], rows[at+action] = rows[at+action], rows[at]
            self.refresh_modules(); self.changed.emit()
        menu.addAction('停用' if target['enabled'] else '啟用', lambda: change('enable'))
        menu.addAction('上移', lambda: change(-1)).setEnabled(at > 0)
        menu.addAction('下移', lambda: change(1)).setEnabled(at+1 < len(rows))
        menu.addAction('複製', lambda: change('duplicate')); menu.addAction('移除', lambda: change('remove')); menu.exec(position)

    def _picker(self):
        widget, layout = page(); self.stack.addWidget(widget)
        self.picker_navigation,pl=page();pl.addLayout(row(tool('back', '返回組合', lambda: self.stack.setCurrentIndex(1)), label('加入素材', 'SectionTitle'), None,
                             button('完成', lambda: self.stack.setCurrentIndex(1), 'Quiet')));layout.addWidget(self.picker_navigation)
        colors=lambda:dialog_colors(visual_tokens(self.host.state['settings']))
        self.category_combo=ComboBox();self.category_combo.setAccessibleName('內容分類');layout.addWidget(self.category_combo);self.category_combo.hide()
        self.picker_split = QuietSplitter(); self.picker_split.setChildrenCollapsible(False);layout.addWidget(self.picker_split, 1)
        self.category_pane, cats=page();cats.setContentsMargins(0,0,12,0)
        self.folders = FolderNavigation(manage=False,colors=colors); self.folders.setProperty('moduleChoices',True);self.folders.setSpacing(3)
        cats.addWidget(self.folders);self.picker_split.addWidget(self.category_pane)
        self.picker, right = page();right.setContentsMargins(12,0,0,0);right.setSpacing(8);self.picker_split.addWidget(self.picker)
        self.picker_header, self.picker_header_layout=page();self.picker_header_layout.setSpacing(8);right.addWidget(self.picker_header)
        self.picker_heading=label('全部分類','Heading');self.picker_header_layout.addWidget(self.picker_heading)
        self.search = QLineEdit(); self.search.setPlaceholderText('搜尋名稱、別名或內容');self.search.setClearButtonEnabled(True);self.picker_header_layout.addWidget(self.search)
        self.candidates = StackedCardList(lambda:self.host.state['settings'],colors)
        self.candidates.setAccessibleName('可加入的素材');self.candidates.setSpacing(3);self.candidates.setMouseTracking(True)
        self.candidates.setStyleSheet('QListWidget {background:transparent;border:0;padding:0;}')
        self.candidates.setItemDelegate(PickerCardDelegate(self.candidates));self.candidates.setUniformItemSizes(False)
        self.candidates.setMinimumSize(0,360);self.candidates.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.candidates.installEventFilter(self)
        self.candidates.viewport().installEventFilter(self);right.addWidget(self.candidates, 1)
        self.empty = label('沒有符合的素材', 'Subtle'); right.addWidget(self.empty)
        self.added = label('', 'Subtle'); layout.addWidget(self.added)
        self.folders.currentItemChanged.connect(self.scope_changed); self.folders.toggleRequested.connect(self.toggle_folder)
        self.search.textChanged.connect(self.refresh_picker)
        self.candidates.itemClicked.connect(lambda item:self.add_material(item.data(Qt.ItemDataRole.UserRole)))
        self.category_combo.currentIndexChanged.connect(self.combo_scope_changed)
        self.picker_split.setSizes([220, 550]); self.refresh_folders()

    def refresh_folders(self):
        state = self.host.state; view = nav.view(state); folders = {f['id']: f for f in view['folders']}
        self.collapsed=set(state['settings'].get('material_folder_collapsed',[]))
        categories = {m['id']: m for m in state['modules']}; counts = {k: sum(i['module'] == k for i in state['items']) for k in categories}
        unclassified=next((m['id'] for m in state['modules'] if m['name']=='未分類'),'@uncategorized')
        if self.category=='@uncategorized':self.category=unclassified
        self.folders.has_folders=bool(folders)
        self.folders.blockSignals(True); self.folders.clear(); self.folders.add_row(None, '全部模組', len(state['items']), 'shortcut')
        for ident in view['order']:
            if ident in folders:
                folder = folders[ident]; self.folders.add_row(ident, folder['name'], sum(counts[k] for k in folder['categories']), 'folder', collapsed=ident in self.collapsed)
                if ident not in self.collapsed:
                    for key in folder['categories']: self.folders.add_row(key, categories[key]['name'], counts[key], parent=ident)
            else: self.folders.add_row(ident, categories[ident]['name'], counts[ident])
        unclassified_count=counts.get(unclassified,sum(i['module'] not in categories for i in state['items']))
        self.folders.add_row(unclassified,'未分類',unclassified_count,'shortcut')
        for at in range(self.folders.count()):
            if self.folders.item(at).data(Qt.ItemDataRole.UserRole) == self.category: self.folders.setCurrentRow(at)
        self.category_combo.blockSignals(True);self.category_combo.clear()
        self.category_combo.addItem('全部模組',None)
        for ident in view['order']:
            if ident in folders:
                folder=folders[ident];self.category_combo.addItem(folder['name'],ident)
                for key in folder['categories']:self.category_combo.addItem('  '+categories[key]['name'],key)
            else:self.category_combo.addItem(categories[ident]['name'],ident)
        self.category_combo.addItem('未分類',unclassified)
        self.category_combo.setCurrentIndex(max(0,self.category_combo.findData(self.category)));self.category_combo.blockSignals(False)
        self.folders.blockSignals(False); self.refresh_picker()

    def toggle_folder(self, ident):
        if nav.folder(self.host.state,ident) is None:return
        if ident in self.collapsed: self.collapsed.remove(ident)
        else: self.collapsed.add(ident)
        self.host.state['settings']['material_folder_collapsed']=sorted(self.collapsed)
        self.host.changed('settings',refresh=False)
        library=getattr(self.host,'prompt_editor',None)
        if library is not None:library._refresh_categories()
        self.refresh_folders()

    def scope_changed(self, current, previous):
        if current:
            self.category = current.data(Qt.ItemDataRole.UserRole)
            self.host._material_browser_category=self.category
            self.category_combo.blockSignals(True);self.category_combo.setCurrentIndex(self.category_combo.findData(self.category));self.category_combo.blockSignals(False)
            self.refresh_picker()

    def combo_scope_changed(self):
        self.category=self.category_combo.currentData();self.host._material_browser_category=self.category;self.refresh_folders()

    def refresh_picker(self):
        folder = nav.folder(self.host.state, self.category) if nav.is_folder(self.category) else None
        rows = materials.search(self.host.state, self.search.text(), None if folder or self.category=='@uncategorized' else self.category)
        if folder: rows = [r for r in rows if r['module'] in folder['categories']]
        if self.category=='@uncategorized':
            known={m['id'] for m in self.host.state['modules']};rows=[r for r in rows if r['module'] not in known]
        matches={r['id'] for r in rows};rows=[r for r in self.host.state['items'] if r['id'] in matches]
        scroll=self.candidates.verticalScrollBar().value();self.candidates.clear()
        for item in rows:
            entry=QListWidgetItem(item['name']+'\n'+item['prompt'].replace('\n',' '));entry.setData(Qt.ItemDataRole.UserRole,item['id'])
            entry.setToolTip(item['name']+'\n'+item['prompt']);self.candidates.addItem(entry)
        self.candidates.verticalScrollBar().setValue(scroll);self.empty.setVisible(not rows)
        category=folder['name'] if folder else '未分類' if self.category=='@uncategorized' else next((m['name'] for m in self.host.state['modules'] if m['id']==self.category),'全部分類')
        self.picker_heading.setText(f'{category} · {len(rows)} 個模組')
        self.candidates.doItemsLayout();self.align_picker()

    def align_picker(self):
        viewport=self.candidates.viewport();origin=viewport.mapTo(self.candidates,QPoint()).x()
        if self.candidates.count():
            card=self.candidates.visualItemRect(self.candidates.item(0));left=origin+card.x()+1;right=self.candidates.width()-(origin+card.x()+card.width()-2)
        else:left=4;right=5
        self.picker_header_layout.setContentsMargins(max(0,left),0,max(0,right),0)

    def eventFilter(self,watched,event):
        if hasattr(self,'candidates') and watched is self.candidates.viewport() and event.type()==QEvent.Type.Resize:self.align_picker()
        if hasattr(self,'candidates') and watched is self.candidates and event.type()==QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_Space):
            item=self.candidates.currentItem()
            if item:self.add_material(item.data(Qt.ItemDataRole.UserRole))
            return True
        return super().eventFilter(watched,event)

    def open_picker(self):
        self.refresh_folders(); self.stack.setCurrentIndex(2)

    def add_material(self, ident):
        item = materials.item(self.host.state, ident); node = comp.clone_node(comp.prototype(item))
        node.update(source_id=ident, source_module=item['module'], source_revision=materials.fingerprint(item))
        self.current_group['modules'].append(node); self.added.setText(f"已加入 {item['name']}")
        self.refresh_modules(); self.changed.emit()

    def back(self):
        self.module_weight_edit.submit(); self.refresh_overview(); self.stack.setCurrentIndex(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < 680
        if hasattr(self, 'preview_toggle'):
            self.fit_content()
            self.category_pane.setVisible(not narrow);self.category_combo.setVisible(narrow)
            self.picker.layout().setContentsMargins(0 if narrow else 12,0,0,0)


class MaterialPages(QWidget):
    """The existing native form owns ordinary text/history; groups stay local."""
    def __init__(self, dialog):
        super().__init__(); self.dialog = dialog; f = dialog.form
        root = copy.deepcopy(comp.prototype(f.base)); root['name'] = root['name'] or '組合 1'
        self.group_editor = None; self.is_group = bool(root.get('sequence')); self.root = root
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 8, 0, 4); layout.setSpacing(12)
        self.type_picker = ComboBox(); self.type_picker.addItem('單模組', False); self.type_picker.addItem('組合', True)
        self.type_picker.setCurrentIndex(1 if self.is_group else 0); self.type_picker.setAccessibleName('素材類型')
        self.type_picker.setMaximumWidth(156)
        # Existing grouped materials cannot silently discard their definition.
        self.type_picker.setVisible(dialog.was_new or not self.is_group)
        self.type_picker.currentIndexChanged.connect(self.switch_type)
        dialog.name_label = label('名稱', 'Subtle'); layout.addLayout(row(dialog.name_label,None,self.type_picker)); layout.addWidget(f.name)
        f.name.setPlaceholderText('素材名稱')
        self.stack = PageStack(); layout.addWidget(self.stack, 1)
        self.single, sl = page(); self.stack.addWidget(self.single)
        sl.addLayout(row(label('Prompt', 'Subtle'), None, button('展開長文', dialog.expand_text, 'Quiet')))
        f.prompt.setMinimumHeight(140); sl.addWidget(f.prompt, 1)
        self.details_toggle = Disclosure('進階功能'); sl.addWidget(self.details_toggle)
        self.details = QTabWidget(); self.details.setDocumentMode(True); self.details.tabBar().setDrawBase(False);self.details.hide()
        self.details_toggle.toggled.connect(self.details.setVisible)
        self.details_toggle.toggled.connect(self.expand_advanced)
        self.single_split = QuietSplitter(); self.stack.removeWidget(self.single)
        sl.setContentsMargins(0,0,0,0)
        self.details_pane,details_layout=page();details_layout.setContentsMargins(12,0,0,0);details_layout.addWidget(self.details)
        self.single_split.setChildrenCollapsible(False)
        self.single_split.addWidget(self.single); self.single_split.addWidget(self.details_pane); self.stack.insertWidget(0, self.single_split)
        self.details_pane.hide();self.details_toggle.toggled.connect(self.details_pane.setVisible)
        self.single.show()
        self.single_split.setSizes([470, 310])
        info, il = page(); fields = QFormLayout(); il.addLayout(fields)
        dialog.require_category = not dialog.target_id and nav.is_folder(dialog.page.get_browser_category())
        if dialog.require_category:
            f.edit_category.insertItem(0, '選擇分類…', None); f.edit_category.setCurrentIndex(0)
            category = QFormLayout(); category.addRow('分類', f.edit_category); layout.insertLayout(2, category)
        else: fields.addRow('分類', f.edit_category)
        fields.addRow('格式', f.kind); fields.addRow('別名', f.aliases); fields.addRow('備註', f.notes); il.addStretch()
        lookup, ll = page(); ll.addWidget(f.lookup); ll.addWidget(f.lookup_hint); ll.addStretch()
        relation, rl = page(); rl.addWidget(label('排除 Tag', 'Subtle')); rl.addWidget(f.excludes); rl.addWidget(f.preview)
        rl.addLayout(row(button('選擇預覽圖', f.choose_image, 'Quiet'), button('移除預覽', f.clear_image, 'Quiet')))
        history, hl = page(); dialog.history_button = button('查看內容歷史', f.history, 'Quiet'); hl.addWidget(dialog.history_button); hl.addStretch()
        for name, widget in [('資訊', info), ('查詢', lookup), ('關係', relation), ('歷史', history)]:
            widget.layout().setContentsMargins(0,8,0,0);self.details.addTab(widget, name)
        dialog.advanced_toggle = self.details_toggle; dialog.advanced = self.details
        # Kept as aliases for code that opens the established advanced entry.
        dialog.info_toggle = dialog.lookup_toggle = dialog.relation_toggle = dialog.history_toggle = self.details_toggle
        dialog.intro = label(''); dialog.intro.hide()
        self.group_container, self.gl = page(); self.stack.addWidget(self.group_container)
        self.dedupe = QCheckBox('去重',self.group_container); self.dedupe.setChecked(root.get('deduplicate', False))
        self.group_details=button('進階功能',self.show_group_details,'Quiet');self.group_details.setParent(self.group_container)
        self.advanced_page,self.al=page();self.stack.addWidget(self.advanced_page)
        self.al.addLayout(row(tool('back','返回組合總覽',self.close_group_details),label('進階功能','SectionTitle'),None))
        self.details_toggle.toggled.connect(self.layout_limits)
        self.switch_type()

    def switch_type(self):
        self.is_group = bool(self.type_picker.currentData())
        if self.is_group and self.group_editor is None:
            root = copy.deepcopy(self.root); root['prompt'] = self.dialog.prompt.toPlainText(); root['name'] = self.dialog.name.text() or '組合 1'
            self.group_editor = GroupEditor(self.dialog.page.window, groups.from_node(root)); self.gl.insertWidget(0, self.group_editor, 1)
            self.group_editor.pageChanged.connect(self.page_changed)
            for toggle in (self.group_editor.preview_toggle,self.group_editor.overview_preview_toggle):
                toggle.toggled.connect(lambda:QTimer.singleShot(0,self.dialog.fit_page))
            self.group_editor.group_navigation.hide();self.group_editor.picker_navigation.hide()
            self.group_editor.overview_options.addWidget(self.dedupe);self.group_editor.overview_options.addStretch()
            self.group_editor.overview_options.addWidget(self.group_details)
        self.stack.setCurrentIndex(1 if self.is_group else 0)
        self.dialog.name.setVisible(not self.is_group or self.group_editor.stack.currentIndex() == 0)
        self.layout_limits()
        if hasattr(self.dialog,'done_button'):self.page_changed(self.group_editor.stack.currentIndex() if self.is_group else 0)

    def page_changed(self,index):
        self.dialog.name.setVisible(index==0);self.dialog.name_label.setVisible(index==0)
        self.type_picker.setVisible(index==0 and self.dialog.was_new)
        self.dedupe.setVisible(self.is_group and index==0)
        self.group_details.setVisible(self.is_group and index==0);self.layout_limits()
        dialog=self.dialog
        if hasattr(dialog,'done_button'):
            inside=self.is_group and index!=0
            dialog.back_button.setVisible(inside);dialog.done_button.setVisible(inside)
            dialog.save_button.setVisible(not inside);dialog.save_button.setDefault(not inside);dialog.done_button.setDefault(inside)
            dialog.add_button.setVisible(not inside and bool(dialog.form.destination_id) and dialog.target_id is not None)
            if inside:
                group=self.group_editor.current_group
                dialog.heading.setText('加入素材' if index==2 else group['name'])
                dialog.identity.setText('第 '+str(self.group_editor.sequence['groups'].index(group)+1)+' 組 / '+str(len(self.group_editor.sequence['groups'])))
            else:
                dialog.heading.setText('編輯素材' if dialog.target_id else '新增素材');dialog.identity.setText('')
            QTimer.singleShot(0,dialog.fit_page)

    def back(self):
        if self.group_editor.stack.currentIndex()==2:self.group_editor.stack.setCurrentIndex(1)
        else:self.group_editor.back()

    def show_group_details(self):
        self.al.addWidget(self.details,1);self.details.show();self.stack.setCurrentIndex(2);self.layout_limits()

    def close_group_details(self):
        self.al.removeWidget(self.details);self.details_pane.layout().addWidget(self.details);self.details.hide()
        self.details_toggle.setChecked(False);self.stack.setCurrentIndex(1);self.layout_limits()

    def layout_limits(self,*_):
        narrow=self.width()<680
        if self.is_group:
            self.setMinimumHeight(0)
        else:
            minimum=390 if self.details_toggle.isChecked() else 330
            if getattr(self.dialog,'_text_expanded',False):minimum+=170
            self.setMinimumHeight(minimum)
        self.stack.updateGeometry();self.updateGeometry()
        self.details_toggle.setIcon(icon('chevron-right'))

    def expand_advanced(self,opened):
        # The established advanced pane opens beside the Prompt. Grow the sheet
        # when the desktop has room, instead of moving the pane below it.
        if opened:
            available=min(self.dialog.page.window.width()-24,self.dialog.screen().availableGeometry().width()-40)
            self.dialog.setMinimumWidth(min(740,available))
            self.dialog.resize(max(self.dialog.width(),min(820,available)),self.dialog.height())
        else:self.dialog.setMinimumWidth(340)
        self.single.layout().setContentsMargins(0,0,12 if opened else 0,0)
        self.single_split.setSizes([500,320]);self.layout_limits()

    def value(self, value):
        if self.is_group:
            self.group_editor.module_weight_edit.submit()
            root = copy.deepcopy(self.root); root.update(name=self.dialog.name.text().strip(), sequence=copy.deepcopy(self.group_editor.sequence), deduplicate=self.dedupe.isChecked(),excludes=copy.deepcopy(value['excludes']),preview=value.get('preview',''))
            root.pop('round_override', None); comp.validate_node(root)
            value.update(composition=root, prompt=comp.render(root), content_kind=groups.output_kind(root))
        elif self.dialog.was_new and 'sequence' in value.get('composition', {}):
            value['composition'].pop('sequence', None)
        return value

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'single_split'):
            self.single_split.setOrientation(Qt.Orientation.Horizontal)
        if hasattr(self,'group_details'):self.layout_limits()
