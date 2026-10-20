"""Reserve the outline's own space; the document uses the remaining width."""
from PySide6.QtCore import QObject, QEvent, QTimer, Qt, QEasingCurve
from PySide6.QtWidgets import QGraphicsOpacityEffect
from shiboken6 import isValid
from .elapsed_animation import ElapsedAnimation
from .dialog_presentation import reduced_motion


class OutlineLayout(QObject):
    def __init__(self, surface):
        super().__init__(surface)
        self.surface=surface;self.requested=False;self.placement='closed'
        self.offset=0.;self.opacity=1.;self.closing=False
        self.effect=QGraphicsOpacityEffect(surface.outline_panel)
        surface.outline_panel.setGraphicsEffect(self.effect)
        self.motion=ElapsedAnimation(self)
        self.motion.valueChanged.connect(self.frame);self.motion.finished.connect(self.settle)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.reflow)
        surface.content_pane.installEventFilter(self)

    def schedule(self):self.timer.start(0)

    def toggle(self):
        panel=self.surface.outline_panel
        was_shown=not panel.isHidden()
        self.motion.stop()
        self.requested=not self.requested
        if self.requested:self.surface.outline.rebuild(self.surface.page.buffer.snapshot().text)
        self.closing=not self.requested and was_shown
        panel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,not self.requested)
        if reduced_motion(self.surface):
            self.settle();return
        if self.requested and not was_shown:self.offset=-28.;self.opacity=0.
        self.start_offset,self.start_opacity=self.offset,self.opacity
        self.end_offset,self.end_opacity=(0.,1.) if self.requested else (-28.,0.)
        self.reflow()
        self.motion.setDuration(180 if self.requested else 140)
        self.motion.setEasingCurve(QEasingCurve.Type.OutCubic if self.requested else QEasingCurve.Type.InCubic)
        self.motion.setStartValue(0.);self.motion.setEndValue(1.);self.motion.start()

    def frame(self,value):
        self.offset=self.start_offset+(self.end_offset-self.start_offset)*value
        self.opacity=self.start_opacity+(self.end_opacity-self.start_opacity)*value
        if isValid(self.surface.outline_panel):
            self.surface.outline_panel.move(self.base_left+round(self.offset),0)
            self.effect.setOpacity(self.opacity)

    def settle(self):
        self.motion.stop();self.closing=False;self.offset=0.;self.opacity=1.
        self.effect.setOpacity(1.)
        self.reflow()

    def reflow(self):
        s=self.surface
        if not isValid(s.outline_panel) or not isValid(s.content_pane):return
        pane=s.content_pane;width=pane.width();host=252
        measure=s.preferences['reading_width']
        if s.mode=='read' and s.reader.advanced:measure=measure*s.zoom_percent/100
        gutter=s.mode!='source' and (width-measure)/2 >= host
        previous=self.placement
        if not self.requested and not self.closing:self.placement='closed'
        elif gutter:self.placement='gutter'
        else:self.placement='column'
        reserve=host if self.placement=='column' else 0
        if pane.layout().contentsMargins().left()!=reserve:
            pane.layout().setContentsMargins(reserve,0,0,0)
            pane.layout().activate()
        panel=s.outline_panel
        left=max(0,round((width-measure)/2)-host-12) if self.placement=='gutter' else 0
        self.base_left=left
        panel.available_height=pane.height();panel.resize(min(host,width),pane.height());panel.move(left+round(self.offset),0)
        self.effect.setOpacity(self.opacity)
        panel.setVisible(self.requested or self.closing);panel.fit_card();panel.raise_()
        s.outline_button.setChecked(self.requested)
        if previous!=self.placement:QTimer.singleShot(0,s,s.update_title_layout)

    def eventFilter(self, watched, event):
        s=self.surface
        if not isValid(s.outline_panel) or not isValid(s.content_pane):return False
        if watched is s.content_pane:
            if event.type()==QEvent.Type.Hide:
                self.motion.stop();self.closing=False;self.offset=0.;self.opacity=1.
                if not self.requested:s.outline_panel.hide()
            if event.type() in (QEvent.Type.Resize,QEvent.Type.Show,QEvent.Type.Hide):self.schedule()
        return False
