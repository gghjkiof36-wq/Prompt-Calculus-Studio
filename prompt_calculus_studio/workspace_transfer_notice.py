"""A bounded top-right failure card that leaves the current editor usable."""
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from .theme import visual_tokens


class TransferNotice(QFrame):
    def __init__(self, parent, workspace, title='加入 Canvas'):
        super().__init__(parent)
        self.workspace = workspace
        self.setObjectName('CanvasTransferNotice')
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 14)
        header = QHBoxLayout()
        header.addWidget(QLabel(title))
        header.addStretch()
        self.close_button = QPushButton('×')
        self.close_button.setAccessibleName('關閉通知')
        self.close_button.setFixedSize(24, 24)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.clicked.connect(self.hide)
        header.addWidget(self.close_button)
        layout.addLayout(header)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.expire = QTimer(self)
        self.expire.setSingleShot(True)
        self.expire.timeout.connect(self.hide)
        parent.installEventFilter(self)
        self.hide()

    def present(self, reason):
        tokens = visual_tokens(self.workspace.display_settings())
        self.setStyleSheet('QFrame#CanvasTransferNotice {background:'+tokens['raised']+'; border:1px solid '+tokens['line']+'; border-radius:10px;} '
                          'QLabel {background:transparent; border:0; color:'+tokens['text']+';} '
                          'QPushButton {border:0; padding:0; background:transparent; color:'+tokens['text']+';}')
        # Long system errors remain available without covering the editor.
        self.message.setText(reason if len(reason) <= 240 else reason[:237]+'…')
        self.message.setToolTip(reason if len(reason) > 240 else '')
        self.reposition()
        self.show()
        self.raise_()
        self.expire.start(6000)

    def reposition(self):
        parent = self.parentWidget()
        self.setFixedWidth(min(352, max(1, parent.width()-24)))
        self.adjustSize()
        self.move(max(12, parent.width()-self.width()-16), 16)

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:self.reposition()
        return False
