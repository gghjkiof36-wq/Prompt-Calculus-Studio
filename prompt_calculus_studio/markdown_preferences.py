"""Document display controls with current values, in one anchored panel."""
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox, QSpinBox, QCheckBox, QFontComboBox
from PySide6.QtGui import QFont
from .theme import visual_tokens, window_appearance


class DisplayPreferences(QFrame):
    def __init__(self, surface):
        super().__init__(surface, Qt.WindowType.Popup);self.surface = surface
        self.setObjectName('MarkdownPreferences');self.setAccessibleName('編輯與閱讀設定');self.setFixedWidth(344)
        t = visual_tokens(window_appearance(surface))
        self.setStyleSheet(f'''QFrame#MarkdownPreferences {{background:{t['surface']};border:1px solid {t['line']};border-radius:8px;}}
            QFrame#MarkdownPreferences QLabel {{background:transparent;}}
            QFrame#MarkdownPreferences QToolButton {{background:transparent;border:1px solid transparent;border-radius:4px;padding:2px;}}
            QFrame#MarkdownPreferences QToolButton:hover {{background:{t['hover']};}}
            QFrame#MarkdownPreferences QToolButton:focus {{border-color:{t['accent']};}}
            QFrame#MarkdownPreferences QComboBox, QFrame#MarkdownPreferences QSpinBox {{min-height:0;padding:4px 8px;border-radius:5px;}}
            QFrame#MarkdownPreferences QComboBox {{padding-right:26px;}}
            QFrame#MarkdownPreferences QComboBox::drop-down {{width:22px;}}
            QFrame#MarkdownPreferences QCheckBox {{padding:0;spacing:8px;}}
        ''')
        layout = QVBoxLayout(self);layout.setContentsMargins(16,12,16,16);layout.setSpacing(14)
        header = QHBoxLayout();label = QLabel('編輯與閱讀');font = label.font();font.setBold(True);label.setFont(font)
        header.addWidget(label);header.addStretch();header.addWidget(surface.tool('關閉設定',self.dismiss,'close'));layout.addLayout(header)
        grid = QGridLayout();grid.setHorizontalSpacing(12);grid.setVerticalSpacing(10);grid.setColumnStretch(1,1)
        layout.addLayout(grid);p = surface.preferences
        def row(label, control):
            index = grid.rowCount();grid.addWidget(QLabel(label),index,0);grid.addWidget(control,index,1)
            control.setAccessibleName(label);control.setFixedHeight(32);return control
        self.family = row('字體',QFontComboBox());self.family.setCurrentFont(QFont(p['reading_family']))
        self.family.currentFontChanged.connect(lambda font:surface.set_preference('reading_family',font.family()))
        self.size = row('字級',QSpinBox());self.size.setRange(9,32);self.size.setSuffix(' pt');self.size.setValue(p['reading_size'])
        self.size.setKeyboardTracking(False);self.size.valueChanged.connect(lambda value:surface.set_preference('reading_size',value))
        def options(label,key,entries):
            control = row(label,QComboBox())
            for title,value in entries:control.addItem(title,value)
            if control.findData(p[key])<0:control.addItem(str(p[key]),p[key])
            control.setCurrentIndex(control.findData(p[key]));control.currentIndexChanged.connect(lambda:surface.set_preference(key,control.currentData()))
            return control
        self.width_control = options('行寬','reading_width',[('窄',640),('標準',880),('寬',1120)])
        self.spacing = options('行距','line_height',[('緊密',130),('標準',150),('寬鬆',180)])
        zoom = QHBoxLayout();zoom.setSpacing(4);zoom.addWidget(QLabel('縮放'));zoom.addStretch()
        zoom.addWidget(surface.tool('縮小',lambda:surface.set_zoom(surface.zoom_percent-10),'minus'))
        self.zoom = surface.tool('重設縮放',lambda:surface.set_zoom(100),text=str(surface.zoom_percent)+'%');self.zoom.setFixedWidth(60)
        zoom.addWidget(self.zoom);zoom.addWidget(surface.tool('放大',lambda:surface.set_zoom(surface.zoom_percent+10),'plus'));layout.addLayout(zoom)
        line = QFrame();line.setFrameShape(QFrame.Shape.HLine);line.setStyleSheet('color:'+t['line']);layout.addWidget(line)
        for key,label in [('line_numbers','行號'),('auto_pairs','自動配對括號'),('wrap','自動折行'),('hard_breaks','保留原文換行')]:
            control = QCheckBox(label);control.setChecked(p[key]);control.toggled.connect(lambda value,k=key:surface.set_preference(k,value));layout.addWidget(control)
        tabs = QHBoxLayout();tabs.addWidget(QLabel('Tab 縮排'));tabs.addStretch();self.tabs = QComboBox()
        for n in (2,4):self.tabs.addItem(str(n),n)
        self.tabs.setCurrentIndex(self.tabs.findData(p['tab_width']));self.tabs.setFixedSize(88,32);self.tabs.setAccessibleName('Tab 縮排')
        self.tabs.currentIndexChanged.connect(lambda:surface.set_preference('tab_width',self.tabs.currentData()));tabs.addWidget(self.tabs);layout.addLayout(tabs)

    def open(self):
        self.adjustSize();anchor = self.surface.view_tools
        pos = anchor.mapToGlobal(QPoint(anchor.width()-self.width(),anchor.height()+6));screen = anchor.screen().availableGeometry()
        pos.setX(max(screen.left(),min(pos.x(),screen.right()-self.width()+1)))
        pos.setY(max(screen.top(),min(pos.y(),screen.bottom()-self.height()+1)))
        self.move(pos);self.show();self.family.setFocus()

    def dismiss(self):self.close();self.surface.focus_document()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:self.dismiss();event.accept();return
        super().keyPressEvent(event)
