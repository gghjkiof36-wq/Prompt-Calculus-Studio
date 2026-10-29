# Prompt Calculus Studio（PCS）

在本機整理 Markdown 筆記、Prompt 素材與 Canvas 工作流程。使用本機模型對話、改寫或續寫筆記，也能保存 Chrome 對話，安排自己的 ComfyUI 生成。

[English](README.en.md) · [開始使用](docs/guide/workspace-0810.md) · [完整文件](docs/README.md) · [Pre-alpha1 更新說明](docs/releases/0.8.10/pre-alpha.1.md)

## 能做什麼

- **本機模型與對話**：加入既有 GGUF，設定自己的 llama-server，保存 `.pcs-chat` 對話、附件、訊息版本與分支。
- **Markdown 文字協作**：用全文作上下文改寫選取文字，或從空稿／文末起稿。在原頁比較候選，審閱後按「保留」才寫入，並可 Undo。
- **保存網頁對話**：用 Chrome 擴充選取 ChatGPT 單則或多則訊息，儲存到 PCS，或下載 JSON／Markdown；從下載紀錄找回文件。
- **編寫與閱讀筆記**：Markdown 原文與閱讀可左右並排，支援公式、表格、圖片、大綱、搜尋、連結與版本紀錄。Ctrl+V 保留來源提供的格式，Ctrl+Shift+V 貼純文字。
- **保留來源與附件**：匯入本機 ChatGPT ZIP／JSON，保留所選對話、分支與可辨識附件；整理後可查回原問答，並匯出資料夾或 ZIP。
- **重用 Prompt 素材**：文字模塊保存變體，組合安排文字與權重，流程設定順序及重複；加入 Canvas 使用獨立副本。
- **安排生成與接續任務**：綁定 CLIP 欄位，設定 Stage 工作流與參數；多份 Canvas 依序執行，任務紀錄及取值進度保存在本機。

筆記、素材整理與複製 Prompt 可單獨使用。生成圖片需要自己的 ComfyUI、模型與工作流，並保持 PCS、後端及工作流網頁開啟。

## 這版更新

Pre-alpha1 加入本機模型管理、對話與 Markdown 選取改寫／全文起稿，改善生成狀態、串流、一般文字讀寫段距與 FPS 顯示。來源版新增 `Start.vbs`，啟動後不必留著 CMD。[完整更新說明](docs/releases/0.8.10/pre-alpha.1.md)。

## 下載與啟動

下載 **[0.8.10 Pre-alpha1](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.10-pre-alpha.1)**（預覽版）。

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.10-Pre-alpha1-source.zip` | 來源主程式、歡迎頁、教材、素材、模板與同版 ComfyUI 配套 |
| `PCS-v0.8.10-Pre-alpha1-Chrome-Connector.zip` | Chrome 擴充與 Windows 連線工具，供「儲存到 PCS」使用 |
| `PCS-v0.8.10-Pre-alpha1-ComfyUI.zip` | 通用 ComfyUI 配套，供手動安裝 |
| `PCS-v0.8.10-Pre-alpha1-ComfyUI-legacy-0.39.2.zip`、`PCS-v0.8.10-Pre-alpha1-ComfyUI-modern-0.40.0.zip` | 相容設定檔，與通用版擇一；見[相容指南](docs/guide/comfyui-compatibility.md) |
| `SHA256SUMS.txt` | 下載完整性校驗 |

主程式提供來源版，**沒有 PCS Windows EXE 公開下載**。安裝 Windows 64 位元 Python 3.12，完整解壓來源包後，在 `run.py` 旁開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.10-pre-alpha.1
```

首次需網路下載依賴，之後雙擊 `Start.vbs`，不需要留著 CMD。Chrome 對話保存另依[連線步驟](docs/guide/chrome-connector.md)安裝配套並載入擴充。要生成圖片，安裝一份同版 ComfyUI 配套，再重新啟動後端並刷新工作流網頁。

本機 AI 另需自行準備 GGUF 與支援的 llama-server，從「探索 → 本地模型」設定；模型與引擎另行準備。見[模型與文字協作指南](docs/guide/local-ai.md)。

## 第一次使用與更新

首次開啟「歡迎使用.md」，可從教材練習筆記、素材、組合與流程，再把副本加入 Canvas。關閉歡迎頁後，重啟會接續已保存的分頁。教材可直接編輯，想保留初始內容時先複製。

新文件預設在 `files`，設定、恢復副本與任務紀錄在 `data`。加入其他本機資料夾後，編輯會保存到原位置。更新前保留舊程式與 `files`／`data`，先用副本試用，新舊版本不要同時寫入相同資料。[啟動與沿用資料](docs/guide/workspace-0810.md) · [保存與匯出](docs/guide/markdown-retention.md)。

## 使用前留意

- 這是 Pre-alpha 預覽版；先用資料副本試用。模型可能遺漏原意或生成錯誤，請先審閱候選再保留；全文超過上下文時需調整文件或設定。
- Chrome 只擷取網頁已載入的訊息；「全選」不包含網站尚未載入的歷史。
- 複雜圖表與小工具可能只保留靜態圖像、文字或來源連結；已在舊資料中遺失的公式字元不會自動補回。
- 不支援所有 Obsidian 外掛。ChatGPT 網頁的整理往返仍採人工複製與貼回；本機文字協作另使用自己的模型。
- 原始紀錄、附件、PNG、恢復副本與備份可能含私人內容，分享前請[檢查資料](docs/guide/data-backup-privacy.md)。

[本版限制](docs/releases/0.8.10/validation/pre-alpha.1.md) · [歷史發布](docs/releases/README.md) · [Issues](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/issues) · [貢獻指南](CONTRIBUTING.md) · [安全回報](SECURITY.md)

## 授權

[AGPL-3.0-only](LICENSE)
