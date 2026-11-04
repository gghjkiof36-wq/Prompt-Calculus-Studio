# Prompt Calculus Studio（PCS）

在本機整理筆記、Prompt、對話與專案資料。用自己的模型聊天、讀取文件與 Skill，將文字和素材帶進 Canvas，再交給 ComfyUI 生成。

[English](README.en.md) · [開始使用](docs/guide/workspace-0810.md) · [完整文件](docs/README.md) · [Pre-alpha2 更新](docs/releases/0.8.10/pre-alpha.2.md)

## 能做什麼

- **編寫與閱讀筆記**：Markdown 原文與閱讀可並排，支援公式、表格、圖片、大綱、搜尋、連結與版本紀錄；也可用本機模型改寫選取內容或起稿，審閱後保留。
- **保存與整理對話**：Chrome 擴充保存選定 ChatGPT 訊息；匯入 ChatGPT ZIP／JSON、Codex JSONL 或 PCS 對話，保留來源與可取得附件，逐份輸出 Markdown。
- **管理本機模型與聊天**：登記 GGUF，設定上下文、思考與 GPU；`.pcs-chat` 保存附件、訊息版本、分支及各次用時。
- **按專案使用 Skill 與 Agent**：在工作資源加入文件與技能，為專案設定規則及角色；聊天可搜尋、讀取已設定來源，查看實際讀取紀錄。也可從 Windows 本機命令委派工作並查回草稿。
- **重用素材與安排生成**：保存 Prompt 變體、組合與流程，加入 Canvas 串接 CLIP 和 Stage，使用自己的 ComfyUI、模型與工作流生成圖片。

## 這版更新

Pre-alpha2 加入工作資源、Skill、專案與 Agent 管理，以及本機任務委派。改善對話轉 Markdown、聊天字體與底部操作列，減少長篇串流及反覆開關 Prompt／Canvas 的等待。[更新說明](docs/releases/0.8.10/pre-alpha.2.md) · [工作資源操作](docs/guide/work-resources.md)。

## 下載與啟動

下載 **[0.8.10 Pre-alpha2](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.10-pre-alpha.2)**（預覽版）。

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.10-Pre-alpha2-source.zip` | 來源主程式、教材、素材、模板與同版 ComfyUI 配套 |
| `PCS-v0.8.10-Pre-alpha2-Chrome-Connector.zip` | Chrome 0.2.7 擴充與 Windows 連線工具 |
| `PCS-v0.8.10-Pre-alpha2-ComfyUI.zip` | 通用 ComfyUI 配套 |
| `PCS-v0.8.10-Pre-alpha2-ComfyUI-legacy-0.39.2.zip`、`PCS-v0.8.10-Pre-alpha2-ComfyUI-modern-0.40.0.zip` | 相容設定檔，與通用版擇一 |
| `SHA256SUMS.txt` | 下載完整性校驗 |

主程式提供來源版。使用 Windows 64 位元 Python 3.12，完整解壓後，在 `run.py` 旁開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

之後雙擊 **`Start.vbs`**。首次安裝需網路下載依賴；啟動記錄保存在 `logs`。命令列可用 `.\.venv\Scripts\python.exe run.py --v0.8.10-pre-alpha.2`。

本機 AI 需自行準備 GGUF 與支援的 `llama-server`，依[模型設定](docs/guide/local-ai.md)操作。Chrome 保存依[連線步驟](docs/guide/chrome-connector.md)設定配套；圖片生成依[ComfyUI 指南](docs/guide/comfyui-compatibility.md)接上後端與工作流。

## 第一次使用與更新

首次從「歡迎使用.md」練習筆記、素材與 Canvas。文件預設在 `files`，設定與內部紀錄在 `data`；加入其他資料夾後會編輯原位置。更新前保留舊程式與資料，先用副本試用，新舊版本請勿同時寫入相同資料。[啟動與沿用資料](docs/guide/workspace-0810.md)。

## 使用前留意

- Agent 長篇分析與引用可能有誤，請核對來源；Skill 目前用於讀取與參考。
- 本機 AI 一次處理一件工作。外部委派目前使用 Windows 本機命令；ChatGPT／Claude 正式任務串接仍在開發。
- Chrome 只保存網頁已載入的訊息；互動圖表與附件依可取得內容保留。
- 對話、來源紀錄、附件與備份可能含私人內容，分享前請[檢查資料](docs/guide/data-backup-privacy.md)。

[本版紀錄](docs/releases/0.8.10/validation/pre-alpha.2.md) · [歷史發布](docs/releases/README.md) · [Issues](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/issues) · [貢獻](CONTRIBUTING.md) · [安全回報](SECURITY.md)

## 授權

[AGPL-3.0-only](LICENSE)
