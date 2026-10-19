"""Native heading rows with bounded wrapping and a stable hierarchy."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QPalette, QTextLayout, QTextOption
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QStyle


class HeadingDelegate(QStyledItemDelegate):
    def option(self, option, index):
        result = QStyleOptionViewItem(option)
        self.initStyleOption(result, index)
        result.font.setWeight(QFont.Weight.DemiBold if not index.parent().isValid() else QFont.Weight.Normal)
        return result

    def lines(self, text, font, width):
        layout = QTextLayout(text, font)
        wrapping = QTextOption();wrapping.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(wrapping);layout.beginLayout()
        first = layout.createLine();first.setLineWidth(max(20, width))
        length = first.textLength();layout.endLayout()
        encoded = text.encode('utf-16-le')
        head = encoded[:length*2].decode('utf-16-le')
        tail = encoded[length*2:].decode('utf-16-le').strip()
        return [head] + ([QFontMetrics(font).elidedText(tail, Qt.TextElideMode.ElideRight, max(20,width))] if tail else [])

    def text_width(self, index):
        depth = 1;parent = index.parent()
        while parent.isValid():depth += 1;parent = parent.parent()
        tree = self.parent()
        return max(20, tree.viewport().width()-depth*tree.indentation()-12)

    def sizeHint(self, option, index):
        opt = self.option(option, index)
        lines = self.lines(opt.text, opt.font, self.text_width(index))
        gap = 4 if not index.parent().isValid() and index.row() else 0
        return QSize(0, max(30, len(lines)*QFontMetrics(opt.font).lineSpacing()+12)+gap)

    def paint(self, painter, option, index):
        opt = self.option(option, index);label = opt.text;opt.text = ''
        gap = 4 if not index.parent().isValid() and index.row() else 0
        opt.rect.adjust(0,gap,0,0)
        # HeadingTree owns the row background/focus, including indentation.
        rect = opt.rect.adjusted(6,6,-6,-6)
        lines = self.lines(label, opt.font, rect.width())
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        painter.save();painter.setClipRect(rect);painter.setFont(opt.font)
        painter.setPen(opt.palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text))
        metrics = QFontMetrics(opt.font)
        for n, line in enumerate(lines):painter.drawText(rect.left(),rect.top()+metrics.ascent()+n*metrics.lineSpacing(),line)
        painter.restore()
