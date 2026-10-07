# 建置與來源包

使用 Windows 64 位元 Python 3.12；依[開始使用](../guide/getting-started.md)準備依賴。主分支的版本描述在 `prompt_calculus_studio/releases.py`。

```powershell
.\.venv\Scripts\python.exe -m pip install PyInstaller
.\.venv\Scripts\python.exe build_windows.py --v0.8.6-alpha.1
```

建置使用 Git 追蹤的來源白名單，需在實際 Git checkout 操作；解壓來源快照不包含 Git 歷史。使用新的空輸出位置，保留原資料與既有交付。

ComfyUI 擴充使用 `build_comfyui.build(destination=..., archive=...)` 指定新的空目錄和 ZIP 路徑。共享模組清單為 `SHARED_MODULES`，不可帶入 Qt。

`SOURCE_MANIFEST.json` 描述來源檔案，`PACKAGE_MANIFEST.json` 描述實際交付；`BUILD_INFO.json` 記錄來源提交與驗證文件。Windows 套件另保留實際編譯提交，文件更新不改變舊 EXE 的來源身分。

重建已發布版本時，使用該版本的固定 Git 標籤與當時建置腳本。0.8.6 的來源排列及重建細節見[該版建置紀錄](../releases/0.8.6/building.md)。第三方授權依實際納入元件核對，見[授權說明](../guide/licensing.md)。
