"""Compact accent menu and keyboard-accessible HSV color popover."""
from PySide6.QtCore import Qt, Signal, QPoint, QSize, QRectF, QEvent
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap, QIcon, QLinearGradient, QPen
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit
from .accent_colors import ACCENT_PRESETS, DEFAULT_CUSTOM_ACCENT, valid_hex
from .widgets import ComboBox, ComboItemDelegate, button
from .popup_surface import PopupSurface
from .theme import visual_tokens
from .ui_icons import icon


def swatch(value):
    pixmap=QPixmap(32,32);pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#808080'),1));painter.setBrush(QColor(value))
    painter.drawEllipse(QRectF(3,3,26,26));painter.end()
    icon=QIcon()
    for mode in QIcon.Mode:
        for state in QIcon.State:icon.addPixmap(pixmap,mode,state)
    return icon


class AccentDelegate(ComboItemDelegate):
    def __init__(self,combo,view):
        super().__init__(view);self.combo=combo

    def paint(self,painter,option,index):
        super().paint(painter,option,index)
        if index.row()==self.combo.currentIndex():
            painter.save();painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(option.palette.text().color(),1.6,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
            x,y=option.rect.right()-20,option.rect.center().y()
            path=QPainterPath();path.moveTo(x-4,y);path.lineTo(x-1,y+3);path.lineTo(x+5,y-4)
            painter.drawPath(path)
            painter.restore()


class AccentCombo(ComboBox):
    def _prepare_popup(self):
        super()._prepare_popup()
        self.view().setItemDelegate(AccentDelegate(self,self.view()))


class ColorPlane(QWidget):
    changed=Signal()

    def __init__(self,owner,hue_only=False):
        super().__init__(owner);self.owner=owner;self.hue_only=hue_only
        self.setFixedHeight(28 if hue_only else 190)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('色相，左右方向鍵調整' if hue_only else '飽和度與亮度，方向鍵調整')

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area=self.rect().adjusted(3,3,-3,-3)
        gradient=QLinearGradient(area.left(),0,area.right(),0)
        if self.hue_only:
            for n in range(7):gradient.setColorAt(n/6,QColor.fromHsvF(n/6,1,1))
        else:
            gradient.setColorAt(0,QColor('white'));gradient.setColorAt(1,QColor.fromHsvF(self.owner.hue,1,1))
        painter.fillRect(area,gradient)
        if not self.hue_only:
            shade=QLinearGradient(0,area.top(),0,area.bottom())
            shade.setColorAt(0,QColor(0,0,0,0));shade.setColorAt(1,QColor('black'))
            painter.fillRect(area,shade)
        x=area.left()+(self.owner.hue if self.hue_only else self.owner.saturation)*area.width()
        y=area.center().y() if self.hue_only else area.top()+(1-self.owner.value)*area.height()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor('#222222'),4));painter.drawEllipse(QRectF(x-6,y-6,12,12))
        painter.setPen(QPen(QColor('white'),2));painter.drawEllipse(QRectF(x-6,y-6,12,12))
        if self.hasFocus():
            painter.setPen(QPen(self.palette().text().color(),1,Qt.PenStyle.DotLine));painter.drawRect(self.rect().adjusted(0,0,-1,-1))

    def move_color(self,event):
        area=self.rect().adjusted(3,3,-3,-3)
        x=max(0,min(1,(event.position().x()-area.left())/max(1,area.width())))
        if self.hue_only:self.owner.hue=x
        else:
            self.owner.saturation=x
            self.owner.value=1-max(0,min(1,(event.position().y()-area.top())/max(1,area.height())))
        self.changed.emit()

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.setFocus();self.move_color(event)

    def mouseMoveEvent(self,event):
        if event.buttons() & Qt.MouseButton.LeftButton:self.move_color(event)

    def keyPressEvent(self,event):
        step=.01*(10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
        if event.key() not in (Qt.Key.Key_Left,Qt.Key.Key_Right,Qt.Key.Key_Up,Qt.Key.Key_Down):
            return super().keyPressEvent(event)
        delta=step if event.key() in (Qt.Key.Key_Right,Qt.Key.Key_Up) else -step
        key='hue' if self.hue_only else 'value' if event.key() in (Qt.Key.Key_Up,Qt.Key.Key_Down) else 'saturation'
        setattr(self.owner,key,max(0,min(1,getattr(self.owner,key)+delta)));self.changed.emit()


class ColorPopover(QWidget):
    chosen=Signal(str)
    finished=Signal()

    def __init__(self,owner,value):
        super().__init__(owner,Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('自訂重點色');self.setFixedWidth(268)
        self.surface=PopupSurface(self,owner)
        color=QColor(value);self.hue=max(0,color.hsvHueF());self.saturation=color.hsvSaturationF();self.value=color.valueF()
        layout=QVBoxLayout(self);layout.setContentsMargins(10,10,10,10);layout.setSpacing(8)
        self.plane=ColorPlane(self);self.strip=ColorPlane(self,True)
        layout.addWidget(self.plane);layout.addWidget(self.strip)
        for plane in (self.plane,self.strip):plane.changed.connect(self.update_planes)

    def update_planes(self):
        self.plane.update();self.strip.update()
        self.chosen.emit(QColor.fromHsvF(self.hue,self.saturation,self.value).name())

    def hideEvent(self,event):
        self.finished.emit()
        super().hideEvent(event)

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:self.close()
        else:super().keyPressEvent(event)


class AccentEditor(QWidget):
    changed=Signal()
    editingFinished=Signal()

    def __init__(self,settings,parent=None):
        super().__init__(parent)
        self.combo=AccentCombo();self.combo.setAccessibleName('重點色')
        self.combo.setIconSize(QSize(14,14))
        for title,key,_ in ACCENT_PRESETS:self.combo.addItem(title,key)
        self.combo.addItem('自訂','custom')
        self.combo.setCurrentIndex(max(0,self.combo.findData(settings.get('accent','neutral'))))
        self.custom=QWidget();custom=QHBoxLayout(self.custom);custom.setContentsMargins(0,0,0,0);custom.setSpacing(4)
        value=settings.get('accent_custom',DEFAULT_CUSTOM_ACCENT)
        self.hex=QLineEdit(value);self.hex.setMaxLength(7);self.hex.setFixedWidth(138)
        self.hex.installEventFilter(self)
        self.hex.setAccessibleName('自訂重點色 HEX 色碼');self.hex.setPlaceholderText('#RRGGBB')
        self.pick=self.hex.addAction(swatch(value),QLineEdit.ActionPosition.LeadingPosition)
        self.pick.setText('開啟自訂選色器');self.pick.setToolTip('選擇顏色')
        self.pick.triggered.connect(self.open_picker)
        custom.addWidget(self.hex)
        self.reset_button=button('',self.reset_accent,'Quiet')
        self.reset_button.setIcon(icon('update'));self.reset_button.setFixedWidth(34)
        self.reset_button.setAccessibleName('重置重點色');self.reset_button.setToolTip('重置重點色')
        layout=QHBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(8)
        layout.addWidget(self.combo,1);layout.addWidget(self.custom);layout.addWidget(self.reset_button)
        self.combo.currentIndexChanged.connect(self.sync_custom)
        self.hex.textChanged.connect(self.sync_hex)
        self.refresh_swatches(settings.get('visual_palette','graphite'))
        self.sync_hex();self.sync_custom()
        self.combo.currentIndexChanged.connect(self.changed)
        self.hex.textChanged.connect(self.color_edited)
        self.hex.editingFinished.connect(self.editingFinished)

    def eventFilter(self,watched,event):
        if watched is self.hex and event.type() in (QEvent.Type.FontChange,QEvent.Type.Polish):
            self.hex.setFixedWidth(max(138,self.hex.fontMetrics().horizontalAdvance('#FFFFFF')+50))
        return super().eventFilter(watched,event)

    def refresh_swatches(self,palette):
        for n,(_,key,_) in enumerate(ACCENT_PRESETS):
            self.combo.setItemIcon(n,swatch(visual_tokens({'visual_palette':palette,'accent':key})['accent']))

    def sync_custom(self,*_):
        self.custom.setVisible(self.combo.currentData()=='custom')
        self.reset_button.setEnabled(self.combo.currentData()!='neutral')

    def reset_accent(self):
        self.combo.setCurrentIndex(self.combo.findData('neutral'))
        self.editingFinished.emit()

    def color_edited(self):
        if valid_hex(self.hex.text()):self.changed.emit()

    def sync_hex(self,*_):
        valid=valid_hex(self.hex.text())
        self.hex.setToolTip('' if valid else '請輸入 # 加上六位十六進位色碼，例如 #F077AF。')
        self.hex.setAccessibleDescription(self.hex.toolTip())
        if valid:self.pick.setIcon(swatch(self.hex.text()))

    def open_picker(self):
        value=self.hex.text() if valid_hex(self.hex.text()) else DEFAULT_CUSTOM_ACCENT
        popup=ColorPopover(self,value);self.popover=popup
        popup.chosen.connect(self.hex.setText);popup.finished.connect(self.editingFinished);popup.adjustSize()
        point=self.hex.mapToGlobal(QPoint(0,self.hex.height()+4));bounds=self.screen().availableGeometry()
        point.setX(max(bounds.left(),min(point.x(),bounds.right()-popup.width())))
        if point.y()+popup.height()>bounds.bottom():point.setY(self.hex.mapToGlobal(QPoint(0,0)).y()-popup.height()-4)
        popup.move(point);popup.show();popup.plane.setFocus()
