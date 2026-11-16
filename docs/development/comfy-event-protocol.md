# ComfyUI 持久收據與事件協定

適用於 pre-alpha3 的擴充事件來源與桌面通道。C1 起，原生／凍結生成任務由事件接受與有界對帳決定生命週期；input 與輔助 operation 暫留既有路徑。需要更新 ComfyUI 端擴充並重啟後端、重新整理網頁；此文件不是已安裝或 GPU 驗收證明。

## 身分與相容

`GET /prompt_studio/events/capability` 回傳：

```json
{
  "capabilities": ["execution_events_v1"],
  "extension_info": {"root": "C:/ComfyUI/custom_nodes/comfyui_prompt_calculus_studio", "version": "開發版"},
  "event_channel": {
    "schema": "pcs.execution-events.v1", "transport": "long-poll",
    "server_instance": "persistent-installation-id", "epoch": "publisher-instance-id",
    "limits": {"events":128,"page_bytes":262144,"payload_bytes":65536,
      "retention_count":100000,"retention_bytes":67108864,"retention_seconds":86400,
      "progress_hz":5,"wait_seconds":8.0}
  }
}
```

`desktop/status` 同樣附加 `event_channel`、能力名稱與 `extension_info`，保留原 `extension` 與所有舊欄位。安裝位置來自已載入擴充的 `build_identity.LOADED_BUILD.root`，不是由 PCS 猜目錄；桌面只在[自動綁定條件](../guide/comfyui-compatibility.md#擴充資料夾自動綁定)全部成立時保存位置。

`server_instance` 存在擴充專用 `integration.sqlite3`，重建 Service 不變；每次 Service 建立產生新 `epoch` 並持久保存。舊 Service 的 writer 會被資料表 trigger 拒絕。每個 epoch 的全域 seq 從 1 遞增，wire 一律使用十進位字串，`"0"` 是初始游標。新 epoch 清除舊事件流、保留收據與 tombstone；必須重新 snapshot，不能接續舊游標。

單一使用者模式才提供此通道。舊 PCS 搭配新版擴充繼續使用原 HTTP 路徑。新版 PCS 遇到沒有有效 `execution_events_v1` 協定的擴充，顯示「需要更新擴充」並禁止新派送；原已在途任務只對原 operation 有界查證，不恢復週期狀態輪詢。studio DB 新增 `storage_layout('event_apply',1)`；journal 保持 1，文件及 PNG 版本不變。舊庫須先複製再搬遷，不支援降版開新庫。

## 桌面通道與持久 inbox

`WorkspaceConnection` 管理目前 server 的共享 `QtChannel`，多文件共用同一條事件流；獨立宿主由 `DocumentChannel` 管理。QThread 持有 QNetworkAccessManager、JSON 解析、計時器與 inbox 連線。主執行緒每個 owner 同時最多接受一頁，最多 16 筆且編碼低於 256KiB；不跨執行緒傳 SQLite 物件或未解析回覆。健康資訊每次變更及有效心跳才交給 GUI，不在無事 pump 掃 inbox。

每個 request 可由回傳的 reply 中止，並有從送出起算的絕對期限，不能靠持續收到少量資料無限延長。events 最多等 8 秒、期限 12 秒，heartbeat 每 8 秒，snapshot／協商期限 6 秒。連線換代會立即使舊回應無效，再在 worker 中中止請求；關閉工作區會非阻塞地等待 worker 退出，才完成關窗。token 只存在程序記憶體；403 重新取 config token 並協商，400／409、reset_required 或錯 server／epoch 都重新協商及 snapshot，不重送任何工作。

GUI 與 worker 共用 request 的完成清理：在 reply 所屬執行緒停止 deadline timer、明確斷開 timer → abort 與本次的 Python 回呼連線、解除 QNAM parent，再沿原完成路徑呼叫 `deleteLater`。不能把這些斷線留給 QObject 解構：Qt 會在持有 receiver 的 signal-slot mutex 時呼叫 sender 的 `disconnectNotify`，Python 建立的 timer 會因此查詢虛擬函式並要求 GIL，與另一執行緒的 Qt 操作互等。worker 關閉時也先拆除 started、command、stopping 與 cycle timer 的入站連線，再中止原請求與排程銷毀。取消、絕對期限、換代拒絕與不重送規則不變。

worker 使用單次期限計時器，在回覆、命令或最近的心跳／證據／重試／清理期限到達時才醒來，沒有每 250ms 的空閒掃描。非空事件頁與 snapshot 後頁收到並保存後即排程續讀，避免人為耗盡 snapshot 的 60 秒有效期。空頁、不一致或儲存錯誤仍由核心期限與退避限制；慢速網路造成 snapshot 過期時重新取固定水位，不推進未完成的游標。

獨立 sidecar 位於工作區資料根目錄的 `comfy-channel.sqlite3`，WAL／SHM 同屬該資料。`streams` 以 server_instance、epoch、scope_digest 分開保存 received 與 applied；inbox 以 server_instance、epoch、seq 去重，衝突的同 seq 內容拒絕接受。單頁事件及 received 在同一交易提交。`delivery_offsets` 保存每個 owner 的交付水位，applied 是該 scope 所有 owner 的最小水位；需要變更文件的事件，只在文件接受交易成功後回報 delivered。純進度或已確認不改文件的觀測可直接在 worker 完成交付。固定水位 snapshot 分頁先暫存，全部完成才推進 received；文件接受完 snapshot 才推進 delivery，接續 W 以後的事件。舊 epoch 收據僅限該次有效 snapshot 接受，不能當作新 epoch 的串流事件。

SQLite／磁碟失敗時停止所有拉取，保留最多一頁待提交資料，以 1／2／4／8 秒退避重試同一交易；收到但未 commit 不推進游標。程序關閉後該記憶體頁可丟棄，下次仍從持久游標讀回。每個 server／epoch 的未套用事件上限 100,000 筆或 64MiB，達上限停止拉取，不刪除未套用證據；每次 snapshot 也受同樣容量限制。完成新 snapshot 後可移除同 scope 的舊暫存 snapshot。sidecar 不隨文件關閉、另存或切換 server 刪除，也不屬文件格式；清理／恢復工具可用 `channel_sidecars` 取得主檔、WAL、SHM，不能把它當成無條件可清除的 cache。

owner 表使用現有 runtime_token，另存 document_id、由 runtime store 路徑推得的 library_id、原 server 及附掛狀態。另存後的新文件有新 owner，舊任務仍屬原 runtime；關閉視圖不改派事件給目前文件。工作區訂閱 default-user 的 server scope，避免同一 server 多文件各開 long-poll；本地 owner 關聯獨立保留。scope 增加或新 owner 加入會補一次 snapshot，不拿原 cursor 當作新增範圍的證據。

12 秒沒有有效通道回覆、running 與本機不符、啟動未結案、needs_reconcile、log.gap 或不一致，都先查原 operation。首次定點查證返回證據或失敗後，才授權固定水位 snapshot；其後接續 W 以後的事件。新 epoch／新 server 先凍結新派送，不發全域 interrupt。送出、取消、收取各有 30 秒證據窗口，失敗以 1／2／4／8 秒退避；history 只在 terminal 或原生 history 證據出現時讀取，成功一次就停止。30 秒不足以確認時保存 unconfirmed 並顯示「無法確認」，不釋放在途權、不重送。有效心跳、晚到終態或「查詢原任務」可開啟下一次有界核對。queued／running 的正常長生成沒有總執行逾時；120 秒無進度只寫診斷。空頁／不一致頁至少間隔 1 秒。

原生／凍結生成的週期 status 查詢已停用。Workspace 的通道健康、snapshot 已接受，且 client 沒有待完成的 owner 註冊或在途 input／apply／輔助 operation 時，desktop/status 的週期請求至少相隔 8 秒（由既有 1.5 秒 timer 排程，通常每 9 秒）；utility client 也共用此健康資訊。其他情況維持 1.5 秒，明確的重新確認與命令仍即時查詢；input／輔助 operation 的查詢保留，接管另行實作。worker 在 12 秒察覺期限到達時，立即投影既有「正在確認 ComfyUI 連線…」狀態，不等待低頻 status 或它的 HTTP 期限；不因此把工作結案。獨立宿主的 status 頻率不變。

desktop/status 不能直接完成通道任務。能力資訊及擴充安裝位置只有內容改變時才重新辨識與綁定；連線換代仍使舊快取失效。HTTP 回覆不再做影子核對或送出 poll 診斷命令，生命週期以通道與有界對帳為準。沒有 interrupted 的排隊取消以 cancel_confirmed 收尾；純 unavailable 不是 missing。

## 文件接受、冪等與清理（C1）

`channel_applied_events` 的 PK 是 `(server,epoch,seq)`，保存 envelope digest、entity、revision、disposition 與接受時間。定點證據使用 `reconcile:<operation>:<digest>` 的本地 receipt key，不冒充 wire seq。generation job、attempt、parameter_state、Stage 結果及 frame 與 receipt 共用同一連線、同一外層 `accepting`／execution_scope 交易。結果圖片沿原最終收圖路徑；節點即時收圖留給 D。通道 metadata 不加入 tx.changes 或 Canvas Undo。記憶體終態、最近生成、通知及 scheduler 只在 after_commit 更新。

文件 receipt 只保存實際變更：首次 queued／prompt、首次 running、終態、取消確認、missing fence、參數收據、unconfirmed 與 retired。progress、重複節點、已 running 的 executed／cached、相同收據與晚到終態不開文件交易，也不為 no-op 插入一列。receipt 數量因此隨狀態變更次數增加，不隨生成時間增加；重播無副作用的觀測不需要文件 receipt。

progress 數值沒有桌面 UI 訂閱者，留在 worker 做健康與 120 秒無進度診斷。任務詳情會顯示最後節點，worker 合併節點變更後最多每 200ms 送一批小物件；GUI 只更新記憶體，既有詳情視窗仍每 2 秒讀取，不廣播 Canvas 或狀態刷新。worker 只有在 owner 確認 running 已接受後，才略過後續 executing／executed／cached 的生命週期交付；首次 running 仍須由文件交易接受。所有頁面仍驗證身分、scope 與序號覆蓋範圍。

原生 active job 使用既有記憶體 map；frozen attempt 對 Queue 主鍵及 active 集合的索引在 owner 開啟時建立一次，之後隨 Queue 提交更新，回滾不發布。job 查詢使用 map／主鍵，health 只檢查在途 owner，不逐事件解碼保留 Queue rows。

文件提交後、sidecar delivery 提交前當機，重送事件由文件 PK 判為 no-op。SQLite 失敗不移動 frame 或游標。相同 seq 不同內容拒絕；receipt revision 倒退忽略，同 revision 不同內容隔離並重新核對；錯 server／owner／epoch 不套用。terminal 可早於 queued，依原 operation 讀回完整收據；晚到 ACK 不回退已結案狀態。retired tombstone 單調，成功／取消晚到不能復活。manual／restart pause 不因終態收取清除；batch_cancel_resume 仍由既有群組取消規則決定。背景文件打不開時該 owner 保持 pending，其他 owner 可繼續接受。

D1 的「停止追蹤此筆」僅接受原 operation 的 fenced missing：擴充在 queue mutex 與收據交易內檢查 queue、history、提交窗口及 ownership，建立阻擋晚到 submit 的 fence。原收據整列遺失時，須帶原 library／workspace／prompt 證據；不能以目前文件代替。unavailable 不可結案。PCS 本地記錄 retired／tombstone，不假稱 ComfyUI 執行取消。

inbox 每頁只增減 `budgets`／`snapshot_budgets`，不做 COUNT／SUM 全表掃描；舊 sidecar 首次開啟才回填計數。worker 每 60 秒最多清理 1024 筆：只刪所有 owner 已 delivery 且收到超過 24 小時的事件，同交易減計數。超過 100,000 筆／64MiB 時停止拉取，絕不以容量為由刪未交付證據。新 epoch snapshot 對某 owner 完整交付後才覆蓋該 owner 的舊 epoch 水位；所有 owner 都已覆蓋、舊事件過期清空後，才清除舊 epoch 的 stream、offset、snapshot、觀測及計數。文件內接受 PK 與 retired tombstone 不按 sidecar 期限刪除。

## 安全與訂閱範圍

四個 GET 通道端點都沿用本機 peer、Host、Origin、Sec-Fetch-Site 檢查，**另外要求 `X-Prompt-Studio` token**。原生客戶端可不帶 Origin；`Origin: null`、跨來源、遠端 peer、錯 token 都拒絕。token 由既有本機 `/prompt_studio/config` 取得，重載後須重新協商；不要存入事件或公開記錄。

除 capability 外，請求都須帶 `server_instance`、`epoch`、`scope`（URL encoded JSON）、`scope_digest`。基本 scope 是 `{"domain":"comfy.execution","user_scope":"default"}`，可加 `library_id`、`workspace_id` 或最多 128 個 `entities`（例如 `native_operations:operation-id`）。digest 是 canonical UTF-8 JSON 的 SHA256；鍵排序、無空白、entities 排序去重。錯 domain、欄位、身分或 digest 會拒絕，不以目前開啟文件補猜。

scope 是已驗證本機 default 使用者之內的資料過濾，並非多使用者 ACL。所有 token 持有者原本即能存取本機配套。沒有文件歸屬的原生事件只出現在全 server scope；已知工作由提交時保存的收據關聯 prompt_id，從不讀當前 config 猜 scope。`log.gap` 是 server／default-user 範圍的控制事件，會送達同 domain／user 的所有訂閱，即使訂閱限定了 library、workspace 或 entities；未知歸屬的遺失不能被篩選掉。增加 scope 必須重新 snapshot；不能以舊覆蓋游標代表新加入的文件。

## 來源、交易與保存

`event_log.EventLog.append` 是唯一 append 入口。以下所有來源表都註冊 INSERT / UPDATE / DELETE trigger，包含啟動修復、直接 SQL、診斷、ACK 刪除與清理；不依賴呼叫者記得額外寫事件：

| 模組 | 來源表／責任 |
|---|---|
| service | collections、jobs、workflow_transfers |
| native_queue、native_handshake | native_operations、native_diagnostics |
| frozen_queue | queue_captures、queue_attempts |
| background_state、background_execution | background_states、background_operations、background_receipts |
| input_bridge | input_bridge、input_requests |

trigger 在原交易內記錄 `event_changes`；`Service.connect` 在 commit 前排空這些變更，經 append 更新 event_log、event_receipts 與 seq。任何步驟出錯，原收據、outbox、projection 和 seq 一起 rollback；commit 後才喚醒 long-poll。原始 body／graph／文件仍留在原表，事件僅含精簡收據、SHA256 與 `receipt_ref`。input bridge 的純 seen 更新不產生重複事件；ACK 刪除另有持久事件。

內部 event_meta、event_usage、event_receipts、event_fences、event_snapshots、event_snapshot_items 由 EventLog 管理，不遞迴當作業務 writer。settings.json 和 queue_assets 沿用原儲存流程，不宣稱與 SQLite 原子提交。擴充對 PCS document 仍用 `mode=ro`；不寫使用者的 PCS 資料庫。

### 一致性、缺口與恢復

`log_consistent` 只表示是否還有**尚未以持久缺口事件揭露的實際遺失**，不是服務可用性、publisher 活動或某項工作的成功與否。只有以下位置可呼叫 `record_loss`：原生 hook 緩衝已滿、ingestion 失敗、worker 丟失無法 append 的原生批次，以及 native／frozen／background 提交已取得 ComfyUI 接受證據、卻無法提交收據的儲存錯誤。ComfyUI 佇列和 SQLite 不是分散式交易；既有 operation／attempt 的 request hash、submit_inflight 與收據仍阻止重送。

沒有外部效果的失敗維持 consistent：包括 fence、ownership、stale epoch、過大 envelope，以及原子回滾的 lock／IO 失敗。`Service.connect` 只負責回滾並把錯誤拋給呼叫者，不推斷遺失。故意的 fence／stale／envelope 拒絕也不會在提交回覆路徑製造缺口。HTTP 恢復或缺口已寫入都不代表未確認工作已成功、可結案或可以重新提交。

真實遺失先將記憶體 `consistent` 設為 false，保存時間、原因與已知 prompt／entity。非 hook 路徑會嘗試寫入 `event_meta.consistent=0` 與 `reason`（內部 JSON 缺口資料）；hook 只改有界記憶體，由 worker 持久化，不能在 executor 呼叫中做磁碟 IO。資料庫完全不可寫時保留記憶體待辦並重試；若在尚未能落盤前整個程序消失，仍由新 epoch 的強制 snapshot／原生對帳覆蓋這個窗口，不能宣稱有未落盤的缺口證據。

worker 經唯一的 `EventLog.append` 寫入 `kind="log.gap"`、`entity="log:gap"`。payload 含 `reason`（第一個原因）、`occurred_at`、`last_occurred_at`、`count`、`affected`（已知的 prompt_id／entity，最多 16 組，每個識別碼最多 256 字元；超過省略並加 affected_truncated）；沒有猜測出的工作身分，最壞 UTF-8 編碼仍低於 64KiB。一次未解除期間的遺失合併成一筆缺口。缺口事件與 `event_meta.consistent=1, reason=''` **同交易 commit**，之後才清除記憶體待辦並喚醒 long-poll；交易失敗則事件、seq 與恢復旗標一起回滾。寫入期間新到的遺失保留為待辦，不會被先前缺口清掉，後續缺口的證據可能與前一筆重疊。固定的 gap projection 只保留最新一筆，事件流仍按既有保留政策保存各筆。

保存 false 與嘗試缺口共用退避：每次嘗試結束後至少 1 秒才可再次存取 DB，不隨 50ms ingestion tick 重寫 meta，也不因仍為 false 重複 notify。先獨立提交 false，確保缺口 append 失敗後仍有持久待辦。新 Service 的 epoch **一律從 consistent 開始**：若上一 epoch 的 event_meta 留有未解除遺失，切換 epoch 的交易會把它寫成新 epoch 的第一筆 `log.gap`（seq="1"，加 previous_epoch）。切換失敗則整筆回滾，舊標記不丟失；不沿用永久 false。

消費端收到 `log.gap`，必須安排**固定 as_of_seq 的 snapshot 加原生 queue/history 對帳**，完成後再接續其後事件；缺口本身不能直接把 generation job／attempt 判為失敗、完成或允許重送。`log_consistent=false` 表示仍等缺口持久化，先定點查證並沿既有 30 秒證據期限處理「無法確認」，不忙重試。看到 true 也仍要處理已收到的 gap；不能只看 heartbeat true 就跳過對帳。epoch 改變、游標過期也照常要求完整對帳。

### 原生 hook 與期限

每個 PromptServer 只有一個 PCS send_sync hook，重裝 hook 替換 publisher、保留舊 BackgroundEvents 的 100 個 active 工作與原 sender。hook 只做有界複製與記憶體排隊；不做 SQLite、網路或等待事件條件鎖。最多 1024 個待處理事件，每次結構複製最多約 128Ki 個字元／結構預算，超大資料改留 fetch_required；不是無限累積圖像。序列化、append、維護都在 daemon worker，約每 50ms 處理一次；progress 與 progress_state 每 prompt 合併、最多 5Hz，配置 seq 前合併。terminal 移除該 prompt 尚未配置 seq 的舊 progress。

worker 每秒做本機 queue/history 對帳及期限維護，沒有 HTTP 輪詢或重新提交。它先取得原生 queue 快照，釋放 queue 鎖後才做維護磁碟寫入；既有明確 cancel／fence 仍在 queue mutex 內維持原子取消的邊界。

- 提交證據期限 30 秒，沿既有 failed／unconfirmed 區分，沒有發出的要求才可失敗。
- 已 queued／running 的正常長生成不因 30 秒或 120 秒被判失敗。
- 120 秒沒有新原生證據僅保存診斷時間，不更改執行狀態；終態 history 可補回遺失的 terminal push。
- cancel 有獨立 30 秒證據期限；沒有確認時保存 cancel_unconfirmed，不宣稱取消成功。
- worker 超過 12 秒未維持活動，只回報 publisher_alive=false；GIL 停滯本身不等於事件遺失，等實際緩衝溢出才記缺口。
- heartbeat 獨立讀 queue：成功為 queue_readable=true，失敗為 queue_readable=false、running=null（不能當作空集合）；不改 log_consistent。
- maintenance 例外只在 heartbeat 的 maintenance_error 回報 message 與 observed_at；下一次維護成功清為 null，不記缺口、不改工作狀態。

## 事件與容量

事件 envelope 含 `schema/server_instance/epoch/seq/kind/entity/scope/prompt_id/receipt_revision/created_at/payload`。entity 帶來源表及原 ID；原生事件為 execution:prompt:event:node，prompt_id 缺失時維持空值、不猜最後執行項目。entity 的 JSON UTF-8 編碼最多 1024 bytes，並為分頁 envelope 保留 4096 bytes，避免 snapshot 的 after／next_after 重複識別值造成無法前進。receipt_revision 對 entity 單調，不能把晚到 queued 當作覆蓋 terminal 的指令。

主要種類：operation.created/prepared/submit/submit_observed/queued/receipt_changed/terminal/cancel_requested/cancel_confirmed/cancel_unconfirmed/missing/retired、workflow.transfer_available/transfer_acked/state_changed/receipt、input.changed/claim_received/apply_receipt、result.collected、`log.gap`（Qt-free 協定常數 `LOG_GAP`），以及 `native.<原事件名>`。完整狀態以收據／history 為準，原生 executed 不是整項成功。

每 epoch 事件流最多 100,000 筆、64MiB 編碼事件、24 小時，任一先到即刪最舊前綴。系統時鐘倒退時仍以最後過期 seq 為界刪除整個前綴，不在序號中間留下缺口。oldest_seq 是目前最舊 seq，空流為 latest_seq+1。容量計算由同交易計數器維護。單 payload ≤64KiB，超大原生輸出或大型收據改回 digest/ref/fetch_required，圖片 bytes 不入 log。

這些是**事件流**限制，不是整個 SQLite 檔案、WAL 或耐久收據的磁碟上限。原生投影跟著 floor 清理；業務收據與 fence/tombstone 不跟 24 小時事件 TTL 清除，避免 ID 被重新使用。snapshot 最多同時 4 份，保存 60 秒；過期在下一次 snapshot 請求清理，故最多保留 4 份投影副本。後續若清理收據須保留拒絕重送的 tombstone。

## 讀取協定

| 端點 | 行為 |
|---|---|
| GET events?since=N&wait=0..8 | 最多 128 筆、整個 UTF-8 JSON ≤256KiB。回 from_cursor、covered_to、scope_digest、oldest_seq、latest_seq、events、log_consistent。最多 8 個同步等待者；等待在 worker，不阻塞 Qt 或 aiohttp loop。 |
| GET events/heartbeat | epoch、latest_seq、oldest_seq、running、queue_readable、publisher_alive、maintenance_error、log_consistent、observed_at、期限。心跳本身不配置 seq、不寫空閒事件。 |
| GET events/snapshot | 首次原子複製收據投影，回 snapshot_token、固定 as_of_seq、receipts、next_after、done；後頁帶 token 與 after。與 events 同樣的筆數／byte 上限。 |

這些端點都以 `/prompt_studio/` 為前綴。since 在 floor 前回 HTTP 409 與 reset_required；未來游標、錯身分／scope、過期或偽造 snapshot token 拒絕。covered_to 表示已掃過的全域範圍，允許跨過屬於別的 scope 的 seq，但未放進頁面的匹配事件不能跨過。使用 `comfy_event_protocol.validate_page` 驗證身分、順序、範圍與 byte 預算後，後續 transport 才能持久保存游標。

`log_consistent=false` 本身不是 long-poll 的返回或喚醒條件。沒有新事件時仍等滿 wait；事件 commit（包括 log.gap）才喚醒重讀，回覆 header 如實帶當下的一致性。一般純讀交易與缺口重試失敗不 notify，不產生不一致忙迴圈。

snapshot 各頁的 receipt 內容固定，新的事件不會修改已複製的頁。最後從 as_of_seq 續讀 events，逐 entity／revision 冪等合併；若這時游標已過期，再取新 snapshot。重複請求同一 token/after 可重讀，直到 TTL。snapshot 內的收據可能來自前 epoch，保留其原 epoch／seq 作證據；它們是目前收據的 snapshot，不可當成當前 epoch 的新事件直接套進事件游標。

## 取消、missing 與停止追蹤

native 與 frozen 都持久保存 cancel_requested，排隊中精確比對 prompt、marker／payload 後才移除，移除後保存 cancel_confirmed；沒有 interrupted 也能對帳。執行中只中斷已核對的工作，等待原生 interrupted 或精確 history 的取消證據。完成與取消競爭時保留兩者證據，由 reducer 依收據與 history 決定終態，不能偽造 interrupted。

原生 recovery／abandon 以及 frozen 的 `/workflow/queue/recovery`、`/abandon`、`/cancel` 都沿用本機 token 的 POST 安全規則。frozen 請求使用 attempt，native 使用 id。missing fence 與 receipt 更新在同一 SQLite immediate transaction、queue mutex 內重新讀取；已接受但仍可能晚到 enqueue 的 submit_inflight 不准 fence。Service 重載保留同一程序的在途標記，真正程序重開才解除舊程序的標記。fence trigger 會拒絕晚到 prepare／submit 寫入。

只有可核對原 owner、queue/history 並排除在途提交後的 missing 才給 fenced 證據。原收據整列遺失時，須提供原 library／workspace／prompt，並與持久 event_receipts 投影核對；投影也遺失則為 unavailable。同程序的 submit_inflight（含擴充重載）仍阻止 fence，只有 queue/history 找到原 prompt，或 submit_process 證明屬於已結束的舊程序，才可排除提交窗口。在 queue mutex 與同一收據交易內重查後建立 fence；單純查不到或 unavailable 不等同 missing。retired/abandon 留 durable tombstone，後來訊息不恢復派送資格。桌面既有「停止追蹤此筆」接受這份證據後才在本地結案。

## 先前階段的原生來源紀錄

下表是先前通道階段留下的固定來源紀錄，**不是 result_manifest_v1 的來源契約**。結果 manifest 這一階段沒有原生 ComfyUI 原始碼或執行證據；cache、全部 cache、group、subgraph、occurrence 與 nested path 的原生對應一律未定。不能用下表、人工 fixture 或 display_node 推導結果的語意鍵或完整性。

| 0.38.0 來源 | 核對結果 |
|---|---|
| execution.py add_message / execute_async | start、cached、success、一般 error 有 prompt_id；非 broadcast 事件在沒有 client_id 時可不送。interrupted 為 broadcast。 |
| execution.py execute / asset_enrichment.emit_cached_output | executed 帶 node、display_node、prompt_id；多圖同一 output.images，快取也可送 executed，快取沒有 UI 時 output 可以是 null。 |
| execution.py subgraph expansion | unique node 與 display_node 不同，父輸出與展開子圖可交錯；不能把 display_node 當全域輸出鍵，也不能假設一 node 只 executed 一次。 |
| execution.py execution_block_cb | ExecutionBlocked 可以先 error，再整個 prompt success。要保留 history 終態與訊息，不用單筆 error 猜整項結案。 |
| main.py hijack_progress / prompt_worker | progress 含 prompt/node（必要時 fallback），執行結束的 executing-null 帶 prompt_id。 |
| server.py websocket_handler | 重連 executing 可以沒有 prompt_id，並走 async send，**不經 send_sync hook**。不能拿它當可靠補讀來源。 |
| server.py queue_updated | status 不帶 prompt_id，只是 queue 總覽提示。 |
| execution.py PromptQueue.delete_queue_item | 刪除排隊項目不經執行錯誤處理，因此不保證 interrupted。 |

固定來源 hash 與來源衍生 fixture 在測試檔中保存；未執行 ComfyUI 本體。第三方 group node、其他後端／前端版本、真 GPU 與真 WebSocket 尚未驗證，見[配套矩陣](../guide/comfyui-compatibility.md)。單一 hook 與服務重建有合成測試；不據此宣稱 ComfyUI 路由整體可熱重載，實際更新仍須重啟後端。

共用協定由 `releases.SHARED_MODULES` 與建置流程產生，不能手動修改 shared 副本。C3 接管以下持久原生輔助操作；早到節點結果尚未啟用。manager、md、chat 的編輯保存不因此改走 ComfyUI 執行通道。

## 耐久結果 manifest v1

擴充的 `result_manifest.py` 提供 `result_manifest_v1`：**final 由原 prompt 的成功 history 封存；原生 executed 的早到候選僅盡力提供顯示**。只有預設 observer、可用的 queue history reader、存活的 result worker，以及耐久版本、原子 outbox、分頁、受限資產讀取都接好時，`events/capability` 和單使用者模式的 `desktop/status` 才宣告能力。注入測試 adapter、缺 history reader 或 worker 已停止時不宣告。早到候選可能不完整；cache 節點沒有 executed 就不會有早到候選，仍由 history 收齊，不依賴原生 occurrence 或 subgraph 映射。

有 `execution_events_v1` 而沒有 `result_manifest_v1` 的擴充，PCS 新 operation 維持既有最終收圖，D 功能關閉。完全沒有 C1 通道能力的擴充仍按既有規則要求更新、禁止新生成。PCS 候選表使用 **event_apply=2**，由 S5B 先複製再搬遷；C2/C3 本身沒有佔用這個版本。已開始 manifest 收取的 operation 若能力暫失，保留待確認，不中途改用另一份結果。

PCS 的 `comfy_result_fetch` 在 worker 讀取 bind／分頁／asset，拒絕 redirect，核對 owner、operation、prompt、graph、server、epoch、revision／checksum、完整 ordinal／digest。`result.manifest` 有獨立耐久通知路由，不隨 DeliveryFilter 的 executed／progress 合併而丟棄。單一背景 writer 先持久化 candidate intent；worker 從同一份 bytes 驗證 hash、PNG 邊界並原子保存原圖。最多 4 路收取（不超過可用 CPU 數減 1，單核心至少 1 路），候選每批至多 32 筆、封存 history 每批至多 64 筆 ready 依序提交，完成後立即接續下一批。writer 只寫候選表，WAL／FULL synchronous、busy_timeout=8 ms；取消與 generation 在提交前核對，摘要在提交後交 GUI。GUI 不等背景 future，鎖衝突走原 DB retry，job／文件／final 接受仍由 GUI 原交易負責。

final metadata 與 journal 內容編碼由 worker 準備；GUI accepting 交易重讀候選 ready／owner、成功證據與封存 header，並以 job／run fence 拒絕過時準備，然後一起寫 result、frame 與 continuation。候選通知可在短窗合併，成功證據略過合併窗。PCS 採方案 C：候選只背景預取原圖，不建立預覽、不通知提前顯示；最終接受後沿既有投影一次顯示完整 history 集合。這些是本機排程與顯示方式，不更改 manifest 或 C1/C2/C3 接受條件。

新寫原圖先 flush／fsync，再原子替換內容定址的檔案，才提交 ready receipt；未完成收取重開時，worker 重核原圖 hash，缺漏或不符就重取原 operation。原圖是 final gate 的必要資產。縮圖與 metadata 是可重建的衍生檔，原子寫入但不逐檔 fsync，`receipt.derived` 在背景補上兩檔 hash；不阻擋 frame。bind 只透過 `derived_pending=1` 的部分索引處理 frame 已提交而衍生收據未補完者；已有 derived hash 的歷史不讀檔、不重 hash。顯示失敗或實際使用時 hash 不符，由單張 asset 索引交 worker 修復。早到預覽使用 worker 的 QImage，只在既有位置合併更新一次。收取路徑不在 GUI 讀圖、解碼或計算圖片 hash。

PCS final 接受在同一 accepting 交易刪除該 manifest 的 candidates 列，保留被 final 重用的檔案。history 與失敗候選沿既有 job／attempt 保留時機清除，孤兒以每次最多 128 列的 keyset sweep 處理；未結案 index、results_pending 及尚持有 frame 的 operation 不清除。不新增檔案清理，亦不改擴充端 manifest 的保留契約。並行 chunk 失敗時，可重用其他 chunk 已驗證寫入的原圖。

`channel_result_candidates` 以 manifest、source、semantic 為 PK；source 區分 candidates 與 history。候選不能作為最終結果；PCS 僅拿來背景預取，不寫 execution projection 或 StageRunner.results。final 的同 node／檔案描述／hash 可重用本機原圖，仍由 worker 重新驗證。成功證據存為 job.result_terminal，封存版存為 job.result_manifest；`results_pending` 保留在原狀態欄位，並納入重開、保留、未結案與 reconcile。三項條件（成功、history seal、所有本機 asset ready）齊備才進原 accepting 交易，一次接受 result、frame 與 continuation／event receipt。失敗、interrupted 或確認取消不推進 frame，候選紀錄保留；收圖／DB 失敗不重送 prompt。

本機已 failed／cancelled／interrupted 且沒有成功證據時，ResultFetch 不安排 manifest 補查。epoch／連線恢復或明確更新僅從既有未結案 operation index 核對，不掃全表 DISTINCT，不重查已確認的歷史 failed／cancelled。新的歸屬正確 manifest 通知仍可觸發一次核對，重播以事件收據去重；不重啟已取消的圖片複製，終態確認仍由原 reconcile／取消流程負責。取消、關閉及 owner 變更會中止工作 token 與在途 socket；晚到工作不得寫 ready 或推進 frame。

### 身分、來源與順序

manifest 的 `schema` 是 `pcs.result-manifest.v1`。完整 `identity` 欄位如下，任何一項不同都不能接續原 manifest：

```json
{
  "owner": {"library_id": "…", "workspace_id": "…", "document_id": "…"},
  "server_instance": "…",
  "operation": {"kind": "native", "id": "…"},
  "prompt_id": "…",
  "graph_digest": "64 lowercase hex SHA256 characters"
}
```

`kind` 僅為 native 或 frozen。`manifest_id = SHA256(canonical(identity))`；同一 server/operation 只容許一份原身分及 frozen path map。canonical 是 UTF-8 JSON、key 排序、無多餘空白、不容許 NaN，和事件 `encode` 相同。`graph_digest` 對實際保存的 `operation.payload.prompt` 用此算法計算，不使用當前 Web graph、畫布名稱或某張 PNG 的 graph 替換。來源 `source_epoch` 只是證據，**不在 manifest、item 或來源重播的語意唯一鍵內**。

現有原生提交收據可以核對 snapshot.library_id、snapshot.state.workspace、prompt 及實際 submitted graph，但沒有 PCS document_id。`POST results/manifests/bind` 由持有本機 token 的 PCS 明確提供完整 owner mapping；擴充依原 native_operations 或 queue_attempts/queue_captures 收據核對 library、workspace、prompt、graph，再凍結 document_id。這個 document mapping 是 PCS 的聲明，不冒充由 ComfyUI 推得；同 operation 再 bind 不同 document 拒絕。缺原收據或 owner 不能 bind，不採 active tab。D-B 必須以原 document owner 呼叫此端點，不能改用目前文件。

item 同時保存 `image` descriptor、`format=png`、size 和 asset_sha256。原生路徑使用兩份獨立集合：`item_source=candidates` 的 revision 只讀候選；`item_source=history` 的 sealed revision 只讀 final。即使兩者 ordinal 相同也不是同一項，不能把兩份清單接起來或把舊候選補進 final。

- 原生候選以 prompt_id、原始 `node` 與 `output.images` 建立。相同 prompt/node 的 executed 依耐久 observation sequence 給顯示用 occurrence（字串 `"0"` 起算）；相同 bytes／完全相同 callback 也可代表第二次執行，不能按內容去重。capture ID 在 worker 重試時保持不變，重開由已落盤 sequence 繼續，不重新編號。
- 候選 node_path 是原事件明示的不透明字串陣列，沒有時為 `[node]`；不切分 node/display_node 字串猜路徑。迴圈、group/subgraph flatten 的原生語意未定，**顯示可能合併或缺漏**，不影響 final。
- final 完全重新列舉 history.outputs，依 history 的 node 插入順序及每個 images 陣列順序給 ordinal；只選既有最終收圖同樣支援的 PNG，不套 64／120 張上限。node_id 使用 history 的原始 key，node_path=`[node_id]`、occurrence=`"history"`、output_slot=`"images"`、item_index 為該輸出陣列下標。不要求 final key 對應候選或 frozen graph 的 nested path。
- 有明確來源契約的 server adapter 可呼叫 `register(identity, paths)` 及 worker 的 `ingest(packet)`。packet 包含 identity、source_id、source_revision、evidence_ref、items、可選 seal；`ResultManifestBuffer.adapter` 在 worker 執行。這是受信任的內部接口，沒有接受 HTTP 或原生 event 自稱 trusted 的入口。測試注入的 adapter 僅代表人工來源契約。
- 已提交 source receipt 的同 source_id/revision canonical packet digest 不同會拒絕。digest 不含 source_epoch；相同來源重播不新增版本、item 或通知。不同來源只重送相同 items 也是 no-op（不新增 source receipt）；同語意 key 改 asset 或 ordinal 拒絕。每個有變更的 header revision 保存 source_evidence 的 id/revision/ref/digest，供追查該版本的來源。
- 內部 adapter 的唯一鍵仍是 `(occurrence, node_path, output_slot, item_index)`，ordinal 唯一，且 node_path 必須在其 frozen path map；原生 event 自稱有 adapter 欄位不生效。
- 原生 candidate/final 都提供 `asset_key=SHA256(canonical([node_id,image,asset_sha256]))`。D-B 在同一 manifest 內按相同 node、描述（filename/subfolder/type）及 hash 重用已複製且再次驗 hash 的 asset，避免重複下載；不相符則只採 final。擴充本身不複製檔案，也不以 hash 去掉不同的輸出位置。final 每次重新驗實際 bytes，不以同名檔假定相同。

### 版本與 final seal

每次有效 item／seal 變更新增 immutable header revision，保存當時 item_count、items_digest、source_epoch 和 checksum；item 保存 added revision，舊 revision 的分頁集合永不變。checksum 對不含 checksum 自身的完整 header 計算。items_digest 是按 ordinal 排序的完整公開 item 陣列 canonical SHA256，包含語意欄位、ordinal、image descriptor、format、size、asset_sha256，不含檔案 stat 或 transport epoch。

原生 terminal success 喚醒 worker 查該 prompt 的 history。ComfyUI 在 execution_success 之後才以 task_done 原子寫入 status／outputs，所以當下缺 history 會等待補讀。seal 必須核 history prompt tuple 的 prompt_id、frozen graph digest、成功且 completed 的 status，以及 outputs 字典；final count/digest 依重新驗證的完整清單計算，ordinal 恰好為 `0..expected_count-1`。封存保存 history_digest；`sealed=true, terminal_state=success, result_state=ready` 才是成功結果。bind 晚於事件、重開或遺失 terminal callback 時，也可由成功 history 的耐久 terminal 證據補齊。error／interrupted 的原生證據保留候選及 `sealed=false`，不進成功 seal。

內部 adapter 原有 `complete=true`、terminal_state、expected_count、final_digest 契約仍可用；其 error/interrupted seal 為 partial，不是原生成功或 Stage 成功。測試 adapter 不屬於生產能力宣告路徑。

合法 zero 必有 `zero_output=true`、expected_count=0、final_digest=SHA256(canonical([]))。原生 history 必須明列非圖片輸出（例如 text），覆蓋 requested output nodes，或明列沒有 requested outputs；原本已觀察到圖片、已知 SaveImage／PreviewImage 缺結果、只有空物件或空 images 都不能證明 zero。成功 history 的已知頂層圖片輸出缺漏也等待補讀；未知/custom/subgraph 的非空圖片清單直接以成功 history 為權威，不猜 flatten 對應，與原最終收圖一致。history 不提供完整性以外的自訂節點語意，無明確零圖證據時保持 `sealed=false, expected_count=null, result_state=results_pending`（結果待確認），包括全部 cache。sealed 後禁止附加新項目或改變終態／digest。final seal 只是擴充結果完整性，PCS 仍要驗原 terminal、成功複製全部資產，再在原接受交易處理 frame 與 continuation。

### 捕捉、交易、缺口與保存

原 send_sync hook 先把結果相關事件交給獨立 `ResultManifestBuffer`；既有 executed/native event projection 及 Web legacy observer 原樣保留。hook 只作有界結構複製及記憶體入列，不做 SQLite、序列化、hash、讀圖或網路。每 capture 最多 512KiB 結構預算（字串以每字元 4 bytes、其他節點 32 bytes 計），深度 20；佇列最多 256 captures、8MiB，worker 每輪最多 8 captures、單一 writer，逐張驗檔，單圖最多 25MiB。這些是捕捉上限，不是 64／120 張顯示上限。

超量或資料無法複製時，以有界 pending gap（最多 16 個已知 prompt 加一個總括項目）保存缺口，由 worker 在 integration.sqlite3 寫 result_gaps 和 log.gap。不能把截斷的 descriptor 當完整清單；對帳只向原 prompt 的可信來源取得證據。儲存失敗保留未提交 capture，至少 1 秒退避；不重送生成。硬中止前尚在記憶體的資料沒有耐久保證，由新 epoch 強制對帳，未 seal 的清單始終待確認。若無精確來源可補齊，不能宣稱恢復成功。

manifest items、immutable header、來源 receipt 及 `kind=result.manifest` outbox **同一筆 integration DB 交易**；commit 後才沿原 EventLog 喚醒。payload 只有 manifest_id/revision/checksum，沒有圖片或完整項目；envelope.prompt_id 留空、entity 為 `result_manifest:<id>`，避免舊 PCS 把結果通知當成原 operation 的生命週期／終態證據。D-B 依新 kind 補讀及核對完整 identity。既有 native.executed 的 payload／壓縮契約不變。

預設 observer 先耐久保存 result_observations，再依 sequence 投影 result_candidates、顯示 occurrence、header/outbox；交易失敗重試同一 capture，不多建 occurrence。final 獨立寫 result_items，舊候選 revision 仍可固定補讀，不升格為 final。progress/progress_state 不進 manifest worker、不產生 manifest DB 寫入。既有 C1 NativeEventBuffer 的進度 outbox（最多 5Hz）維持原行為，這個限制不是宣稱整個 C1 integration DB 從來不記 progress。

result worker 在啟動做一次 pending manifest 恢復；bind、capture、stop 以 wake 事件喚醒。沒有工作時無 timeout，閒置每秒定時喚醒為 0；未完整 history 以 0.1／0.5／1／5／30 秒退避重讀原 prompt，儲存失敗至少等 1 秒。只要結果仍待確認就保留補讀，不重送生成。這不改原 C1 event worker 的維護計時。

result_manifests、result_versions、result_items、result_candidates、result_native_state、result_occurrences、result_sources、result_observations、result_gaps 都只在擴充 integration DB。事件流 floor、snapshot TTL、epoch 重開不刪它們；本版沒有 manifest GC／配額回收，不以最近 120 張或 Web 選取 64 張裁切。已註冊的 output/temp 檔案仍由 ComfyUI 管理，擴充不搬移、不延長其實體壽命；移走、覆寫或刪除後精確讀取會拒絕，不能退回最新同名圖片。磁碟失敗會暴露 gap／待確認，不回報完成。

### 讀取端點與安全

以下皆在 `/prompt_studio/`，經原 `event_route`/`local_route` 的 loopback peer、Host、Origin、Sec-Fetch-Site、本機 X-Prompt-Studio token 及單使用者模式檢查。GET 也要 token。每次必帶目前 server_instance、epoch、scope、scope_digest，scope 必含與完整 owner 相等的 library_id/workspace_id；owner 是上述三欄的 JSON。跨重開需重新握手，語意身分與 item ordinal 不變。

| 端點 | 查詢／內容 |
|---|---|
| POST results/manifests/bind | JSON body 為完整 identity；query 的 owner/scope 必相符。核對既有收據後取得初始 header；重播不另建 manifest。 |
| GET results/manifests | 按精確 owner 列 header。首次回 as_of（全域 version sequence），後頁帶相同 as_of、after=前頁 next_after。最多 64 headers、256KiB，版本固定於 as_of。 |
| GET results/manifest | 必帶 manifest_id、revision、checksum；after 為最後 ordinal（起始 -1）。最多 64 items、256KiB，回 header/items/next_after/done，以及 expires/asset_token。 |
| GET results/evidence | 同一 manifest reference；after 為 observation sequence（起始 0）。每頁一個 native observation，另附最多 16 個 gap 摘要；空頁 done=true。這是診斷補讀，不是 final completeness；capture 受 512KiB 結構預算限制。 |
| GET results/asset | 同一 reference，另帶 ordinal、asset_sha256、expires、asset_token；回 image/png 原 bytes，Cache-Control=no-store，不接受使用者提供的檔案路徑。 |

asset_token 是以啟動期隨機密鑰 HMAC 綁定完整 identity、revision、checksum 及 300 秒期限的讀取 lease；它不是可替代 X-Prompt-Studio 的授權。過期或擴充重開後重新讀同 revision 頁取得 lease；不修改 manifest。錯 scope／owner／server、revision/checksum 衝突、外來或過期 token 一律拒絕。

資產只能是 worker 登記過的 output/temp PNG，相對 subfolder 和單一 filename；禁止 input、絕對路徑、`..`、反斜線繞路、URL/百分比編碼、Windows ADS、symlink／reparse point（含父路徑）。開檔前後比對 file identity、size、mtime/ctime，驗 PNG signature、SHA256；讀取時再對登記的 stat/hash 驗證。候選及 adapter 有 prompt_studio submission_id 時仍核對；final 的精確成功 history 才能引用 cache 舊圖，允許其 PNG metadata 保留舊 submission_id，不能由 event／HTTP 參數自稱這項權限。缺檔、短讀、檔案被覆寫或重新導向時拒絕，從不回傳「最新圖」。原 `desktop/results`、`desktop/collect`、`node_images` 及 Web 64 張選取限制未修改。

## 原生輔助操作與輸入（C3）

`comfy_aux_operations` 是 open、inspect、capture、apply 的單一接受入口。HTTP ACK 只排入定點對帳；事件與 snapshot 的 `receipt_ref` 也只通知讀回原 `workflow/native/recovery` 收據。收據新增 action、target、request_hash、event_identity、scope，以及取消／fence 證據。需要配套 C3 擴充；舊收據缺少完整身分時維持「無法確認」，不猜歸屬、不重送或退回高頻輪詢。

每次 HTTP 前，在原文件 journal 保存 `aux_version=1` 的 input／aux_operation：runtime owner、原 operation ID、server URL／instance／epoch、library／workspace、workflow target、凍結 request 及 hash、sent、期限與 continuation。新 run 只保存 input_ids；舊 input_operations 副本仍可讀取。request／result／input_images 使用 journal=1 既有 content_paths，不新增 SQL 表或欄位，event_apply 維持 1，document／PNG 版本不變。

只有原 intent 與完整收據的 ID、action、request hash、workflow identity、owner、server 和目前 epoch 相符才接受。無 scope 的 inspect 由 worker 的原 intent owner 索引交付，不廣播給文件。超大收據的事件保留 action 與 ref，內容由有期限的原收據查詢取得。新 epoch 仍先對帳；不能把舊 epoch 回應套到新連線。

接受使用同一連線的 accepting 外層交易，包含終態、continuation 與 channel_applied_events。一般 capture 的 work_queue 加入也在這筆交易；SQLite 失敗全部回滾。文件提交後才通知 UI、喚醒排程、回報 sidecar delivered；重開或重播遇到已結案 intent 不再續行。

| 操作 | 持久續行 |
|---|---|
| inspect | 保存已核對 inspection、解析 profile／parameter_description 與 receipt 後結束。沒有自動套用參數或綁定；手動套用、再次 inspect、衝突核對及 Undo 沿原編輯入口。關閉面板後的晚到結果只保存讀取；期限後不改用存檔副本。 |
| capture，store_result=True | 收據中的 work 與本地 work_queue 同交易，只加入一次。遠端已有 capture、本地尚未加入時由重讀收據補齊。 |
| capture，store_result=False | 只接受底稿的收據、歸屬、期限與 lane；不加入底稿、不在重開後重啟 variant／upload。活著的原請求可以繼續；回覆遺失則停止準備，保留既有工作。 |
| apply | input_batch 保存順序與 continuation；每次接受原 input ID 後才進下一步。foreign_ready、free_state 與原收據同交易；重開後保留 restart pause，不自動重新派送。使用者繼續時以原 attempt／input ID 接回尚未送出的下一步。 |
| open | 保存原生定位結果並釋放 lane，不改綁定或觸發生成。 |

12 秒未取得證據即定點查原 operation；單次讀回有絕對期限，失敗以 1／2／4／8 秒退避，30 秒保存「無法確認」。期限後跨窗採 30／60／120／300 秒退避，300 秒持續核對，不永久放棄。一般心跳不重啟查詢窗；新事件、epoch／連線恢復、明確更新或取消允許立即核對一次，不清除退避或「無法確認」、不釋放 lane、不重送。正在等待的 HTTP 也受截止時間約束，不以回呼一定會到達為前提。120 秒診斷沿既有通道，不將長執行判成 failed。

HTTP rejected 與 failed 收據不同；只有核對原收據，或取消後取得同 epoch 的 missing fence，才能結案。delivered apply 的 cancelling 須等原瀏覽器 cancel_confirmed；已 applied 的收據勝過晚到取消 ACK。純 unavailable、未收到取消確認都保留 lane。刪失的 aux 收據須從持久 event_receipts 核對原 intent，並建立拒絕晚到寫入的 fence；無投影時不得猜 missing。

新操作不再使用 100／300／500ms native/status，也不進 Workspace 的 1.5 秒補查集合。通道健康且有完整 intent 時，desktop/status 仍採 C1 的 8 秒門檻（1.5 秒 timer 合併，實際約 9 秒）；斷線仍由 12 秒通道心跳察覺。舊而不完整的 input／workspace_operations、v5／v6 run_id、legacy apply_queue／cancel，以及待 library 註冊仍維持原節奏。variant／upload、InputBridge、DirectRuns、workflow transfer、Web 背景操作、原生選圖與舊 desktop bridge 不在本段接管範圍。

## 本機一致性收據

開文件／繼續 gate 與原 operation 的對帳入口會核對本機 attempt、job、frame。`stage_invariants` 的 local receipt 使用 server=`local`、epoch=`stage-continuation-v1`、seq=`attempt_id:action`；它不是傳輸事件，不需要對外發布，亦不改 sidecar received/applied。原表的 PK 保證重開與重複核對不多消耗來源、不多推 frame。

正常 collect 將 continuation receipt、完整 result、frame.job 清除與 index 推進放進既有 accepting 外層交易。修復同樣先在該交易檢查 result 身分／圖片與既有 receipt；曾接受的 continuation 不會再次執行。已取消 run 只清原 job 指標，保留結果但不提供下游 continuation。SQLite 失敗回滾全部變更，通知及 job 記憶體發布只在 after_commit。

既有 attempt JSON 增加可選 `submission_intent`（not_sent／issued）、`operation_evidence` 與 `local_issue`／`local_origin`。不新增 SQL schema，不提升 event_apply=1；舊記錄沒有意圖時必須視為不確定。issued 與初始 job 在 HTTP 前同交易保存；耐久 payload 證據只由接受過的 job 保存，重建仍為 unconfirmed。無證據顯示「資料不一致」，缺結果顯示「結果待確認」，未知傳輸顯示「無法確認」。皆不自動派送；只有 not_sent 可 failed 並暫停。

operation_evidence 依 journal=1 的既有 generic content_paths 拆到內容表，避免 status／readiness 標頭隨 payload 變大。完整結果若有耐久 outputs manifest，還須比對 node/index 集合、prompt 身分及圖片來源完整性，不能以少一張圖片的 result 推進 frame。

只寫 `kind=operation.missing` 不構成 fence。事件須包含與其 epoch 相同的 missing_fence 及 checked 證據；缺少時改做原 operation 的定點對帳，不標 retired，也不釋放 lane。

已完成任務的晚到參數收據仍可在同一接受交易保存到 job、parameter_state 與 channel_applied_events；原 prompt／owner 與 receipt revision 必須相符。這條路徑不重收圖片、不再推 frame，也不改終態。retired tombstone 仍直接忽略晚到寫入。
