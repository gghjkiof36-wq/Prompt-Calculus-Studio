"""An isolated Windows visual fixture; no production data or model scans."""
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor')]
os.environ['QT_QPA_PLATFORM']='windows'
from PySide6.QtWidgets import QApplication, QWidget, QDialogButtonBox
from PySide6.QtGui import QPainter, QColor, QLinearGradient
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from prompt_studio.window import Window
from prompt_studio.widgets import RoundMenu
from prompt_studio.dialogs import ModuleDialog
from prompt_studio.core import uid

app=QApplication([])
OUT=ROOT/'qa'/'ui-v041'; OUT.mkdir(exist_ok=True)
class Backing(QWidget):
    def paintEvent(self,event):
        painter=QPainter(self)
        grad=QLinearGradient(0,0,self.width(),self.height())
        grad.setColorAt(0,QColor('#234759')); grad.setColorAt(0.5,QColor('#705842')); grad.setColorAt(1,QColor('#373363'))
        painter.fillRect(self.rect(),grad)
backing=Backing(); backing.setWindowTitle('Prompt Studio - backdrop fixture')
backing.resize(1850,1050); backing.move(20,20); backing.show()
w=Window(ROOT/'qa'/('ui-review-'+uid())); w.setWindowTitle('Prompt Studio - UI review')
w.state['settings']['online']=False; w.first_models=False
w.state['selections']={w.state['items'][i]['module']:[w.state['items'][i]['id']] for i in (0,2,4,6)}
w.state['temporary']=['white wall','soft lighting']; w.refresh_library(); w.refresh_builder()
w.move(100,70); w.show(); w.raise_()

def render():
    w.state['settings']['material']='acrylic'; w.apply_theme(); QTest.qWait(250)
    w.grab().save(str(OUT/'automatic.png'))
    w.final.setPlainText('1girl, red dress, standing,\nwhite wall, soft lighting, cinematic composition')
    QTest.qWait(250); w.grab().save(str(OUT/'manual.png'))
    w.copy_final(); w.grab().save(str(OUT/'copied.png'))
    menu=RoundMenu(w)
    for text in ('複製此提示詞','加入／取消選擇','編輯提示詞與預覽圖'): menu.addAction(text)
    menu.addSeparator(); menu.addAction('刪除項目…'); menu.open_at(w.mapToGlobal(w.rect().center()))
    QTest.qWait(250); img=menu.grab().toImage(); img.save(str(OUT/'menu.png'))
    assert all(img.pixelColor(x,y).alpha()==0 for x,y in ((0,0),(img.width()-1,0),(0,img.height()-1),(img.width()-1,img.height()-1)))
    menu.close(); QTest.qWait(30)
    dialog=ModuleDialog(w); dialog.show(); QTest.qWait(250)
    dialog.grab().save(str(OUT/'dialog.png'))
    buttons=dialog.findChild(QDialogButtonBox).buttons()
    assert len({b.height() for b in buttons})==1,[(b.text(),b.size()) for b in buttons]
    dialog.close()
    for width,height,font in ((1100,740,11),(960,620,11),(1100,740,18)):
        w.resize(width,height); w.state['settings']['ui_size']=font; w.apply_theme(); QTest.qWait(250)
        w.grab().save(str(OUT/f'compact-{width}-{font}.png'))
    w.state['settings']['ui_size']=11; w.resize(1440,900); w.apply_theme()
    w.builder_fold.set_expanded(True,animated=False); w.builder_hint.show()
    w.module_fold.set_expanded(True,animated=False)
    if '--interactive' not in sys.argv: w.close(); backing.close(); app.quit()
QTimer.singleShot(100,render)
QTimer.singleShot(1200000,app.quit)
app.exec()
