# 開始使用 0.8.10 Pre-alpha1

[更新內容](../releases/0.8.10/pre-alpha.1.md) · [本機 AI](local-ai.md) · [本版限制](../releases/0.8.10/validation/pre-alpha.1.md)

## 第一次啟動來源版

從 [Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.10-pre-alpha.1) 下載 `PCS-v0.8.10-Pre-alpha1-source.zip`，完整解壓到可寫入的新資料夾。使用 Windows 64 位元 Python 3.12，在 `run.py` 旁開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

之後雙擊 **`Start.vbs`**；它使用同資料夾 `.venv` 中的 Python 啟動程式，不需要保留 CMD。`Start.cmd` 會轉交相同入口後退出，可能短暫閃窗。啟動失敗時會提示記錄位置，每次啟動的記錄分開保存在 `logs`。

需要命令列診斷或指定資料位置時：

```powershell
.\.venv\Scripts\python.exe run.py --v0.8.10-pre-alpha.1
```

## 文件與舊資料

預設 `files` 保存文件、`data` 保存設定及內部資料。首次開啟歡迎頁與教材；新增筆記、素材、Canvas 或本機對話後各自保存。加入其他本機資料夾會直接編輯原位置。

更新前保留舊程式與 `files`／`data`，先使用副本，不讓新舊程式同時寫入相同資料。要指定既有位置，可在下列命令換成自己的絕對路徑；來源包不自動搬移或清理原資料：

```powershell
.\.venv\Scripts\python.exe run.py --v0.8.10-pre-alpha.1 --root "D:/PCS-data/files" --data "D:/PCS-data/data"
```

## 開始使用功能

- 本機模型與文字協作：自行提供 GGUF 及支援的 llama-server，依[本機 AI 指南](local-ai.md)設定；不需要 LM Studio。
- Markdown 與文件：原文／編寫／閱讀、大綱、搜尋、連結、貼上及匯入，見[筆記操作](workspace-089.md)與[貼上／匯入](markdown-import.md)。
- Chrome 對話保存：本版沿用 Chrome 0.2.7。將配套中的 `PCS-Chrome-Connector` 改名為 `connector-package`，放到 `run.py` 旁，再由「探索 → 下載紀錄 → 加入 Chrome」設定；見[連線步驟](chrome-connector.md)。
- ComfyUI：安裝同版通用／legacy／modern 其中一份配套，重新啟動後端與刷新網頁。圖片生成仍需自己的模型與工作流，見[相容指南](comfyui-compatibility.md)。

本次不提供完整 PCS EXE、GGUF 或推理引擎下載；Chrome 配套只有獨立連線工具，不是 PCS 主程式。
