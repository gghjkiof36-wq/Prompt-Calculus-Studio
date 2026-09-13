# 開始使用

[返回首頁](../README.md)

## 準備環境

目前的桌面操作說明以 Windows 64 位元與 Python 3.12 為準。從 [Python 官方網站](https://www.python.org/downloads/windows/) 安裝 Python；此版本尚未完成其他作業系統的桌面相容性驗證。

在 [專案首頁](https://github.com/gghjkiof36-wq/modular-prompt-manager) 按 **Code → Download ZIP**，解壓到可寫入的資料夾。這份 ZIP 是原始碼，沒有 EXE。已使用 Git 的人也可 clone 儲存庫。

## 安裝與啟動

在包含 `run.py`、`requirements.txt` 的資料夾開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

第一次安裝需要網路，依賴套件來自 PyPI。後續啟動只需最後一行；建立的 `.venv` 只供這份專案使用，不必修改 PowerShell 執行原則。

如果找不到 `py`，請使用你安裝的 Python 3.12 執行檔路徑取代第一行的 `py -3.12`。安裝錯誤時，先確認 `py -3.12 --version` 能顯示版本。

## 建立不用輸入指令的捷徑

完成第一次安裝後，在桌面新增捷徑，目標填入下列格式，將兩處路徑改成實際專案位置：

```text
"C:\Apps\PromptStudio\.venv\Scripts\pythonw.exe" "C:\Apps\PromptStudio\run.py"
```

之後雙擊捷徑即可開啟。專案內現有的 `Start.cmd` 不會自動選擇 `.venv`；使用上述安裝方式時，請使用新建捷徑。

## 初次使用與保存

初次開啟會建立可修改的範例模組及專案根目錄的 `data` 資料夾。先從內建角色、動作、表情選取素材，再調整成自己的組合。修改會保存到本機資料庫。

不使用網路候選時，可在「設定」關閉聯網。尚未連接 ComfyUI 時，右側提供「複製完整 Prompt」；接線與生成方式請看 [ComfyUI 整合](../COMFYUI_GUIDE.md)。

## 更新與備份

更新前先使用「資料與備份」建立 ZIP 備份，再關閉程式。保留原有 `data`，將新原始碼解壓到另一個資料夾，完成依賴安裝後再搬入資料副本。請保留原備份，直到新版本能正常開啟。

資料庫之外的圖片及模型可能只是外部連結，需另行備份。詳細範圍見 [資料與隱私](DATA_AND_PRIVACY.md)。
