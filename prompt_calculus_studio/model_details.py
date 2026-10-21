"""A compact model preview with one dismissible details editor."""
from PySide6.QtCore import Qt, QRect, QRectF
from PySide6.QtGui import QPainter, QColor, QPainterPath
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QFormLayout, QGridLayout, QLineEdit, QPlainTextEdit, QTabWidget, QSizePolicy
from .widgets import StudioDialog, label, row, button, panel, scrolling, ComboBox
from .civitai_controls import selectable
from .civitai_assets import categories
from .image_drop import ImageDropLabel
from .ui_icons import icon


class CopyTrigger(QLabel):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip('複製觸發詞')
        self.setAccessibleName('複製觸發詞')
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def copy(self):
        if self.text():self.page.window.copy_text(self.text())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.copy();event.accept()
        else:super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.copy();event.accept()
        else:super().keyPressEvent(event)


class ModelPreview(ImageDropLabel):
    def __init__(self, page):
        super().__init__(page.window.store)
        self.page = page
        self.setObjectName('DialogEditor')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip('拖入或選擇預覽圖')
        self.setAccessibleName('預覽圖，點選或拖入圖片')
        self.setMouseTracking(True)
        self.hover_y = -1

    def enterEvent(self, event):
        self.hover_y = event.position().y()
        self.update()
        super().enterEvent(event)

    def mouseMoveEvent(self, event):
        self.hover_y = event.position().y()
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hover_y = -1
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.pixmap().isNull() or self.hover_y < 0:return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip=QPainterPath();clip.addRoundedRect(QRectF(self.rect()),8,8);painter.setClipPath(clip)
        half = self.height()//2
        for top, text, rgb in ((0, '更換', (255,255,255)), (half, '移除', (153,63,76))):
            rect = QRect(0,top,self.width(),half if top == 0 else self.height()-half)
            active = (self.hover_y < half) == (top == 0)
            painter.fillRect(rect,QColor(*rgb,(125 if active else 55) if top == 0 else (100 if active else 38)))
            # A text shadow keeps captions legible without a separate button surface.
            painter.setPen(QColor(255,255,255,180) if top == 0 else QColor(0,0,0,150))
            painter.drawText(rect.translated(0,1),Qt.AlignmentFlag.AlignCenter,text)
            painter.setPen(QColor(25,29,33) if top == 0 else Qt.GlobalColor.white)
            painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,text)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            if not self.pixmap().isNull() and event.position().y() >= self.height()/2:self.page.clear_preview()
            else:self.page.choose_image()
            event.accept()
        else:super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.page.choose_image();event.accept()
        elif event.key() == Qt.Key.Key_Delete and not self.pixmap().isNull():
            self.page.clear_preview();event.accept()
        else:super().keyPressEvent(event)


class ModelDetails(StudioDialog):
    def __init__(self, page):
        super().__init__(page.window)
        self.page = page
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.set_outside_dismiss(True)
        self.setWindowTitle('模型詳細資料')
        self.resize(1080, 800)
        self.setMinimumSize(480, 480)
        self.heading.setWordWrap(True)
        self.heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.heading_row.setStretch(0, 1)
        self.tabs = QTabWidget()
        self.body.addWidget(self.tabs, 1)
        self.body.addLayout(row(None, button('完成', self.reject, 'Primary')))

    def section(self, title, *, scrollable=True):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 16, 8, 8)
        layout.setSpacing(14)
        if scrollable:
            scroll = scrolling(content)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.tabs.addTab(scroll, title)
        else:self.tabs.addTab(content, title)
        return layout

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self.page,'preview'):
            size = 112 if self.height()<680 or self.width()<700 else 176
            if self.page.preview.width()!=size:
                self.page.preview.setFixedSize(size,size)
                self.page.refresh_preview()

    def reject(self):
        super().reject()
        self.page.save()


def build(page):
    card, body = panel('InsetPanel')
    card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    body.setContentsMargins(20, 20, 20, 20)
    body.setSpacing(14)
    page.detail = card
    close = button('', lambda: page.list.setCurrentRow(-1), 'IconButton')
    close.setIcon(icon('close'));close.setToolTip('關閉預覽');close.setAccessibleName('關閉預覽')
    body.addLayout(row(label('模型預覽', 'Subtle'), None, close))
    page.detail_title = label('', 'Heading', True)
    page.detail_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    body.addWidget(page.detail_title)
    page.identity = label('', 'Subtle', True)
    body.addWidget(page.identity)
    page.summary_preview = label('')
    page.summary_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
    page.summary_preview.setFixedHeight(180)
    body.addWidget(page.summary_preview)
    page.summary_trigger = CopyTrigger(page)
    body.addWidget(page.summary_trigger)
    page.details_button = button('詳細資料', page.open_details)
    body.addWidget(page.details_button)
    container = QWidget();layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 2, 0, 16);layout.addWidget(card);layout.addStretch()
    page.detail_scroll = scrolling(container)
    page.detail_scroll.setMinimumWidth(0)
    page.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    page.split.addWidget(page.detail_scroll);page.split.setChildrenCollapsible(False)

    editor = page.details_editor = ModelDetails(page)
    local = editor.section('資料', scrollable=False)
    page.preview = ModelPreview(page)
    page.preview.pathReady.connect(page.receive_image);page.preview.failed.connect(page.window.notice)
    page.preview.setFixedSize(160, 160);page.preview.setWordWrap(True)
    preview_column = QVBoxLayout();preview_column.setSpacing(4)
    preview_column.addWidget(page.preview)
    page.name = QLineEdit();page.category = ComboBox()
    page.category.addItems(categories(page.window.state['settings']))
    page.trigger = QPlainTextEdit();page.trigger.setMinimumHeight(60)
    page.notes = QPlainTextEdit();page.notes.setMinimumHeight(70)
    form = QFormLayout();form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    for title, widget in (('名稱', page.name), ('分類', page.category)):
        form.addRow(title, widget)
    top = row();top.setSpacing(24);top.addLayout(preview_column);top.addLayout(form,1)
    local.addLayout(top)
    local.addSpacing(10)
    form = QGridLayout();form.setHorizontalSpacing(16);form.setVerticalSpacing(16)
    for index,(title, widget) in enumerate((('觸發詞', page.trigger), ('備註', page.notes))):
        form.addWidget(label(title),index,0,Qt.AlignmentFlag.AlignTop)
        form.addWidget(widget,index,1)
        form.setRowStretch(index,2+index)
    form.setColumnStretch(1,1)
    local.addLayout(form,1)
    local.addLayout(row(button('複製觸發詞', lambda: page.window.copy_text(page.trigger.toPlainText()), 'Quiet'),
                        button('加入組合', lambda: page.window.add_temporary(page.trigger.toPlainText()), 'Quiet')))
    source = editor.section('來源')
    page.url = QLineEdit();page.url.setPlaceholderText('https://…')
    page.base_model = selectable();page.creator = selectable();page.civitai_status = selectable()
    page.source_type = selectable();page.source_version = selectable();page.source_trigger = selectable()
    form = QFormLayout();form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    for title, widget in (('類型', page.source_type), ('底模', page.base_model), ('版本', page.source_version),
                          ('作者', page.creator), ('CivitAI', page.civitai_status), ('觸發詞', page.source_trigger), ('網址', page.url)):
        form.addRow(title, widget)
    source.addLayout(form)
    page.source_link = button('開啟 CivitAI', page.open_source_link, 'Quiet')
    source.addLayout(row(page.source_link, button('開啟網址', page.open_link, 'Quiet')))
    source.addStretch()
    files = editor.section('檔案')
    page.path = label('', 'Subtle', True)
    page.path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    page.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    files.addWidget(page.path)
    files.addWidget(button('顯示檔案位置', page.reveal, 'Quiet'))
    files.addStretch()
    files.addWidget(button('移至資源回收筒…', page.recycle, 'DangerQuiet'))
    page.compact_preview = None
