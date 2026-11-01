"""Canvas-local detail pages; the original document remains the edit owner."""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence, QShortcut, QPainter, QColor
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QSizePolicy
from shiboken6 import isValid
from .widgets import button, ElidedLabel
from .ui_icons import icon


class ModulePage(QFrame):
    PANEL_WIDTH = 920
    PANEL_HEIGHT = 700

    def __init__(self, canvas, title, content, on_close=None, *, kind):
        previous = getattr(canvas, '_module_page', None)
        if previous is not None and isValid(previous):
            previous.finish()
        super().__init__(canvas)
        self.canvas, self.content, self.on_close = canvas, content, on_close
        self.kind, self.workspace = kind, canvas.window.state['workspace']
        self.closed = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.surface = QFrame(self)
        self.surface.setObjectName('DialogSurface')
        body = self.body = QVBoxLayout(self.surface)
        body.setContentsMargins(24, 20, 24, 24)
        body.setSpacing(16)
        self.back = button('', self.finish, 'DialogClose')
        self.back.setIcon(icon('close'));self.back.setProperty('iconName','close')
        self.back.setFixedSize(36,36);self.back.setAccessibleName('關閉');self.back.setToolTip('關閉')
        self.heading = ElidedLabel(title);self.heading.setObjectName('DialogTitle')
        self.heading.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        header=QHBoxLayout();header.setContentsMargins(0,0,0,0)
        header.addWidget(self.heading,1);header.addWidget(self.back)
        body.addLayout(header)
        from .module_chrome import prompt_divider
        self.refresh_visual_theme()
        body.addWidget(prompt_divider())
        body.addWidget(content, 1)
        self.escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape.activated.connect(self.finish)
        canvas._module_page = self
        canvas.installEventFilter(self)
        self.fit()
        self.show()
        self.raise_()
        self.back.setFocus()

    def refresh_visual_theme(self):
        from .module_chrome import apply_module_dialog,dialog_context,dialog_colors
        colors,heading_size=dialog_context(self.canvas.window)
        signature=(colors,heading_size)
        if getattr(self,'_theme',None)==signature:return
        self._theme=signature
        apply_module_dialog(self,colors,heading_size)
        t=dialog_colors(colors)
        self.setStyleSheet(self.styleSheet()+f'''
            QPushButton#Quiet:checked {{background:{t['selected']};color:{t['text']};}}
            QListWidget#ScheduleGroupsList {{background:transparent;border:0;padding:0;outline:0;}}
            QListWidget#ScheduleGroupsList::item {{background:transparent;border:0;padding:0;}}
            QTreeWidget#ModuleSourceOrder {{background:transparent;border:0;padding:0;}}
        ''')
        self.body.setContentsMargins(24,18,24,20);self.body.setSpacing(14)

    def fit(self):
        self.setGeometry(self.canvas.rect())
        gap=32 if self.width()>=800 else 16
        width=min(self.PANEL_WIDTH,max(1,self.width()-gap*2))
        adapt=getattr(self.content,'adapt_width',None)
        if adapt:adapt(width-48)
        height=min(self.PANEL_HEIGHT,max(1,self.height()-gap*2))
        self.surface.setGeometry((self.width()-width)//2,(self.height()-height)//2,width,height)

    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor(0,0,0,144))

    def mousePressEvent(self,event):
        self._outside_press=not self.surface.geometry().contains(event.position().toPoint());event.accept()

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and getattr(self,'_outside_press',False) and not self.surface.geometry().contains(event.position().toPoint()):self.finish()
        self._outside_press=False
        event.accept()

    def validate_owner(self):
        if (self.canvas.window.state['workspace'] != self.workspace
                or self.content.key not in self.canvas.data().get(self.kind, {})):
            self.finish()
            return False
        return True

    def eventFilter(self, watched, event):
        if watched is self.canvas:
            if event.type() == QEvent.Type.Resize:
                self.fit()
            elif event.type() == QEvent.Type.Hide:
                self.finish()
        return False

    def finish(self):
        if self.closed:
            return
        self.closed = True
        self.canvas.removeEventFilter(self)
        if getattr(self.canvas, '_module_page', None) is self:
            self.canvas._module_page = None
        self.hide()
        if isValid(self.canvas.view) and not self.canvas.window.closing:
            self.canvas.view.setFocus()
        if self.on_close:
            self.on_close()
        self.deleteLater()
