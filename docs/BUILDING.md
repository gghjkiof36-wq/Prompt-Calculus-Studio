# 0.8.6 Alpha 1 來源與建置

一般使用者依[開始使用](GETTING_STARTED.md)安裝 Python 套件後啟動；下列步驟供需要重建的人使用。

從 Git 取得 `v0.8.6-alpha.1` 標籤，使用 Windows 64 位元 Python 3.12。`prompt_studio/releases.py` 的當前建置旗標為 `--v0.8.6-alpha.1`，程式啟動參數為 `--v0.8.6-alpha`。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install PyInstaller
.\.venv\Scripts\python.exe build_windows.py --v0.8.6-alpha.1
```

Windows builder 使用 PySide6 與 PyInstaller。公開來源 ZIP 不含 Git 歷史；建置時請使用標籤的 Git checkout，使來源白名單和提交資訊可核對。

ComfyUI 擴充由 `build_comfyui.build(destination=..., archive=...)` 建立，請指定新的空輸出資料夾與 ZIP 路徑。24 個共享模組由 `SHARED_MODULES` 收集；資料、憑證和建置輸出由白名單排除。

`SOURCE_MANIFEST.json` 核對來源，`PACKAGE_MANIFEST.json` 核對交付；`BUILD_INFO.json` 記錄實際來源提交，Windows 包另保留 `binary_git_head`。來源對照見 [SOURCE_PROVENANCE](SOURCE_PROVENANCE.md)。

重新散布自行建立的 Windows 包前，應按實際包含的第三方元件完成授權、通知、對應來源與替換重建材料；本版既有候選的檢查見 [THIRD_PARTY_086](THIRD_PARTY_086.md)。

## 來源版隨附擴充

公開的來源版下載包包含專案檔案、`extensions/comfyui_prompt_calculus_studio` 已備妥的同版擴充，以及 `extensions/PromptCalculusStudio-ComfyUI.zip`。程式從自身資料夾尋找該配套，呼叫根目錄 `install_comfyui.ps1` 安裝；不需要先連線或使用 Manager。

擴充內的 `PromptCalculusStudio-source.zip` 是固定 Git 的純來源快照，與 `SOURCE_MANIFEST.json` 逐檔相符。下載用來源版包另含已備妥的擴充，因此與這份內嵌純來源 ZIP 不同；各自的雜湊由 `BUILD_INFO.json`、套件清單及下載校驗檔追溯。這個排列避免來源包與擴充互相遞迴包含。

自行由 Git 建立来源版配套時，在新的空輸出資料夾呼叫 `build_comfyui.build(destination=..., archive=...)`，再把產物放入來源根目錄 `extensions` 的上述位置。單純 Git checkout 尚未建立配套時，安裝頁會提示未提供。
