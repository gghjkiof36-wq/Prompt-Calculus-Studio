# 0.84 Alpha 1 發布來源對照

2026-09-30 將原 0.831 Alpha 1 統一改名為 **0.84 Alpha 1**。功能與資料格式沿用同一輪成果，這次更新版本宣告、建置參數、啟動器名稱、測試檔名與公開文件。下載以 `v0.84-alpha.1` 的來源及同版擴充為準。

- 功能基準為 `ea7df89dc714fc83c7e11ca32e5c7655619ec8a0`；改名前公開提交為 `f8a43af783e160a30cfabe290567e705bee7f4a2`。本次提交接續公開歷史，未推送全部本機開發祖先，未重寫既有標籤。
- `prompt_studio/releases.py` 更新 CURRENT、版本及 launcher，接受舊 `--v0831-*` 參數與舊 build-info 的 release 值；新輸出一律採 0.84。`run.py` 明列 `--v084-alpha`，既有啟動參數仍可使用。儲存目錄、節點 ID、序列化欄位及資料內容沒有改版遷移。
- ComfyUI 程式與22個共享核心維持功能基準內容。測試檔名和相互匯入改用084；歷史 `verify_v083.py` 的私有絕對路徑可攜化沿用前次公開修正。
- 來源由 Git 白名單收集，ZIP 根目錄包含 run.py；排除個人資料、憑證、模型、Git 歷史、qa、vendor 和第三方 runtime。擴充附相同來源 ZIP 與 runtime 來源映射。
- `SOURCE_MANIFEST.json` 描述來源；`PACKAGE_MANIFEST.json` 描述交付。`BUILD_INFO.json` 的 git_head／product_git_head 指本次含版本更名的來源，functional_baseline_git_head 另記既有功能基準，previous_public_git_head 記改名前公開提交；包不附 EXE，沒有 binary 建置聲明。
- 舊 0.831 Release 保留既有 URL、tag、附件及雜湊作歷史追溯，頁首明示已更名並連到 0.84。舊附件內容仍是原始版本，不能以重新命名附件冒充重建。

原本機 Windows 候選的 binary_git_head 仍為 ea7df89，本次未重建或公開 EXE；其原 ZIP／EXE 雜湊、58個實際 runtime 二進位及散布缺口見 [第三方核對](THIRD_PARTY_084.md)。本機包整合與重建另依正式交付記錄確認。

既有功能驗收、開發測試及本機離屏 EXE 證據見 [驗收範圍](validation/084_STAGE.md)。本次版本相容性、來源／套件及公開下載核對，均不擴寫成真 GPU、新機或完整升級／回退驗收。
