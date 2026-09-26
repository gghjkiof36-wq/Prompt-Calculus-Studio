# 0.81 Alpha 2 維護測試版 1

本版基於已驗證的 0.81 Alpha 2，只整理內部責任、資料轉換及打包資訊，不新增產品功能，不重排畫布或設定頁。沒有升為正式 0.9。

雙擊 `Start-v0.81-Maintenance.vbs`。程式在自己的 `data` 目錄建立資料，不會取代原 Alpha 1／Alpha 2。此乾淨程式包不附個人資料；需要沿用資料時先關閉來源程式，再將其完整 `data` 資料夾複製到這份獨立程式旁邊，保留原件。

維護內容：

- 集中保存、畫布刷新與同步通知；版面、Notes 和一般設定不再無條件更新 Prompt。
- 統一手動稿與綁定／解除入口，保留空手動稿、不同畫布與清單的草稿及復原／重做。
- 舊資料在載入／匯入／恢復時驗證及轉換，不在畫面刷新時遷移；有原文件時先建立備份。
- 版本、測試輸出路徑與啟動參數由 `prompt_studio/releases.py` 描述。舊建置旗標保留，不代表重新建立當年的原始碼。
- 每包附 `BUILD_INFO.json`、`SOURCE_MANIFEST.json`、原始碼與驗證范围。ComfyUI 擴充包含純 Python 共用轉換模組。

最新驗收與限制見 `docs/DEVELOPMENT_STATUS.md`；來源、備份與提交見 `docs/MAINTENANCE_REPORT.md`。全程使用資料副本和背景／offscreen 驗證，沒有操作使用者桌面或進行 GPU 生成。
