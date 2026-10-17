"""Shared module header, elevation and optional folded presentation."""
from PySide6.QtCore import Qt, QRect, QRectF
from PySide6.QtGui import QColor, QPen, QFontMetricsF


def canvas_dialog_context(parent):
    """Resolve Canvas ownership, including nested sheets, without global QSS."""
    while parent is not None:
        context=getattr(parent,'_module_dialog_context',None)
        if context:return context
        stack=getattr(parent,'surface_stack',None)
        canvas=getattr(parent,'canvas',None)
        if stack is not None and canvas is not None:
            current=stack.currentWidget();content=getattr(parent,'canvas_content',None)
            if ((current is not None and current is getattr(canvas,'editor_page',None)) or
                    (current is getattr(parent,'canvas_shell',None) and content is not None and content.currentWidget() is canvas)):
                from .canvas_items import canvas_colors
                from .theme import font_pixels,window_appearance
                return canvas_colors(canvas),max(16,font_pixels(window_appearance(parent)['ui_size']))
        parent=parent.parentWidget()
    return None


def dialog_colors(colors):
    """Canvas sheets share roles; legacy pages can adopt these independently."""
    from .color_roles import mix
    result=matte_colors(colors)
    result['base']=colors.get('module_dialog',mix(colors['base'],result['surface'],.22))
    result['module_dialog']=result['base']
    result['raised']=result['surface']
    result['line']=mix(result['base'],result['text'],.20)
    return result


def module_dialog_styles(colors, heading_size):
    from .theme import destructive_button_styles
    t=dialog_colors(colors)
    return f'''
        QFrame#DialogSurface {{background:{t['base']};border:1px solid {t['line']};border-radius:14px;}}
        QFrame#DialogEditor {{background:{t['surface']};border:0;border-radius:10px;}}
        QLabel#DialogTitle {{font-size:{heading_size}px;font-weight:500;}}
        QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{background:{t['surface']};border:1px solid {t['line']};border-radius:8px;color:{t['text']};placeholder-text-color:{t['secondary']};}}
        QFrame#DialogEditor QLineEdit, QFrame#DialogEditor QPlainTextEdit, QFrame#DialogEditor QTextEdit,
        QFrame#DialogEditor QComboBox, QFrame#DialogEditor QSpinBox, QFrame#DialogEditor QDoubleSpinBox {{background:{t['field']};}}
        QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{border-color:{t['accent']};}}
        QPushButton {{background:{t['surface']};border:1px solid {t['line']};border-radius:8px;color:{t['text']};}}
        QPushButton:hover {{background:{t['hover']};}}
        QPushButton#Quiet, QPushButton#DialogClose {{background:transparent;border:0;}}
        QPushButton#Quiet:hover, QPushButton#DialogClose:hover {{background:{t['hover']};}}
        QPushButton#Primary {{background:{t['accent']};border-color:{t['accent']};color:{t['on_accent']};}}
        QPushButton:disabled {{background:{t['field']};color:{t['secondary']};border-color:{t['line']};}}
        {destructive_button_styles(t)}
        QListWidget[moduleChoices="true"] {{background:transparent;border:0;padding:0;outline:0;}}
        QListWidget[moduleChoices="true"]::item {{padding:8px 10px;border:0;border-radius:8px;}}
        QListWidget[moduleChoices="true"]::item:hover {{background:{t['hover']};}}
        QListWidget[moduleChoices="true"]::item:selected {{background:{t['selected']};color:{t['text']};}}
    '''


def apply_module_dialog(dialog, colors, heading_size):
    """Shared shell and choice rows for the picker and module-related sheets."""
    from PySide6.QtWidgets import QFrame,QPushButton
    from .ui_icons import icon
    t=dialog_colors(colors)
    dialog._module_dialog_context=(colors,heading_size)
    dialog.setStyleSheet(module_dialog_styles(colors,heading_size))
    surface=dialog.findChild(QFrame,'DialogSurface')
    surface.layout().setContentsMargins(20,16,20,18)
    surface.layout().setSpacing(14);dialog.body.setSpacing(12)
    close=dialog.findChild(QPushButton,'DialogClose')
    if close:close.setText('');close.setIcon(icon('close',t['text']))
    return t['base'],t['line']


def apply_prompt_dialog(dialog, window):
    """Use the same sheet, fields and typography as CanvasPalette."""
    suppress_prompt_tooltips(dialog)
    # Only the deliberate side-card transition animates. A whole-window fade
    # exposes the old/native backing surface while these editors are opening.
    dialog._prompt_instant_show = True
    from .theme import visual_tokens,font_pixels,widget_palette,window_appearance
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QFrame
    settings=window_appearance(window)
    tokens=visual_tokens(settings)
    colors=dialog_colors(tokens)
    apply_module_dialog(dialog,tokens,max(16,font_pixels(settings['ui_size'])))
    palette=widget_palette(settings)
    palette.setColor(QPalette.ColorRole.Window,QColor(colors['base']))
    palette.setColor(QPalette.ColorRole.Base,QColor(colors['surface']))
    dialog.setPalette(palette)
    dialog.setStyleSheet(dialog.styleSheet()+f'''
        QFrame#PromptDivider {{background:{colors['line']};border:0;}}
        QSpinBox#PromptPager {{background:transparent;border:1px solid transparent;padding:4px;color:{colors['text']};}}
        QSpinBox#PromptPager:focus {{border-color:{colors['accent']};}}
        QStackedWidget, QScrollArea {{background:transparent;border:0;}}
        QListWidget#PromptEntryList {{background:transparent;border:0;padding:0;outline:0;}}
        QListWidget#PromptEntryList::item {{padding:0;border:0;background:transparent;}}
        QTabWidget::pane {{background:transparent;border:0;top:0;}}
        QTabBar::tab {{background:transparent;color:{colors['secondary']};border:0;border-bottom:2px solid {colors['line']};border-radius:0;padding:10px 12px;margin:0;}}
        QTabBar::tab:selected {{border-bottom-color:{colors['accent']};color:{colors['text']};}}
        QTabBar::tab:hover {{background:transparent;color:{colors['text']};}}
    ''')
    surface=dialog.findChild(QFrame,'DialogSurface')
    surface.layout().setContentsMargins(24,24,24,20)
    surface.layout().setSpacing(16)
    surface.layout().insertWidget(1,prompt_divider())
    dialog.body.setSpacing(12)
    return colors


def suppress_prompt_tooltips(root):
    """Keep this editor's labels and accessible names without floating hints."""
    from PySide6.QtCore import QObject, QEvent
    from PySide6.QtWidgets import QApplication, QWidget, QToolTip
    class EditorHints(QObject):
        def eventFilter(self, watched, event):
            if event.type()==QEvent.Type.ToolTip and isinstance(watched,QWidget) and (watched is root or root.isAncestorOf(watched)):
                QToolTip.hideText();event.accept();return True
            return False
    if not hasattr(root,'_prompt_hints'):
        root._prompt_hints=EditorHints(root)
        QApplication.instance().installEventFilter(root._prompt_hints)


def fit_prompt_form(dialog, scroll, minimum_height=480):
    """Measure the current page after invalidating hidden-page layout caches."""
    if not dialog.isVisible() or getattr(dialog,'_fitting_prompt_form',False):return
    from PySide6.QtWidgets import QLayout,QWidget
    dialog._fitting_prompt_form=True
    try:
        content=scroll.widget()
        for layout in reversed(content.findChildren(QLayout)):layout.invalidate()
        for widget in reversed(content.findChildren(QWidget)):widget.updateGeometry()
        content.layout().activate();content.updateGeometry();dialog.layout().activate()
        chrome=dialog.height()-scroll.height()
        bounds=dialog.screen().availableGeometry().adjusted(20,20,-20,-20)
        parent=dialog.parentWidget()
        parent_bounds=parent.rect().adjusted(12,12,-12,-12)
        parent_bounds.moveTopLeft(parent.mapToGlobal(parent_bounds.topLeft()))
        bounds=bounds.intersected(parent_bounds)
        width=min(dialog.width(),bounds.width())
        height=min(bounds.height(),max(minimum_height,content.sizeHint().height()+chrome))
        dialog.setGeometry(max(bounds.left(),min(dialog.x(),bounds.right()-width+1)),
                           max(bounds.top(),min(dialog.y(),bounds.bottom()-height+1)),width,height)
        dialog.layout().activate();content.layout().activate()
    finally:dialog._fitting_prompt_form=False


def toggle_prompt_panel(owner, panel, opened, fit):
    """Publish disclosure visibility and final geometry in the same paint."""
    window=owner.window();updates=window.updatesEnabled()
    window.setUpdatesEnabled(False)
    try:
        panel.setVisible(opened);fit()
    finally:
        window.setUpdatesEnabled(updates)
        if updates and window.isVisible():window.repaint()


def prompt_divider():
    from PySide6.QtWidgets import QFrame
    divider=QFrame();divider.setObjectName('PromptDivider');divider.setFixedHeight(1)
    return divider


def source_controls_style(colors):
    """Compact source controls use the established Canvas color roles."""
    t=matte_colors(colors)
    return f'''
        QFrame#CanvasModuleBody QSpinBox#SourcePager {{background:transparent;border:1px solid transparent;padding:4px;color:{t['text']};}}
        QFrame#CanvasModuleBody QSpinBox#SourcePager:focus {{border-color:{t['accent']};}}
        QFrame#CanvasModuleBody QPushButton#SourceMode {{background:{t['field']};border:1px solid {t['line']};border-radius:8px;padding:0;}}
        QFrame#CanvasModuleBody QPushButton#SourceMode:hover {{background:{t['hover']};border-color:{t['accent']};}}
        QFrame#CanvasModuleBody QPushButton#SourceMode:pressed {{background:{t['selected']};}}
    '''


def matte_colors(colors):
    """One finish for Canvas cards, controls and their real-node previews."""
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
    from .preview_nodes import contains,display
    if contains(state,key):return display(state,key,writing)
    if key == '__workflow_order__':
        return data.get('workflow_order', {})
    return state.get('canvas_functions', {}).get('text_sources', {}).get(key) or state.get('canvas_functions', {}).get('images', {}).get(key, {})


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


def style_action(widget, colors, pending=False, primary=False, dropdown=False):
    background = colors['accent'] if primary else colors.get('module_field',colors['field']) if pending else colors['surface']
    foreground = colors['on_accent'] if primary else colors['text']
    border = colors['line']
    padding='7px 28px 7px 12px' if dropdown else '7px 12px'
    style=f"QPushButton {{background:{background}; color:{foreground}; border:1px solid {border}; border-radius:8px; padding:{padding};}} QPushButton:hover {{border-color:{colors['accent']};}} QPushButton:pressed {{background:{colors['selected']};color:{colors['text']};}} QPushButton:disabled {{background:{colors['field']};color:{colors['secondary']};border-color:{colors['line']};}}"
    if widget.styleSheet()!=style:widget.setStyleSheet(style)
    widget.setProperty('modulePending',pending)
