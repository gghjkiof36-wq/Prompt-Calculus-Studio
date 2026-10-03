"""Aligned model rows: identity, model type and file/source status."""
from PySide6.QtCore import Qt, QSize, QRectF
from PySide6.QtGui import QColor, QFont, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from .civitai_assets import category_for
from .ui_icons import icon
from .theme import visual_tokens


def model_row_columns(rect):
    """Collapse metadata columns by available row width, never by screen size."""
    compact=rect.width()<520
    preview_size=60 if compact else 72
    preview=QRectF(rect.left()+12,rect.center().y()-preview_size/2,preview_size,preview_size)
    left=preview.right()+16; right=rect.right()-14
    columns={'preview':preview}
    if rect.width()>=700:
        columns['type']=QRectF(right-356,rect.top(),164,rect.height())
        columns['status']=QRectF(right-180,rect.top(),180,rect.height())
        right-=372
    elif not compact:
        columns['status']=QRectF(right-180,rect.top(),180,rect.height())
        right-=196
    columns['name']=QRectF(left,rect.top(),max(0,right-left),rect.height())
    return columns


class ModelLibraryDelegate(QStyledItemDelegate):
    def sizeHint(self,option,index):
        return QSize(240,max(92,option.fontMetrics.height()*3+24))

    def paint(self,painter,option,index):
        painter.save()
        owner=self.parent().window()
        settings=dict(getattr(owner,'state',{}).get('settings',{}))
        settings.update(getattr(owner,'appearance_preview',{}))
        tokens=visual_tokens(settings)
        rect=option.rect.adjusted(1,2,-9,-2)
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        hovered=bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setPen(Qt.PenStyle.NoPen)
        if selected or hovered:
            painter.setBrush(QColor(tokens['selected'] if selected else tokens['hover']))
            painter.drawRoundedRect(QRectF(rect),10,10)
        columns=model_row_columns(rect)
        thumb=columns['preview']; preview=index.data(Qt.ItemDataRole.DecorationRole)
        if preview and not preview.isNull():preview.paint(painter,thumb.toRect(),Qt.AlignmentFlag.AlignCenter)
        else:
            painter.setBrush(QColor(tokens['surface'])); painter.drawRoundedRect(thumb,8,8)
            icon('package',tokens['secondary']).paint(painter,thumb.adjusted(20,20,-20,-20).toRect())
        record=index.data(Qt.ItemDataRole.UserRole) or {}
        source=record.get('civitai') or {}
        display=str(index.data(Qt.ItemDataRole.DisplayRole) or '').split('\n')
        state=display[2].split(' · ') if len(display)>2 else []
        file_state=state[1] if len(state)>1 else '檔案狀態未提供'
        source_state=' · '.join(state[2:]) if len(state)>2 else '未連結來源'
        model_type=source.get('model_type') or record.get('kind') or '模型'
        base=record.get('base_model') or '底模未提供'
        category=category_for(record)
        name_lines=[record.get('name') or (display[0] if display else ''),category]
        if 'type' not in columns:name_lines[1]=' · '.join((model_type,base,category))
        if 'status' not in columns:name_lines.append(' · '.join((file_state,f"{record.get('size',0)/1024**2:,.1f} MiB",source_state)))
        self.draw_lines(painter,option,columns['name'],name_lines,tokens,title=True)
        if 'type' in columns:self.draw_lines(painter,option,columns['type'],[model_type,base],tokens)
        if 'status' in columns:self.draw_lines(painter,option,columns['status'],[file_state,f"{record.get('size',0)/1024**2:,.1f} MiB",source_state],tokens)
        if not selected:
            separator=QPen(QColor(tokens['divider']),1); separator.setCosmetic(True); painter.setPen(separator)
            painter.drawLine(rect.left()+12,rect.bottom(),rect.right()-12,rect.bottom())
        painter.restore()

    @staticmethod
    def draw_lines(painter,option,rect,lines,tokens,title=False):
        height=option.fontMetrics.height(); top=rect.center().y()-height*len(lines)/2
        for i,line in enumerate(lines):
            font=QFont(option.font)
            if title and i==0:font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font); painter.setPen(QColor(tokens['text'] if i==0 else tokens['secondary']))
            painter.drawText(QRectF(rect.left(),top+i*height,rect.width(),height),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,
                painter.fontMetrics().elidedText(line,Qt.TextElideMode.ElideRight,int(rect.width())))
