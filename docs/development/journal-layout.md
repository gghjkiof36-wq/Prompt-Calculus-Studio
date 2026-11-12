# Journal 儲存格式 1

本契約對應目前工作樹的 journal 拆分實作。它只改 `studio.sqlite3` 的執行紀錄；
文件 `version`、PNG `schema_version`、記憶體 state、公開 StageStore 回傳值，以及
`document`／`content_history` 均不改。目錄複製、路徑重寫、`--migrate-from` 和
VACUUM 由[複製後搬遷指令](workspace-migration.md)負責，不由正常開庫隱式執行。

## 表與編碼

| 表 | 欄位與用途 |
| --- | --- |
| `storage_layout` | `key TEXT PRIMARY KEY, version INTEGER NOT NULL`。目前寫 `journal=1` 與 `event_apply=1`；`local_state` 尚未拆分，不寫標記，明確標為 0 亦可讀。 |
| `channel_applied_events` | `server, epoch, seq` 為複合 PK；另有 `digest, entity, revision, disposition, created`。文件接受收據與 job／attempt／frame／parameter_state 同交易；不進 Canvas Undo。 |
| `stage_header` | `id TEXT PRIMARY KEY, kind TEXT, workspace TEXT, owner TEXT, position INTEGER, body TEXT NOT NULL, projection TEXT NOT NULL, content_paths TEXT NOT NULL`。`body` 是原紀錄的骨架；`projection` 是衍生摘要；`content_paths` 是預期的 pointer→hash 清單。 |
| `stage_content` | `sha256 TEXT PRIMARY KEY, encoding TEXT NOT NULL, bytes INTEGER NOT NULL, body BLOB NOT NULL`。encoding 固定 `json-utf8-v1`，bytes 為實際 BLOB 長度。 |
| `stage_content_ref` | `record_id TEXT NOT NULL, pointer TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(record_id,pointer)`；record_id 外鍵指向 header，刪除動作 CASCADE；sha256 外鍵指向 content。另有 `stage_ref_hash(sha256)`。 |

標準 JSON 編碼固定 UTF-8、`ensure_ascii=False`、`sort_keys=True`、
`separators=(',', ':')`、`allow_nan=False`。不正規化 Unicode，不把 `false`、0、
0.0、空字串、null 或空容器合併。內容鍵是標準 bytes 的 SHA-256；相同鍵若 encoding、
長度或 bytes 不同，拋出 `JournalCorrupt`，由最外層交易回復，不能覆蓋。

SQL `position` 是排序槽，可有間隙；attempt body 的 `position` 是 Stage 內索引。
兩者獨立保存。`replace_record(body, position)` 不把 SQL 欄位加進 body，亦不重編 id。
遷移同時保留舊 SQL 的 id/kind/workspace/owner，而不是從 body 推導替換它們。

## 內容切割

只抽下列已知位置，未知欄位不遞迴猜測、不因欄位恰好叫 scene 就抽取。空容器、null、
其他假值及全為 null 的 parameters 對照留在標頭，避免空 frame 的不必要查表。
非空內容的抽取路徑如下；`saved.*` 指 scene、values、inputs、batches、readers、
parameters、parameter_overrides、parameter_profiles。

| kind | 內容位置 |
| --- | --- |
| entry | `saved.*` |
| attempt | `snapshot_state`、`images`、`result` |
| batch_snapshot | `scene`、`base`、`supply`、`segments`、`results`、`parameters` |
| run | `frozen_scene`、`free_scene`、`results`、`produced`；`ctx.scene/results/sources/saved.*`；`round_saved[*].*` 的 saved 欄位；legacy `feed.scene/plan` |
| run stack frame | `produced/results/sources/variants`；`ctx.scene/results/sources/saved.*`；`saved.*`；`feed.scene/plan/supply`；`retry.snapshot_state/images/result/replay` |
| batch_control、input、parameter_state、control | 不抽取 |

切割點不重疊，只有 record→content 參照，內容不參照其他內容。位置以 RFC 6901 表示，
`~`→`~0`、`/`→`~1`；陣列索引原位保留。被抽取的位置用 null 占位；只依
`content_paths` 與 ref 表重建，不把普通 null 或使用者自訂物件猜成參照。
游標、供應進度、feed next/total、expanded、控制收據，以及 saved.source_positions
等未列出的欄位留在標頭。batch_control 的 cursor_before/after/keys 不依賴 batch_snapshot。

沒有另存可漂移的參照計數；有效數目以 `COUNT(*) FROM stage_content_ref WHERE sha256=?`
計算。寫入比較舊新參照，替換或刪除後，以同一交易內的 NOT EXISTS 刪除剛變成零參照的
內容。刪除已展開的 batch_snapshot 不刪除 entry/run/attempt 仍使用的內容。取消、移除
等待項目仍保留紀錄，沒有保留期限或自動清歷史。

完整讀取會核對 manifest、實際參照、內容存在、encoding、長度和 SHA-256。
缺內容、缺參照或重疊路徑會拒讀並帶紀錄 id／內容鍵，不自動補回。寫入既有紀錄也先核對
參照，不能以一次狀態更新默默補回遺失參照。

## 查詢與所有權

`read`、`rows`、`open_runs`、`next_entry`、`entry_attempts` 都回傳完整、各自獨立的
dict。單次讀取可共用已核對的不可變 bytes，但每個出現位置各自解碼；沒有跨筆共用的可變
dict/list，也沒有以 live dict 的 `id()` 為鍵的快取。`add_captured` 在同步交易內編碼
借用內容；write-only 呼叫不重建將被丟棄的完整紀錄。

摘要只讀 header 或下列 projection，不重建場景：

| API／欄位 | 來源與保留規則 |
| --- | --- |
| `attempt_heads.has_parameters` | projection.parameter_stages 記錄 snapshot_state 中 parameters 存在且不是 null 的 Stage key。missing/null 為 false，`{}`、false、`""` 仍是存在。 |
| `entry_summaries.image/image_count` | 沿原 SQLite 查詢，在寫入時依 source → saved.values 第一個 image → saved.inputs 第一個 image → saved.batches 第一個非空陣列取圖。source 真值時 count=1；否則採 batches 最大長度，保留 0；無 batch 時依 image 真值回 1 或 null。values 找到空值時不改用 inputs。 |
| `projection.saved_order` | 保存 saved.values/inputs/batches 的第一層 key 順序，讀回時恢復，確保 canonical 排序不改變下次編輯／重存的圖片優先順序。 |
| `batch_heads`、`pending_batches` | projection.segments 只留 owner/start/total/deferred；missing、null、空陣列有別。其餘 run/scheduler/group/total/expanded/deferred 取標頭。 |
| `progress_runs` | 標頭的 id/server/plan/status/message 與 frame kind/key/owner/done/feed.total；非空 feed 與 missing/null feed 的原判斷保留。 |
| `schedule_groups` | entry 計數、group/created/label/position，加 legacy frame node.key、ctx.execution_group、feed.next/total，以及 batch_control 的未展開量。 |
| `preferred_entry`、`next_entry` | group rank、serial 的存在性、SQL position、cancel/bypass、running 優先序均沿用。 |
| `scheduler_runs` | 標頭 entry.run/status/scheduler 與 run.plan.scopes/stages，不解碼場景。 |
| 其他狀態／parent 查詢 | latest_stage_status、status_value、latest_entry_attempt、entry_states、entry_descendants、active_count、waiting_ids 等沿原標頭欄位；保留遞迴 parent 與 ownership 限制。 |

舊紀錄若有標準 JSON 但不完整的圖片／參數摘要欄位，完整紀錄仍能無損保存和轉換。
`projection.error` 保存 entry 原摘要運算的例外型別與參數，僅在原本會失敗的摘要 API 重拋；
attempt 的 `parameter_errors` 逐 stage 保存錯誤，只有查詢的 stage 符合時才重拋，避免其他 stage 的不完整欄位干擾。
不能把預先建立投影變成新的完整紀錄讀取或搬遷門檻。修正相應內容後重新計算投影。

索引全部建於 stage_header：stage_header_owner、batch_control、position、owner_order、
group、entry_display、latest、active、open_runs、parent（後九項同樣有 `stage_header_`
前綴）。顯式 INDEXED BY 使用 `stage_header_entry_display` 與 `stage_header_open_runs`；parent/latest 查詢仍用索引。
完整讀取先以索引檢查參照存在性；有內容時再批次載入參照及內容，空紀錄也不能略過多餘參照的檢查。

## 交易與接點

codec 不 commit。StageStore、Storage 的 generation_jobs 與 parameter_state 寫入共用
同一連線及現有 `_Transactions`；最外層決定 commit/rollback。巢狀交易仍無 savepoint，
捕捉內層例外不自動回復內層。S3 的 tx.changes 只記錄實際 live state tuple path，
不加入 hash、參照或其他儲存欄位。

queue_history.restore 用一個 store.transaction 包住 pause、scheduler control、
replace_record 及參照回收；失敗不移動 Undo/Redo stack。workspace_runtime.unfinished、
schedule_batch_actions.cancel 和 local_workspace_p0 的直接 SQL 已改讀新表。
ready window=20、flow_data.CAPACITY、SLICE_ITEMS 與排程行為不因格式改變。

## 開庫防護

Storage connect 後，先作唯讀 `inspect_layout`，再進初始化交易。StageStore 對傳入連線
也先核對，然後才建索引或把未完成 run 改為 paused/restart。空庫在一個初始化交易建立
完整 journal 三表、索引及 journal=1；Storage 自己的 document/cache 等表也在同一交易內。
PreviewRuntime 明確初始化獨立的 `:memory:` 新庫。

- 非空且沒有標記、journal 標記／實際表不符、舊表與新標記共存：拒絕開啟，訊息只有
  「這份資料是舊版格式，請用搬遷功能匯入。」
- journal>1、local_state>0 或未知布局版本：拒絕，訊息只有
  「這份資料來自較新版本，無法開啟。」
- 拒絕前不建表、不升級、不讀文件、不改 restart 狀態。Storage 失敗時關閉自己的連線。
- Window、DesktopDocument.retarget_execution、WorkspaceRuntime acquire/recover/history_owner、
  工具與啟動路由都經 Storage；StageRunner 再經 StageStore。recover 保留錯誤並顯示同一句。
- backup 先核對來源及目標，完整複製布局；不把備份當作自動轉換。
- execution-index、notebook、work-resources/catalog、synthetic-jobs、integration.sqlite3
  是其他資料庫，不能套用 studio 的布局檢查。
- 擴充的 Service.read_library 維持 URI mode=ro，僅讀 document，不觸發防護、暫停或搬遷。

## 單庫轉換與 registry

`storage_layout.REGISTRY` 的每項為
`LayoutStep(key, from_version, to_version, inspect, transform, verify)`；目前只註冊
journal 0→1，後續 local_state 可加入自己的步驟。正常開庫只 initialize/inspect，
不呼叫 transform。

`transform_journal(connection, checkpoint=None)` 的輸入必須是呼叫方**已複製**的舊庫。
它以 raw SQL 讀舊表，不建立 StageStore；依序編碼、寫入、解碼、逐筆比較值與 Python JSON
型別，核對 id 集合、完整 SQL identity/position 序列、kind×status 計數、外鍵與
integrity_check。全部通過後才刪 stage_journal 並寫標記。沒有 journal 的舊 document 庫
也可轉換為空 journal。回傳 records、ids、positions、kind_status、contents、references
與 integrity_check，供後續指令寫報告。

最外層使用 BEGIN IMMEDIATE／COMMIT／ROLLBACK，一庫一交易；若呼叫方已有交易，用
savepoint 保留呼叫方的交易所有權。checkpoint 可在 record、verified、marked 階段回報
或注入失敗；例外不吞掉。重複 key、NaN/Infinity、溢位、不可編碼 Unicode、超過解析遞迴限制或其他有損 JSON
會產生帶 record_ids 的 MigrationError，來源值不改。文件與 content_history 不讀寫，
也不在此 VACUUM。

驗證入口：`test_journal_layout.py`、`test_journal_layout_behavior.py`，以及既有 Stage、
參數回執、Undo、批次快照與 storage transaction 套件。journal_codec/storage_layout
保持 Qt-free 並列入 SHARED_MODULES，因為共用 core.Storage 也使用開庫防護；沒有修改
擴充端 Service 的唯讀行為。
