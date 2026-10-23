# Prompt Calculus Studio 0.8.9 Beta

這版把外部文字與本機對話封存帶進 Markdown，並整理分頁、素材、模型與 Canvas 的日常操作。歡迎頁與教材繼續保留，可從筆記一路練習到 Prompt 與工作流。

## 主要變更

- **保格式貼上**：在編寫或原文按 Ctrl+V，優先使用 Markdown，其次 HTML、純文字。保留常見標題、清單、表格、程式碼與可取得來源的公式，整段可一次 Undo；Ctrl+Shift+V 貼純文字。
- **選段對話匯入**：在 Markdown 的「⋯ → 匯入對話…」拖入或選擇 ChatGPT ZIP／JSON，勾選對話、預覽分支並確認位置。保存全部分支、所選原始 JSON 與可辨識附件，來源封存不改動；同內容略過，更新另存。
- **閱讀與側欄**：整理原文／閱讀首幀、欄寬、表格與大綱，文件連結優先回到已開頁。側欄文件可拖至分頁或右側並排；浮動時切換內容、調寬仍保留浮動。
- **關閉與恢復**：關頁或退出先自存，保存失敗保留本機恢復資料後可離開。只查看素材不再變成未儲存；編輯視窗可點外側或按 Esc 關閉，未確認表單保留草稿。
- **模型與媒體**：媒體庫新增資料夾直接在側欄命名。模型先看預覽，點開詳細資料再編輯；觸發詞用圖示複製，預覽可拖入圖片，上方更換、下方移除。來源與檔案資訊整合。
- **Canvas 與任務接續**：修正分頁 Canvas 的 CLIP／圖片／Stage 綁定。多文件完整流程依序執行，保存任務紀錄與取值進度；重開先核對已送出工作，待續流程由任務紀錄恢復。

另修正大型貼上期間新開同檔視圖時，輸入錯序與拒存的問題；選段匯入也排除誤作附件的對話來源 JSON，避免複製未選對話。實際操作見[貼上與匯入](../../guide/markdown-import.md)及[工作區入門](../../guide/workspace-089.md)。

## 更新與啟動

從[本次 Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.9-beta)取得：

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.9-Beta-source.zip` | 來源主程式、歡迎頁、教材、配套與安裝腳本 |
| `PCS-v0.8.9-Beta-ComfyUI.zip` | 通用配套 |
| `PCS-v0.8.9-Beta-ComfyUI-legacy-0.39.2.zip` | 後端 0.39.2／前端 1.53.10 對照設定檔 |
| `PCS-v0.8.9-Beta-ComfyUI-modern-0.40.0.zip` | 後端 0.40.0／前端 1.55.17 對照設定檔 |
| `SHA256SUMS.txt` | 完整性校驗 |

本次沒有 Windows EXE 公開下載。來源版需 Windows 64 位元 Python 3.12；完整解壓後在 `run.py` 旁開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.9-beta
```

首次安裝依賴後雙擊 `Start.cmd`。更新前保留舊程式、配套與原資料，以獨立資料目錄及內容副本使用；搬移環境時一併保留 `files` 與 `data`，只複製文件不包含執行歷史。

關閉 ComfyUI 後端，安裝一份同版配套，再重新啟動並刷新網頁。舊 preparation.7 只接受格式 1–6，使用格式 7 前請更新配套並確認載入版本，詳見[相容指南](../../guide/comfyui-compatibility.md)。生成時保持 PCS、後端及工作流網頁開啟。

## 已知問題與處理

- **舊匯入輸出不會自動清理**：先前若把整份對話來源誤作附件，本版不刪舊資料。重新匯入另建不含該誤附來源檔的副本，保留手改筆記；分享前檢查原始紀錄與附件。
- **再次大型 HTML 貼上可能一直整理**：同檔兩視圖關閉一側後可能發生。原文與磁碟不變，仍可輸入、保存、關閉；關閉該頁再重開後可重新貼上。
- **大型文件仍可能停頓**：貼上、排版、大 JSON 與大量短段落仍有延遲。
- **CivitAI 偶發退出**：遇到時可保留錯誤紀錄供回報，先移除私人資訊。

舊資料庫、對話封存、輸入法、媒體及 ComfyUI 的相容限制，見[已知問題與限制](validation/beta.md)。生成 PNG、原始 JSON、恢復副本及完整備份可能有私人內容，見[資料與隱私](../../guide/data-backup-privacy.md)。

## 授權

[AGPL-3.0-only](../../../LICENSE)
