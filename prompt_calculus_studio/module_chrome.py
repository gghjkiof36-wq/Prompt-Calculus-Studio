"""Shared module header, elevation and optional folded presentation."""
from PySide6.QtCore import Qt, QRect, QRectF
from PySide6.QtGui import QColor, QPen, QFontMetricsF


def module_value(canvas, key, state=None):
    writing = state is not None
    state = canvas.window.state if state is None else state
    data = state.get('multi_output', {})
    for kind in ('canvases', 'outputs', 'clip_inputs', 'image_inputs', 'schedulers', 'stages'):
        if key in data.get(kind, {}):
            return data[kind][key]
    if key == '__result_preview__':
        if writing:return state.setdefault('canvas_functions', {}).setdefault('preview_display', {})
        return state.get('canvas_functions', {}).get('preview_display', {})
    if key == '__workflow_order__':
        return data.get('workflow_order', {})
    return state.get('canvas_functions', {}).get('images', {}).get(key, {})


def toggle_module(canvas, key):
    def apply(state):
        value = module_value(canvas, key, state)
        value['collapsed'] = not value.get('collapsed', False)
    canvas.commit(apply, label='收合／展開模組')


def header_rects(width, title, font, with_icon=True, extra=0):
    left = 46 if with_icon else 14
    metrics = QFontMetricsF(font)
    available = max(0, width - left - 82 - extra)
    text = metrics.elidedText(title, Qt.TextElideMode.ElideRight, int(available))
    return text, QRectF(left, 7, available, 30), {
        'collapse': QRectF(left + metrics.horizontalAdvance(text) + 5, 7, 28, 30),
        'more': QRectF(width - 46, 7, 30, 30),
    }


def paint_header(painter, width, title, font, colors, collapsed=False, icon_name=None, hot=None, extra=0):
    from .ui_icons import icon
    font.setBold(True)
    text, title_rect, actions = header_rects(width, title, font, bool(icon_name), extra)
    painter.save(); painter.setFont(font); painter.setPen(QColor(colors['text']))
    if icon_name:
        icon(icon_name, colors['secondary']).paint(painter, QRect(17, 14, 19, 19))
    painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter, text)
    for action, rect in actions.items():
        if hot == action:
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(colors['hover']))
            painter.drawRoundedRect(rect, 6, 6)
        painter.save(); painter.translate(rect.center())
        if action == 'collapse':
            if collapsed: painter.rotate(-90)
            icon('chevron-down', colors['text']).paint(painter, QRect(-8, -8, 16, 16))
        else:
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(colors['secondary']))
            for x in (-5, 0, 5): painter.drawEllipse(QRectF(x-1, -1, 2, 2))
        painter.restore()
    painter.restore()


def paint_shadow(painter, outline, nested=False):
    painter.save()
    shape = outline.translated(0, 3 if nested else 6)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for spread, alpha in ((12, 3), (9, 5), (6, 8), (3, 12)):
        painter.setPen(QPen(QColor(0, 0, 0, alpha // 2 if nested else alpha), spread,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(shape)
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(0, 0, 0, 20 if nested else 45))
    painter.drawPath(shape); painter.restore()


def visual_bounds(width, height):
    return QRectF(-10, -4, width + 20, height + 24)


def style_action(widget, colors, pending=False, primary=False):
    from .color_roles import mix
    background = colors['accent'] if primary else mix(colors['surface'], colors['accent'], .20) if pending else colors['surface']
    foreground = colors['on_accent'] if primary else colors['text']
    border = mix(colors['line'], colors['accent'], .5) if pending else colors['line']
    widget.setStyleSheet(f"QPushButton {{background:{background}; color:{foreground}; border:1px solid {border}; border-radius:8px; padding:7px 12px;}} QPushButton:hover {{border-color:{colors['accent']};}} QPushButton:pressed {{background:{colors['selected']};color:{colors['text']};}} QPushButton:disabled {{background:{colors['field']};color:{colors['secondary']};border-color:{colors['line']};}}")
    widget.setProperty('modulePending',pending)
