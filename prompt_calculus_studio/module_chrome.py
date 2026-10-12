"""Shared module header, elevation and optional folded presentation."""
from PySide6.QtCore import Qt, QRect, QRectF
from PySide6.QtGui import QColor, QPen, QFontMetricsF


def apply_module_dialog(dialog, colors, heading_size):
    """Shared shell and choice rows for the picker and module-related sheets."""
    from PySide6.QtWidgets import QFrame
    from .color_roles import mix
    background=mix(colors['base'],colors['surface'],.22)
    line=mix(background,colors['text'],.20)
    dialog.setStyleSheet(f'''
        QFrame#DialogSurface {{background:{background};border:1px solid {line};border-radius:14px;}}
        QLabel#DialogTitle {{font-size:{heading_size}px;font-weight:500;}}
        QListWidget[moduleChoices="true"] {{background:transparent;border:0;padding:0;outline:0;}}
        QListWidget[moduleChoices="true"]::item {{padding:8px 10px;border:0;border-radius:8px;}}
        QListWidget[moduleChoices="true"]::item:hover {{background:{colors['hover']};}}
        QListWidget[moduleChoices="true"]::item:selected {{background:{mix(background,colors['accent'],.20)};color:{colors['text']};}}
    ''')
    surface=dialog.findChild(QFrame,'DialogSurface')
    surface.layout().setContentsMargins(20,16,20,18)
    surface.layout().setSpacing(14);dialog.body.setSpacing(12)
    return background,line


def matte_colors(colors):
    """One finish for the Stage card, its controls and its real-node preview."""
    result = dict(colors)
    for role, source in (
        ('surface','module_body'), ('canvas_body','module_body'),
        ('canvas_header','module_header'), ('field','module_field'),
        ('canvas_field','module_field'), ('canvas_inset','module_field'),
        ('line','module_line'), ('canvas_line','module_line'),
        ('canvas_divider','module_divider'), ('text','module_text'),
        ('secondary','module_secondary'), ('pending','module_pending'),
        ('pending_line','module_pending_line'), ('hover','module_hover'),
        ('selected','module_selected')):
        result[role] = colors[source]
    result['shadow_strength'] = .65
    return result


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


def paint_shadow(painter, outline, nested=False, strength=1.0):
    from .canvas_raster import paint_cached, SHADOWS
    geometry = tuple((e.x, e.y, e.type.value)
                     for i in range(outline.elementCount())
                     for e in (outline.elementAt(i),))
    bounds = outline.boundingRect().translated(0, 3 if nested else 6).adjusted(-7, -7, 7, 7)
    paint_cached(painter, ('shadow', nested, strength, outline.fillRule().value, geometry), bounds,
                 lambda target: _draw_shadow(target, outline, nested, strength), SHADOWS)


def _draw_shadow(painter, outline, nested=False, strength=1.0):
    painter.save()
    shape = outline.translated(0, 3 if nested else 6)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for spread, alpha in ((12, 3), (9, 5), (6, 8), (3, 12)):
        painter.setPen(QPen(QColor(0, 0, 0, round((alpha // 2 if nested else alpha)*strength)), spread,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(shape)
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(0, 0, 0, round((20 if nested else 45)*strength)))
    painter.drawPath(shape); painter.restore()


def visual_bounds(width, height):
    return QRectF(-10, -4, width + 20, height + 24)


def style_action(widget, colors, pending=False, primary=False):
    from .color_roles import mix
    background = colors['accent'] if primary else colors.get('pending',mix(colors['surface'], colors['accent'], .20)) if pending else colors['surface']
    foreground = colors['on_accent'] if primary else colors['text']
    border = colors.get('pending_line',mix(colors['line'], colors['accent'], .5)) if pending else colors['line']
    widget.setStyleSheet(f"QPushButton {{background:{background}; color:{foreground}; border:1px solid {border}; border-radius:8px; padding:7px 12px;}} QPushButton:hover {{border-color:{colors['accent']};}} QPushButton:pressed {{background:{colors['selected']};color:{colors['text']};}} QPushButton:disabled {{background:{colors['field']};color:{colors['secondary']};border-color:{colors['line']};}}")
    widget.setProperty('modulePending',pending)
