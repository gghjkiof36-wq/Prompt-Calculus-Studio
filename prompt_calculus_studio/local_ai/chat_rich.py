"""Lazy chat projection pages sharing one closed, memory-only browser profile."""
import json
import os
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QApplication

if os.environ.get('QT_QPA_PLATFORM') == 'offscreen':
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView

from .chat_render import project_chat

ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'markdown'
_shared = None


class ChatResourceGate(QWebEngineUrlRequestInterceptor):
    def __init__(self, parent):
        super().__init__(parent)
        self.blocked = []
        self.allowed = {str((ASSETS / path).resolve()) for path in (
            'chat.html', 'chat.js', 'chat.css', 'reader.css', 'dompurify/purify.min.js',
            'katex/katex.min.css', 'katex/katex.min.js')}
        self.allowed.update(str(path.resolve()) for path in (ASSETS / 'katex/fonts').glob('*.woff2'))

    def interceptRequest(self, info):
        url = info.requestUrl()
        allowed = ((url.scheme() == 'file' and str(Path(url.toLocalFile()).resolve()) in self.allowed)
                   or url.toString() == 'qrc:///qtwebchannel/qwebchannel.js'
                   or url.toString() == 'about:blank'
                   or url.toString().startswith('data:image/png;base64,'))
        if not allowed:
            self.blocked = (self.blocked + [url.toString()])[-32:]
            info.block(True)


class ChatProfile(QObject):
    def __init__(self):
        super().__init__(QApplication.instance())
        self.users = 0
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self.gate = ChatResourceGate(self.profile)
        self.profile.setUrlRequestInterceptor(self.gate)
        self.profile.downloadRequested.connect(lambda request: request.cancel())

    def release(self):
        global _shared
        self.users -= 1
        if self.users == 0:
            if _shared is self:
                _shared = None
            self.deleteLater()


class ChatPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, kind, main):
        return (kind != QWebEnginePage.NavigationType.NavigationTypeLinkClicked
                and url.toString() == QUrl.fromLocalFile(str(ASSETS / 'chat.html')).toString())

    def createWindow(self, kind):
        return None

    def javaScriptConsoleMessage(self, level, message, line, source):
        self.last_console = (str(level), message, line)


class ChatBridge(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    @Slot(result=str)
    def document(self):
        return json.dumps(self.view.payload, ensure_ascii=False)

    @Slot(int, int)
    def presented(self, version, height):
        self.view.presented(version, height)

    @Slot(str, int)
    def activate(self, command, version):
        self.view.activate(command, version)


class RichChatView(QWebEngineView):
    contentReady = Signal(int)

    def __init__(self, parent=None):
        global _shared
        super().__init__(parent)
        self.setAccessibleName('格式化回覆')
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        if _shared is None:
            _shared = ChatProfile()
        shared = self.shared = _shared
        shared.users += 1
        page = ChatPage(shared.profile, self)
        self.setPage(page)
        page.destroyed.connect(shared.release)
        page.setBackgroundColor(QColor(Qt.GlobalColor.transparent))
        settings = page.settings()
        for key, value in (('JavascriptEnabled', True), ('JavascriptCanOpenWindows', False),
                           ('JavascriptCanAccessClipboard', False), ('LocalStorageEnabled', False),
                           ('LocalContentCanAccessRemoteUrls', False), ('LocalContentCanAccessFileUrls', True),
                           ('PluginsEnabled', False), ('PdfViewerEnabled', False), ('WebGLEnabled', False)):
            settings.setAttribute(getattr(QWebEngineSettings.WebAttribute, key), value)
        self.channel = QWebChannel(page)
        self.bridge = ChatBridge(self)
        self.channel.registerObject('chat', self.bridge)
        page.setWebChannel(self.channel)
        self.ready = False
        self.version = 0
        self.presented_version = -1
        self.payload = {}
        self.snapshots = {}
        self._source = None
        self._projection = None
        self.loadFinished.connect(self._loaded)
        self.setUrl(QUrl.fromLocalFile(str(ASSETS / 'chat.html')))

    def _loaded(self, ok):
        self.ready = bool(ok)
        if ok and self.payload:
            self.page().runJavaScript('window.refreshChat && window.refreshChat()')

    def set_content(self, text, tokens, font):
        if text != self._source:
            self._source = text
            self._projection = project_chat(text)
        projection = self._projection
        self.version += 1
        self.snapshots[self.version] = projection
        self.snapshots = {key: value for key, value in self.snapshots.items()
                          if key in (self.version, self.presented_version)}
        colors = {key: tokens[key] for key in ('text', 'secondary', 'surface', 'accent', 'info', 'selected')}
        colors['border'] = tokens['divider']
        self.payload = dict(version=self.version, html=projection.body, math=projection.math,
                            colors=colors, light=tokens['scheme'] == 'light', font=font.family(),
                            size=font.pixelSize() if font.pixelSize() > 0 else round(font.pointSizeF() * 96 / 72))
        if self.ready:
            self.page().runJavaScript('window.refreshChat && window.refreshChat()')

    def presented(self, version, height):
        if version not in self.snapshots or version < self.presented_version:
            return
        self.presented_version = version
        self.contentReady.emit(max(24, min(20000, height + 2)))

    def activate(self, command, version):
        projection = self.snapshots.get(version)
        if projection is None or version != self.presented_version:
            return
        action, _, key = command.partition('/')
        if not key.isdigit():
            return
        index = int(key)
        if action == 'copy' and 0 <= index < len(projection.codes):
            QApplication.clipboard().setText(projection.codes[index])
        elif action == 'link' and 0 <= index < len(projection.links):
            url = QUrl(projection.links[index])
            if url.scheme() in ('https', 'http') and url.host():
                QDesktopServices.openUrl(url)
