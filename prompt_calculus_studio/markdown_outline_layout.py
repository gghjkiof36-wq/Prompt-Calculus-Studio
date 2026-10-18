"""Place an outline in the reading gutter, a column, or a compact overlay."""
from PySide6.QtCore import QObject, QEvent, QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid


class OutlineLayout(QObject):
    def __init__(self, surface):
        super().__init__(surface)
        self.surface=surface;self.requested=False;self.peek=False;self.placement='closed';self.listening=False
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.reflow)
        surface.content_pane.installEventFilter(self)

    def schedule(self):self.timer.start(0)

    def toggle(self):
        if self.surface.outline_panel.isVisible():self.requested=False;self.peek=False
        else:self.requested=True;self.peek=True
        self.reflow()

    def dismiss(self):
        if self.placement=='overlay':self.peek=False;self.reflow()

    def reflow(self):
        s=self.surface
        if not isValid(s.outline_panel) or not isValid(s.content_pane):return
        pane=s.content_pane;width=pane.width();host=252
        measure=s.preferences['reading_width']
        if s.mode=='read' and s.reader.advanced:measure=measure*s.zoom_percent/100
        gutter=s.mode!='source' and (width-measure)/2 >= host
        narrow=width < host+580
        previous=self.placement
        if not self.requested:self.placement='closed'
        elif gutter:self.placement='gutter';self.peek=False
        elif not narrow:self.placement='column';self.peek=False
        else:self.placement='overlay' if self.peek else 'compact'
        reserve=host if self.placement=='column' else 0
        if pane.layout().contentsMargins().left()!=reserve:
            pane.layout().setContentsMargins(reserve,0,0,0)
        panel=s.outline_panel
        left=max(0,round((width-measure)/2)-host-12) if self.placement=='gutter' else 0
        panel.available_height=pane.height();panel.resize(min(host,width),pane.height());panel.move(left,0)
        panel.fit_card();panel.setVisible(self.placement in ('gutter','column','overlay'));panel.raise_()
        listening=self.placement=='overlay' and panel.isVisible()
        if listening!=self.listening:
            self.listening=listening
            app=QApplication.instance()
            app.installEventFilter(self) if listening else app.removeEventFilter(self)
        s.outline_button.setChecked(panel.isVisible())
        if previous!=self.placement:QTimer.singleShot(0,s,s.update_title_layout)

    def eventFilter(self, watched, event):
        s=self.surface
        if not isValid(s.outline_panel) or not isValid(s.content_pane):return False
        if watched is s.content_pane and event.type() in (QEvent.Type.Resize,QEvent.Type.Show,QEvent.Type.Hide):self.schedule()
        if self.placement=='overlay' and s.outline_panel.isVisible() and isinstance(watched,QWidget):
            if (event.type()==QEvent.Type.KeyPress and event.key()==Qt.Key.Key_Escape
                    and s.isAncestorOf(watched) and watched.window() is s.window()):
                self.dismiss();s.focus_document();return True
            if event.type()==QEvent.Type.MouseButtonPress:
                inside=lambda widget:watched is widget or widget.isAncestorOf(watched)
                if not inside(s.outline_panel) and not inside(s.outline_button):self.dismiss()
        return False
