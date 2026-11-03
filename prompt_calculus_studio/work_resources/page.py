"""Explore → Work resources. Originals, working copies and frozen packs have separate actions."""
from pathlib import Path
from collections import Counter

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QPlainTextEdit, QSizePolicy, QTabWidget, QVBoxLayout, QWidget)

from ..conversation_archive import decode_json
from ..job_outcomes import JobOutcome
from ..management_reading import PREFERRED_CONTENT_WIDTH, READING_MARGIN, READING_NARROW_MARGIN
from ..markdown_source import SourceLedger, python_position, utf16_position
from ..widgets import ComboBox, button, label
from . import bundles
from .catalog import Catalog, now
from .stories import Story


def column(parent=None):
    widget = QWidget(parent)
    body = QVBoxLayout(widget)
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(8)
    return widget, body


def line(layout, *widgets):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        if widget is None:
            row.addStretch()
        else:
            row.addWidget(widget)
    layout.addLayout(row)
    return row


def viewer(name):
    view = QPlainTextEdit()
    view.setObjectName('ResourcePreview')
    view.setReadOnly(True)
    view.setAccessibleName(name)
    view.setPlaceholderText(name)
    return view


def conversation_text(messages, focused=''):
    chunks = []
    position = 0
    focus = None
    labels = {'user':'你','assistant':'助理','system':'系統','tool':'工具','developer':'開發者'}
    for msg in messages:
        if msg['role'] == 'root':
            continue
        part = f"{labels.get(msg['role'],msg['role'])}  ·  {msg['message_id']}\n{msg['text']}\n\n"
        if msg['node_id'] == focused:
            focus = position
        chunks.append(part)
        position += len(part)
    return ''.join(chunks), focus


def content_text(ref, raw):
    if ref['kind'] == 'branch':
        return conversation_text(decode_json(raw)['messages'])[0]
    return raw.decode('utf-8-sig')


class WorkResourcesPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.workspace = host.workspace_owner
        self.store = Catalog(self.workspace.data)
        self.jobs = host.jobs
        self.jobs.became_idle.connect(self.on_idle)
        self.offset = self.ticket = 0
        self.search_pending = False
        self.library_pending = ''
        self.current = self.story = self.raw = None
        self.selections = []
        self.saved_record = None
        self.narrow = self.detail_open = False
        self.outer = QHBoxLayout(self)
        self.outer.setContentsMargins(READING_MARGIN, READING_MARGIN, READING_MARGIN, READING_MARGIN)
        center, body = column()
        center.setMaximumWidth(PREFERRED_CONTENT_WIDTH)
        self.outer.addStretch(); self.outer.addWidget(center,1); self.outer.addStretch()
        self.add_button = button('加入來源', self.add_menu, 'Primary')
        line(body, label('工作資源','DialogTitle'), None, self.add_button)
        self.tabs = QTabWidget()
        body.addWidget(self.tabs,1)
        self.make_browser()
        self.make_draft()
        self.make_library()
        self.status = label('', 'Muted', True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.cancel_button = button('取消處理',self.cancel_job,'Quiet')
        line(body,self.status,None,self.cancel_button)
        self.cancel_button.hide()
        self.search_timer = QTimer(self)
        self.search_timer.setInterval(180)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.search)
        self.filter.textChanged.connect(self.reset_search)
        self.sources.currentIndexChanged.connect(self.reset_search)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.reload_sources()
        self.reset_search()

    def make_browser(self):
        page, layout = column()
        self.tabs.addTab(page,'來源')
        self.sources = ComboBox()
        self.sources.setAccessibleName('來源範圍')
        self.sources.setMinimumWidth(0)
        self.sources.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.manage = button('管理',self.manage_menu,'Quiet')
        line(layout,self.sources,self.manage)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText('搜尋檔名、來源、日期或內文')
        self.filter.setClearButtonEnabled(True)
        layout.addWidget(self.filter)
        self.panes = QHBoxLayout()
        self.panes.setSpacing(12)
        layout.addLayout(self.panes,1)
        self.list_pane, left = column()
        self.results = QListWidget()
        self.results.setAccessibleName('來源結果')
        self.results.itemActivated.connect(self.open_hit)
        self.results.itemClicked.connect(self.open_hit)
        left.addWidget(self.results,1)
        self.previous = button('上一頁', lambda:self.turn(-1),'Quiet')
        self.next = button('下一頁', lambda:self.turn(1),'Quiet')
        self.page_number = label('','Muted')
        line(left,self.previous,None,self.page_number,None,self.next)
        self.detail, right = column()
        self.go_back = button('返回',self.back,'Quiet')
        self.heading = label('預覽','SectionTitle')
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setWordWrap(True)
        line(right,self.go_back,self.heading,None)
        self.scope = label('','Muted',True)
        self.scope.setTextFormat(Qt.TextFormat.PlainText)
        right.addWidget(self.scope)
        self.branches = ComboBox()
        self.branches.setAccessibleName('對話分支')
        self.branches.currentIndexChanged.connect(self.show_branch)
        right.addWidget(self.branches)
        self.preview = viewer('來源原文')
        right.addWidget(self.preview,1)
        self.preview_back = button('上一段',lambda:self.slice_preview(-1),'Quiet')
        self.preview_next = button('下一段',lambda:self.slice_preview(1),'Quiet')
        line(right,self.preview_back,None,self.preview_next)
        self.actions = button('開啟／副本',self.file_menu,'Quiet')
        self.add = button('加入資料包',self.add_selection,'Primary')
        line(right,self.actions,None,self.add)
        self.panes.addWidget(self.list_pane,2); self.panes.addWidget(self.detail,3)
        self.clear_detail()

    def make_draft(self):
        page, body = column()
        self.tabs.addTab(page,'本次資料包')
        self.pack_name = QLineEdit()
        self.pack_name.setPlaceholderText('資料包名稱')
        self.pack_name.setMaxLength(160)
        self.pack_name.setText('工作資料包')
        body.addWidget(self.pack_name)
        self.draft_list = QListWidget()
        self.draft_list.setAccessibleName('已選範圍')
        self.draft_list.currentRowChanged.connect(self.show_draft)
        body.addWidget(self.draft_list,1)
        self.draft_detail = viewer('選取內容與排除範圍')
        body.addWidget(self.draft_detail,1)
        self.capacity = label('','Muted',True)
        self.drop = button('移除',self.drop_selection,'Quiet')
        self.save = button('保存資料包',self.save_draft,'Primary')
        body.addWidget(self.capacity)
        line(body,self.drop,None,self.save)
        self.pack_name.textChanged.connect(self.measure)
        self.measure()

    def make_library(self):
        page, body = column()
        self.tabs.addTab(page,'已保存')
        self.library = ComboBox()
        self.library.setAccessibleName('已保存資料包')
        self.library.currentIndexChanged.connect(self.read_pack)
        body.addWidget(self.library)
        self.saved_items = QListWidget()
        self.saved_items.setAccessibleName('資料包項目')
        self.saved_items.currentRowChanged.connect(self.show_saved)
        body.addWidget(self.saved_items,1)
        self.saved_detail = viewer('保存的原文')
        body.addWidget(self.saved_detail,2)
        self.compare = button('核對目前來源',self.check_current,'Quiet')
        line(body,None,self.compare)

    def say(self,text):
        self.status.setText(text)

    def job(self,title,work,done):
        if self.jobs.active:
            self.say('目前工作仍在處理中。')
            return False
        self.say(title)
        self.cancel_button.show()
        def success(value):
            self.cancel_button.hide()
            self.say('')
            done(value)
        def failed(error):
            self.cancel_button.hide()
            self.say(error)
        return self.jobs.start(title,work,success,failed,announce=False)

    def cancel_job(self):
        if self.jobs.active:
            self.jobs.active.cancel.set()
            self.say('正在取消…')

    def on_idle(self):
        if self.search_pending and self.tabs.currentIndex()==0:
            self.search_timer.start(0)
        elif self.library_pending and self.tabs.currentIndex()==2:
            action=self.library_pending
            self.library_pending=''
            (self.list_packs if action=='list' else self.read_pack)()

    def menu(self,control,options):
        popup = QMenu(self)
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        for text, action in options:
            popup.addAction(text, action)
        popup.popup(control.mapToGlobal(control.rect().bottomLeft()))

    def add_menu(self):
        self.menu(self.add_button,[('引用資料夾',lambda:self.choose('folder')),('匯入對話封存',lambda:self.choose('archive'))])

    def manage_menu(self):
        sid = self.sources.currentData()
        if not sid:
            self.say('請先選取一個來源。')
            return
        self.menu(self.manage,[('重建索引',lambda:self.rebuild(sid)),('重新指定位置',lambda:self.choose(self.store.source(sid)['kind'],sid)),
                               ('移出清單',lambda:self.remove_source(sid))])

    def choose(self,kind,sid=None):
        value = (QFileDialog.getExistingDirectory(self,'選擇來源資料夾') if kind=='folder' else
                 QFileDialog.getOpenFileName(self,'選擇來源檔案','','對話封存 (*.zip *.json *.pcs-chat *.jsonl);;所有檔案 (*)')[0])
        if value:
            self.add_source(value,kind,sid)

    def add_source(self,path,kind,sid=None):
        try:
            if sid:
                self.store.relink(sid,path)
            else:
                sid = self.store.register(path,kind)
            self.tabs.setCurrentIndex(0)
            self.reload_sources(sid)
            self.rebuild(sid)
        except (ValueError,OSError) as error:
            self.say(str(error))

    def remove_source(self,sid):
        self.store.remove(sid)
        self.reload_sources()
        self.reset_search()

    def rebuild(self,sid):
        def done(_):
            selected=self.sources.currentData()
            self.reload_sources(selected)
            self.reset_search()
            self.say(self.store.source(sid)['state'])
        self.job('正在建立索引…', lambda cancel:JobOutcome.completed(self.store.rebuild(sid,cancel)), done)

    def reload_sources(self,selected=''):
        self.sources.blockSignals(True)
        self.sources.clear()
        self.sources.addItem('全部來源','')
        sources=self.store.sources()
        names=Counter(Path(src['path']).name.casefold() for src in sources)
        for src in sources:
            path = Path(src['path'])
            title = path.name
            if names[title.casefold()]>1:
                title += ' · '+path.parent.name
            if src['kind']=='copy':
                title = '管理副本 · '+path.name
            self.sources.addItem(title,src['id'])
            self.sources.setItemData(self.sources.count()-1,src['state']+'\n'+str(path),Qt.ItemDataRole.ToolTipRole)
        self.sources.setCurrentIndex(max(0,self.sources.findData(selected)))
        self.sources.blockSignals(False)
        self.manage.setEnabled(bool(self.sources.currentData()))

    def reset_search(self,*_):
        self.offset = 0
        self.ticket += 1
        self.manage.setEnabled(bool(self.sources.currentData()))
        self.results.clear()
        self.clear_detail()
        self.back()
        self.search_pending = True
        self.search_timer.start()

    def turn(self,step):
        self.offset = max(0,self.offset+50*step)
        self.ticket += 1
        self.clear_detail()
        self.search()

    def search(self):
        if self.jobs.active:
            self.search_pending = True
            return
        self.search_pending = False
        owner = self.ticket
        sid,query,offset = self.sources.currentData() or '',self.filter.text(),self.offset
        def done(rows):
            if owner != self.ticket:
                return
            self.results.clear()
            for row in rows[:50]:
                prefix = '訊息 · ' if row['kind']=='message' else ''
                title = prefix+row['title']
                location = row['location']
                sub = (row['node']+' · ' if row['node'] else '')+Path(location).name+' · '+row['state']
                item = QListWidgetItem(title+'\n'+sub)
                item.setToolTip(location+'\n'+row['title']+'\n'+row['sha'])
                item.setData(Qt.ItemDataRole.UserRole,row)
                self.results.addItem(item)
            self.page_number.setText(f'{offset+1}–{offset+min(50,len(rows))}' if rows else '沒有結果')
            self.previous.setEnabled(offset>0); self.next.setEnabled(len(rows)>50)
            if not rows and sid:
                self.say(self.store.source(sid)['state'])
        self.job('正在搜尋…',lambda cancel:self.store.results(sid,query,offset,51,cancel),done)

    def clear_detail(self):
        self.current = self.story = self.raw = None
        self.heading.setText('預覽')
        self.scope.clear(); self.preview.clear(); self.branches.hide()
        self.preview_back.hide(); self.preview_next.hide()
        self.actions.setEnabled(False); self.add.setEnabled(False)

    def open_hit(self,item):
        row = item.data(Qt.ItemDataRole.UserRole)
        if not row or self.jobs.active:
            return
        self.clear_detail()
        self.current = row
        self.detail_open = True
        self.arrange()
        self.heading.setText(row['title'])
        self.scope.setText(row['state']+' · '+row['location'])
        if row['kind']!='document':
            self.actions.setEnabled(True)
        if not row['story'] and row['kind']!='document':
            return
        def done(result):
            if self.current != row:
                return
            if isinstance(result,Story):
                self.story = result
                self.branches.blockSignals(True)
                self.branches.clear()
                for i,leaf in enumerate(result.branches,1):
                    suffix = ' · 目前' if leaf==result.current else ''
                    self.branches.addItem(f'分支 {i} · {leaf}'+suffix,leaf)
                selected = result.branch_for(row['node']) if row['node'] else result.current
                self.branches.setCurrentIndex(max(0,self.branches.findData(selected)))
                self.branches.blockSignals(False)
                self.branches.show()
                self.show_branch()
            else:
                self.raw,path = result
                self.actions.setEnabled(True)
                self.add.setEnabled(True)
                ledger=SourceLedger(self.raw)
                self.set_preview(ledger.text_of(ledger.pieces))
                self.scope.setText(f'{path.parent.name} · {len(self.raw):,} bytes · 版本 {row["sha"][:12]}')
                self.scope.setToolTip(str(path)+'\n'+row['sha'])
        self.job('正在讀取來源…', lambda cancel:bundles.document(self.store,row,cancel) if row['kind']=='document'
                 else bundles.load_story(self.store,row,cancel),done)

    def show_branch(self,*_):
        leaf = self.branches.currentData()
        if not self.story or not leaf:
            self.add.setEnabled(False)
            return
        msgs = self.story.selected(leaf)
        text,focus = conversation_text(msgs,self.current['node'])
        self.set_preview(text, focus or 0)
        self.scope.setText(f'{len(msgs)} 個節點 · {self.current["sha"][:12]}')
        self.add.setEnabled(True)

    def set_preview(self,text,position=0):
        self.preview_text = text
        self.preview_start = (position//60000)*60000
        self.slice_preview(0,position)

    def slice_preview(self,step,position=None):
        self.preview_start += 60000*step
        text = self.preview_text[self.preview_start:self.preview_start+60000]
        self.preview.setPlainText(text)
        self.preview_back.setVisible(self.preview_start>0)
        self.preview_next.setVisible(len(self.preview_text)>self.preview_start+len(text))
        if position is not None:
            cursor=self.preview.textCursor()
            cursor.setPosition(utf16_position(text,position-self.preview_start))
            self.preview.setTextCursor(cursor)
            self.preview.centerCursor()

    def file_menu(self):
        if not self.current:
            return
        if self.current['kind']!='document':
            self.menu(self.actions,[('匯出封存原件',self.export_original)])
            return
        src = self.store.source(self.current['source'])
        actions = [('開啟原文分頁',self.open_original)]
        if src['kind']=='copy':
            actions.append(('編輯管理副本',self.edit_copy))
        if Path(self.current['title']).suffix.casefold()=='.md':
            actions.append(('複製到工作區',self.copy))
        self.menu(self.actions,actions)

    def export_original(self):
        destination=QFileDialog.getExistingDirectory(self,'選擇保存原件的資料夾')
        if not destination:
            return
        row=dict(self.current)
        from ..retained_files import directory_identity
        ident=directory_identity(destination)
        self.job('正在保存封存原件…',lambda cancel:JobOutcome.completed(
            bundles.export_original(self.store,row,destination,ident,cancel)),lambda path:self.say('原件已保存於 '+str(path)))

    def open_original(self):
        row = dict(self.current)
        def done(result):
            if self.current != row or not self.isVisible():
                return
            raw,path = result
            from ..workspace_shell import Tab
            from .catalog import token
            view=viewer(str(path))
            view.setPlainText(raw.decode('utf-8-sig'))
            view.setToolTip(str(path))
            self.workspace.add_tab(Tab('reference:'+token(),path.name+' · 原文',view,True),replace_current=False)
        self.job('正在核對原件…',lambda cancel:bundles.document(self.store,row,cancel),done)

    def edit_copy(self):
        row = dict(self.current)
        self.open_managed(row)

    def open_managed(self,row):
        # Keep the leaf non-replaceable throughout the ordinary Markdown open,
        # including path matching, buffer construction and recovery handling.
        from .source_io import verified_stream
        from .catalog import root_identity
        from ..conversation_archive import fingerprint
        try:
            src = self.store.source(row['source'])
            if src['kind']!='copy':
                raise ValueError('請先建立管理副本。')
            path = Path(src['path'])
            with verified_stream(path.parent,path.name,root_identity(src)) as (stream,_):
                if fingerprint(stream,None)!=row['sha']:
                    raise ValueError('管理副本已變更，請重建索引。')
                self.workspace.open_path(path,True)
        except (ValueError,OSError) as error:
            self.say(str(error))

    def copy(self):
        row = dict(self.current)
        destination = self.workspace.locations.current
        if not destination:
            self.say('請先在工作區選擇內容資料夾。');return
        from ..retained_files import directory_identity
        try:
            identity=directory_identity(destination)
        except (OSError,ValueError) as error:
            self.say(str(error))
            return
        def done(result):
            path,sid = result
            self.workspace.refresh_tree()
            if self.current == row and self.isVisible() and self.workspace.locations.current == destination:
                self.reload_sources(sid)
                self.reset_search()
                rows=self.store.results(sid)
                if rows:self.open_managed(rows[0])
        self.job('正在建立管理副本…',lambda cancel:JobOutcome.completed(
            bundles.copy_markdown(self.store,row,destination,cancel,expected=identity)),done)

    def add_selection(self):
        if not self.current:
            return
        row = dict(self.current)
        if self.story:
            self.append_selection(bundles.select_branch(row,self.story,self.branches.currentData()))
            return
        cursor = self.preview.textCursor()
        span = None
        if cursor.hasSelection():
            text = self.preview_text[self.preview_start:self.preview_start+60000]
            span = tuple(self.preview_start+python_position(text,p) for p in (cursor.selectionStart(),cursor.selectionEnd()))
        self.job('正在固定選取範圍…',lambda cancel:bundles.select_document(self.store,row,span,cancel),
                 lambda result:self.append_selection(result) if self.current==row else None)

    def append_selection(self,selection):
        if selection in self.selections:
            self.say('此範圍已在資料包中。');return
        try:
            bundles.prepared([*self.selections,selection], key='0'*32, title=self.pack_name.text() or '工作資料包', created=now())
        except ValueError as error:
            self.say(str(error))
            return
        self.selections.append(selection)
        self.draft_list.addItem(selection.ref['title']+' · '+selection.ref['scope'])
        self.draft_list.setCurrentRow(len(self.selections)-1)
        self.measure()
        self.say(f'已加入資料包 · {len(self.selections)} 項')

    def measure(self,*_):
        if not self.selections:
            self.capacity.setText('尚未選取內容'); self.drop.setEnabled(False); self.save.setEnabled(False)
        else:
            self.drop.setEnabled(True)
            try:
                _,size=bundles.prepared(self.selections,key='0'*32,title=self.pack_name.text(),created=now())
                self.capacity.setText(f'{len(self.selections)} 項 · 含清單 {size:,} bytes ／ 64 MiB')
                self.capacity.setToolTip('包含選取內容與來源清單。此階段尚未檢查模型容量。')
                self.save.setEnabled(True)
            except ValueError as error:
                self.capacity.setText(str(error)); self.save.setEnabled(False)
        self.tabs.setTabText(1,'本次資料包'+(f' · {len(self.selections)}' if self.selections else ''))

    def show_item(self,view,ref,raw):
        detail = (f"位元組 {ref['range'][0]}–{ref['range'][1]}（結尾不含） · 排除 {ref['excluded_bytes']:,} bytes"
                  if ref['kind']=='document' else f"分支 {ref['leaf']} · {ref['nodes']} 個節點 · 排除 {ref['excluded_nodes']} 個節點")
        notes = '\n'.join(ref['notes'])
        view.setPlainText(f"{ref['title']} · {ref['version'][:12]}\n{detail}\n{notes}\n\n"+content_text(ref,raw))

    def show_draft(self,index):
        if 0<=index<len(self.selections):
            item=self.selections[index]
            self.show_item(self.draft_detail,item.ref,item.content)
        else:
            self.draft_detail.clear()

    def drop_selection(self):
        i=self.draft_list.currentRow()
        if i>=0:
            self.selections.pop(i); self.draft_list.takeItem(i); self.show_draft(self.draft_list.currentRow()); self.measure()

    def save_draft(self):
        chosen=tuple(self.selections)
        title=self.pack_name.text()
        def done(key):
            if tuple(self.selections)==chosen:
                self.selections.clear(); self.draft_list.clear(); self.measure()
            self.requested_pack=key
            self.tabs.setCurrentIndex(2)
        self.job('正在保存資料包…',lambda cancel:JobOutcome.completed(bundles.save(self.store,chosen,title,cancel)),done)

    def tab_changed(self,i):
        if i==0:
            self.reset_search()
        elif i==2:
            self.list_packs()

    def list_packs(self):
        if self.jobs.active:
            self.library_pending='list'
            return
        self.library_pending=''
        def done(rows):
            self.library.blockSignals(True);self.library.clear()
            for date,title,key in rows:
                self.library.addItem(title+' · '+date[:10],key)
            index=self.library.findData(getattr(self,'requested_pack',None))
            self.library.setCurrentIndex(max(0,index))
            self.library.blockSignals(False)
            self.read_pack()
        if not self.jobs.active:
            self.job('正在讀取已保存資料包…',lambda cancel:bundles.saved(self.store),done)

    def read_pack(self,*_):
        key=self.library.currentData()
        self.saved_record=None; self.saved_items.clear(); self.saved_detail.clear(); self.compare.setEnabled(False)
        if not key:return
        if self.jobs.active:
            self.library_pending='read'
            return
        def done(result):
            if self.library.currentData()!=key:return
            self.saved_record=result
            for ref in result[0]['selections']:
                self.saved_items.addItem(ref['title']+' · '+ref['scope'])
            self.saved_items.setCurrentRow(0)
            self.compare.setEnabled(True)
        self.job('正在核對資料包…',lambda cancel:bundles.open_bundle(self.store,key,cancel),done)

    def show_saved(self,index):
        if self.saved_record and index>=0:
            record,raw=self.saved_record
            self.show_item(self.saved_detail,record['selections'][index],raw[index])

    def check_current(self):
        i=self.saved_items.currentRow()
        if self.saved_record and i>=0:
            record=self.saved_record
            ref=record[0]['selections'][i]
            self.job('正在核對來源…',lambda cancel:bundles.current_state(self.store,ref,cancel),
                     lambda state:self.say(state) if record is self.saved_record and i==self.saved_items.currentRow() else None)

    def back(self):
        self.detail_open=False
        self.arrange()

    def arrange(self):
        self.list_pane.setVisible(not self.narrow or not self.detail_open)
        self.detail.setVisible(not self.narrow or self.detail_open)
        self.go_back.setVisible(self.narrow)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.narrow=event.size().width()<760
        margin=READING_NARROW_MARGIN if self.narrow else READING_MARGIN
        self.outer.setContentsMargins(margin,margin,margin,margin)
        self.arrange()
