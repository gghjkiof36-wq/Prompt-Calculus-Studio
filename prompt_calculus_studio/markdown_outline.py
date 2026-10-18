"""A scoped heading tree; hierarchy and navigation share source positions."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem
from .markdown_commands import heading_sections, move_section_edits
from .markdown_syntax import visible_text
from .markdown_outline_items import HeadingDelegate


class HeadingTree(QTreeWidget):
    def __init__(self, surface):
        super().__init__();self.surface = surface;self.items = []
        self.setObjectName('MarkdownOutlineTree');self.setAccessibleName('文件大綱')
        self.setHeaderHidden(True);self.setIndentation(12);self.setUniformRowHeights(False)
        self.setItemDelegate(HeadingDelegate(self))
        self.setAnimated(False);self.setExpandsOnDoubleClick(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.itemClicked.connect(self.navigate);self.itemActivated.connect(self.navigate)

    def rebuild(self, text):
        sections = heading_sections(text,self.surface.editor.lines if hasattr(self.surface,'editor') else None)
        if getattr(self, 'sections', None) == sections:return
        old=getattr(self,'sections',[])
        if len(old)==len(sections) and all(a[0]==b[0] and a[2:4]==b[2:4] for a,b in zip(old,sections)):
            for item,a,b in zip(self.items,old,sections):
                if a!=b:item.setData(0,Qt.ItemDataRole.UserRole,b)
            self.sections=sections;self.sync_current();return
        collapsed = {tuple(item.data(0, Qt.ItemDataRole.UserRole)[2:4]) for item in self.items
                     if item.childCount() and not item.isExpanded()}
        self.blockSignals(True)
        self.sections = sections;self.clear();self.items = [];parents = []
        for section in sections:
            start, end, level, title = section[:4]
            while parents and parents[-1][0] >= level:parents.pop()
            item = QTreeWidgetItem([visible_text(title)])
            item.setData(0, Qt.ItemDataRole.UserRole, section)
            item.setToolTip(0, visible_text(title))
            if parents:parents[-1][1].addChild(item)
            else:self.addTopLevelItem(item)
            self.items.append(item);parents.append((level, item))
        for item in self.items:item.setExpanded(tuple(item.data(0, Qt.ItemDataRole.UserRole)[2:4]) not in collapsed)
        self.blockSignals(False)
        self.sync_current()
        self.surface.outline_panel.fit_card()

    def sync_current(self):
        from .markdown_source import python_position
        if not hasattr(self.surface, 'editor'):return
        position = python_position(self.surface.page.buffer.snapshot().text, self.surface.editor.textCursor().position())
        current = None
        for item in self.items:
            if item.data(0, Qt.ItemDataRole.UserRole)[0] <= position:current = item
            else:break
        self.setCurrentItem(current)

    def navigate(self, item, column=0):
        self.surface.navigate(item.data(0, Qt.ItemDataRole.UserRole)[0])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.scheduleDelayedItemsLayout()
        QTimer.singleShot(0,self,self.surface.outline_panel.fit_card)

    def dropEvent(self, event):
        source, target = self.currentItem(), self.itemAt(event.position().toPoint())
        if self.surface.mode == 'read' or not source or not target or source is target:return event.ignore()
        start, end = source.data(0, Qt.ItemDataRole.UserRole)[:2]
        destination = target.data(0, Qt.ItemDataRole.UserRole)[0]
        if start <= destination < end:return event.ignore()
        snapshot = self.surface.page.buffer.snapshot()
        self.surface.apply(move_section_edits(snapshot, start, end, destination),
                           destination if destination < start else destination-(end-start))
        event.accept()


class OutlinePanel(QFrame):
    def __init__(self, surface):
        super().__init__();self.surface=surface;self.available_height=600;self.setObjectName('MarkdownOutlineHost')
        outer=QVBoxLayout(self);outer.setContentsMargins(16,20,4,12);outer.setSpacing(0)
        self.card=QFrame();self.card.setObjectName('MarkdownOutlinePanel');outer.addWidget(self.card);outer.addStretch(1)
        layout = QVBoxLayout(self.card);layout.setContentsMargins(8,10,8,10);layout.setSpacing(8)
        header = QHBoxLayout();header.setSpacing(4)
        title = QLabel('大綱');title.setObjectName('MarkdownPanelTitle');header.addWidget(title);header.addStretch()
        self.tree = HeadingTree(surface)
        header.addWidget(surface.tool('收合大綱', self.tree.collapseAll, 'fold-all'))
        header.addWidget(surface.tool('關閉大綱', surface.toggle_outline, 'close'))
        layout.addLayout(header);layout.addWidget(self.tree)
        self.tree.itemExpanded.connect(lambda _:self.fit_card());self.tree.itemCollapsed.connect(lambda _:self.fit_card())

    def fit_card(self):
        if self.isHidden():return
        def shown(item):
            parent=item.parent()
            while parent:
                if not parent.isExpanded():return False
                parent=parent.parent()
            return True
        self.tree.doItemsLayout()
        rows=0
        for item in self.tree.items:
            if shown(item):rows+=self.tree.sizeHintForIndex(self.tree.indexFromItem(item)).height()
            if rows>=self.available_height:break
        self.card.setFixedHeight(min(max(96,self.available_height-32),58+max(30,rows)))
        self.resize(self.width(),self.card.height()+32)
        self.layout().activate();self.card.layout().activate()

    def resizeEvent(self, event):super().resizeEvent(event);self.fit_card()
