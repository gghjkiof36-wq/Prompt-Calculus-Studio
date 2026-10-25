# 建置與來源包

使用 Windows 64 位元 Python 3.12；依[開始使用](../guide/getting-started.md)準備依賴。主分支的版本描述在 `prompt_calculus_studio/releases.py`。

Beta3 本機完整包使用 `build_windows.py --v0.8.9-beta.3`，產生全新的 `build/v0.8.9-beta.3/PromptCalculusStudio`。再以 `tools/build_user_delivery_089.py --desktop <純程式包> --output <新交付目錄> --connector <已驗證的連接器目錄>` 組入教材、素材、BAT 與 Chrome 套件，重新產生 manifest。不要對已有個人資料的資料夾執行組包。

`run_workspace.py` 的 `--choose-workspace` 透過原生資料夾選擇器沿用舊版 `files` 與 `data`，在新程式旁原子寫入 `PCS-WORKSPACE.json`；取得資料鎖後才記住選擇。設定只保存已驗證的絕對目錄，不搬移內容；原位置失效時回報錯誤，不悄悄建立空白替代品。明確 `--root`／`--data` 及 `PROMPT_STUDIO_DATA` 保持既有啟動優先序。可從下一版選中前一版程式目錄，沿用已記住的原始位置。

```powershell
.\.venv\Scripts\python.exe -m pip install PyInstaller
.\.venv\Scripts\python.exe build_windows.py --v0.8.9-alpha.1
```

建置使用 Git 追蹤的來源白名單，需在實際 Git checkout 操作；解壓來源快照不包含 Git 歷史。使用新的空輸出位置，保留原資料與既有交付。

桌面輸出為 `build/v0.8.9-alpha.1/PromptCalculusStudio/`，以 `Start-PCS-v0.8.9-Alpha1.cmd` 啟動分頁工作區，資料存於該套件的 `data/`，新內容預設在 `files/`。建置會同時產生通用、`legacy-0.39.2`、`modern-0.40.0` 三種配套擴充。

單獨建置 ComfyUI 擴充時，使用 `build_comfyui.build(destination=..., archive=..., release=RELEASES['--v0.8.9-alpha.1'])` 指定新的空目錄和 ZIP 路徑；`compatibility_profile=None` 為通用包，另可指定 `legacy-0.39.2` 或 `modern-0.40.0`。三種包共用同一份 34 個 `SHARED_MODULES`，不可帶入 Qt。相容設定檔的版本標示不代表已通過真實服務或 GPU 驗收。

`SOURCE_MANIFEST.json` 描述來源檔案，`PACKAGE_MANIFEST.json` 描述實際交付；`BUILD_INFO.json` 記錄來源提交與驗證文件。Windows 套件另保留實際編譯提交，文件更新不改變舊 EXE 的來源身分。

重建已發布版本時，使用該版本的固定 Git 標籤與當時建置腳本。0.8.6 的來源排列及重建細節見[該版建置紀錄](../releases/0.8.6/building.md)。第三方授權依實際納入元件核對，見[授權說明](../guide/licensing.md)。
