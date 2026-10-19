"""Reserve the outline's own space; the document uses the remaining width."""
from PySide6.QtCore import QObject, QEvent, QTimer
from shiboken6 import isValid


class OutlineLayout(QObject):
    def __init__(self, surface):
        super().__init__(surface)
        self.surface=surface;self.requested=False;self.placement='closed'
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.reflow)
        surface.content_pane.installEventFilter(self)

    def schedule(self):self.timer.start(0)

    def toggle(self):
        self.requested=not self.requested
        if self.requested:self.surface.outline.rebuild(self.surface.page.buffer.snapshot().text)
        self.reflow()

    def reflow(self):
        s=self.surface
        if not isValid(s.outline_panel) or not isValid(s.content_pane):return
        pane=s.content_pane;width=pane.width();host=252
        measure=s.preferences['reading_width']
        if s.mode=='read' and s.reader.advanced:measure=measure*s.zoom_percent/100
        gutter=s.mode!='source' and (width-measure)/2 >= host
        previous=self.placement
        if not self.requested:self.placement='closed'
        elif gutter:self.placement='gutter'
        else:self.placement='column'
        reserve=host if self.placement=='column' else 0
        if pane.layout().contentsMargins().left()!=reserve:
            pane.layout().setContentsMargins(reserve,0,0,0)
            pane.layout().activate()
        panel=s.outline_panel
        left=max(0,round((width-measure)/2)-host-12) if self.placement=='gutter' else 0
        panel.available_height=pane.height();panel.resize(min(host,width),pane.height());panel.move(left,0)
        panel.setVisible(self.requested);panel.fit_card();panel.raise_()
        s.outline_button.setChecked(panel.isVisible())
        if previous!=self.placement:QTimer.singleShot(0,s,s.update_title_layout)

    def eventFilter(self, watched, event):
        s=self.surface
        if not isValid(s.outline_panel) or not isValid(s.content_pane):return False
        if watched is s.content_pane and event.type() in (QEvent.Type.Resize,QEvent.Type.Show,QEvent.Type.Hide):self.schedule()
        return False
