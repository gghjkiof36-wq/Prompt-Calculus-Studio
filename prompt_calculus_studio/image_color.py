"""Annotation colors use PCS's existing palette surface, never a system dialog."""
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QLineEdit
from .accent_colors import valid_hex
from .accent_picker import ColorPopover, swatch
from .widgets import button


class AnnotationColors(ColorPopover):
    def __init__(self,owner,value):
        super().__init__(owner,value)
        self.setWindowTitle('標註顏色')
        presets=QHBoxLayout();presets.setSpacing(2);self.presets=[]
        for title,color in (('紅','#ff4545'),('橙','#ff9f43'),('黃','#ffd43b'),('綠','#51cf66'),('藍','#4dabf7'),('紫','#b197fc'),('白','#ffffff'),('黑','#202124')):
            choice=button('',lambda checked=False,c=color:self.select_color(c),'Quiet')
            choice.setIcon(swatch(color));choice.setFixedWidth(28);choice.setAccessibleName(title);choice.setToolTip(title)
            presets.addWidget(choice);self.presets.append(choice)
        self.layout().insertLayout(0,presets)
        self.hex=QLineEdit(value.upper());self.hex.setMaxLength(7);self.hex.setPlaceholderText('#RRGGBB')
        self.hex.setAccessibleName('自訂 HEX 色碼')
        self.sample=self.hex.addAction(swatch(value),QLineEdit.ActionPosition.LeadingPosition)
        self.layout().addWidget(self.hex)
        self.hex.textEdited.connect(self.hex_edited);self.hex.returnPressed.connect(self.accept_hex)
        self.chosen.connect(self.update_hex)

    def select_color(self,value):
        color=QColor(value)
        self.hue=max(0,color.hsvHueF());self.saturation=color.hsvSaturationF();self.value=color.valueF()
        self.update_planes()

    def update_hex(self,value):
        self.hex.setText(value.upper());self.sample.setIcon(swatch(value));self.hex.setToolTip('')

    def hex_edited(self,value):
        if valid_hex(value):self.select_color(value)
        else:self.hex.setToolTip('請輸入 #RRGGBB 色碼')

    def accept_hex(self):
        if valid_hex(self.hex.text()):self.close()

    def open_for(self,control):
        self.adjustSize();area=control.screen().availableGeometry().adjusted(4,4,-4,-4)
        point=control.mapToGlobal(QPoint(0,control.height()+4))
        point.setX(max(area.left(),min(point.x(),area.right()+1-self.width())))
        if point.y()+self.height()>area.bottom()+1:point.setY(control.mapToGlobal(QPoint()).y()-self.height()-4)
        point.setY(max(area.top(),point.y()));self.move(point);self.show();self.plane.setFocus(Qt.FocusReason.PopupFocusReason)
