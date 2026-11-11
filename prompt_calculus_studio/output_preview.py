"""Six-line, wheel-transparent Prompt preview; no editor or scroll area."""
from PySide6.QtCore import Qt,QSize
from PySide6.QtGui import QPainter,QTextLayout,QTextOption,QColor
from PySide6.QtWidgets import QWidget,QSizePolicy


class PromptCache:
    """One input snapshot per canvas, shared by its lightweight output cards.

    Compilation reads the live workspace, not history or inactive scenes.
    Use the existing geometry-only contract so releasing a dragged card and
    host refreshes cannot recompile unchanged text. Copy only on invalidation:
    runtime cursors can mutate their live branches without replacing state.
    """
    def __init__(self):self.inputs=None;self.values={}

    def text(self,state,key):
        from .changes import layout_only
        from .multi_output import compile_output
        import copy
        inputs={k:v for k,v in state.items() if k not in ('content_history','workspace_scenes','canvas_view')}
        if self.inputs is None or not layout_only(self.inputs,inputs):
            self.inputs=copy.deepcopy(inputs);self.values={}
        if key not in self.values:
            try:self.values[key]=compile_output(state,key)['final_prompt']
            except ValueError:self.values[key]=state['multi_output']['outputs'][key]['draft'] or ''
        return self.values[key]


class PromptPreview(QWidget):
    def __init__(self,opened):
        super().__init__();self.opened=opened;self._text='';self.colors={};self.lines=[];self.copy_button=None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus);self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('Prompt 預覽');self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)

    def text(self):return self._text

    def setText(self,text):
        if text==self._text:return
        self._text=text;self.reflow();self.update()

    def reflow(self):
        # Layout only six lines, even for very large manual drafts. Keep full
        # text solely for Copy; dragging/panning only paints the retained lines.
        width=max(40,self.width()-40);self.lines=[]
        text=self._text.replace('\n','\u2028') or '空白'
        layout=QTextLayout(text,self.font());option=QTextOption();option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere);layout.setTextOption(option)
        layout.beginLayout()
        for i in range(6):
            line=layout.createLine()
            if not line.isValid():break
            line.setLineWidth(width);start=line.textStart();length=line.textLength()
            value=text[start:start+length].replace('\u2028','')
            if i==5 and start+length<len(text):value=self.fontMetrics().elidedText(value+'…',Qt.TextElideMode.ElideRight,width)
            self.lines.append(value)
        layout.endLayout();self.updateGeometry()

    def sizeHint(self):return QSize(260,max(36,len(self.lines or [''])*self.fontMetrics().lineSpacing()+8))
    def minimumSizeHint(self):return QSize(0,self.sizeHint().height())

    def resizeEvent(self,event):
        super().resizeEvent(event);self.reflow()
        if self.copy_button:self.copy_button.move(self.width()-32,0)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setFont(self.font());painter.setPen(QColor(self.colors.get('text' if self._text else 'secondary','#999999')))
        if not self.lines:self.reflow()
        for i,line in enumerate(self.lines):painter.drawText(0,4+self.fontMetrics().ascent()+i*self.fontMetrics().lineSpacing(),line)

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.opened();event.accept()
        else:super().mouseReleaseEvent(event)

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_Space):self.opened();event.accept()
        else:super().keyPressEvent(event)

    def wheelEvent(self,event):event.ignore()
