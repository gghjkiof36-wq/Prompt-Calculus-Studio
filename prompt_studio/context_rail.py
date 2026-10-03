"""Compact context navigation alongside the shared workspace surface."""
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout

from .theme import visual_tokens
from .ui_icons import icon
from .widgets import button


class ContextRail(QWidget):
    def __init__(self,window):
        super().__init__();self.window=window;self.setObjectName('ContextRail')
        self.setFixedWidth(56);self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        self.body=QVBoxLayout(self);self.body.setContentsMargins(8,10,8,10);self.body.setSpacing(6)
        self.buttons={};self._signature=None;self.peek_trigger=None;self.hide()

    def refresh(self,target,docked):
        self.setVisible(target is not None and not docked)
        if target is None:return
        page=getattr(self.window,'settings_page',None)
        if page is not None and target is page.navigation_area:
            entries=tuple(page.nav_routes);parent=page.return_button.text()
            signature=('navigation',parent,entries)
            current=entries[max(0,page.navigation.currentRow())][0]
        else:
            name,symbol=('資料夾','folder') if target is getattr(getattr(self.window,'gallery',None),'folder_panel',None) else ('圖片來源','media')
            entries=(('sidebar',name,symbol),);parent=None;signature=entries;current=None
            if name=='資料夾':
                entries+=(('add-folder','新增資料夾','plus'),('folder-more','資料夾操作','more-horizontal'))
                signature=entries
        if signature!=self._signature:
            self._signature=signature;self.buttons={};self.peek_trigger=None
            while self.body.count():
                item=self.body.takeAt(0)
                if item.widget():
                    item.widget().hide()
                    item.widget().deleteLater()
            if parent:
                self._add('parent',parent,'back',page.return_parent)
                self.body.addSpacing(12)
            for key,title,symbol in entries:
                callback=(lambda checked=False,k=key:self.window.settings(k)) if parent else self.window.toggle_context_sidebar
                if key=='add-folder':callback=lambda:self.window.gallery.add_album()
                elif key=='folder-more':callback=lambda:self.window.gallery.folder_menu(self.buttons['folder-more'])
                entry=self._add(key,title,symbol,callback)
                if key in ('add-folder','folder-more'):entry.setCheckable(False)
                elif not parent:self.peek_trigger=entry
                elif key in ('appearance','dictionary'):self.body.addSpacing(8)
            self.body.addStretch()
        colors=visual_tokens(self.window.state['settings'])
        for key,entry in self.buttons.items():
            entry.setChecked(key==current)
            entry.setIcon(icon(entry.property('iconName'),colors['text' if key==current else 'secondary']))

    def _add(self,key,title,symbol,callback):
        entry=button('',callback,'ContextRailAction');entry.setFixedSize(40,38)
        entry.setProperty('iconName',symbol);entry.setIconSize(QSize(19,19))
        entry.setCheckable(True);entry.setToolTip(title);entry.setAccessibleName(title)
        entry.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.body.addWidget(entry);self.buttons[key]=entry;return entry

    def contains_peek_trigger(self,position):
        entry=self.peek_trigger
        if not self.isVisible() or entry is None:return False
        return entry.rect().contains(entry.mapFrom(self.window,QPoint(position)))
