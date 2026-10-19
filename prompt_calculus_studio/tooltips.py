"""Finish native hints synchronously, before their first visible paint."""
import weakref
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QAbstractButton, QToolTip, QWidget
from shiboken6 import isValid


def hint_owner(widget):
    while isinstance(widget,QWidget):
        for candidate in (widget,getattr(widget,'window',None),getattr(widget,'canvas',None)):
            if isinstance(getattr(candidate,'state',None),dict):return candidate
            owner=getattr(candidate,'window',None)
            if isinstance(getattr(owner,'state',None),dict):return owner
        widget=widget.parentWidget()
    return None


def redundant_hint(widget):
    if not isinstance(widget,QAbstractButton):return False
    title=widget.text().replace('&','').strip()
    width=widget.contentsRect().width()-24-(widget.iconSize().width()+6 if not widget.icon().isNull() else 0)
    return bool(title and title==widget.toolTip().replace('&','').strip()
                and widget.fontMetrics().horizontalAdvance(title)<=width)


def hint_settings(owner):
    settings=dict(owner.state.get('settings',{}))
    settings.update(getattr(owner,'appearance_preview',{}))
    return settings


class TooltipStyler(QObject):
    def __init__(self,app):
        super().__init__(app)
        self.owner=None
        self.target=None
        self._dispatching_help=False
        from .application_events import listen
        listen(self, QEvent.Type.ToolTip, QEvent.Type.Polish, QEvent.Type.Show, QEvent.Type.Resize)

    @staticmethod
    def round_tip(tip):
        if not isValid(tip):return
        # A binary QRegion cuts off the stylesheet's antialiasing at fractional
        # DPI. The alpha window already supplies the rounded transparent edge.
        if not tip.mask().isEmpty():tip.clearMask()

    def style_tip(self,tip,owner):
        if not isValid(tip) or not isValid(owner):return
        from .theme import TOOLTIP_PADDING,widget_palette
        # Qt supplies the real stylesheet parent in placeTip(), including for
        # item-view hints and Windows' parentless QTipLabel. Copying the entire
        # window stylesheet here fights Qt's reset when it reuses that label.
        tip.ensurePolished()
        # QTipLabel is reused by Qt and reads ToolTipText, which QSS does not
        # consistently update when a previous owner used another palette.
        palette=tip.palette();source=widget_palette(hint_settings(owner))
        for group in (QPalette.ColorGroup.Active,QPalette.ColorGroup.Inactive,QPalette.ColorGroup.Disabled):
            for role in (QPalette.ColorRole.ToolTipText,QPalette.ColorRole.ToolTipBase):
                palette.setColor(group,role,source.color(group,role))
        tip.setPalette(palette)
        screen=tip.screen().availableGeometry()
        limit=max(80,min(480,screen.width()-24));tip.setMaximumWidth(limit)
        # Windows 11's native frame recalculates contents margins on reuse,
        # dropping the QSS padding. Restore the same padding plus 1px border
        # before measuring; QLabel margin/indent must not add a second layer.
        tip.setMargin(0);tip.setIndent(0)
        horizontal,vertical=TOOLTIP_PADDING
        tip.setContentsMargins(horizontal+1,vertical+1,horizontal+1,vertical+1)
        # QLabel's wrapped sizeHint prefers a narrow multi-line rectangle even
        # for a short hint. Measure its unwrapped hint, including Qt's native
        # margin/indent and QSS padding, before deciding whether wrapping fits.
        tip.setWordWrap(False)
        natural=tip.sizeHint().width()
        tip.setWordWrap(natural>limit);tip.adjustSize()
        x,y=tip.x(),tip.y()
        target=self.target() if self.target else None
        if isinstance(target,QAbstractButton) and isValid(target) and target.isVisible():
            bounds=QRect(target.mapToGlobal(QPoint()),target.size())
            ancestor=target
            side=None
            while ancestor is not None and side is None:
                side=ancestor.property('pcsTooltipSide');ancestor=ancestor.parentWidget()
            if side=='right':
                x=bounds.right()+9;y=bounds.center().y()-tip.height()//2
                if x+tip.width()>screen.right()-8:x=bounds.left()-8-tip.width()
            else:
                x=bounds.center().x()-tip.width()//2;y=bounds.bottom()+9
                if y+tip.height()>screen.bottom()-8:y=bounds.top()-8-tip.height()
        tip.move(max(screen.left()+8,min(x,screen.right()-tip.width()-8)),
                 max(screen.top()+8,min(y,screen.bottom()-tip.height()-8)))
        self.round_tip(tip)

    def refresh_tip(self):
        owner=self.owner() if self.owner else None
        if owner is None or not isValid(owner):return
        for tip in QApplication.topLevelWidgets():
            if tip.inherits('QTipLabel') and tip.isVisible():self.style_tip(tip,owner)

    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.ToolTip and isinstance(watched,QWidget) and not self._dispatching_help:
            owner=hint_owner(watched)
            self.owner=weakref.ref(owner) if owner else None
            self.target=weakref.ref(watched) if owner else None
            if owner and redundant_hint(watched):
                QToolTip.hideText()
                event.ignore();return True
            if owner:
                from .theme import widget_palette
                QToolTip.setPalette(widget_palette(hint_settings(owner)))
                # Let the widget/delegate supply the text and native lifetime,
                # then finish reuse in the same event. A queued refresh is too
                # late: Qt may paint the reset stylesheet/geometry in between.
                self._dispatching_help=True
                try:
                    QApplication.sendEvent(watched,event)
                finally:
                    self._dispatching_help=False
                self.refresh_tip()
                return True
        if isinstance(watched,QWidget) and watched.inherits('QTipLabel'):
            if event.type()==QEvent.Type.Polish:
                # QTipLabel polishes in its constructor, before creating its
                # native handle. Set translucency now, not after the first show.
                watched.setWindowFlags(watched.windowFlags() | Qt.WindowType.FramelessWindowHint |
                                       Qt.WindowType.NoDropShadowWindowHint)
                watched.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                watched.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent,False)
                watched.setAutoFillBackground(False)
            owner=self.owner() if self.owner else None
            if owner is not None and isValid(owner):
                if event.type()==QEvent.Type.Show:
                    self.style_tip(watched,owner)
                elif event.type()==QEvent.Type.Resize:self.round_tip(watched)
        return super().eventFilter(watched,event)


def install_tooltip_styling():
    from .popup_surface import install_native_popup_finish
    install_native_popup_finish()
    app=QApplication.instance()
    if app is not None and not hasattr(app,'_pcs_tooltip_styler'):
        # Native fading captures the label before placeTip()/reuse has finished
        # its geometry and style, producing a second, stale rectangular frame.
        for effect in (Qt.UIEffect.UI_AnimateTooltip,Qt.UIEffect.UI_FadeTooltip):
            app.setEffectEnabled(effect,False)
        app._pcs_tooltip_styler=TooltipStyler(app)
