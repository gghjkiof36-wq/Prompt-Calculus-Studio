from copy import deepcopy
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QFileDialog, QLineEdit, QListWidget, QListWidgetItem,
                              QTabWidget, QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem,
                              QSpinBox, QAbstractItemView)
from PySide6.QtWidgets import QCheckBox, QHBoxLayout
from . import conversation_review as review
from .retained_dialog import RetainedSheet, plain_editor
from .retained_files import load_record, read_local
from .widgets import ComboBox, label, button, row


class ReviewSheet(RetainedSheet):
    def __init__(self, surface):
        super().__init__(surface, '查回與整理對話')
        self.source_data = None
        self.request = None
        self.items = []
        self.received = set()
        self.pins = set()
        self.previous = None
        self.search_results = []
        self.result_page = 0
        self.loading_grid = False
        self.record = None
        self.chunks = None
        self.controls = []
        self.source_button = button('選擇原始對話.json…', self.choose_source)
        self.branches = ComboBox()
        self.branches.setMinimumContentsLength(8)
        self.branches.setSizeAdjustPolicy(ComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.body.addLayout(row(self.source_button, self.branches))
        self.tabs = QTabWidget(); self.body.addWidget(self.tabs, 1)
        self.lookup_ui(); self.review_ui(); self.continuation_ui()
        self.finish_layout()
        self.branches.currentIndexChanged.connect(self.branch_changed)
        candidate = None
        for parent in self.document.parents:
            if not parent.is_relative_to(self.root):break
            if (parent / '原始對話.json').is_file():
                candidate = parent / '原始對話.json'; break
        if candidate:
            self.load_source(candidate)
        else:
            self.status.setText('請選第 2 項匯入保存的原始對話；純貼上文字不會補造角色或分支。')

    def tab(self, name):
        widget = QWidget(); layout = QVBoxLayout(widget)
        self.tabs.addTab(widget, name)
        return layout

    def lookup_ui(self):
        layout = self.tab('原問答')
        self.query = QLineEdit(); self.query.setPlaceholderText('搜尋以前問過或回答的內容')
        self.role_filter = ComboBox()
        for text, value in [('所有角色', ''), ('使用者', 'user'), ('助理', 'assistant'), ('工具', 'tool')]:
            self.role_filter.addItem(text, value)
        self.scope = ComboBox(); self.scope.addItems(['目前分支與共同歷史', '全部分支'])
        self.since = QLineEdit(); self.since.setPlaceholderText('起日 YYYY-MM-DD（UTC）')
        self.until = QLineEdit(); self.until.setPlaceholderText('迄日 YYYY-MM-DD（UTC）')
        self.find_button = button('搜尋', self.search)
        layout.addLayout(row(self.query, self.find_button)); layout.addLayout(row(self.scope, self.role_filter))
        layout.addLayout(row(self.since, self.until))
        self.results = QListWidget(); self.results.setMinimumHeight(100)
        layout.addWidget(self.results, 1)
        self.result_count = label('', 'Muted')
        layout.addLayout(row(button('上一頁', lambda: self.page_results(-1)), self.result_count,
                             button('下一頁', lambda: self.page_results(1))))
        self.context_branch = ComboBox()
        layout.addLayout(row(self.context_branch, button('釘選此則原文', self.pin)))
        self.original = plain_editor(True); layout.addWidget(self.original, 2)
        self.results.currentRowChanged.connect(self.show_context)
        self.context_branch.currentIndexChanged.connect(self.render_context)
        self.query.returnPressed.connect(self.search)

    def review_ui(self):
        layout = self.tab('ChatGPT 往返')
        layout.addWidget(label('人工整理：複製分段內容到 ChatGPT，貼回每段結果，再核對原文。PCS 不會自動傳送。', 'Muted', wrap=True))
        self.purpose = ComboBox(); self.purpose.addItems(['接續工作', '閱讀整理'])
        self.length = ComboBox(); self.length.addItems(['精簡', '標準', '詳盡']); self.length.setCurrentIndex(1)
        self.capacity = QSpinBox(); self.capacity.setRange(1000, 50000); self.capacity.setValue(16000)
        self.capacity.setSuffix(' 字元／段')
        self.packet_button = button('準備整理內容', self.prepare_packets)
        layout.addLayout(row(self.purpose, self.length))
        layout.addLayout(row(self.capacity, self.packet_button))
        self.pin_label = label('釘選 0 則原文', 'Muted')
        layout.addLayout(row(self.pin_label, None, button('清除釘選', self.clear_pins)))
        self.chunk = ComboBox(); self.chunk.currentIndexChanged.connect(self.show_packet)
        layout.addLayout(row(self.chunk, button('複製本段', self.copy_packet)))
        self.prompt = plain_editor(True); layout.addWidget(self.prompt, 1)
        self.reply = plain_editor(); self.reply.setPlaceholderText('貼上 ChatGPT 回傳的 JSON。來源身份不符的結果不會採用。')
        layout.addWidget(self.reply, 1)
        layout.addLayout(row(button('載入已保存候選…', self.choose_candidate), None,
                             button('核對引用並加入候選', self.accept_reply)))

    def continuation_ui(self):
        layout = self.tab('候選與接續')
        layout.addWidget(label('雙擊可編輯內容、分類及來源。勾選代表你已核對該項語意；重新整理會保留已保存版本。', 'Muted', wrap=True))
        self.grid = QTableWidget(0, 4)
        self.grid.setHorizontalHeaderLabels(['已核對', '分類', '內容', '來源訊息'])
        self.grid.horizontalHeader().setStretchLastSection(True)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.grid.setColumnWidth(0, 60); self.grid.setColumnWidth(1, 120); self.grid.setColumnWidth(2, 260)
        layout.addWidget(self.grid, 2)
        self.grid.itemChanged.connect(self.item_changed)
        self.grid.cellDoubleClicked.connect(self.citation_clicked)
        self.maximum = QSpinBox(); self.maximum.setRange(1000, 1000000); self.maximum.setValue(12000)
        self.maximum.setSuffix(' 字元上限')
        layout.addLayout(row(self.maximum, button('組成接續內容', self.compose)))
        self.output = plain_editor(); self.output.setPlaceholderText('組成後可手改；保存會保留這裡的實際內容。')
        layout.addWidget(self.output, 2)
        layout.addLayout(row(button('複製接續內容', self.copy_output), None,
                             button('另存新版本', self.save), button('另存 MD…', self.export_md)))

    def set_busy(self, busy):
        self.source_button.setEnabled(not busy)
        self.branches.setEnabled(not busy)
        self.tabs.setEnabled(not busy)

    def choose_source(self):
        path, _ = QFileDialog.getOpenFileName(self, '選擇原始對話', str(self.document.parent), 'JSON (*.json)')
        if path:self.load_source(Path(path))

    def load_source(self, path):
        if not Path(path).is_relative_to(self.root):
            self.status.setText('請先把對話匯入目前接入根，再選擇原始對話。'); return
        self.run(lambda cancel, progress: review.Source.load(path, cancel), self.source_loaded)

    def source_loaded(self, source):
        self.source_data = source
        self.source_button.setText(source.item.title[:16].replace('\n',' ') + ('…' if len(source.item.title)>16 else ''))
        self.source_button.setToolTip(str(source.path))
        self.branches.blockSignals(True); self.branches.clear()
        for number, leaf in enumerate(source.item.leaves, 1):
            self.branches.addItem(f'分支 {number} · {leaf[:24]}', leaf)
        selected = self.branches.findData(source.item.current)
        # Imported branch filenames are a hint; all identity checks use node IDs.
        import re
        match = re.match(r'分支-(\d+)-', self.document.name)
        if match and 1 <= int(match[1]) <= len(source.item.leaves):selected = int(match[1])-1
        self.branches.setCurrentIndex(max(0, selected)); self.branches.blockSignals(False)
        self.branch_changed()
        self.search()

    def branch_changed(self, *_):
        # Never silently discard an edited candidate when browsing another branch.
        self.results.clear(); self.original.clear()
        self.pins.clear(); self.pin_label.setText('釘選 0 則原文')
        if self.request:
            self.status.setText('已切換查回分支；既有候選仍屬原分支。準備新整理前請先另存。')

    def search(self):
        if not self.source_data:return
        source = self.source_data
        args = dict(query=self.query.text(), branch=self.branches.currentData() if self.scope.currentIndex()==0 else None,
                    role_filter=self.role_filter.currentData(), since=self.since.text().strip(), until=self.until.text().strip())
        def find(cancel, progress):
            source.verify()
            return source.search(**args, cancel=cancel)
        self.run(find, self.searched)

    def searched(self, results):
        self.search_results = results; self.result_page = 0; self.render_results()
        self.status.setText('查回保存的原始訊息；手改 Markdown 不會改寫這份歷史。')

    def page_results(self, delta):
        self.result_page = max(0, min((max(1, len(self.search_results))-1)//200, self.result_page+delta))
        self.render_results()

    def render_results(self):
        self.results.clear()
        for hit in self.search_results[self.result_page*200:(self.result_page+1)*200]:
            item = QListWidgetItem(hit['role'] + ' · ' + hit['body'][:120].replace('\n', ' ')
                                   + (f' · 共用於 {len(hit["branches"])} 分支' if len(hit['branches'])>1 else ''))
            item.setData(Qt.ItemDataRole.UserRole, hit); self.results.addItem(item)
        self.result_count.setText(f'{len(self.search_results)} 則 · 第 {self.result_page+1} 頁')
        self.results.setCurrentRow(0)

    def show_context(self, *_):
        item = self.results.currentItem()
        self.context_branch.blockSignals(True); self.context_branch.clear()
        if item:
            for branch in item.data(Qt.ItemDataRole.UserRole)['branches']:
                self.context_branch.addItem('問答所在分支：' + branch[:32], branch)
        self.context_branch.blockSignals(False); self.render_context()

    def render_context(self, *_):
        item = self.results.currentItem()
        if not item or self.context_branch.currentData() is None:return
        hit = item.data(Qt.ItemDataRole.UserRole)
        text = self.source_data.context(hit['node'], self.context_branch.currentData())
        self.original.setPlainText(text[:2000000] + ('\n（顯示上限 200 萬字元，完整內容仍在原始紀錄。）' if len(text)>2000000 else ''))

    def pin(self):
        item = self.results.currentItem()
        if not item:return
        key = item.data(Qt.ItemDataRole.UserRole)['node']
        if key not in self.source_data.scope(self.branches.currentData()):
            self.status.setText('請先切到這則訊息所在分支，再釘選。'); return
        self.pins.add(key); self.pin_label.setText(f'釘選 {len(self.pins)} 則原文')

    def clear_pins(self):
        self.pins.clear(); self.pin_label.setText('釘選 0 則原文')

    def prepare_packets(self):
        if not self.source_data:return
        if self.items:
            self.status.setText('請先另存目前候選，再重新開啟整理視窗準備下一份；人工修正不會被覆蓋。'); return
        source, branch = self.source_data, self.branches.currentData()
        args = (source, branch, self.purpose.currentText(), self.length.currentText(), tuple(sorted(self.pins)), self.capacity.value())
        self.run(lambda cancel, progress: review.packets(*args), self.packets_ready)

    def packets_ready(self, result):
        self.request, self.chunks, self.packet_texts = result
        self.chunk.clear()
        for index in range(len(self.packet_texts)):
            self.chunk.addItem(f'第 {index+1}/{len(self.packet_texts)} 段', index)
        self.status.setText('以字元切段，並非模型 token 容量估算。每段均須貼回；尚未自動摘要。')

    def show_packet(self, *_):
        if self.request and self.chunk.currentIndex() >= 0:
            self.prompt.setPlainText(self.packet_texts[self.chunk.currentIndex()])

    def copy_packet(self):
        if self.prompt.toPlainText():QApplication.clipboard().setText(self.prompt.toPlainText())

    def accept_reply(self):
        try:
            if not self.request or self.chunks is None:raise ValueError('請先準備本次整理內容；已保存候選請另開新整理作比較。')
            chunk, items = review.accept_reply(self.reply.toPlainText(), self.request, self.chunks, self.source_data)
            if chunk in self.received:raise ValueError('這一段已有候選；請保留人工修正，另開新整理建立比較版本。')
            self.received.add(chunk); self.items.extend(items); self.render_grid()
            self.status.setText(f'收到 {len(self.received)}/{self.request["chunk_count"]} 段；引用結構有效，語意仍待核對。')
            self.tabs.setCurrentIndex(2)
        except (ValueError, OSError, KeyError) as error:self.status.setText(str(error))

    def render_grid(self):
        self.loading_grid = True; self.grid.setRowCount(len(self.items))
        for index, item in enumerate(self.items):
            wrapper=QWidget(); layout=QHBoxLayout(wrapper);layout.setContentsMargins(8,0,8,0)
            check=QCheckBox(); check.setAccessibleName('此項語意已核對');check.setChecked(bool(item.get('checked')))
            layout.addWidget(check,0,Qt.AlignmentFlag.AlignCenter)
            check.toggled.connect(lambda value,index=index:self.mark_checked(index,value))
            self.grid.setCellWidget(index,0,wrapper);self.grid.setRowHeight(index,38)
            for col, text in ((1,item['category']), (2,item['text']), (3,', '.join(item['sources']))):
                self.grid.setItem(index, col, QTableWidgetItem(text))
        self.loading_grid = False

    def item_changed(self, cell):
        if self.loading_grid:return
        item = self.items[cell.row()]
        self.grid.cellWidget(cell.row(),0).findChild(QCheckBox).setChecked(False)
        item['category'] = self.grid.item(cell.row(),1).text()
        item['text'] = self.grid.item(cell.row(),2).text()
        item['sources'] = [v.strip() for v in self.grid.item(cell.row(),3).text().split(',') if v.strip()]
        item['edited'] = True; self.record = None

    def mark_checked(self,index,value):
        if not self.loading_grid:
            self.items[index]['checked']=value;self.record=None

    def citation_clicked(self, row_index, column):
        if column != 3 or not self.request:return
        try:
            text = '\n\n'.join(self.source_data.context(key, self.request['branch']) for key in self.items[row_index]['sources'])
            self.original.setPlainText(text); self.tabs.setCurrentIndex(0)
        except (ValueError, KeyError):self.status.setText('引用找不到，請修正來源訊息 ID。')

    def build_record(self):
        if not self.request:raise ValueError('尚無整理候選。')
        if any(item['category'] not in review.CATEGORIES or not item['text'].strip() or not item['sources'] for item in self.items):
            raise ValueError('請核對分類、內容及來源欄位。分類：' + '、'.join(review.CATEGORIES))
        return review.candidate(self.request, self.items, self.received, self.previous)

    def compose(self):
        try:
            self.record = self.build_record()
            self.output.setPlainText(review.continuation(self.record, self.source_data, self.maximum.value()))
            self.status.setText(review.freshness(self.record, self.source_data, self.branches.currentData()))
        except (ValueError, OSError, KeyError) as error:self.status.setText(str(error))

    def copy_output(self):
        try:
            _, text = self.checked_output()
            QApplication.clipboard().setText(text)
            self.status.setText('已複製接續內容；人工核對狀態與必要原文一併保留。')
        except (ValueError, OSError, KeyError) as error:self.status.setText(str(error))

    def checked_output(self):
        if self.record is None:raise ValueError('請先組成接續內容；分類或引用變更後需重新組成。')
        record = self.build_record()
        review.continuation(record, self.source_data, self.maximum.value())
        text = self.output.toPlainText()
        if not text.strip():raise ValueError('請先組成或填寫接續內容。')
        if len(text)>self.maximum.value():raise ValueError('手改內容超過字元上限，請調整內容或上限。')
        for key in record['request'].get('pins', []):
            if review.message_body(self.source_data.item.raw['mapping'][key]) not in text:
                raise ValueError('接續稿缺少釘選原文，請重新組成；要改變釘選請建立另一份整理。')
        return record, text

    def save(self):
        try:
            record, text = self.checked_output()
            source = self.source_data
            self.run(lambda cancel, progress: review.save_candidate(source, record, text, cancel, progress),
                     lambda folder: self.status.setText('已另存新版本：' + str(folder / '整理.md')))
        except (ValueError, OSError, KeyError) as error:self.status.setText(str(error))

    def choose_candidate(self):
        if not self.source_data:return
        if self.items:self.status.setText('目前已有候選，請另存後在新視窗載入比較版本。'); return
        path, _ = QFileDialog.getOpenFileName(self, '載入整理候選', str(self.source_data.path.parent), '整理資料 (整理.json)')
        if path:
            def load(cancel, progress):
                record = load_record(Path(path), 'conversation-summary')
                review.validate_candidate(record)
                text = read_local(Path(path).parent/'整理.md',8*1024*1024,cancel).decode('utf-8-sig')
                return record, text
            self.run(load, self.candidate_loaded)

    def candidate_loaded(self, result):
        record, text = result
        self.previous = deepcopy(record); self.request = record['request']
        self.items = deepcopy(record['items']); self.received = set(record['received'])
        self.record = deepcopy(record); self.output.setPlainText(text)
        self.render_grid(); self.tabs.setCurrentIndex(2)
        self.status.setText(review.freshness(record, self.source_data, self.branches.currentData()))

    def export_md(self):
        try:_, text = self.checked_output()
        except (ValueError, OSError, KeyError) as error:self.status.setText(str(error)); return
        path, _ = QFileDialog.getSaveFileName(self, '另存接續 MD（不覆寫既有檔案）', str(self.document.parent/'接續.md'), 'Markdown (*.md)')
        if not path:return
        try:
            from .local_documents.directory_pin import pinned_target
            from .retained_files import _write
            destination = Path(path)
            with pinned_target(destination.parent, destination.name) as target:_write(target, text.encode('utf-8'))
            self.status.setText('已另存：' + str(destination))
        except (ValueError, OSError) as error:self.status.setText(str(error))


def open_review(surface):
    sheet = ReviewSheet(surface); surface.review_sheet = sheet; sheet.show(); return sheet
