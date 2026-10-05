"""Brief import notifications that never interrupt editing or take focus."""
import json
from PySide6.QtCore import Qt,QVariantAnimation,QEasingCurve,QTimer,QEvent
from PySide6.QtWidgets import QFrame,QVBoxLayout,QGraphicsOpacityEffect
from .widgets import label


class ImportErrorToast(QFrame):
    def __init__(self,parent,message):
        super().__init__(parent)
        self.setObjectName('ErrorToast'); self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        layout=QVBoxLayout(self); layout.setContentsMargins(18,12,18,14); layout.setSpacing(5)
        layout.addWidget(label('匯入失敗','Heading')); self.message=label('',None,True); layout.addWidget(self.message)
        # Only this small, short-lived notification gets an opacity effect.
        self.opacity=QGraphicsOpacityEffect(self); self.setGraphicsEffect(self.opacity)
        self.motion=QVariantAnimation(self); self.motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.motion.valueChanged.connect(self.animate); self.motion.finished.connect(self.finished_motion)
        self.expire=QTimer(self); self.expire.setSingleShot(True); self.expire.timeout.connect(self.fade)
        self.retired=False; self.progress=0.0; parent.installEventFilter(self)
        self.present(message)

    def present(self,message):
        self.motion.stop(); self.expire.stop(); self.exiting=False; self.message.setText(message)
        self.resize_to_parent(); self.animate(0.0); self.show(); self.raise_()
        self.motion.setDuration(180); self.motion.setStartValue(0.0); self.motion.setEndValue(1.0); self.motion.start()

    def resize_to_parent(self):
        self.setFixedWidth(max(180,min(480,self.parentWidget().width()-40)))
        self.setFixedHeight(self.layout().totalHeightForWidth(self.width()))

    def animate(self,value):
        self.progress=float(value); self.opacity.setOpacity(self.progress)
        parent=self.parentWidget(); bottom=parent.height()-self.height()-24
        distance=12 if self.exiting else self.height()+24
        self.move((parent.width()-self.width())//2,bottom+round(distance*(1-self.progress)))

    def fade(self):
        if self.retired or self.exiting: return
        self.exiting=True; self.motion.stop()
        self.motion.setDuration(180); self.motion.setStartValue(self.progress); self.motion.setEndValue(0.0); self.motion.start()

    def finished_motion(self):
        if self.exiting: self.dispose()
        else: self.expire.start(1800)

    def dispose(self):
        if self.retired: return
        self.retired=True; self.expire.stop(); self.motion.stop(); self.hide(); self.deleteLater()

    def eventFilter(self,watched,event):
        if watched is self.parentWidget():
            if event.type()==QEvent.Type.Resize: self.resize_to_parent(); self.animate(self.progress)
            elif event.type() in (QEvent.Type.Hide,QEvent.Type.Close): self.dispose()
        return super().eventFilter(watched,event)


def error_message(message):
    if isinstance(message,json.JSONDecodeError): message=f'JSON 格式錯誤（第 {message.lineno} 行）。'
    elif isinstance(message,FileNotFoundError): message='找不到檔案，請確認檔案位置。'
    elif isinstance(message,PermissionError): message='無法讀取檔案，請確認存取權限。'
    return str(message)


def import_error(parent,message):
    text=error_message(message)
    for toast in parent.findChildren(ImportErrorToast):
        if not toast.retired:
            toast.present(text); return toast
    return ImportErrorToast(parent,text)
