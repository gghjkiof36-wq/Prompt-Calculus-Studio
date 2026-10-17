"""One inset contour; page and sidebar backgrounds share its clipped edge."""
from PySide6.QtCore import QEvent, QRectF, Qt, QSize
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QStackedWidget, QWidget

from .workspace_style import workspace_colors
from .theme import visual_tokens


RADIUS = 14


def left_contour(rect, radius, top_round=True):
    """Only the outside corners are rounded; the internal divider is straight."""
    r = min(radius, rect.width()/2, rect.height()/2)
    top_r = r if top_round else 0
    path = QPainterPath()
    path.moveTo(rect.left()+top_r, rect.top())
    path.lineTo(rect.right(), rect.top())
    path.lineTo(rect.right(), rect.bottom())
    path.lineTo(rect.left()+r, rect.bottom())
    path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom()-r)
    path.lineTo(rect.left(), rect.top()+top_r)
    path.quadTo(rect.left(), rect.top(), rect.left()+top_r, rect.top())
    path.closeSubpath()
    return path


class WorkspaceSurface(QStackedWidget):
    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace
        self.setObjectName('WorkspaceInset')
        self.edge = SurfaceEdge(self)
        self.currentChanged.connect(self.sync_surface)

    def minimumSizeHint(self):
        # Utility pages reflow within the desktop; their preferred reading
        # height must not enlarge the top-level window when switching routes.
        return QSize(0, 0)

    def joined_top(self):
        w = self.workspace
        return getattr(w, '_sidebar_header_docked', False) or (w.active_route == 'home' and getattr(w, 'tabs_in_row', False))

    def sync_surface(self, *_):
        page = self.currentWidget()
        if page is not None:
            page.installEventFilter(self)
            joined = self.joined_top()
            geometry = (page.width(), page.height(), joined)
            if getattr(page, '_pcs_contour_size', None) != geometry or page.mask().isEmpty():
                # Clip the full page subtree, including opaque scroll viewports.
                # Keep its hard mask just inside our antialiased painted edge.
                path = left_contour(QRectF(page.rect()), RADIUS+1, not joined)
                page.setMask(QRegion(path.toFillPolygon().toPolygon()))
                page._pcs_contour_size = geometry
        self.edge.setGeometry(self.rect())
        self.edge.raise_()
        self.edge.update()
        self.update()

    def eventFilter(self, watched, event):
        if watched is self.currentWidget() and event.type()==QEvent.Type.Resize:
            self.sync_surface()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_surface()

    def paintEvent(self, event):
        settings = dict(self.workspace.settings, **self.workspace.appearance_preview)
        colors = workspace_colors(settings)
        sidebar = self.workspace.sidebar_target()
        expanded = sidebar is not None and not sidebar.isHidden()
        fill = colors['sidebar'] if expanded else colors['content']
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        joined = self.joined_top()
        painter.fillPath(left_contour(QRectF(self.rect()), RADIUS, not joined), QColor(fill))
        painter.end()
        super().paintEvent(event)


class SurfaceEdge(QWidget):
    """A physical-pixel highlight; it never takes focus or pointer input."""
    def __init__(self, surface):
        super().__init__(surface)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event):
        workspace = self.parentWidget().workspace
        colors = visual_tokens(dict(workspace.settings, **workspace.appearance_preview))
        ink = QColor(colors['text'])
        ink.setAlpha(20)
        width = 1/self.devicePixelRatioF()
        rect = QRectF(self.rect()).adjusted(width/2, width/2, 0, -width/2)
        # The right edge is already owned by the native window frame.
        path = QPainterPath()
        path.moveTo(rect.right(), rect.top())
        if workspace.active_route == 'home' and getattr(workspace, 'tabs_in_row', False):
            # The active tab owns the top contour; never draw a horizontal
            # frame through its join with the document surface.
            path.moveTo(rect.left(), rect.top())
        elif getattr(workspace, '_sidebar_header_docked', False):
            path.lineTo(rect.left()+workspace.sidebar_target().width(), rect.top())
            path.moveTo(rect.left(), rect.top())
        else:
            path.lineTo(rect.left()+RADIUS, rect.top())
            path.quadTo(rect.left(), rect.top(), rect.left(), rect.top()+RADIUS)
        path.lineTo(rect.left(), rect.bottom()-RADIUS)
        path.quadTo(rect.left(), rect.bottom(), rect.left()+RADIUS, rect.bottom())
        path.lineTo(rect.right(), rect.bottom())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(ink, width))
        painter.drawPath(path)
