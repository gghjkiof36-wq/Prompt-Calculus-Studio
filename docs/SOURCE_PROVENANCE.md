# 0.8.5 Alpha 1 來源對照

本版公開歷史接續 `317847f0be145f0723115f837daab877a658a0cd`，產品功能固定於 `3033acc9a9f69481dbd2a33565d4fad08f12e3f6`。完整 0.8.5 功能變更涵蓋 `8b123f77087a521c32737a73ffcc2a6ec8ba5465` 至該產品提交；公開整合保留現行 main 文件及歷史。

- 桌面與 ComfyUI 功能程式按固定產品來源整合。公開識別差異為 `releases.py` 新增 0.8.5 Alpha 1、`run.py` 支援對應啟動參數、擴充狀態回覆更新版號，以及 `Start.cmd` 使用本地虛擬環境啟動。
- 歷史建置旗標及資料／節點識別保留。23 個共享模組從相同公開來源收集，包含本版 `stage_parameters`。
- `BUILD_INFO.json` 記公開整合提交、固定功能來源、標籤及版本；`SOURCE_MANIFEST.json` 記來源檔案，`PACKAGE_MANIFEST.json` 記交付檔案。
- `RUNTIME_SOURCE_MAP.json` 將擴充與共享模組映射回公開來源；擴充內附的來源 ZIP 與獨立下載完全相同。
- 來源包由已提交 Git 檔案建立。來源不包含使用者資料、Token、模型、Git 內部目錄或第三方執行環境。

Windows 私人候選的 binary 固定於原產品提交，未被改名成此次公開整合的建置成果。對應材料核對見 [Windows runtime 核對](THIRD_PARTY_085.md)。
