# Prompt Calculus Studio（PCS）

在本機整理 Markdown 筆記、Prompt 素材與 Canvas 工作流程。貼入帶格式的文字，或把對話封存變成可編輯筆記；再安排文字、圖片與參數，交給自己的 ComfyUI 生成。

**化繁為簡，從混亂中找出秩序。**

[English](README.en.md) · [開始使用](docs/guide/workspace-089.md) · [完整文件](docs/README.md) · [Beta 更新說明](docs/releases/0.8.9/beta.md)

## 能做什麼

- **編寫與整理筆記**：Markdown 原文與閱讀可左右並排，用大綱、搜尋、標籤、連結與版本紀錄整理內容。
- **保留貼上格式**：Ctrl+V 使用剪貼簿提供的 Markdown 或 HTML，保留常見標題、清單、表格、程式碼與公式來源；Ctrl+Shift+V 貼純文字。
- **匯入選中的對話**：從本機 ChatGPT ZIP／JSON 選對話，保存全部分支、所選原始紀錄及能辨識的附件，來源封存保持原樣。
- **重用 Prompt 素材**：文字模塊保存變體，組合安排文字與權重，流程設定順序及重複；加入 Canvas 使用獨立副本。
- **安排生成與接續任務**：CLIP 綁定欄位，Stage 選擇工作流與參數；多份 Canvas 依序執行，任務紀錄及取值進度保存在本機。

筆記、素材整理與複製 Prompt 可單獨使用。生成需自己的 ComfyUI、模型與工作流，並保持 PCS、後端與工作流網頁開啟。

## 這版更新

新增保格式貼上與選段對話匯入，整理閱讀、原文欄寬、側欄拖頁及模型詳情。關閉文件時自存，保存失敗會保留可用的本機恢復副本；素材只查看不變成未儲存。分頁 Canvas 的綁定與多文件任務恢復也一併更新。[完整操作與限制](docs/releases/0.8.9/beta.md)。

## 下載與啟動

適用：**[0.8.9 Beta](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.9-beta)**（預覽版）。

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.9-Beta-source.zip` | 來源主程式，內含歡迎頁、教材、同版配套與安裝腳本 |
| `PCS-v0.8.9-Beta-ComfyUI.zip` | 通用配套，供手動安裝 |
| `PCS-v0.8.9-Beta-ComfyUI-legacy-0.39.2.zip`、`PCS-v0.8.9-Beta-ComfyUI-modern-0.40.0.zip` | 相容設定檔，與通用版擇一；見[相容指南](docs/guide/comfyui-compatibility.md) |
| `SHA256SUMS.txt` | 下載完整性校驗 |

本次提供來源版，**沒有 Windows EXE 公開下載**。安裝 Windows 64 位元 Python 3.12，完整解壓後，在 `run.py` 旁開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.9-beta
```

首次需網路下載依賴，之後雙擊 `Start.cmd`。要生成圖片，安裝同版 PCS 擴充，再由你啟動 ComfyUI 後端並刷新工作流網頁。舊 preparation.7 配套只支援格式 1–6，格式 7 需更新；安裝後仍須核對實際載入版。[完整步驟](docs/guide/workspace-089.md)。

## 第一次使用

首次開啟「歡迎使用.md」，從「入口」資料夾的教材依序練習海岸筆記、Prompt 素材、組合與流程，再把副本加入 Canvas。新 Canvas 有六個已接線模組；關閉歡迎頁後，重啟會接續已保存的分頁，不強制插回。教材可以編輯，保留初始內容時先複製。

新文件在程式旁的 `files`，設定、恢復副本與任務紀錄在 `data`。加入其他本機資料夾後會保存到原位置；更新先保留原件並用副本。貼上與對話匯入見[操作指南](docs/guide/markdown-import.md)。

## 使用前留意

- 舊版匯入結果若夾帶整份對話來源，不會自動清除。請重新匯入另存一份，保留手改筆記，分享前檢查附件。
- 關閉同檔另一視圖後，第二次大型 HTML 貼上可能停在整理；原文與磁碟不變，仍可輸入、保存及關閉。關閉該頁再重開可恢復貼上。
- 大型文件仍可能停頓；CivitAI 偶發退出的原因尚未確認，本版未修。舊資料庫、真實帳號封存及原生／GPU 的驗證範圍見[本版摘要](docs/releases/0.8.9/validation/beta.md)。
- 生成 PNG、匯入原始紀錄、附件、恢復副本與完整備份可能含私人內容，分享前[檢查資料](docs/guide/data-backup-privacy.md)。

[歷史發布與畫面](docs/releases/0.8.9/README.md) · [Issues](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/issues) · [貢獻指南](CONTRIBUTING.md) · [安全回報](SECURITY.md)

## 授權

[AGPL-3.0-only](LICENSE)
