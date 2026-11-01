"""Keep Windows browser and Canvas composition compatible within the workspace."""
import os

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication


def configure_canvas_composition(requested):
    """Choose the session API before any document creates its graphics surface."""
    if not requested or QApplication.platformName() != 'windows':
        return requested
    # An explicit Quick adaptation or RHI backend belongs to the caller. Keep
    # its choice and use an ordinary Canvas viewport if it cannot share GL.
    adaptation = os.environ.get('QT_QUICK_BACKEND') or os.environ.get('QMLSCENE_DEVICE')
    if adaptation and adaptation != 'rhi':
        return False
    backend = os.environ.get('QSG_RHI_BACKEND')
    if backend:
        return backend in ('gl', 'gles2', 'opengl')
    from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
    # Windows defaults to D3D for Quick. Mixing that with a QOpenGLWidget
    # leaves stale pixels when its native sibling Chat or reader is shown.
    QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.OpenGL)
    return True


def prepare_native_host(view):
    """Call on a new browser or its content host, before creating browser pages.

    Qt stops RHI configuration at a native child. The browser itself owns that
    boundary, so its existing parent, geometry and visibility remain unchanged.
    In particular, never promote an already visible reader/scroll area instead.
    """
    if QApplication.platformName() != 'windows':
        return
    attribute = Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings
    previous = QCoreApplication.testAttribute(attribute)
    QCoreApplication.setAttribute(attribute, True)
    try:
        view.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        view.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
    finally:
        QCoreApplication.setAttribute(attribute, previous)
    # Do not force winId(): a new browser can choose its RHI surface directly.
    # A plain Chat host stays raster until its first actual browser is needed;
    # Qt may replace that child's HWND without promoting the workspace root.
