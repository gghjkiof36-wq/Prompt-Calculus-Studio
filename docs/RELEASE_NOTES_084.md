# Prompt Calculus Studio 0.8.4 Alpha 1

## 本次變更

本版讓你在畫布上安排生成步驟，把提示詞、圖片與多個 ComfyUI 工作流串在一起。

- **安排生成步驟**：用「執行階段（Stage）」選擇要執行的工作流，設定整串流程重複的次數。
- **串接工作流**：將前一步生成的圖片交給下一個工作流，依序完成多個步驟；可選擇傳送整批圖片或指定圖片。
- **預排多組內容**：先保存不同的提示詞、圖片或一組生成步驟，再依序執行；等待中的項目可以調整順序、修改或移除。
- **修正生成與恢復問題**：修正種子更新及切換工作流後的流程恢復問題，改善連續生成的操作。

此次更新修正了之前版本命名規則錯亂的問題。[查看版本對照](VERSIONING.md)。

## 下載與使用方式

| 下載項目 | 用途 |
|---|---|
| `PCS-v0.8.4-Alpha1-source.zip` | PCS 原始碼版主程式 |
| `PCS-v0.8.4-Alpha1-ComfyUI.zip` | 讓 PCS 連接 ComfyUI 的配套擴充 |
| `SHA256SUMS.txt` | 檢查下載檔案是否完整 |

這次提供 Python 原始碼版。請先安裝 **Windows 64 位元版 Python 3.12**，解壓來源包，在含有 `run.py` 的資料夾開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.4-alpha
```

首次安裝需要網路下載套件，以後只需執行最後一行。若想雙擊啟動，請[下載更新的 Start.cmd](https://raw.githubusercontent.com/gghjkiof36-wq/Prompt-Culculus-Studio/main/Start.cmd)，存到 `run.py` 所在資料夾取代原有檔案，再雙擊執行。

要生成圖片，請安裝同版 ComfyUI 擴充、重啟 ComfyUI 並重新整理網頁，再把畫布中的文字或圖片輸入接到 Stage。生成時保持 PCS、ComfyUI 及其工作流網頁開啟。詳細步驟見[開始使用](GETTING_STARTED.md)與[ComfyUI 指南](../COMFYUI_GUIDE.md)。

更新前備份資料，將新版解壓到新資料夾並使用資料副本。0.8.4 已保存的等待工作會保留並暫停，請確認內容後再繼續；0.8.3 的等待工作需重新安排。

## 重要已知問題

- **切換工作流後可能停在載入中**：先到 ComfyUI 查看是否已有生成任務，保存需要保留的工作流，再重新整理網頁；確認任務狀態後再操作，避免重複生成。
- **ComfyUI 已生成圖片，PCS 卻沒有顯示**：從底部活動任務數開啟該筆紀錄，使用「重取結果／重試失敗項」重新讀取圖片。其他連線問題見[疑難排解](TROUBLESHOOTING.md)。

## 授權

[AGPL-3.0-only](../LICENSE)
