# Prompt Calculus Studio（PCS）

在本機整理 Markdown 筆記、Prompt 素材與 Canvas 工作流程。把 ChatGPT 對話保存成可編輯文件，整理文字、圖片與參數，再交給自己的 ComfyUI 生成。

[English](README.en.md) · [開始使用](docs/guide/workspace-089.md) · [完整文件](docs/README.md) · [Beta3 更新說明](docs/releases/0.8.9/beta.3.md)

## 能做什麼

- **保存網頁對話**：用 Chrome 擴充選取 ChatGPT 單則或多則訊息，儲存到 PCS，或下載 JSON／Markdown；從下載紀錄找回文件。
- **編寫與閱讀筆記**：Markdown 原文與閱讀可左右並排，支援公式、表格、圖片、大綱、搜尋、連結與版本紀錄。Ctrl+V 保留來源提供的格式，Ctrl+Shift+V 貼純文字。
- **保留來源與附件**：匯入本機 ChatGPT ZIP／JSON，保留所選對話、分支與可辨識附件；整理後可查回原問答，並匯出資料夾或 ZIP。
- **重用 Prompt 素材**：文字模塊保存變體，組合安排文字與權重，流程設定順序及重複；加入 Canvas 使用獨立副本。
- **安排生成與接續任務**：綁定 CLIP 欄位，設定 Stage 工作流與參數；多份 Canvas 依序執行，任務紀錄及取值進度保存在本機。

筆記、素材整理與複製 Prompt 可單獨使用。生成圖片需要自己的 ComfyUI、模型與工作流，並保持 PCS、後端及工作流網頁開啟。

## 這版更新

Beta3 加入 Chrome 對話保存、引用卡片及文件可攜匯出，改善公式、表格、缺檔草稿恢復與同名另存。附上 59 份可編輯素材、8 份筆記模板，以及公式與 Markdown 參考；設定中可選擇櫻花、下雨或雪花動畫。[完整更新說明](docs/releases/0.8.9/beta.3.md)。

## 下載與啟動

下載 **[0.8.9 Beta3](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.9-beta.3)**（預覽版）。

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.9-Beta3-source.zip` | 來源主程式、歡迎頁、教材、素材、模板與同版 ComfyUI 配套 |
| `PCS-v0.8.9-Beta3-Chrome-Connector.zip` | Chrome 擴充與 Windows 連線工具，供「儲存到 PCS」使用 |
| `PCS-v0.8.9-Beta3-ComfyUI.zip` | 通用 ComfyUI 配套，供手動安裝 |
| `PCS-v0.8.9-Beta3-ComfyUI-legacy-0.39.2.zip`、`PCS-v0.8.9-Beta3-ComfyUI-modern-0.40.0.zip` | 相容設定檔，與通用版擇一；見[相容指南](docs/guide/comfyui-compatibility.md) |
| `SHA256SUMS.txt` | 下載完整性校驗 |

主程式提供來源版，**沒有 PCS Windows EXE 公開下載**。安裝 Windows 64 位元 Python 3.12，完整解壓來源包後，在 `run.py` 旁開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.9-beta.3
```

首次需網路下載依賴，之後雙擊 `Start.cmd`。Chrome 對話保存另依[連線步驟](docs/guide/chrome-connector.md)安裝配套並載入擴充。要生成圖片，安裝一份同版 ComfyUI 配套，再重新啟動後端並刷新工作流網頁。

## 第一次使用與更新

首次開啟「歡迎使用.md」，可從教材練習筆記、素材、組合與流程，再把副本加入 Canvas。關閉歡迎頁後，重啟會接續已保存的分頁。教材可直接編輯，想保留初始內容時先複製。

新文件預設在 `files`，設定、恢復副本與任務紀錄在 `data`。加入其他本機資料夾後，編輯會保存到原位置。更新前保留舊程式與 `files`／`data`，先用副本試用，新舊版本不要同時寫入相同資料。[啟動與沿用資料](docs/guide/workspace-089.md) · [保存與匯出](docs/guide/markdown-retention.md)。

## 使用前留意

- Chrome 只擷取網頁已載入的訊息；「全選」不包含網站尚未載入的歷史。
- 複雜圖表與小工具可能只保留靜態圖像、文字或來源連結；已在舊資料中遺失的公式字元不會自動補回。
- 不支援所有 Obsidian 外掛；本版沒有自動執行 LLM，ChatGPT 整理採人工複製與貼回。
- 原始紀錄、附件、PNG、恢復副本與備份可能含私人內容，分享前請[檢查資料](docs/guide/data-backup-privacy.md)。

[本版限制](docs/releases/0.8.9/validation/beta.3.md) · [歷史發布](docs/releases/0.8.9/README.md) · [Issues](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/issues) · [貢獻指南](CONTRIBUTING.md) · [安全回報](SECURITY.md)

## 授權

[AGPL-3.0-only](LICENSE)
