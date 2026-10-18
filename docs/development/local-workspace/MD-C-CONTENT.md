# Markdown C：內容格式試用

本文件說明 MD 1-1 的 C 階段。這是本機開發來源試用，尚不是正式安裝版；D 的文件關係、E 的筆記管理、F 的整合驗收仍在後續階段。

## 怎麼使用

上方保留單列工具。窄欄右端箭頭展開其餘圖示；表格圖示開啟儲存格編輯器，＋選單插入附件、程式碼、公式、Mermaid、提示區塊、腳註與註解。右上鉛筆、原文與書本圖示分別切換編寫、原文、閱讀，滑鼠停留可看到名稱。

| 內容 | 操作及保存方式 |
| --- | --- |
| 表格 | 游標放在表格中按表格圖示，或從閱讀區按「編輯表格」。第一列為欄名；可增刪、移動與排序列／欄，設定欄對齊。Tab／Shift+Tab 換格，Shift+Enter 格內換行。支援中文、空格、基本 Markdown 格式、連結、pipe 與反斜線、TSV 複製貼上。套用是一筆可復原修改，取消保留原文。 |
| 圖片與附件 | 從＋選單選檔，或在編寫區拖入本機檔案、貼上圖片。已在工作區內的檔案保留原位置；外部檔案成功複製至 assets 後才插入相對引用。復原移除引用，不刪共享檔案。 |
| 尺寸 | `![說明\|420](assets/image.png)` 的 alt 尾端 `\|420` 表示寬度；實際原文使用未跳脫的 pipe。也支援 `![[assets/image.png\|420x240]]`。尺寸會按可用欄寬縮小。 |
| 音訊／影片 | 使用圖片形式引用本機檔案，例如 `![](assets/sample.webm)`；播放器提供基本控制，不自動播放。切離閱讀區會暫停。 |
| PDF | `![[assets/reference.pdf#page=2\|360x460]]` 指定初始頁碼與顯示尺寸，實際原文使用未跳脫的 pipe。上一頁／下一頁只改當前預覽，不改筆記。密碼保護、讀取失敗或超過 64 MiB 會顯示原因。 |
| 程式碼 | 反引號行內語法與 fenced code 保持可編輯原文；區塊依語言標記著色，可複製程式碼內容，不執行。 |
| 公式 | `$…$` 與 `$$…$$` 本機顯示常用 LaTeX。使用 KaTeX，不保證每個 MathJax 擴充語法相容；無法呈現時顯示公式原文。 |
| Mermaid | 使用 mermaid fenced code，本機顯示圖表；「編輯圖表原文」返回對應來源位置。錯誤或超過渲染上限仍保留來源。 |
| Callout | `> [!note] 標題` 等類型；`+` 預設展開、`-` 預設收合，支援巢狀。展收不寫回原文。 |
| 腳註與註解 | `[^name]` 與 `[^name]: …` 支援往返定位及編輯來源。`%% … %%` 與 HTML 註解在閱讀時隱藏，程式碼內的同樣字元保持可見。完整腳註側欄屬 E。 |
| 一般貼上 | 編寫模式轉換 HTML 的基本文字、粗斜體、清單、連結、表格與程式碼；原文模式貼純文字。不是網頁完整重製，也不猜測破損內容。 |

### 媒體範圍

| 類別 | 接受的副檔名 | 本輪實際驗證 |
| --- | --- | --- |
| 圖片 | PNG、JPG／JPEG、GIF、WebP、BMP | 合成 PNG 的讀取、相對引用及尺寸；其他格式採 Qt／瀏覽器解碼，未逐格式驗收 |
| 音訊 | WAV、MP3、OGG、FLAC、M4A | 合成 PCM WAV 由實際播放控制啟動、時間前進；其他編碼未驗收 |
| 影片 | WebM、MP4、OGV | 合成 VP9 WebM 的解碼及播放時間前進；容器不保證其中每個影音編碼均能播放 |
| PDF | PDF | 合成兩頁文件、指定頁碼／尺寸、換頁，來源保持 |

缺檔或無法解碼會提示原因。遠端圖片預設只顯示佔位；按「載入這個資源」才提出請求，內容只留在當次閱讀快取。遠端音訊、影片及 PDF 沒有串流載入功能。

## 原文、依賴與信任邊界

共用 SourceBuffer 是唯一正文，閱讀結果不會寫回 Markdown。表格編輯使用開啟時的修訂快照；其他視圖已變更內容時，過期草稿拒絕提交。未修改的表格儲存格保留原始切片；取消或未修改套用不新增 Undo。既有 BOM／換行保存底座沿用。

內容閱讀使用 PySide6 6.11.2 的 QtWebEngine 與 QtPdf。Python 依賴固定為 markdown-it-py 4.0.0、mdit-py-plugins 0.5.0、Pygments 2.19.2；本機資產為 Mermaid 12.2.0、KaTeX 0.19.0、DOMPurify 3.4.17。版本、下載來源及檔案 SHA-256 見 [資產清單](../../../prompt_calculus_studio/assets/markdown/DEPENDENCIES.json)，各套件授權文字隨檔保留。開發下載工具不在產品啟動流程中；試用版不自動下載渲染依賴。

閱讀頁採無持久狀態的 profile、CSP、HTML 清理及檔案白名單。文件內腳本與外部頁面不能執行；本機資源需位於目前資料根，沿用 .obsidian 與 reparse 拒絕規則。自有操作橋接檢查視圖版本及來源修訂，避免舊預覽作用到新文件。

設計依據：[Qt profile](https://doc.qt.io/qtforpython-6.11/PySide6/QtWebEngineCore/QWebEngineProfile.html) 與 [請求攔截](https://doc.qt.io/qt-6/qwebengineurlrequestinterceptor.html) 提供頁面與資源隔離；[KaTeX](https://katex.org/docs/security) 使用 trust=false 及有界展開；[Mermaid](https://mermaid.js.org/config/schema-docs/config.html) 使用 strict 模式與有界文字／連線數；[DOMPurify](https://github.com/cure53/DOMPurify) 清理產生的 HTML／SVG。尺寸只允許原生端產生的有界整數，沒有放寬 URL 載入規則。

## 驗證程度

本輪使用隔離合成資料。內容與表格回歸、實際 QtWebEngine DOM／控制、腳註往返、程式碼複製、圖片尺寸、PDF 換頁、WAV／VP9 WebM 播放、1440／960 寬度及獨立資料／安全複驗分別記錄於 C checkpoint。

沒有操作私人筆記庫、實體 Windows 桌面、原生選檔／拖曳／剪貼簿、實體 IME 或音效輸出，也沒有真實遠端圖片下載、GPU／ComfyUI 或公開發布驗收。接近 8 MiB 的完整效能、三主題與高 DPI、完整語法對照及使用者接受留在整合驗收；不能把本批正常樣本通過當成整個 1-1 完成。
