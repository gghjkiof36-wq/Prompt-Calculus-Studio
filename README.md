# Prompt Calculus Studio（PCS）

把常用提示詞整理成模組，選取素材、調整順序與權重，再送入 ComfyUI 工作流。PCS 是 Windows 桌面工具，提供清單與 Canvas 兩種編輯方式，角色、風格、場景與工作區都保存在本機。

[English](README.en.md) · [開始使用](docs/GETTING_STARTED.md) · [操作指南](docs/USER_GUIDE.md) · [ComfyUI 整合](COMFYUI_GUIDE.md)

## 能做什麼

- **組合提示詞**：保存常用文字，調整順序、權重與排除 Tag，隨時加入臨時片段或手動修改。
- **安排生成流程**：在 Canvas 用「執行階段（Stage）」選擇工作流，將前一步的圖片送入下一步，設定整串流程重複的次數。
- **預排多組內容**：保存多組提示詞、圖片或生成步驟，依序執行；等待項目可以排序、修改與移除。
- **查看與重用圖片**：查看整批結果、放大預覽、收藏圖片，或將選定圖片交給另一個工作流。
- **整理素材**：管理模型與 CivitAI 資產，讀取圖片中的提示詞及 PCS 畫布紀錄。

生成圖片需要你自己的 ComfyUI、模型與工作流；只編輯或複製提示詞時，可以單獨使用 PCS。

## 下載與啟動

目前版本：**[0.8.4 Alpha 1](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.8.4-alpha.1)**。

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.4-Alpha1-source.zip` | PCS 原始碼版主程式 |
| `PCS-v0.8.4-Alpha1-ComfyUI.zip` | 配套 ComfyUI 擴充 |
| `SHA256SUMS.txt` | 下載完整性校驗 |

安裝 Windows 64 位元版 Python 3.12，解壓來源包，在包含 `run.py` 的資料夾開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.4-alpha
```

首次安裝需要網路下載套件。之後只需最後一行，也可依[開始使用](docs/GETTING_STARTED.md)建立雙擊捷徑。

要生成圖片，請安裝同版擴充、重啟 ComfyUI 並重新整理網頁。在 Canvas 將文字或圖片輸入接到 Stage，選好工作流後按「執行」。生成時保持 PCS、ComfyUI 及工作流網頁開啟。

更新前備份資料，將新版放入新資料夾並使用資料副本。請搭配同版 PCS 與擴充；舊版下載與名稱見[版本對照](docs/VERSIONING.md)。

## 使用提示

- PCS 的文字會在按「執行」時送入 ComfyUI；只有文字輸入、沒有接 Stage 時，只會更新欄位。
- 「暫停」會保留等待內容，已開始的工作繼續產生結果；確認內容後按「繼續」恢復排程。
- 切換工作流後若卡在載入中，先查看 ComfyUI 是否已有任務、保存工作流，再重新整理網頁。處理方式見[疑難排解](docs/TROUBLESHOOTING.md)。

## 文件與支援

| 想做的事 | 文件 |
|---|---|
| 安裝、啟動與建立捷徑 | [開始使用](docs/GETTING_STARTED.md) |
| 模組、工作區與圖片操作 | [操作指南](docs/USER_GUIDE.md) |
| 綁定工作流與生成 | [ComfyUI 整合](COMFYUI_GUIDE.md) |
| 資料位置、備份與聯網 | [資料與隱私](docs/DATA_AND_PRIVACY.md) |
| 查看版本更新 | [發布說明](docs/RELEASE_NOTES_084.md)、[更新紀錄](CHANGELOG.md) |
| 回報問題或參與開發 | [Issues](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/issues)、[貢獻指南](CONTRIBUTING.md)、[安全回報](SECURITY.md) |

## 授權

[AGPL-3.0-only](LICENSE)
