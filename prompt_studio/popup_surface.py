"""One frosted popup surface, independent of Qt's native frame painting."""
from PySide6.QtCore import Qt, QObject, QEvent, QPoint, QRect, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import QWidget, QGraphicsScene, QGraphicsBlurEffect
from .color_roles import mix


POPUP_RADIUS = 12
POPUP_WIDTH = 240
POPUP_MAX_WIDTH = 480


def prepare_popup_window(popup):
    # Qt's private combo container otherwise paints an opaque native palette
    # below the rounded stylesheet view, even when that view uses RGBA.
    popup.setWindowFlags(popup.windowFlags() | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.NoDropShadowWindowHint)
    popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    popup.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
    popup.setAutoFillBackground(False)


def popup_row_height(metrics, icon_height=0):
    """A single measured text line and padding, never an input-control height."""
    return max(28, metrics.height() + 10, icon_height + 10)


def popup_width(owner_width, text_width, available_width):
    # Short menus share a usable width instead of shrinking to a one-word
    # selector. Long names can grow; excessively wide form fields do not turn
    # their menus into full-width panels.
    desired = max(POPUP_WIDTH, min(owner_width, 360), text_width + 44)
    return max(1, min(available_width, POPUP_MAX_WIDTH, desired))


def refresh_popup_surfaces(host):
    """Repaint open popups for a live setting change without recapturing them."""
    from PySide6.QtWidgets import QApplication
    app=QApplication.instance()
    if app is None:return
    for popup in app.topLevelWidgets():
        if not popup.isVisible():continue
        for surface in popup.findChildren(PopupSurface):
            if surface.surface_host() is host:surface.popup.update()


class PopupSurface(QObject):
    """Paint a single rounded surface with a softened in-app backdrop.

    Capture only the owning PCS widget at opening, never the desktop. Popup
    menus block changes to the underlying controls, so the snapshot remains
    stable while keyboard focus and hover move over fully opaque glyphs.
    """
    def __init__(self,popup,owner,paint_in_filter=True):
        super().__init__(popup)
        self.popup=popup;self.owner=owner;self.paint_in_filter=paint_in_filter
        self.backdrop=None;self._capturing=False
        prepare_popup_window(popup)
        popup.installEventFilter(self)

    def surface_host(self):
        source=self.owner or self.popup
        while source.parentWidget() is not None:source=source.parentWidget()
        proxy=source.graphicsProxyWidget()
        if proxy is not None and proxy.scene() is not None and proxy.scene().views():
            source=QWidget.window(proxy.scene().views()[0])
        return source

    def surface_palette(self):
        # Qt deliberately does not inherit ToolTipBase into ordinary child
        # widgets; their value can be the native pale-yellow tooltip color.
        # Read the theme palette at its actual owning top-level instead.
        return QWidget.palette(self.surface_host())

    def tint_alpha(self):
        from .core import DEFAULT_SETTINGS
        host=self.surface_host()
        settings=dict(getattr(host,'state',{}).get('settings',{}))
        settings.update(getattr(host,'appearance_preview',{}))
        value=settings.get('menu_transparency',DEFAULT_SETTINGS['menu_transparency'])
        return round(255*(100-value)/100)

    def capture(self):
        if self._capturing:return
        self.backdrop=None
        if self.owner is None or self.popup.width()<=0 or self.popup.height()<=0:return
        owner=self.owner
        while owner is not None and owner.windowType() in (Qt.WindowType.Popup,Qt.WindowType.ToolTip):
            owner=owner.parentWidget()
        if owner is None:return
        host=QWidget.window(owner)
        if host is self.popup or not host.isVisible():return
        self._capturing=True
        try:
            margin=24
            size=self.popup.size()
            source=QRect(host.mapFromGlobal(self.popup.mapToGlobal(QPoint())),size).adjusted(-margin,-margin,margin,margin)
            visible=source.intersected(host.rect())
            if visible.isEmpty():return
            # Start from an opaque palette fallback for portions outside the
            # app, rather than reading windows behind it or exposing raw lines.
            sampled=QPixmap(source.size());sampled.fill(self.surface_palette().color(QPalette.ColorRole.ToolTipBase))
            painter=QPainter(sampled)
            painter.drawPixmap(visible.topLeft()-source.topLeft(),host.grab(visible));painter.end()
            scene=QGraphicsScene();item=scene.addPixmap(sampled)
            blur=QGraphicsBlurEffect();blur.setBlurRadius(20)
            blur.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint);item.setGraphicsEffect(blur)
            softened=QPixmap(sampled.size());softened.fill(Qt.GlobalColor.transparent)
            painter=QPainter(softened)
            scene.render(painter,QRectF(softened.rect()),QRectF(sampled.rect()));painter.end()
            self.backdrop=softened.copy(margin,margin,size.width(),size.height())
        finally:
            self._capturing=False

    def paint(self):
        widget=self.popup;palette=self.surface_palette()
        raised=palette.color(QPalette.ColorRole.ToolTipBase)
        border=QColor(mix(palette.color(QPalette.ColorRole.Button).name(),
                          palette.color(QPalette.ColorRole.WindowText).name(),.10))
        painter=QPainter(widget)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(widget.rect(),Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path=QPainterPath();path.addRoundedRect(QRectF(widget.rect()).adjusted(.5,.5,-.5,-.5),POPUP_RADIUS,POPUP_RADIUS)
        painter.fillPath(path,raised)
        if self.backdrop is not None:
            painter.save();painter.setClipPath(path);painter.drawPixmap(0,0,self.backdrop)
            tint=QColor(raised);tint.setAlpha(self.tint_alpha());painter.fillPath(path,tint);painter.restore()
        painter.setPen(border);painter.drawPath(path)

    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.Show:
            self.capture()
        elif event.type()==QEvent.Type.Resize and watched.isVisible():
            self.capture()
        elif event.type()==QEvent.Type.Hide:
            self.backdrop=None
        elif event.type()==QEvent.Type.Paint and self.paint_in_filter:
            self.paint();return True
        return False
