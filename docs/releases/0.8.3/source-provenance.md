# 0.83 Alpha 1 發布來源對照

Release標籤為 `v0.83-alpha.1`，固定產品來源為 `986da442f605512d30b9c3ef9005114a6a175825`。公開提交由前次公開main `a859ebccebc8fe3363663845d2d3419fcb21ce4d` 衍生，保留舊公開歷史和Release。這次整合固定產品樹；沒有把本機完整開發歷史推到公開Git，上述產品SHA可能無法在公開倉庫解析。下載與重建以此Release tag和附件manifest為準。

- `prompt_studio/`、`comfyui_prompt_studio/`、根目錄執行／建置／部署腳本與固定來源逐位元組一致；不納入未提交內容。
- `tests/` 除 `verify_v083.py` 的本機專用測試資料絕對路徑改為本checkout下的 `qa/083/data` 外，其餘保持相同。這是公開路徑可攜化，不改測試斷言、產品行為，也未執行該歷史測試驅動器。
- 公開指南取自文件提交 `e2556874827caa1e330339c8748a7660724a6677` 的11份文件；發布來源、建置及狀態說明由發布整合補齊。歷史候選文件標記適用時點，不以過時流程覆蓋本版直接提交行為。
- 原固定交付的343份來源以換行正規化對照Git一致，原來源ZIP與擴充ZIP的SHA256已核對。公開包重新收集固定程式及公開文件，校驗值因此不同；不直接公開含本機路徑的候選啟動器、DELIVERY或驗證私有資料。
- 來源ZIP依既有 `package_documents.source_paths` Git白名單收集。解壓根目錄含 `run.py`，不含Git歷史、個人資料、模型、第三方runtime、vendor及研究素材。歷史PNG截圖只保留於Git倉庫，不影響來源啟動。
- ComfyUI ZIP以 `comfyui_prompt_calculus_studio/` 為根，內含擴充及 `releases.SHARED_MODULES` 的17個共用核心、公開文件、同一份來源ZIP與來源映射。版本字串、flag、資料夾與內嵌來源檔名均由 `prompt_studio/releases.py` 取得。
- `SOURCE_MANIFEST.json` 列來源；`PACKAGE_MANIFEST.json` 列實際交付檔；`BUILD_INFO.json` 區分公開提交與固定產品提交，擴充亦記內嵌來源SHA256。沒有EXE，沒有 `binary_git_head` 或可執行檔建置聲明。
- 程式標題保留「v0.83 直接執行修復候選（0928）」原字串，公開版本為Alpha prerelease；未改產品以重新命名候選。

使用者正常使用回報、既有150項Python／75項JavaScript及另16項選定檢查分開記錄；本次發布檢查不代表GPU、新機、升級或所有情境通過。兩項靜態待驗風險與詳細界線見 [QA摘要](validation/alpha.1.md)。

本次不散布Windows EXE或第三方runtime；完整第三方散布附件、相應來源與Qt重建／替換材料仍待補齊。專案授權沿用 [LICENSE](../../../LICENSE) 與 [授權說明](../../guide/licensing.md)，未制定新的商業替代授權或CLA。
