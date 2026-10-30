"""Keep a Windows browser's texture composition out of the workspace window."""
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication


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
    # Do not force winId(): the first native surface must see the browser's RHI
    # requirement, rather than first creating an unnecessary raster window.
