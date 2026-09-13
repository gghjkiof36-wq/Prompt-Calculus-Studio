# 開發指南

[返回文件索引](README.md)

依 [安裝指南](GETTING_STARTED.md) 建立 `.venv`。開發與測試請使用獨立資料位置，不要用個人素材庫驗證匯入、刪除或恢復。

```powershell
$env:PROMPT_STUDIO_DATA = Join-Path $PWD 'qa/dev-data'
.\.venv\Scripts\python.exe run.py
```

## 既有檢查

在專案根目錄執行：

```powershell
.\.venv\Scripts\python.exe tests/test_studio.py
.\.venv\Scripts\python.exe tests/test_comfy_integration.py
.\.venv\Scripts\python.exe tests/test_desktop_control.py
node --test tests/comfy_binding.test.mjs
```

最後一項需要另外安裝 Node.js。選擇與修改相關的檢查即可；通過隔離或模擬測試不代表真實工作流、GPU 生成與 Windows 桌面已驗證。

共享核心位於 `prompt_studio`，桌面使用 PySide6；ComfyUI 擴充透過 `build_comfyui.py` 複製無 Qt 的共享程式。修改共享程式後需重新建立擴充。

## 舊開發紀錄

根目錄的 DESIGN、IMPLEMENTATION_NOTES、NEXT_UI、UI_UPDATE、TAG_AUTOCOMPLETE 與 QA 文件保留歷史設計、環境及實驗記錄，其中可能有過時內容。訪客操作以新指南為準；規劃以 [Roadmap](../ROADMAP.md) 為準。

此次保留舊文件位置，避免影響現有工具及其他開發工作。發布前還需獨立驗證打包、乾淨環境安裝、資料恢復、相容性與第三方授權。
