"""Two separated editor surfaces inside one modal window and transaction."""
from PySide6.QtCore import Qt, QRect, QSize, QPoint, QEvent, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QScrollArea, QLayout, QSizePolicy, QStackedWidget
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

    def __init__(self, dialog, details, close, *, title='進階功能', content=None, scroll=None, minimum_height=480):
        super().__init__(dialog)
        self.dialog = dialog; self.opened = False; self.preferred_width = 580
        self.progress = 0.; self._fitting = False; self._envelope = False
        self.content = content if content is not None else getattr(dialog,'pages',None)
        self.form_scroll = scroll if scroll is not None else getattr(dialog,'form_scroll',None)
        self.minimum_height = minimum_height; self.close_side = close
        self.main = dialog.findChild(QFrame, 'DialogSurface')
        dialog.layout().removeWidget(self.main)
        dialog.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.main.setParent(self)
        dialog.layout().addWidget(self)
        self.side = QFrame(self); self.side.setObjectName('DialogSurface')
        content = QVBoxLayout(self.side); content.setContentsMargins(24, 24, 24, 20); content.setSpacing(16)
        self.collapse = button('', lambda: self.close_side(), 'Quiet'); self.collapse.setIcon(icon('close')); self.collapse.setFixedSize(40, 40)
        self.collapse.setAccessibleName('收合'+title)
        self.heading = label(title, 'DialogTitle')
        content.addLayout(row(self.heading, None, self.collapse))
        content.addWidget(prompt_divider())
        self.scroll = QScrollArea(self.side); self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.side_pages = QStackedWidget(self.side)
        self.scroll.setWidget(self.side_pages)
        self.scroll.viewport().setAutoFillBackground(False)
        content.addWidget(self.scroll, 1)
        if details is not None:self.set_content(details, title, close)
        self.main.show(); self.main.raise_(); self.side.hide()
        self.motion = QVariantAnimation(self); self.motion.setDuration(220)
        self.motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.motion.valueChanged.connect(self.place); self.motion.finished.connect(self.finished)
        dialog.parentWidget().installEventFilter(self)

    def set_content(self, widget, title, close):
        if self.side_pages.indexOf(widget) < 0: self.side_pages.addWidget(widget)
        self.side_pages.setCurrentWidget(widget); widget.setAutoFillBackground(False)
        self.heading.setText(title); self.collapse.setAccessibleName('收合'+title)
        self.close_side = close
        self.scroll.verticalScrollBar().setValue(0)

    def sizeHint(self): return QSize(self.preferred_width - 12, 620)
    def minimumSizeHint(self): return QSize(280, 0)

    def bounds(self):
        parent = self.dialog.parentWidget()
        bounds = QRect(parent.mapToGlobal(QPoint(12, 12)), parent.size() - QSize(24, 24))
        return bounds.intersected(self.dialog.screen().availableGeometry().adjusted(20, 20, -20, -20))

    def set_main_width(self, width):
        self.preferred_width = width

    def center_frame(self, width, height):
        bounds = self.bounds(); size = QSize(min(width, bounds.width()), min(height, bounds.height()))
        rect = QRect(QPoint(), size); rect.moveCenter(bounds.center())
        self.dialog.setGeometry(rect)

    def expanded_width(self):
        total = min(self.preferred_width+self.GAP+self.SIDE_WIDTH, self.bounds().width())
        # Share a constrained viewport instead of retaining a wide main card
        # and squeezing the preview below the text editor's usable width.
        self.side_width = min(self.SIDE_WIDTH, max(240, round(total*.38))) if total < self.preferred_width+self.GAP+self.SIDE_WIDTH else self.SIDE_WIDTH
        self.main_width = max(1, total-12-self.GAP-self.side_width)
        return total

    def place(self, progress=None):
        if progress is not None: self.progress = float(progress)
        # Keep both cards at their own width through the final animation frame.
        # QAbstractAnimation is already Stopped when its last value is emitted.
        if self._envelope:
            main_width = self.main_width; side_width = self.side_width
            offset = round((self.width() - main_width) / 2 * (1 - self.progress))
            side_start = (self.width() - main_width) / 2 + main_width - side_width
            side_x = round(side_start + (main_width + self.GAP - side_start) * self.progress)
            self.side.setGeometry(side_x, 0, side_width, self.height())
            self.main.setGeometry(offset, 0, main_width, self.height())
        else:
            self.main.setGeometry(self.rect())
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event); self.place()

    def set_open(self, opened, animate=True):
        if self.opened == opened:
            if not animate and self._envelope:
                self.motion.stop(); self.place(1. if opened else 0.); self.finished()
            return
        self.motion.blockSignals(True); self.motion.stop(); self.opened = opened
        dialog = self.dialog
        try:
            if opened:
                self.side.show(); self.main.raise_()
            self.motion.setDuration(max(1, round((220 if opened else 180)*abs((1. if opened else 0.)-self.progress))))
            self.motion.setStartValue(self.progress); self.motion.setEndValue(1. if opened else 0.)
            self.motion.setCurrentTime(0); self.motion.blockSignals(False)
            if animate and not reduced_motion(dialog): self.motion.start()
            else:
                self.place(1. if opened else 0.); self.finished()
        finally:
            self.motion.blockSignals(False)
            dialog.update()

    def finished(self):
        if not self.opened:
            self.side.hide(); self.progress = 0.; self.place()
        self.dialog.update()

    def fit_content(self, *, before_show=False):
        dialog = self.dialog
        if self.content is None or self._fitting or (not before_show and not dialog.isVisible()) or self.motion.state() == QVariantAnimation.State.Running: return
        self._fitting = True
        try:
            dialog.ensurePolished(); content = self.content
            # Reserve the union of both cards before the first native paint.
            # Opening/closing only moves child surfaces; resizing a translucent
            # top-level HWND at either endpoint flashes its backing store.
            width = self.expanded_width(); self._envelope = True
            dialog.resize(width,dialog.height());dialog.layout().activate();self.place();self.main.layout().activate()
            for layout in reversed(content.findChildren(QLayout)): layout.invalidate()
            for widget in reversed(content.findChildren(QWidget)): widget.updateGeometry()
            content.layout().activate(); content.updateGeometry(); self.main.layout().activate()
            chrome = self.main.height() - self.form_scroll.viewport().height() + 12
            height = max(self.minimum_height, content.sizeHint().height() + chrome)
            bounds = self.bounds(); height = min(height, bounds.height())
            self.center_frame(width, height)
            dialog.layout().activate(); self.place(); content.layout().activate()
        finally: self._fitting = False

    def eventFilter(self, watched, event):
        from shiboken6 import isValid
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move) and isValid(self.dialog) and self.dialog.isVisible():
            if self.opened:
                self.motion.stop(); self.progress = 1.
            elif self._envelope:
                self.motion.stop();self.finished()
            self.fit_content()
        return super().eventFilter(watched, event)
