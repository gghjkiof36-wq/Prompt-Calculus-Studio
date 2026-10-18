"""Create synthetic, local-only C examples; never reads a personal note library."""
from pathlib import Path
import math
import struct
import wave
from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QImage,QPainter,QColor,QFont,QPdfWriter,QPageSize

CONTENT=r'''# 雨停之後 · 章節筆記

把故事裡的線索整理在一起。文字、表格與圖像，都回到同一份可以繼續修改的筆記。

## 留下的線索

| 線索 | 出現的位置 | 備註 |
| :--- | :---: | --- |
| 舊地圖 | 書桌 | **河流的另一側**，標記著一間小車站 |
| 沒有署名的信 | 抽屜 | 日期是週三<br>字跡有些模糊 |
| 停擺的懷錶 | 外套口袋 | 顯示 08:15；刻字 A\|B |

> [!note] 寫作提醒
> 先留下具體細節，再決定線索之間的關係。
>
> > [!tip]- 下一場的想法
> > 讓角色回到舊車站，找到另一個見證者。

## 故事的路徑

```mermaid
flowchart LR
    A[舊書店] --> B[河邊的車站]
    B --> C[新的線索]
```

## 時間與距離

若速度是 $v$，經過的時間是 $t$，距離可以記成：

$$
d = vt,\qquad \bar v = \frac{d_1+d_2}{t_1+t_2}
$$

## 資料片段

```python
clues = ["地圖", "信", "懷錶"]
for clue in clues:
    print(clue)  # 僅為範例，不會執行
```

有些答案，需要回到故事開始的地方。[^station]

[^station]: 舊車站的名稱尚未確定，修稿時再補上。

%% 這段是保留在原始文件中的作者註解，閱讀時隱藏。 %%
'''

MEDIA='''# 圖片與媒體

## 場景示意

![場景示意|420](assets/scene.png)

## 參考頁面

![[assets/notes.pdf#page=2|360x460]]

## 聲音與片段

![](assets/tone.wav)

![](assets/scene.webm)

## 資源狀態

![](assets/missing.png)

![遠端圖片](https://example.invalid/image.png)
'''


def create(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True);assets=root/'assets';assets.mkdir(exist_ok=True)
    (root/'雨停之後 · 內容格式.md').write_text(CONTENT,encoding='utf-8')
    (root/'圖片與媒體.md').write_text(MEDIA,encoding='utf-8')
    image=QImage(840,360,QImage.Format.Format_RGB32);image.fill(QColor('#d9ddd7'))
    painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillRect(0,230,840,130,QColor('#a7b5a9'));painter.fillRect(510,108,175,122,QColor('#5f726e'))
    painter.fillRect(540,132,47,58,QColor('#d4d8ca'));painter.fillRect(610,155,40,75,QColor('#bcc5b4'))
    painter.setPen(QColor('#576b6a'));painter.setFont(QFont('Microsoft JhengHei UI',20));painter.drawText(QRectF(36,38,420,90),Qt.AlignmentFlag.AlignLeft,'河邊的舊車站')
    painter.setPen(QColor('#738c9a'));painter.drawLine(35,284,780,284);painter.drawLine(35,304,780,304);painter.end();image.save(str(assets/'scene.png'))
    pdf=QPdfWriter(str(assets/'notes.pdf'));pdf.setPageSize(QPageSize(QPageSize.PageSizeId.A5));pdf.setResolution(96)
    painter=QPainter(pdf)
    for number in (1,2):
        if number>1:pdf.newPage()
        painter.setPen(QColor('#303c3b'));painter.setFont(QFont('Microsoft JhengHei UI',20));painter.drawText(30,65,'章節資料 · '+str(number))
        painter.setFont(QFont('Microsoft JhengHei UI',12));painter.drawText(30,115,'合成文件，用於核對 PDF 頁碼與尺寸。')
        painter.drawImage(QRectF(30,160,440,189),image)
    painter.end()
    with wave.open(str(assets/'tone.wav'),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000)
        stream.writeframes(b''.join(struct.pack('<h',int(1800*math.sin(2*math.pi*440*i/16000))) for i in range(24000)))
    return root
