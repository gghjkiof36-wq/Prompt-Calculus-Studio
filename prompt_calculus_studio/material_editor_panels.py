"""Two separated editor surfaces inside one modal window and transaction."""
from PySide6.QtCore import Qt, QRect, QSize, QPoint, QEvent, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QScrollArea, QLayout, QSizePolicy
from .widgets import button, label, row, preview_path
from .image_drop import ImageDropLabel
from .module_chrome import prompt_divider
from .dialog_presentation import reduced_motion
from .ui_icons import icon


class MaterialPreviewImage(ImageDropLabel):
    """Fit the whole picture to its dedicated page, including after resizing."""
    def __init__(self, store):
        super().__init__(store)
        self.original = QPixmap()
        self.setObjectName('MaterialPreviewImage')
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

    def set_source(self, relative):
        path = preview_path(self.store, relative)
        self.original = QPixmap(str(path)) if path else QPixmap()
        self.render_image()

    def render_image(self):
        if self.original.isNull():
            self.setPixmap(QPixmap()); self.setText('拖入圖片')
        else:
            area = self.contentsRect().size() - QSize(12, 12)
            self.setPixmap(self.original.scaled(area.expandedTo(QSize(1, 1)),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event); self.render_image()


class MaterialPanels(QWidget):
    GAP = 14
    SIDE_WIDTH = 440

    def __init__(self, dialog, details, close):
        super().__init__(dialog)
        self.dialog = dialog; self.opened = False; self.preferred_width = 580
        self.progress = 0.; self._fitting = False
        self.main = dialog.findChild(QFrame, 'DialogSurface')
        dialog.layout().removeWidget(self.main)
        self.main.setParent(self)
        dialog.layout().addWidget(self)
        self.side = QFrame(self); self.side.setObjectName('DialogSurface')
        content = QVBoxLayout(self.side); content.setContentsMargins(24, 24, 24, 20); content.setSpacing(16)
        collapse = button('', lambda: close(), 'Quiet'); collapse.setIcon(icon('close')); collapse.setFixedSize(40, 40)
        collapse.setAccessibleName('收合進階功能')
        content.addLayout(row(label('進階功能', 'DialogTitle'), None, collapse))
        content.addWidget(prompt_divider())
        self.scroll = QScrollArea(self.side); self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(details); details.setAutoFillBackground(False)
        self.scroll.viewport().setAutoFillBackground(False)
        details.show(); content.addWidget(self.scroll, 1)
        self.main.show(); self.main.raise_(); self.side.hide()
        self.motion = QVariantAnimation(self); self.motion.setDuration(220)
        self.motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.motion.valueChanged.connect(self.place); self.motion.finished.connect(self.finished)
        dialog.parentWidget().installEventFilter(self)

    def sizeHint(self): return QSize(self.preferred_width - 12, 620)
    def minimumSizeHint(self): return QSize(280, 0)

    def bounds(self):
        parent = self.dialog.parentWidget()
        bounds = QRect(parent.mapToGlobal(QPoint(12, 12)), parent.size() - QSize(24, 24))
        return bounds.intersected(self.dialog.screen().availableGeometry().adjusted(20, 20, -20, -20))

    def set_main_width(self, width):
        self.preferred_width = width
        if not self.opened:
            self.dialog.resize(min(width, self.bounds().width()), self.dialog.height())

    def place(self, progress=None):
        if progress is not None: self.progress = float(progress)
        if self.opened or self.motion.state() == QVariantAnimation.State.Running:
            main_width = self.main_width; side_width = self.side_width
            offset = round((self.width() - main_width) / 2 * (1 - self.progress))
            side_start = (self.width() - main_width) / 2 + main_width - side_width
            side_x = round(side_start + (main_width + self.GAP - side_start) * self.progress)
            self.side.setGeometry(side_x, 0, side_width, self.height())
            self.main.setGeometry(offset, 0, main_width, self.height())
        else:
            self.main.setGeometry(self.rect())
        self.main.layout().activate(); self.side.layout().activate()

    def resizeEvent(self, event):
        super().resizeEvent(event); self.place()

    def set_open(self, opened, animate=True):
        if self.opened == opened: return
        self.motion.stop(); self.opened = opened
        dialog = self.dialog; updates = dialog.updatesEnabled(); dialog.setUpdatesEnabled(False)
        try:
            if opened:
                if self.side.isHidden(): self.closed_geometry = QRect(dialog.geometry())
                bounds = self.bounds()
                total = min(self.preferred_width + self.GAP + self.SIDE_WIDTH, bounds.width())
                self.side_width = min(self.SIDE_WIDTH, max(310, total - self.preferred_width - self.GAP))
                self.main_width = total - self.GAP - self.side_width - 12
                x = max(bounds.left(), min(dialog.geometry().center().x() - total // 2, bounds.right() - total + 1))
                dialog.setGeometry(x, dialog.y(), total, dialog.height())
                dialog.layout().activate(); self.side.show(); self.main.raise_()
            self.motion.setStartValue(self.progress); self.motion.setEndValue(1. if opened else 0.)
            if animate and not reduced_motion(dialog): self.motion.start()
            else:
                self.place(1. if opened else 0.); self.finished()
        finally:
            dialog.setUpdatesEnabled(updates)
            if updates: dialog.repaint()

    def finished(self):
        if not self.opened:
            self.side.hide()
            rect = QRect(self.closed_geometry); rect.setHeight(self.dialog.height())
            self.dialog.setGeometry(rect); self.dialog.layout().activate(); self.place(0.)
        self.fit_content()

    def fit_content(self):
        dialog = self.dialog
        if self._fitting or not dialog.isVisible() or self.motion.state() == QVariantAnimation.State.Running: return
        self._fitting = True
        try:
            content = dialog.pages
            for layout in reversed(content.findChildren(QLayout)): layout.invalidate()
            for widget in reversed(content.findChildren(QWidget)): widget.updateGeometry()
            content.layout().activate(); content.updateGeometry(); self.main.layout().activate()
            chrome = self.main.height() - dialog.form_scroll.height() + 12
            height = max(480, content.sizeHint().height() + chrome)
            if self.opened: height = max(height, 620)
            bounds = self.bounds(); height = min(height, bounds.height())
            width = min(dialog.width(), bounds.width())
            dialog.setGeometry(max(bounds.left(), min(dialog.x(), bounds.right() - width + 1)),
                max(bounds.top(), min(dialog.y(), bounds.bottom() - height + 1)), width, height)
            dialog.layout().activate(); self.place(); content.layout().activate()
        finally: self._fitting = False

    def eventFilter(self, watched, event):
        if self.dialog.isVisible() and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            if self.opened:
                self.motion.stop(); self.progress = 1.
                total = min(self.preferred_width + self.GAP + self.SIDE_WIDTH, self.bounds().width())
                self.side_width = min(self.SIDE_WIDTH, max(310, total - self.preferred_width - self.GAP))
                self.main_width = total - self.GAP - self.side_width - 12
                self.dialog.resize(total, self.dialog.height())
            self.fit_content()
        return super().eventFilter(watched, event)
