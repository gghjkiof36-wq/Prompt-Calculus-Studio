# Prompt Calculus Studio 0.8.10 Pre-alpha1

在同一個本機工作區裡管理模型、保存對話，並讓本機模型協助改寫或續寫 Markdown。筆記、Prompt 素材、Canvas、Chrome 對話保存與 ComfyUI 工作流都可繼續使用。

[開始使用](../../guide/workspace-0810.md) · [本機模型與文字協作](../../guide/local-ai.md)

## 本機模型與對話

從「探索 → 本地模型」加入已下載的 GGUF，或選擇本機模型資料夾。可搜尋、排序及選用模型，為每個模型保存上下文、生成與思考設定；引擎位置另在「引擎設定」指定。選取模型不會立即載入，送出時才準備模型，閒置後依設定釋放資源。

本機對話保存成 `.pcs-chat` 文件，與筆記一樣放在文件樹和分頁中。可保存訊息與附件、重試回覆、查看訊息版本與分支，並匯出 Markdown。思考與正文分開顯示，保留各次回覆的設定與用時；停止生成會保留已收到的內容。

模型與 llama-server 需自行準備。引擎使用 [llama.cpp b11900](https://github.com/ggml-org/llama.cpp/releases/tag/b11900) 的 Windows 套件，完整解壓並保留同目錄依賴檔。本版不提供引擎自動安裝、更新或模型網路搜尋下載。

## 在 Markdown 中改寫與起稿

在編寫／原文模式選取文字，於浮動輸入框輸入要求。模型會以完整文件作上下文，只產生選取部分的候選改寫；沒有選取時，可從空白文件起稿或在文末續寫。

候選直接在文件頁比較：原文以淡灰刪除線、新文以藍色呈現，框上方可「保留」「重試」或「刪除」。**先審閱，再按「保留」**；生成及預覽不會自行覆寫原稿，保留後可 Undo。原稿變動或請求失效時，舊候選不會直接寫回。每份筆記可保留自己的模型選擇。

## 介面與日常操作

- 改善準備中／生成中的狀態文字、串流刷新與對話計時。
- 對齊一般文字在編寫及閱讀模式的空白行與段距；表格、公式等仍採各自的語意排版。
- 側欄可顯示 FPS 與 Lag。FPS 表示實際介面重繪批次，靜止時可以是 0。
- 修正素材、Canvas、配色與圖片匯入的日常操作，保留既有筆記、Chrome 對話保存、教材及可攜匯出。
- 來源版新增 `Start.vbs`，啟動後不需要留著 CMD。原 `Start.cmd` 也會轉交無命令視窗入口後退出。

## 下載與啟動

從[本次 Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.10-pre-alpha.1)下載：

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.10-Pre-alpha1-source.zip` | 來源主程式、教材、素材、模板及同版 ComfyUI 配套 |
| `PCS-v0.8.10-Pre-alpha1-Chrome-Connector.zip` | 沿用 Chrome 0.2.7 擴充與獨立 Windows 連線工具 |
| `PCS-v0.8.10-Pre-alpha1-ComfyUI.zip` | 通用 ComfyUI 配套 |
| `PCS-v0.8.10-Pre-alpha1-ComfyUI-legacy-0.39.2.zip`、`PCS-v0.8.10-Pre-alpha1-ComfyUI-modern-0.40.0.zip` | 相容設定檔，與通用版擇一 |
| `SHA256SUMS.txt` | 下載完整性校驗 |

本次提供來源版，沒有完整 PCS EXE，也不包含模型或推理引擎。安裝 Windows 64 位元 Python 3.12，完整解壓到可寫入的資料夾，在 `run.py` 旁開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

之後雙擊 `Start.vbs`。首次需網路安裝依賴；本機模型與引擎設定見[本機 AI 指南](../../guide/local-ai.md)。更新前保留舊程式及 `files`／`data`，先用副本試用，新舊版本不要同時寫入相同資料。

## 使用前留意

- 模型可能遺漏、改變原意或產生錯誤內容，請先檢查候選再保留；思考上限及附件能力依模型和引擎而異。
- 全文超過模型上下文時，需縮短文件或調整上下文設定。圖片附件目前供保存與查看，文字模型不支援圖片辨識。
- 選取用的浮動輸入框會暫時覆蓋後文。複雜 Markdown 不保證與原文逐行等高。
- 這是 Pre-alpha 預覽版，模型、硬體、輸入法及螢幕縮放的適用範圍見[本版限制](validation/pre-alpha.1.md)。文件、對話、附件、日誌及備份可能含私人內容，分享前請檢查。

[Chrome 設定](../../guide/chrome-connector.md) · [資料與隱私](../../guide/data-backup-privacy.md) · [先前 Beta3](../0.8.9/beta.3.md)
