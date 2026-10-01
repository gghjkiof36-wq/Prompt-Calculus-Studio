# 0.8.5 Alpha 1 來源與建置

一般使用者依[開始使用](GETTING_STARTED.md)安裝 Python 套件後啟動；下列步驟供需要重建的人使用。

從 Git 取得 `v0.8.5-alpha.1` 標籤，使用 Windows 64 位元 Python 3.12。`prompt_studio/releases.py` 的當前建置旗標為 `--v0.8.5-alpha.1`，程式啟動參數為 `--v0.8.5-alpha`。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install PyInstaller
.\.venv\Scripts\python.exe build_windows.py --v0.8.5-alpha.1
```

Windows builder 使用 PySide6 與 PyInstaller。公開來源 ZIP 不含 Git 歷史；建置時請使用標籤的 Git checkout，使來源白名單和提交資訊可核對。

ComfyUI 擴充由 `build_comfyui.build(destination=..., archive=...)` 建立，請指定新的空輸出資料夾與 ZIP 路徑。23 個共享模組由 `SHARED_MODULES` 收集；資料、憑證和建置輸出由白名單排除。

`SOURCE_MANIFEST.json` 核對來源，`PACKAGE_MANIFEST.json` 核對交付；`BUILD_INFO.json` 記錄實際來源提交，Windows 包另保留 `binary_git_head`。來源對照見 [SOURCE_PROVENANCE](SOURCE_PROVENANCE.md)。

重新散布自行建立的 Windows 包前，應按實際包含的第三方元件完成授權、通知、對應來源與替換重建材料；本版既有候選的檢查見 [THIRD_PARTY_085](THIRD_PARTY_085.md)。
