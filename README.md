# Prompt Calculus Studio（PCS）

**化繁為簡，從混亂中找出秩序。**

把常用提示詞整理成模組，選取素材、調整順序與權重，再送入 ComfyUI 工作流。PCS（原名 Prompt Studio）是以本機資料為主的 Windows 桌面工具，提供清單、Canvas 與 ComfyUI 擴充，方便保存角色、風格與場景組合。

[English](README.en.md) · [開始使用](docs/GETTING_STARTED.md) · [操作指南](docs/USER_GUIDE.md) · [ComfyUI 整合](COMFYUI_GUIDE.md) · [回報問題](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/issues)

## 能做什麼

- **組合 Prompt**：用模組、順序、權重與排除 Tag 整理文字，保留臨時片段及手動稿。
- **整理 Canvas**：在畫布安排模組、Prompt、CLIP 與圖片接線，保存工作區並使用復原／重做。
- **連接 ComfyUI**：明確選擇由 PCS 提供文字或保留網頁手動文字，提交目前開啟的單一工作流。
- **查看圖片**：整批排列生成結果、點圖放大；同一工作流的圖片輸入可選定一張送入 LoadImage。
- **管理素材與生成脈絡**：整理模型、CivitAI 資產及媒體庫，收藏圖片並讀取可用的 PNG 資料與模組快照。

圖片生成由你自己的 ComfyUI 執行，硬體需求取決於工作流與模型。PCS 的提示詞編輯不需要載入生成模型，也不保證生成品質或空間控制精度。

## 版本與下載

本文件適用 **0.82 Alpha 1 Repair 5**。目前仍為預覽版，尚未發布正式 1.0。

| 版本 | 取得方式與適用範圍 |
|---|---|
| 0.82 Alpha 1 Repair 5 | [本次 Release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.82-alpha.1-repair.5)：已公開的 Alpha 預覽版，提供來源包、配套 ComfyUI 擴充與 SHA256SUMS；以下指南適用此版。 |
| 0.81 Alpha 2 UI Repair 1 | [既有預覽版](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.81-alpha.2-ui-repair.1)：保留來源與擴充；啟動依該頁說明，勿混用新版擴充。 |
| main | 開發分支；Code → Download ZIP 取得當時的來源，固定版本請選 Release。 |

本次附件為 `PCS-v0.82-Alpha1-Repair5-source.zip`、`PCS-v0.82-Alpha1-Repair5-ComfyUI.zip` 與 `SHA256SUMS.txt`，**不提供 Windows EXE**。原始碼不含 Python、Qt、模型或私人資料。程式標題仍保留「0927-2 批次預覽候選」，對應本次固定產品來源；來源對照與驗收界線見 [QA 摘要](docs/validation/082_REPAIR5.md)。

### 安裝與第一次使用

準備 Windows 64 位元、Python 3.12，解壓本次來源包，在包含 `run.py` 的資料夾開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v082-alpha
```

首次安裝從 PyPI 取得依賴。以後只需最後一行，或依 [開始使用](docs/GETTING_STARTED.md) 建立雙擊捷徑。

1. 第一次開啟選清單或 Canvas；先選一個模組與素材，查看組合後的 Prompt。
2. 調整順序與權重，或保存自己的素材；先複製文字確認結果。
3. 要生成時，安裝同版擴充、開啟 ComfyUI 工作流，依 [ComfyUI 指南](COMFYUI_GUIDE.md) 綁定 CLIP。
4. 選「使用 PCS 提示詞」或「使用 ComfyUI 手動文字」，確認目前工作流，再執行一次。

### 本版限制

ComfyUI **網頁必須保持開啟**，每次 PCS 執行提交**單一工作流一次**；ComfyUI 自身的 `batch_size` 可保留大於 1。跨工作流、多輪、無網頁背景提交、純圖片而無有效 CLIP 綁定，以及將多張圖依序送往下游，尚未完成。整批預覽不代表整批下游執行；圖片輸入目前要明確選定一張。

未綁定的尺寸、採樣與其他參數沿用網頁目前值。官方 ComfyUI Desktop、多 DPI、新電腦安裝及受限 CivitAI 下載不能由本次人工校驗一概視為通過。詳見 [驗收範圍](docs/validation/082_REPAIR5.md) 與 [Roadmap](ROADMAP.md)。

## 介面

本次尚無已確認可公開的 Repair 5 截圖，先提供操作文字。既有 [清單介面截圖](docs/images/prompt-workspace.png) 來自早期公開 main 與範例資料，不代表本版 Canvas。

## 文件與支援

| 想做的事 | 文件 |
|---|---|
| 安裝、啟動及建立捷徑 | [開始使用](docs/GETTING_STARTED.md) |
| 模組、工作區與圖片操作 | [操作指南](docs/USER_GUIDE.md) |
| 綁定工作流與生成 | [ComfyUI 整合](COMFYUI_GUIDE.md) |
| 資料位置、備份與聯網 | [資料與隱私](docs/DATA_AND_PRIVACY.md) |
| 排除啟動或連線問題 | [疑難排解](docs/TROUBLESHOOTING.md) |
| 版本、計畫與實際維護 | [更新紀錄](CHANGELOG.md)、[Roadmap](ROADMAP.md)、[維護證據](docs/MAINTENANCE_EVIDENCE.md) |
| 參與開發及安全回報 | [貢獻指南](CONTRIBUTING.md)、[安全問題](SECURITY.md) |

## 授權

本專案採用 [GNU AGPL v3.0 only](LICENSE)，識別碼為 `AGPL-3.0-only`。依授權條款可使用、修改及散布，也允許商業使用。另行提供商業授權的方案仍在規劃中，目前沒有可直接套用的商業授權合約。詳見 [授權說明](docs/LICENSING.md)。
