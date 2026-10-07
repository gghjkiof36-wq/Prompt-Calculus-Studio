# 資料與相容邊界（2026-09-14）

桌面文件入口為 `Storage.load_current`，先由 `Storage.load` 驗證原資料，再用 `state_loading.prepare_state` 取得轉換副本。只有原文件確實存在且需要轉換時才先備份、再保存；新安裝不製造空文件備份。JSON 匯入在確認取代之前準備並驗證候選資料。歷史快照恢复也使用同一轉換入口，最後仍比對歷史生成文字。

| 相容資料 | 保留原因與處理 | 驗證 |
|---|---|---|
| 文件 version 1–4 | `core.validate_state` 先拒絕不支援版本；預設設定補齊不改既有值 | core／保存往返測試；未知版本不覆寫原文件 |
| 舊 Canvas 素材分類（0.7.0 時期） | `composition.migrate_canvas_library` 保留順序、權重、原型封存與工作區；比對轉換前後文字 | `test_canvas_refinement`、`test_maintenance_loading`，備份含原文件 |
| 多輸出早期連線與舊預設名稱（0.8.0 Alpha） | `multi_output.migrate` 升級一次；目前版本不重接已移除連線 | `test_multi_output`、`test_v08_alpha3` |
| PNG 快照 schema 1–4 | `snapshots.validate_snapshot/restore_snapshot` 驗證其自身文字；從未使用最新 Prompt 修補歷史 | `test_comfy_integration`、`test_snapshot_ui`、多畫布恢復 |
| SQLite／ZIP 副本 | 保留原資料目錄的相對圖片引用；ZIP 還原在副本目錄操作 | Alpha 2 備份驗證與維護載入往返；不新增 ZIP 匯入 UI |

`refresh_builder` 不再執行資料格式遷移。它仍整理目前組合的有效選項與順序，這是原操作的一部分，並非歷史資料版本轉換。`Storage.load` 保留為原始文件唯讀檢查入口；新增介面不能自行增加相同遷移判斷。

CivitAI 的 API 映射已集中於 `civitai.normalize_version`，詳細資料與下載計畫均使用它；缺失數據保留缺失，UI 顯示「未提供」。`civitai_assets` 將所選版本／實際檔案轉成下載計畫，Type 的目錄映射與 Base Model、用途分類各自保存。來源以 `asset_sources` 的 Hash 索引保存；`resources` 保存實際檔案與使用者資料，辨識更新只合併 `SOURCE_FIELDS/HASH_FIELDS`。未改動這些既有資料欄位，亦未引入新的資料格式。
