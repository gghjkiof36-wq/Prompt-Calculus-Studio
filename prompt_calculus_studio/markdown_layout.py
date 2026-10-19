"""Viewport paragraph layout with the editor's existing block spacing."""
from PySide6.QtCore import QPointF, QRectF, Qt
import weakref
from PySide6.QtWidgets import QPlainTextDocumentLayout


class SourceLayout(QPlainTextDocumentLayout):
    def __init__(self,document,view):
        super().__init__(document);self.view=weakref.ref(view)

    def blockBoundingRect(self, block):
        bounds=super().blockBoundingRect(block)
        if not block.isValid() or not block.isVisible():return bounds
        layout=block.layout();fmt=block.blockFormat()
        if not layout.lineCount():return bounds
        # QPlainTextDocumentLayout virtualizes paragraphs but ignores their
        # spacing. Apply those metrics to the visible paragraph's QTextLayout;
        # its cursor, selection and hit testing use these same line positions.
        y=fmt.topMargin();left=fmt.leftMargin();right=fmt.rightMargin()
        option=layout.textOption()
        justify=bool(fmt.alignment() & Qt.AlignmentFlag.AlignJustify)
        view=self.view()
        wrapping=bool(view and view.lineWrapMode()==view.LineWrapMode.WidgetWidth)
        viewport_width=view.viewport().width()-2*self.document().documentMargin() if view else bounds.width()
        native_width=viewport_width if wrapping else max(viewport_width,layout.lineAt(0).naturalTextWidth()+left+right)
        width=max(1,native_width-left-right)
        if (left or right or justify) and (abs(layout.lineAt(0).width()-width)>.01 or bool(option.alignment() & Qt.AlignmentFlag.AlignJustify)!=justify):
            option.setAlignment(Qt.AlignmentFlag.AlignJustify if justify else Qt.AlignmentFlag.AlignLeft)
            layout.setTextOption(option);layout.beginLayout()
            while True:
                line=layout.createLine()
                if not line.isValid():break
                line.setLeadingIncluded(True);line.setLineWidth(width)
            layout.endLayout()
            if block.lineCount()!=layout.lineCount():
                block.setLineCount(layout.lineCount());self.documentSizeChanged.emit(self.documentSize())
        for number in range(layout.lineCount()):
            line=layout.lineAt(number)
            base_x=self.document().documentMargin()
            alignment=fmt.alignment()
            available=line.width() if wrapping else width
            x=base_x+left
            if alignment & Qt.AlignmentFlag.AlignHCenter:x+=(available-line.naturalTextWidth())/2
            elif alignment & Qt.AlignmentFlag.AlignRight:x+=available-line.naturalTextWidth()
            line.setPosition(QPointF(x,y))
            height=line.height()
            y+=fmt.lineHeight(height,1.0) if fmt.lineHeight() else height
        return QRectF(0,0,bounds.width(),(max(y,layout.boundingRect().bottom()) if block.text().strip() else y)+fmt.bottomMargin())
