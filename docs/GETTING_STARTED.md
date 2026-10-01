# 開始使用

[回首頁](../README.md) · 適用：0.8.5 Alpha 1

## 取得版本

從 [0.8.5 Release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.8.5-alpha.1) 取得 `PCS-v0.8.5-Alpha1-source.zip`；要連接 ComfyUI 時另取同版擴充 ZIP。來源 ZIP 解壓後根目錄直接包含 `run.py`。本次提供原始碼版，不提供 Windows EXE 下載；請先安裝 Python，步驟如下。

需要舊版時，依[版本對照](VERSIONING.md)取得對應版本與啟動說明；不要混用不同版本的主程式、擴充及唯一資料副本。

## 從原始碼安裝與啟動

準備 Windows 64 位元與 Python 3.12（含 Python Launcher）。在包含 `run.py` 和 `requirements.txt` 的資料夾開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.5-alpha
```

首次安裝需要連接 PyPI。之後只用最後一行，無須啟用虛擬環境或修改 PowerShell 執行原則。找不到 `py` 時，將第一行改用已安裝的 Python 3.12 執行檔完整路徑。

啟動後確認版本為 0.8.5，並使用同版 ComfyUI 擴充。

## 雙擊啟動

完成上方安裝後，雙擊 `Start.cmd` 即可啟動。它會使用同一資料夾的 `.venv`；如果啟動失敗，視窗會保留錯誤訊息供查看。

本次來源 ZIP 已包含配套的 `Start.cmd`，不必另取其他版本的啟動檔。

想從桌面開啟，可在 `Start.cmd` 按右鍵建立捷徑，再將捷徑移到桌面。

## 第一次使用

1. 初次開啟選清單或 Canvas；後續可從設定切換。
2. 選取模組與素材，查看組合後的 Prompt，調整順序和權重。
3. 保存自己的素材並複製文字；修改資料會保存在本機。
4. 要直接生成時，依 [ComfyUI 指南](../COMFYUI_GUIDE.md) 安裝同版擴充與綁定 CLIP。PCS、服務與網頁保持開啟；CLIP／圖片輸入接入Stage才生成；沒有有效Stage接入時只套用輸入。

清單與 Canvas 有各自操作及手動稿；CLIP只套用左側文字輸入，未綁定原生欄位保留，見 [操作指南](USER_GUIDE.md)。

## Stage、參數與任務紀錄

在 Canvas 加入 Stage／執行階段，將 CLIP 或圖片輸入的紫色輸出接入 Stage，確認工作流。單Stage可連按，多Stage依接線執行，次數代表完整流程輪數。雙擊 Stage 可選節點並保存 CFG、步數、尺寸或種子；「套用」不生成。每筆等待任務的參數可以獨立修改，詳見[Stage 參數指南](STAGE_PARAMETERS.md)。詳細圖片與預排程操作見[使用指南](USER_GUIDE.md)。

底欄為次數、執行、取消、活動任務數；點活動任務數查看紀錄與恢復入口。桌面與擴充須同版，更新後重啟 ComfyUI 並刷新網頁。不要同時讓新舊 PCS 連接同一服務。

更新後未完成流程會保留並暫停，請確認內容後再繼續。舊 Stage 或等待項沒有參數設定時，不會自動套入目前面板的數值；沒有指定的欄位仍在執行前讀取 ComfyUI。0.8.3 的等待工作需重新安排。PNG及undo/redo只還原編輯內容，不生成新任務。若無法確認是否已開始生成，先到 ComfyUI 查看佇列與歷史，再決定是否重新執行。

## 資料、更新與回退

原始碼版預設資料位於 `run.py` 旁的 `data`，Windows版位於EXE旁的 `data`；環境變數 `PROMPT_STUDIO_DATA` 可另指定位置。擴充必須指向同一份含 `studio.sqlite3` 的桌面資料夾。

更新前建立 ZIP 備份並關閉程式，把新版本放在新資料夾、使用資料副本。保留舊程式、舊擴充及舊資料；回退時三者一起回復到相容版本。不要讓舊程式開啟已被新版修改的唯一資料，舊資料也不要覆蓋新版新增內容。跨版資料無自動合併保證。

外部圖片、模型、擴充資料與憑證需要分別處理，完整範圍見 [資料與隱私](DATA_AND_PRIVACY.md)。SHA256SUMS 用於核對下載內容，不是數位簽章。
