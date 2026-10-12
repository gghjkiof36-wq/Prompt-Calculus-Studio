"""Compact source rows; the Canvas and sorting dialog use the same text icon."""
import copy
from PySide6.QtCore import Qt,QSize
from PySide6.QtWidgets import QStyledItemDelegate,QStyleOptionViewItem,QStyle,QApplication
from .ui_icons import icon

DETAIL_ROLE=int(Qt.ItemDataRole.UserRole)+1


class SourceDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        opt=QStyleOptionViewItem(option);self.initStyleOption(opt,index)
        name=opt.text;opt.text=''
        style=opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem,opt,painter,opt.widget)
        rect=style.subElementRect(QStyle.SubElement.SE_ItemViewItemText,opt,opt.widget).adjusted(4,0,-10,0)
        painter.save();painter.setFont(opt.font)
        color=opt.palette.color(opt.palette.ColorRole.Text);painter.setPen(color)
        detail=index.data(DETAIL_ROLE) or '';width=min(rect.width()//2,opt.fontMetrics.horizontalAdvance(detail)+28)
        painter.drawText(rect.adjusted(0,0,-width,0),Qt.AlignmentFlag.AlignVCenter,
                         opt.fontMetrics.elidedText(name,Qt.TextElideMode.ElideRight,max(0,rect.width()-width)))
        painter.setPen(opt.palette.color(opt.palette.ColorRole.PlaceholderText))
        painter.drawText(rect.adjusted(rect.width()-width,0,-22,0),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignRight,detail)
        icon('chevron-right',color.name(),16).paint(painter,rect.right()-14,rect.center().y()-8,16,16)
        painter.restore()

    def sizeHint(self,option,index):return QSize(0,max(42,option.fontMetrics.height()+20))


def source_kind(state,key):
    data=state['multi_output']
    if key in data['canvases']:return '文字畫布'
    if key in data['outputs']:return 'Prompt'
    if key in data.get('stages',{}):return 'Stage'
    if '::' in key:return '預排程'
    return '文字' if state.get('canvas_functions',{}).get('images',{}).get(key,{}).get('reader')=='text' else '圖片提示詞'


def source_text(state,output,key):
    """Compile a disposable projection, preserving every real manual draft."""
    from .multi_output import compile_output
    projection=copy.deepcopy(state);data=projection['multi_output'];item=data['outputs'][output]
    item.update(text_sources=[key],canvases=[key] if key in data['canvases'] else [],
                canvas=key if key in data['canvases'] else None,draft=None)
    projection['draft']=None
    try:return compile_output(projection,output)['generated_prompt']
    except ValueError as exc:return str(exc)
