# Prompt Calculus Studio（PCS）

在本機整理 Markdown 筆記、Prompt 素材與 Canvas 工作流程。貼入帶格式的文字，或把對話封存變成可編輯筆記；再安排文字、圖片與參數，交給自己的 ComfyUI 生成。

**化繁為簡，從混亂中找出秩序。**

[English](README.en.md) · [開始使用](docs/guide/workspace-089.md) · [完整文件](docs/README.md) · [Beta2 更新說明](docs/releases/0.8.9/beta.2.md)

## 能做什麼

- **編寫與整理筆記**：Markdown 原文與閱讀可左右並排，用大綱、搜尋、標籤、連結與版本紀錄整理內容。
- **保留貼上格式**：Ctrl+V 使用剪貼簿提供的 Markdown 或 HTML，保留常見標題、清單、表格、程式碼與公式來源；Ctrl+Shift+V 貼純文字。
- **匯入選中的對話**：從本機 ChatGPT ZIP／JSON 選對話，保存全部分支、所選原始紀錄及能辨識的附件，來源封存保持原樣。
- **重用 Prompt 素材**：文字模塊保存變體，組合安排文字與權重，流程設定順序及重複；加入 Canvas 使用獨立副本。
- **安排生成與接續任務**：CLIP 綁定欄位，Stage 選擇工作流與參數；多份 Canvas 依序執行，任務紀錄及取值進度保存在本機。

筆記、素材整理與複製 Prompt 可單獨使用。生成需自己的 ComfyUI、模型與工作流，並保持 PCS、後端與工作流網頁開啟。

## 這版更新

Beta2 修正恢復稿、關閉保護、匯入部分完成與備份取消，並改善大型 Markdown 閱讀、搜尋及末段捲動。變更筆記資料夾後，閱讀內容會依目前範圍更新；重複收圖與 Stage 來源處理減少同次操作的重複讀檔。[修復內容與內部整理](docs/releases/0.8.9/beta.2.md)。

## 下載與啟動

適用：**[0.8.9 Beta2](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.9-beta.2)**（預覽版）。

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.9-Beta2-source.zip` | 來源主程式，內含歡迎頁、教材、同版配套與安裝腳本 |
| `PCS-v0.8.9-Beta2-ComfyUI.zip` | 通用配套，供手動安裝 |
| `PCS-v0.8.9-Beta2-ComfyUI-legacy-0.39.2.zip`、`PCS-v0.8.9-Beta2-ComfyUI-modern-0.40.0.zip` | 相容設定檔，與通用版擇一；見[相容指南](docs/guide/comfyui-compatibility.md) |
| `SHA256SUMS.txt` | 下載完整性校驗 |

本次提供來源版，**沒有 Windows EXE 公開下載**。安裝 Windows 64 位元 Python 3.12，完整解壓後，在 `run.py` 旁開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.9-beta.2
```

首次需網路下載依賴，之後雙擊 `Start.cmd`。要生成圖片，安裝同版 PCS 擴充，再由你啟動 ComfyUI 後端並刷新工作流網頁。舊 preparation.7 配套只支援格式 1–6，格式 7 需更新；安裝後仍須核對實際載入版。[完整步驟](docs/guide/workspace-089.md)。

## 第一次使用

首次開啟「歡迎使用.md」，從「入口」資料夾的教材依序練習海岸筆記、Prompt 素材、組合與流程，再把副本加入 Canvas。新 Canvas 有六個已接線模組；關閉歡迎頁後，重啟會接續已保存的分頁，不強制插回。教材可以編輯，保留初始內容時先複製。

新文件在程式旁的 `files`，設定、恢復副本與任務紀錄在 `data`。加入其他本機資料夾後會保存到原位置；更新先保留原件並用副本。貼上與對話匯入見[操作指南](docs/guide/markdown-import.md)。

## 使用前留意

- 大型文件、貼上及大量 JSON 仍可能停頓；局部改善不代表所有文件都有相同比例的加速。
- 舊版匯入結果若夾帶整份對話來源，不會自動清理。重新匯入另存，保留手改筆記，分享前檢查附件。
- 原生輸入法、DPI、真實封存、舊資料庫及 ComfyUI／GPU 尚未完整驗證，另有異常退出情境待釐清；見[本版限制](docs/releases/0.8.9/validation/beta.2.md)。
- 生成 PNG、原始紀錄、附件、恢復副本及備份可能含私人內容，分享前[檢查資料](docs/guide/data-backup-privacy.md)。

[歷史發布與畫面](docs/releases/0.8.9/README.md) · [Issues](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/issues) · [貢獻指南](CONTRIBUTING.md) · [安全回報](SECURITY.md)

## 授權

[AGPL-3.0-only](LICENSE)
