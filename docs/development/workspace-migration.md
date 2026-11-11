# 複製後搬遷

`run_workspace.py` 和 `run.py` 共用不開視窗的搬遷入口。正常開庫仍只執行
[journal 開庫防護](journal-layout.md)，不會自動搬遷，也不修改舊交付資料。

```text
python run_workspace.py --migrate-from <舊交付資料夾> [--migrate-into <新交付根目錄>] [--migrate-allow-inflight] [--migrate-source-alias <以前的交付位置> ...]
```

`--migrate-into` 預設為程式根目錄；打包程式使用執行檔所在目錄。
搬遷忽略 `PROMPT_STUDIO_DATA`，不能與 `--root`、`--data`、`--choose-workspace`、
`--check`、`--smoke-test`、歷史版本旗標或其他啟動選項混用。
stdout 只有一行 JSON。exit code：0 已提交；1 資料、空間、鎖或搬遷失敗；2 旗標錯誤。
JSON 提供中文 `reason`、`status`、目的地、計數、各庫邏輯清點及大小、總秒數。
位置診斷只列檔案／JSON pointer 或表／欄，不輸出 prompt 或欄位內容。

`--migrate-source-alias` 可重複指定，每個值須為絕對路徑，且只能搭配
`--migrate-from`。例如來源複本在 A，但資料仍記錄以前的 B 位置，就指定 B 為別名。
別名只用於字串比對，不讀寫、不解析實體連結、不檢查存在，也不要求該位置可存取。
來源與各別名的 files/data 都映射到同一份新資料，按路徑元件、不分大小寫及斜線方向
比對；`B-sibling`、`B copy` 等相似開頭資料夾不命中。多個根前綴重疊時優先最長者。
不提供別名時，不推測外部位置；owners 仍不在新的 files 時拒絕。

JSON `source_aliases` 依參數順序列出每個 `path` 及 `hits`。命中數是在原始 JSON
字串值／key、SQLite 文字欄位（JSON 先解碼）中出現該別名前綴的次數，包含略過的
recovery、凍結內容及 work-resources，但排除 sessions 與其他未選入資料。
每份原始值只計一次，驗證／改寫不重複累計；未命中為 0，失敗時為已檢查部分的數量。
這是路徑命中數，不代表改寫數或檔案數，不列命中內容。

## 接點與資料流

- `migration_files`：一般檔案只讀、hash、實體路徑與程序檢查、目的地互斥、複製／硬連結、空間檢查。
- `migration_data`：SQLite／檔案分類、欄位型別與路徑映射、舊執行中工作盤點、邏輯 hash。
- `workspace_migration`：暫存、恢復、registry 轉換、驗證、VACUUM、提交與清理。
- `workspace_profile.remember`：既有暫存寫入、flush、fsync、原子替換，是唯一提交點。

這三個新增模組沒有 Qt 依賴，也沒有加入擴充端的 `SHARED_MODULES`。
不改 StageStore 的重開規則、原執行器、文件 writer、document／journal body、id 或 position。

## 來源與複製

來源必須有實體 `files/`、`data/`。來源和目的地不得相同或互相包含；所有祖先與
來源子項目拒絕 symlink、junction 及其他 reparse point。目的地若已有
`PCS-WORKSPACE.json`，拒絕再次搬遷。外部 linked folder 不擅自複製進來。

開始時只讀來源 `data/workspace.lock`、`data/studio.lock`、`studio.lock` 的 QLockFile
內容（pid、應用名、主機名），不呼叫 tryLock。核對程序映像；仍存活的 Python／PCS
程序會回「請先關閉舊版 PCS。」失效 pid 可通過；無法查驗的程序／其他主機保守拒絕。
開始前清點整個來源的檔名、大小、mtime_ns、SHA-256，包含不複製的檔案。
複製後比大小與修改時間，提交前再次比檔案清單、大小、時間和 hash；不同便拒絕。
硬連結的連結數可以變化，但來源不得增刪檔案或改變內容。

`files/` 完整複製（含空資料夾）；可編輯文件不使用硬連結。`data/` 的非排除資料
完整複製。`originals/`、`thumbnails/` 內的已知圖片格式優先硬連結，失敗後完整複製。
不複製 `before-import-*.sqlite3` 及其旁檔；交付根的程式、logs、temp、historical-*、
__pycache__ 不屬資料複製範圍。

`data/sessions/` 整個子樹是每次啟動新建的 EmbeddedHost 暫存與快取；搬遷不複製
（包含空資料夾）、分類、轉換或掃描其中路徑。來源清點及提交前的 hash 比對仍包含
所有 sessions 檔案，含 hot journal。JSON 的 `counts.skipped_sessions_files` 和
`sizes.skipped_sessions_bytes` 列出略過的檔案數與原始 bytes，不列檔案內容；空資料夾
不計檔案數。空間估算不包含這些不複製的資料。`files/sessions/`、`data/sessions-extra/`
和文件自己的子目錄不受此排除影響。根 `data/studio.sqlite3` 可以不存在。

一般複製保留來源檔案的修改時間；這讓已登記的本地模型在路徑搬遷後，仍能以原
size／mtime_ns 接受檢查。硬連結自然保留相同 metadata，來源不寫入。

SQLite **從不在來源開啟**：主檔及 `-wal`、`-shm`、`-journal` 都先以普通檔案複製。
全部檔案完成後，才在暫存目錄開啟 SQLite、恢復 hot journal／WAL、檢查 integrity，
checkpoint 並轉成 DELETE journal 模式。事前邏輯清點由恢復後的複本取得，包含 WAL
已提交資料，不包含 hot journal 尚未提交資料。

資料庫清單：根與 `documents/<token>/studio.sqlite3`、`execution-index.sqlite3`、
`notebook.sqlite3`、`work-resources/catalog.sqlite`，另識別 MCP probe 目錄的
`synthetic-jobs.sqlite3` 與根 `integration.sqlite3`。schema 清單在
`migration_data.SCHEMAS`；未知資料庫（也依 SQLite 檔頭辨識）、孤立旁檔、未知表／欄、
view／trigger 都會停止並列位置。資料檔接受 JSON／JSONL、文字／設定／鎖、可攜 PCS
文件、已知圖片及 work-resources 的封存載體；未知資料副檔名不靜默略過。

## 路徑清單

只把位於舊 `files/` 或 `data/` 下的**完整路徑**映射到暫存目錄的對應位置，按路徑
元件比對，不能用字串前綴誤改相似名稱的外部資料夾。

| 對象 | 可改欄位 |
| --- | --- |
| execution-index owners.body | `path`、`receipt.path` |
| workspace-layout.json | `panes[*].views[*].path` |
| workspace-tree.json（version 1） | `selected`、`expanded[*]`，只改舊 files 下的路徑 |
| recent-files.json | 頂層路徑陣列 |
| linked-folders.json | `roots[*]`、`current` |
| md-models.json | `models` 的 key；值原樣保留 |
| notebook versions | `path` |
| local_ai/registry.json（version 1） | `models[*].path`、`model_folder`、`settings.runtime_path` |

其他 JSON 設定（含 JSONL）與上述資料庫文字欄位都掃描來源及所有別名的絕對路徑；
SQLite 內的 JSON 字串先解碼再掃描。清單外命中即停止，不能全文替換。
凍結 document／journal body（含 generation、chain、input、work queue）、owners
內的 document／runtime_document／baseline 保留內容。管理媒體的原 `path` 只有在
owned、合法 `original_relative` 且新目錄檔案確實存在時可保留；圖片另比 hash。
`work-resources` 完全保留，不清除索引；報告的 `work_resources_relink` 計數是
catalog 中指向舊 files 的 source 筆數，使用者之後自行重新連結。
owners 的文件與 receipt 最終都必須位於新的 files；無法符合者停止並列位置。
workspace-tree 的 selected 可為 null，expanded 必須是字串陣列；scroll、其他欄位
與外部路徑原樣保留。若未列入改寫的欄位仍有舊前綴，仍適用上述全掃描拒絕規則。

本地 AI 清單的 UUID、選用模型、size／mtime_ns、模型預設指示與其他設定原樣保留。
外部模型／引擎路徑不改；映射到新位置的模型、引擎或資料夾必須實際存在，指向被
排除 sessions 的路徑會停止。引擎檔不會被啟動，模型檔不會載入推論。

`data/recovery/<hash>.json` 與 `data/documents/<token>/recovery/<hash>.json` 是
DraftCache 保存的凍結恢復記錄。CloseRecovery 的原路徑會參與 key 與檔名 hash，
記錄內還有當時的文件、baseline、runtime 與狀態；搬遷不改寫或重新綁定這些快照。
搬遷在複製前以一般檔案唯讀檢查這兩種目錄的直接 JSON 子檔；任一 JSON 字串值／key
（包括 envelope key、value.path、value.data 及巢狀快照內容）命中來源或別名前綴時，
整份略過，不複製、不改寫、不因此停止。沒有命中者完整複製並比 hash。
這避免以舊 key／檔名 hash 或已排除的 sessions 路徑，把快照恢復回舊位置；原件仍可
由舊版恢復。JSON 的 `counts.skipped_recovery_files` 及 `skipped_recovery` 只列數量
與相對檔名，沒有內容；來源清點與 hash 比對仍包括所有快照，空間估算排除略過者。
格式無法解析仍停止；其他位置的 JSON 不適用此例外。local_ai 的預設指示或未知
欄位命中來源／別名舊路徑，也仍依 U3 停止。

## 執行中工作與驗證

在複本上用 raw SQL 檢查舊 stage_journal 的 submitted、collecting、preparing、
unconfirmed、cancel_requested（含 boolean 取消意圖）及 input sent 且未 ended；
另查 generation_jobs／舊 queue 的提交中、排隊中、執行中與未確認狀態，以及
未結束 workspace_operations。不能借用只讀新表的 `workspace_runtime.unfinished`。
預設回「舊版還有執行中的工作，請先在舊版完成或取消。」並列數量、id。
只有 `--migrate-allow-inflight` 才可保留帶入。

每個 studio 庫經 `storage_layout.REGISTRY`，目前是 journal 0→1。
轉換期間不建立 StageStore、不修改 run 狀態；第一次正常開啟才依既有規則改成
paused/restart，保留 prompt_id，不重新提交。

逐庫驗證 document 去除 content_history 的 canonical hash、revision、內容歷史
筆數和 hash；journal 經 codec API 讀回，核對每筆 id 的 hash、SQL identity、position
序列及 kind×status 計數；其他表的所有欄位以保留型別的列編碼核對筆數與 hash。
只有路徑清單欄位套用預期映射。原始 document 列也比較完整內容，沒有忽略歷史差異。
每庫轉換後 integrity_check，VACUUM 後再次比清點和 integrity。圖片及其他未改檔案
逐一核對 hash。報告列出原始、恢復後、轉換前後、VACUUM 前後 bytes。

ComfyUI 擴充要重新選新資料位置。`read_library` 的 revision 不變，library_id 因
資料庫位置改變而不同；舊位置尚未取回的工作流匯出要在新版重新傳送。

## 提交、清理與空間

目的地側 `.pcs-migration.lock` 使用 OS 檔案鎖，整次搬遷及清理期間互斥；程序死亡
自動釋鎖，鎖檔保留以避免刪除／重建 inode 的競態。暫存目錄為
`migrating-<UTC時間>-<隨機id>/`，`INCOMPLETE` 記錄來源與本次 id。

全部驗證後再查來源、確認目的地仍無設定檔，呼叫 `remember`。設定檔直接指向
暫存內 files/data，**提交後不改名**，再刪標記。提交前失敗只刪本次暫存；若
remember 已原子替換後才拋錯，先回讀設定檔確認已提交，絕不刪已引用資料。
標記刪除失敗回報 `cleanup_pending`，仍是已提交，下一次啟動完成收尾。

一般啟動和搬遷開始時，先成功讀設定檔，再持有同一目的地鎖清理。只處理目的地
直屬、名稱為 migrating-*、有 INCOMPLETE、非 reparse point 的目錄：被 profile
引用者只刪標記；未引用者可刪；沒有標記不刪。子樹含 reparse point 則停止清理，
不沿連結遞迴。設定檔損壞時停止，另一搬遷持鎖時不清理。

開始前估算：files＋一般 data＋所有圖片完整複製容量＋全部 DB／旁檔的 **6 倍**
峰值預留＋16 MiB 餘裕。硬連結 fallback 再查剩餘預算；仍遇 ENOSPC 就按提交前
失敗處理。估算是保守門檻，不是任意輸入的空間上限證明。

## 唯讀診斷與測試

`--check` 額外列出 data 下每個 studio.sqlite3 的 storage_layout 及 refused/reason，
但直接略過 `data/sessions/` 整個子樹，不檢查、不列入結果及 refused 判斷，也不另報數量。
有拒絕庫回 1，全部可開回 0，不執行 orphan 清理。SQLite `mode=ro` 在 WAL 情況
仍可能修改來源 SHM，因此診斷先只讀複製檔案組，在可拋棄的 scratch 恢復後，以
`mode=ro` 開複本檢查；JSON 標為 `inspection=readonly-copy`。來源不新增或改寫檔案。
這不是正常開庫或隱式搬遷，scratch 結束即清除。

合成資料驗證位於 `test_workspace_migration.py` 與
`test_workspace_migration_behavior.py`：多庫／多 token／owners／notebook／JSON／圖片、
WAL 和 hot journal、來源不變、四處故障與重跑、提交後中斷、清理／鎖／空間／未知
路徑拒絕、CLI 無 Qt，以及 offscreen 假執行器的繼續／取消／重試和已送出 prompt 取回。
合成真實路徑型態另含多個 session app-services、hot journal、Civitai `.image`、JPEG
縮圖、空 session、兩層 recovery、local_ai、workspace 設定、error.log、文件匯入備份
及 generation PNG／JPEG；其中不建立根 studio 庫。另驗 sessions 中途內容改動仍拒絕。
別名合成情境涵蓋 A 複本記錄 B、重複旗標／缺來源／互斥、兩種斜線與大小寫、
相似名稱兄弟目錄、別名不做檔案操作、workspace-tree、兩處 recovery 的略過／保留，
以及別名前綴在清單外 JSON／SQLite 欄位的拒絕。
這些測試不讀取真實使用者資料，也不代表真實 ComfyUI／GPU 或舊版執行檔驗收。
