"""Notebook navigation inside the existing sidebar, with root-scoped results."""
from pathlib import Path
import json,re
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLineEdit, QTreeWidget, QTreeWidgetItem, QToolButton, QLabel, QAbstractItemView
from .widgets import RoundMenu as QMenu, ComboBox as QComboBox
from .ui_icons import icon
from .notebook_index import Query, snippet

MODES = {'files':'內容', 'search':'搜尋', 'tags':'標籤', 'properties':'屬性',
         'links':'連結', 'footnotes':'腳註', 'bookmarks':'書籤'}


class NotebookPanel(QFrame):
    def __init__(self, manager):
        super().__init__(); self.manager=manager; self.mode='files'; self.records={}
        self.setObjectName('NotebookPanel')
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(8)
        self.root=QComboBox(); self.root.setAccessibleName('筆記資料夾')
        layout.addWidget(self.root); self.root.currentIndexChanged.connect(manager.schedule)
        search=QHBoxLayout(); search.setSpacing(4)
        self.query=QLineEdit(); self.query.setAccessibleName('搜尋筆記'); self.query.setPlaceholderText('搜尋')
        self.query.textChanged.connect(manager.schedule); search.addWidget(self.query,1)
        self.case=QToolButton(); self.case.setText('Aa'); self.case.setCheckable(True)
        self.case.setAccessibleName('區分大小寫'); self.case.setToolTip('區分大小寫'); self.case.clicked.connect(manager.schedule)
        search.addWidget(self.case)
        self.save_query=QToolButton(); self.save_query.setIcon(icon('bookmark')); self.save_query.setAccessibleName('收藏搜尋')
        self.save_query.setToolTip('收藏搜尋'); self.save_query.clicked.connect(manager.bookmark_search); search.addWidget(self.save_query)
        self.search_host=QFrame(); self.search_host.setLayout(search); search.setContentsMargins(0,0,0,0); layout.addWidget(self.search_host)
        self.results=QTreeWidget(); self.results.setObjectName('NotebookResults'); self.results.setHeaderHidden(True)
        self.results.setIndentation(13); self.results.setWordWrap(True); self.results.setUniformRowHeights(False)
        self.results.setFrameShape(QFrame.Shape.NoFrame); self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        from .notebook_items import NotebookDelegate
        self.results.setItemDelegate(NotebookDelegate(self))
        self.results.itemActivated.connect(self.activate); self.results.itemClicked.connect(self.activate)
        self.results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self.context_menu); layout.addWidget(self.results,1)
        self.status=QLabel(); self.status.setWordWrap(True); self.status.setObjectName('Subtle'); layout.addWidget(self.status)
        self.hide()

    def roots(self):
        current=self.root.currentData(); self.root.blockSignals(True); self.root.clear()
        for root in self.manager.w.locations.roots:self.root.addItem(root.name,str(root))
        preferred=self.manager.current_root()
        index=self.root.findData(current or (str(preferred) if preferred else ''))
        self.root.setCurrentIndex(max(0,index)); self.root.blockSignals(False)
        self.root.setVisible(self.root.count()>1)

    def selected_root(self):
        value=self.root.currentData()
        return Path(value) if value else self.manager.current_root()

    def group(self, text, parent=None):
        item=QTreeWidgetItem([text]); (parent or self.results).addChild(item) if parent else self.results.addTopLevelItem(item)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        font=item.font(0); font.setBold(True); item.setFont(0,font); item.setExpanded(True)
        item.setSizeHint(0,QSize(0,32)); return item

    def entry(self, text, data, parent=None, detail='',kind=''):
        from .notebook_items import DETAIL,KIND
        item=QTreeWidgetItem([text]); item.setData(0,Qt.ItemDataRole.UserRole,data)
        item.setData(0,DETAIL,detail);item.setData(0,KIND,kind)
        item.setToolTip(0,text+('\n'+detail if detail else '')); item.setSizeHint(0,QSize(0,52 if detail else 30))
        if parent:parent.addChild(item)
        else:self.results.addTopLevelItem(item)
        return item

    def note(self, record, position=0, parent=None, detail=''):
        return self.entry(record.path.stem, {'path':str(record.path),'position':position},parent,detail)

    def populate(self, records, relation_data=None, errors=()):
        errors=tuple(errors)
        context=(self.mode,str(self.selected_root()),self.query.text(),self.case.isChecked(),
                 str(self.manager.current_path()) if self.mode in ('links','properties','footnotes') else None,
                 json.dumps(self.manager.bookmarks(),sort_keys=True) if self.mode=='bookmarks' else '')
        same=getattr(self,'_context',None)==context
        if same and self.records==records and getattr(self,'_related',None)==relation_data and getattr(self,'_errors',None)==errors:return
        def walk(parent,prefix=()):
            occurrences={}
            for index in range(parent.childCount()):
                item=parent.child(index);data=item.data(0,Qt.ItemDataRole.UserRole)
                stable=dict(data) if isinstance(data,dict) else {}
                stable.pop('position',None);stable.pop('bookmark',None)
                title=re.sub(r'\s+\d+$','',item.text(0)) if not data or 'query' in stable else item.text(0)
                identity=title+'|'+json.dumps(stable,sort_keys=True)
                ordinal=occurrences.get(identity,0);occurrences[identity]=ordinal+1
                key=prefix+((identity,ordinal),)
                yield key,item
                yield from walk(item,key)
        states={key:(item.isExpanded(),item is self.results.currentItem()) for key,item in walk(self.results.invisibleRootItem())} if same else {}
        scroll=self.results.verticalScrollBar().value() if same else 0
        self.results.setUpdatesEnabled(False);self.results.blockSignals(True)
        try:
            self._populate(records,relation_data,errors)
            for key,item in walk(self.results.invisibleRootItem()):
                if key in states:
                    expanded,current=states[key];item.setExpanded(expanded)
                    if current:self.results.setCurrentItem(item)
            self.results.doItemsLayout();self.results.verticalScrollBar().setValue(scroll)
            self._context=context;self._related=relation_data;self._errors=errors
        finally:self.results.blockSignals(False);self.results.setUpdatesEnabled(True)

    def _populate(self, records, relation_data=None, errors=()):
        self.records=records; self.results.clear(); self.status.clear(); self.status.hide()
        current=self.manager.current_path(); active=records.get(str(current)); count=0
        if self.mode=='search':
            try:query=Query(self.query.text(),self.case.isChecked())
            except ValueError as error:self.show_status(str(error)); return
            if not self.query.text().strip():return
            for note in records.values():
                if query.matches(note):
                    count+=1
                    if count<=500:
                        position=query.position(note)
                        self.note(note,position,detail=snippet(note.text,position,72))
            self.show_status(str(count)+' 個檔案'+(' · 顯示前 500 個' if count>500 else ''))
        elif self.mode=='tags':
            nodes={}; tags={}
            for note in records.values():
                for tag in note.tags:
                    pieces=tag.split('/')
                    for i in range(1,len(pieces)+1):tags.setdefault('/'.join(pieces[:i]),set()).add(str(note.path))
            for tag in sorted(tags):
                parent=nodes.get(tag.rpartition('/')[0]); node=self.group(tag.split('/')[-1]+'  '+str(len(tags[tag])),parent)
                nodes[tag]=node; node.setData(0,Qt.ItemDataRole.UserRole,{'query':'tag:"'+tag+'"'})
                node.setFlags(node.flags()|Qt.ItemFlag.ItemIsSelectable)
                if '/' in tag:node.setExpanded(False)
        elif self.mode=='properties':
            if active:
                group=self.group(active.path.stem)
                for key,value in active.properties.items():
                    display=' · '.join(map(str,value)) if isinstance(value,list) else json.dumps(value,ensure_ascii=False) if isinstance(value,dict) else ('✓' if value else '—') if isinstance(value,bool) else str(value if value is not None else '')
                    self.entry(key,{'properties':True},group,display,'property')
            keys={}
            for note in records.values():
                for key in note.properties:keys[key]=keys.get(key,0)+1
            group=self.group('資料夾屬性')
            for key,total in sorted(keys.items()):self.entry(key+'  '+str(total),{'query':'['+key+']'},group)
        elif self.mode=='links':
            incoming,outgoing,mentions=relation_data or ([],[],[])
            for title,entries in [('反向連結',incoming),('未連結提及',mentions)]:
                group=self.group(title+'  '+str(len(entries)))
                for note,position,detail in entries[:250]:self.note(note,position,group,detail[:72])
            group=self.group('連出連結  '+str(len(outgoing)))
            for note,link,targets,anchor in outgoing[:250]:
                self.entry(link.label or link.target,{'link':link.target,'wiki':link.wiki},group,
                           '找不到筆記' if not targets and not link.target.startswith(('https:','http:')) else '')
        elif self.mode=='footnotes' and active:
            import re
            from .markdown_links import prose_mask
            masked=prose_mask(active.text)
            for name,position,detail in active.footnotes:
                group=self.group(name)
                self.entry(detail or name,{'path':str(current),'position':position},group)
                for index,match in enumerate(re.finditer(re.escape('[^'+name+']')+r'(?!:)',masked)):
                    self.entry('引用 '+str(index+1),{'path':str(current),'position':match.start()},group)
        elif self.mode=='bookmarks':
            for index,entry in enumerate(self.manager.bookmarks()):self.entry(entry['title'],dict(entry,bookmark=index))
        if not self.results.topLevelItemCount() and self.mode!='search':self.show_status('沒有項目')
        if errors:self.show_status(str(len(errors))+' 個檔案無法讀取')

    def show_status(self,text):self.status.setText(text); self.status.setVisible(bool(text))

    def activate(self,item,*_):
        data=item.data(0,Qt.ItemDataRole.UserRole)
        if not data:return
        if 'query' in data:
            self.manager.show('search'); self.query.setText(data['query']); self.case.setChecked(data.get('case',False)); self.manager.schedule()
        elif 'properties' in data:
            if self.manager.surface():self.manager.surface().edit_properties()
        elif 'link' in data:
            from .markdown_relations import open_link
            if self.manager.surface():open_link(self.manager.surface(),data['link'],wiki=data['wiki'])
        elif 'path' in data:self.manager.open_note(Path(data['path']),data.get('position',0),data.get('heading'))

    def context_menu(self,point):
        item=self.results.itemAt(point); data=item.data(0,Qt.ItemDataRole.UserRole) if item else None
        if not data:return
        menu=QMenu(self)
        if 'path' in data:
            menu.addAction('預覽',lambda:self.manager.preview(Path(data['path']),data.get('position',0)))
            menu.addAction('開啟',lambda:self.activate(item))
        if 'bookmark' in data:menu.addAction('移除書籤',lambda:self.manager.remove_bookmark(data['bookmark']))
        if menu.actions():menu.open_at(self.results.viewport().mapToGlobal(point))
