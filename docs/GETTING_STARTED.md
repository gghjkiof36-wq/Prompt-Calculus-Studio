# 開始使用

[回首頁](../README.md) · 適用：0.82 Alpha 1 Repair 5

## 取得版本

從 [Repair 5 Release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.82-alpha.1-repair.5) 取得 `PCS-v0.82-Alpha1-Repair5-source.zip`；要連接 ComfyUI 時另取同版擴充 ZIP。附件是否可下載以 Release 頁實際狀態為準。來源 ZIP 解壓後根目錄直接包含 `run.py`。Windows EXE 本次未提供。

需要舊版時用 [0.81 Release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.81-alpha.2-ui-repair.1) 及該頁的啟動指令；不要把 0.81 的來源、Repair 5 擴充與同一份資料混用。

## 安裝與啟動

準備 Windows 64 位元與 Python 3.12（含 Python Launcher）。在包含 `run.py` 和 `requirements.txt` 的資料夾開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v082-alpha
```

首次安裝需要連接 PyPI。之後只用最後一行，無須啟用虛擬環境或修改 PowerShell 執行原則。找不到 `py` 時，將第一行改用已安裝的 Python 3.12 執行檔完整路徑。

標題應為 PCS、Repair 5，並保留「0927-2 批次預覽候選」字樣。這是本次固定產品的標題。`run.py` 已預設開啟新版介面；上述明列 `--v082-alpha` 方便識別適用版本。

## 建立雙擊捷徑

完成安裝後建立桌面捷徑，將下列兩處路徑改成實際解壓位置：

```text
"D:\Apps\PCS\.venv\Scripts\pythonw.exe" "D:\Apps\PCS\run.py" --v082-alpha
```

專案內舊 `Start.cmd` 不會自動選擇此 `.venv`，請使用自己建立的捷徑。

## 第一次使用

1. 初次開啟選清單或 Canvas；後續可從設定切換。
2. 選取模組與素材，查看組合後的 Prompt，調整順序和權重。
3. 保存自己的素材並複製文字；修改資料會保存在本機。
4. 要直接生成時，依 [ComfyUI 指南](../COMFYUI_GUIDE.md) 安裝同版擴充與綁定 CLIP。網頁保持開啟，每次執行一個工作流一次。

清單與 Canvas 有各自操作及手動稿；網頁的「ComfyUI 手動文字」是 CLIP 的來源選擇，與桌面手動稿分開，見 [操作指南](USER_GUIDE.md)。

## 資料、更新與回退

預設資料位於 `run.py` 旁的 `data`；環境變數 `PROMPT_STUDIO_DATA` 可另指定位置。擴充必須指向同一份含 `studio.sqlite3` 的桌面資料夾。

更新前建立 ZIP 備份並關閉程式，把新版本放在新資料夾、使用資料副本。保留舊程式、舊擴充及舊資料；回退時三者一起回復到相容版本。不要讓舊程式開啟已被新版修改的唯一資料，舊資料也不要覆蓋新版新增內容。跨版資料無自動合併保證。

外部圖片、模型、擴充資料與憑證需要分別處理，完整範圍見 [資料與隱私](DATA_AND_PRIVACY.md)。SHA256SUMS 用於核對下載內容，不是數位簽章。本輪未重新執行新電腦安裝或產品測試，驗收範圍見 [QA 摘要](validation/082_REPAIR5.md)。
