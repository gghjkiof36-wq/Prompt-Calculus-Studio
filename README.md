# Prompt Calculus Studio（PCS）

**化繁為簡，從混亂中找出秩序。**

把常用提示詞整理成模組，選取素材、調整順序與權重，再送入 ComfyUI 工作流。PCS（原名 Prompt Studio）是以本機資料為主的 Windows 桌面工具，提供清單、Canvas 與 ComfyUI 擴充，方便保存角色、風格與場景組合。

[English](README.en.md) · [開始使用](docs/GETTING_STARTED.md) · [操作指南](docs/USER_GUIDE.md) · [ComfyUI 整合](COMFYUI_GUIDE.md) · [回報問題](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/issues)

## 能做什麼

- **組合 Prompt**：用模組、順序、權重與排除 Tag 整理文字，保留臨時片段及手動稿。
- **整理 Canvas**：在畫布安排模組、Prompt、CLIP 與圖片接線，保存工作區並使用復原／重做。
- **連接 ComfyUI**：將 CLIP 接入文字套用到明確綁定欄位，透過 Stage 生成，按接線串接工作流、傳遞本輪圖片。
- **查看圖片**：整批排列生成結果、點圖放大；圖片來源可供應單張或集合，接入 LoadImage，並可用預排程保存後續輸入。
- **管理素材與生成脈絡**：整理模型、CivitAI 資產及媒體庫，收藏圖片並讀取可用的 PNG 資料與模組快照。

圖片生成由你自己的 ComfyUI 執行，硬體需求取決於工作流與模型。PCS 的提示詞編輯不需要載入生成模型，也不保證生成品質或空間控制精度。

## 版本與下載

本文件適用 **0.831 Alpha 1**。目前仍為預覽版，尚未發布正式 1.0。

| 版本 | 取得方式與適用範圍 |
|---|---|
| 0.831 Alpha 1 | [本次 Release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.831-alpha.1)：本次 Alpha 預覽版，提供來源包、配套 ComfyUI 擴充與 SHA256SUMS；以下指南適用此版。 |
| 0.83 Alpha 1 | [舊版 Release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.83-alpha.1)：舊操作依該版指南。 |
| 0.82 Alpha 1 Repair 5 | [既有預覽版](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.82-alpha.1-repair.5)：保留原附件與該版指南。 |
| 0.81 Alpha 2 UI Repair 1 | [既有預覽版](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.81-alpha.2-ui-repair.1)：保留來源與擴充；啟動依該頁說明，勿混用新版擴充。 |
| main | 開發分支；Code → Download ZIP 取得當時的來源，固定版本請選 Release。 |

本次附件為 `PCS-v0.831-Alpha1-source.zip`、`PCS-v0.831-Alpha1-ComfyUI.zip` 與 `SHA256SUMS.txt`。**本次暫不提供 Windows 執行檔下載**，待執行環境的散布材料補齊後另行處理；請使用下方來源啟動方式。原始碼不含 Python、Qt、模型或私人資料。程式標題仍保留「v0.831 種子與流程恢復修復候選（0930）」，對應本次固定產品來源；來源對照與驗收界線見 [QA 摘要](docs/validation/0831_STAGE.md)。

### 安裝與第一次使用

準備 Windows 64 位元、Python 3.12，解壓本次來源包，在包含 `run.py` 的資料夾開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v083-alpha
```

來源版首次安裝從 PyPI 取得依賴。以後只需最後一行，或依 [開始使用](docs/GETTING_STARTED.md) 建立雙擊捷徑。

1. 第一次開啟選清單或 Canvas；先選一個模組與素材，查看組合後的 Prompt。
2. 調整順序與權重，或保存自己的素材；先複製文字確認結果。
3. 要生成時，安裝同版擴充、開啟 ComfyUI 工作流，依 [ComfyUI 指南](COMFYUI_GUIDE.md) 綁定 CLIP。
4. 將 CLIP／圖片輸入的紫色控制輸出接入 Stage，確認 Stage 工作流後執行；沒有有效 Stage 接入時只套用輸入。

### 執行方式與限制

PCS、ComfyUI 服務與原生網頁都須保持開啟。**Stage 是生成入口**：CLIP 或 ComfyUI 圖片輸入接入 Stage，按執行才套用並生成；修改 PCS 文字不即時覆蓋原生欄位。沒有有效 Stage 接入時只套用綁定輸入。

單一 Stage 可在生成中再次點擊；多 Stage 按控制接線與結果依賴依序執行，次數代表完整流程輪數。本輪支持線性 A→B→C，以及使用不同 Stage 實例的 A→B→A；圖片來源可把本輪 A 的結果供給 B，不以舊圖替代。

資料預排程保存指定文字／圖片，Stage 預排程保存一個或一組階段並逐輸入選保存或即時取值。圖片清單最多十個未結束項目、完成後補入。底欄保留次數、執行、取消、活動任務數，取消只操作確認歸屬的 PCS 任務。

原生載入一直不返回時仍需重新整理 ComfyUI 網頁；第三方入口無法安全隔離時也會提示刷新。未知提交不自動重送。關頁執行、AI 轉譯、Manager整合、條件分支及循環未納入。使用者人工驗收與 EXE 本機離屏自驗分列，見 [QA 與限制](docs/validation/0831_STAGE.md)。

## 介面

本次尚無已確認可公開的 0.831 截圖，先提供操作文字。既有 [清單介面截圖](docs/images/prompt-workspace.png) 來自早期公開 main 與範例資料，不代表本版 Canvas。

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
