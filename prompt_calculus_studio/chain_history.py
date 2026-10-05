"""Paged chain history; control actions are separate from canvas undo."""
import json
from PySide6.QtCore import QTimer,Qt,QSize
from PySide6.QtWidgets import QListWidget,QListWidgetItem,QPlainTextEdit,QMessageBox
from PySide6.QtGui import QIcon
from .widgets import StudioDialog,ComboBox,button,label,row

NAMES=dict(waiting='等待',preparing='準備',submitted='已提交',unconfirmed='提交未確認',collecting='保存圖片',
           complete='完成',failed='失敗',result_error='結果尚未取得',paused='暫停',running='執行中',cancelled='已取消')


class ChainHistory(StudioDialog):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.runner=window.comfy.input_flow.chain
        self.setWindowTitle('串接流程與階段紀錄');self.resize(880,720);self.page=0;self.rows=[]
        self.runs=ComboBox();self.body.addWidget(self.runs)
        for run in self.runner.store.runs(window.state['workspace']):
            self.runs.addItem(run['plan']['name']+' · '+NAMES.get(run['status'],run['status'])+' · '+run['id'][:8],run['id'])
        self.summary=label('','Subtle',True);self.body.addWidget(self.summary)
        self.list=QListWidget();self.list.setIconSize(QSize(64,64));self.body.addWidget(self.list,2)
        self.page_label=label('');self.body.addLayout(row(button('上一頁',lambda:self.turn(-1),'Quiet'),self.page_label,button('下一頁',lambda:self.turn(1),'Quiet')))
        self.detail=QPlainTextEdit();self.detail.setReadOnly(True);self.body.addWidget(self.detail,2)
        self.body.addLayout(row(button('暫停',lambda:self.action('pause'),'Quiet'),button('繼續',lambda:self.action('resume'),'Quiet'),
            button('取消本次流程',lambda:self.action('cancel'),'Quiet'),button('查看這項圖片',self.results,'Quiet')))
        self.body.addLayout(row(button('重試／重取結果',lambda:self.retry(False),'Quiet'),button('用目前設定重新準備',lambda:self.retry(True),'Quiet'),
            button('核對原提交',self.recheck,'Quiet'),None,button('關閉',self.accept,'Quiet')))
        self.runs.currentIndexChanged.connect(lambda:(setattr(self,'page',0),self.refresh()))
        self.list.currentRowChanged.connect(self.selected)
        self.timer=QTimer(self);self.timer.setInterval(1500);self.timer.timeout.connect(self.refresh);self.timer.start();self.finished.connect(self.timer.stop)
        self.refresh()

    def turn(self,delta):self.page=max(0,self.page+delta);self.refresh()

    def refresh(self):
        ident=self.runs.currentData()
        if not ident:return
        run=self.runner.store.read('chain_runs',ident);items=self.runner.store.items(ident)
        self.page=min(self.page,max(0,(len(items)-1)//50));selected=self.item();selected=selected['id'] if selected else None
        self.rows=items[self.page*50:(self.page+1)*50];self.list.blockSignals(True);self.list.clear()
        for item in self.rows:
            stage=run['plan']['stages'][item['stage']]
            row_item=QListWidgetItem(f"第 {item['round']} 輪 · {stage['name']} · 第 {item['position']+1} 項 · {NAMES[item['status']]}")
            self.list.addItem(row_item)
        self.list.setCurrentRow(next((i for i,item in enumerate(self.rows) if item['id']==selected),0));self.list.blockSignals(False)
        self.summary.setText(' → '.join(s['name'] for s in run['plan']['stages'])+'\n'+NAMES[run['status']]+' · '+run.get('message',''))
        self.page_label.setText(f'{self.page+1}／{max(1,(len(items)+49)//50)} 頁 · {len(items)} 個階段輸入項')
        self.selected(self.list.currentRow())

    def item(self):
        index=self.list.currentRow()
        return self.rows[index] if 0<=index<len(self.rows) else None

    def selected(self,index):
        item=self.item()
        if not item:return
        job=self.window.comfy.generation.record(item.get('attempt'))
        text=item.get('error','')
        if job:
            from .job_details import describe
            text+='\n'+describe(job)
        text+='\n來源與結果：\n'+json.dumps(dict(input=item['entry'],results=self.runner.store.results(item['id'])),ensure_ascii=False,indent=2)
        if text!=self.detail.toPlainText():self.detail.setPlainText(text)

    def action(self,method):
        try:
            if self.runs.currentData():getattr(self.runner,method)(self.runs.currentData())
        except ValueError as exc:self.window.notice(str(exc))
        self.refresh()

    def retry(self,reprepare):
        item=self.item()
        if not item:return
        allow=False
        if item['status']=='unconfirmed':
            allow=QMessageBox.question(self,'建立新的生成嘗試','原提交可能已生成。保留原紀錄，仍要再次生成這一項嗎？')==QMessageBox.StandardButton.Yes
            if not allow:return
        try:self.runner.retry(item['id'],reprepare,allow)
        except ValueError as exc:self.window.notice(str(exc))
        self.refresh()

    def recheck(self):
        item=self.item()
        if item and item.get('attempt'):self.window.comfy.generation.recheck(item['attempt'])

    def results(self):
        item=self.item()
        if not item:return
        from .widgets import scrolling
        from PySide6.QtGui import QImageReader,QPixmap
        results=self.runner.store.results(item['id'])
        if not results:self.window.notice('這項還沒有已保存的圖片。');return
        dialog=StudioDialog(self);dialog.setWindowTitle('這項已保存的圖片');dialog.resize(800,720)
        picker=ComboBox();picker.addItems(['第 '+str(r['index']+1)+' 張 · #'+r['node'] for r in results]);dialog.body.addWidget(picker)
        photo=label('');photo.setAlignment(Qt.AlignmentFlag.AlignCenter);dialog.body.addWidget(scrolling(photo),1)
        def show():
            source=results[picker.currentIndex()]['source'];reader=QImageReader(str(self.window.store.directory/source['relative']))
            size=reader.size();size.scale(740,580,Qt.AspectRatioMode.KeepAspectRatio);reader.setScaledSize(size)
            photo.setPixmap(QPixmap.fromImage(reader.read()))
        picker.currentIndexChanged.connect(show);show();dialog.body.addWidget(button('關閉',dialog.accept,'Quiet'));dialog.exec();dialog.deleteLater()


def show(window):
    dialog=ChainHistory(window);dialog.exec();dialog.deleteLater()
