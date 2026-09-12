"""Compare the Windows compositor path, using only an isolated test window."""
import os,sys,ctypes,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor')]; os.environ['QT_QPA_PLATFORM']='windows'
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QPainter,QColor,QLinearGradient
from PySide6.QtWidgets import QApplication,QMainWindow,QWidget,QVBoxLayout,QLabel
app=QApplication([])
class Backing(QWidget):
 def paintEvent(self,e):
  p=QPainter(self); g=QLinearGradient(0,0,self.width(),self.height()); g.setColorAt(0,QColor('#42799c')); g.setColorAt(1,QColor('#955637')); p.fillRect(self.rect(),g)
b=Backing(); b.setWindowTitle('Probe backdrop'); b.resize(1300,850); b.move(100,50)
w=QMainWindow(); w.setWindowTitle('Prompt Studio - Native frame probe')
w.setWindowFlags(Qt.WindowType.Window|Qt.WindowType.ExpandedClientAreaHint|Qt.WindowType.NoTitleBarBackgroundHint)
w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
body=QWidget(); w.setCentralWidget(body); box=QVBoxLayout(body); box.setContentsMargins(30,60,30,30)
box.addWidget(QLabel('Native frame / Acrylic / dark surface')); box.addStretch()
w.setStyleSheet('QMainWindow{background:transparent;} QWidget{color:white;} QWidget#NativeBody{background:rgba(14,14,14,90);}')
body.setObjectName('NativeBody'); w.resize(1000,640); w.move(200,120)
h=ctypes.c_void_p(int(w.winId())); dwm=ctypes.windll.dwmapi
for a,v in ((20,1),(33,2),(34,0xfffffffe),(38,3)):
 value=ctypes.c_uint(v); print('DWM',a,dwm.DwmSetWindowAttribute(h,a,ctypes.byref(value),4))
class Margins(ctypes.Structure): _fields_=[(x,ctypes.c_int) for x in ('left','right','top','bottom')]
m=Margins(-1,-1,-1,-1); dwm.DwmExtendFrameIntoClientArea(h,ctypes.byref(m))
b.show(); w.show()
def render():
 out=ROOT/'qa/native-frame'; out.mkdir(exist_ok=True)
 style=ctypes.windll.user32.GetWindowLongPtrW; style.argtypes=[ctypes.c_void_p,ctypes.c_int]; style.restype=ctypes.c_ssize_t
 print('style',hex(style(h,-16)&0xffffffff),'exstyle',hex(style(h,-20)&0xffffffff),'alpha',w.windowHandle().format().alphaBufferSize(),'visible',ctypes.windll.user32.IsWindowVisible(h),flush=True)
 w.grab().save(str(out/'widget.png'))
 w.screen().grabWindow(0).save(str(out/'desktop.png'))
 if '--interactive' not in sys.argv: w.close(); b.close(); app.quit()
QTimer.singleShot(600,render); QTimer.singleShot(300000,app.quit); app.exec()
