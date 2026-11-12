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

原生／凍結生成的週期 status 查詢已停用。Workspace 的通道健康、snapshot 已接受，且 client 沒有待完成的 owner 註冊或在途 input／apply／輔助 operation 時，desktop/status 的週期請求至少相隔 8 秒（由既有 1.5 秒 timer 排程，通常每 9 秒）；utility client 也共用此健康資訊。其他情況維持 1.5 秒，明確的重新確認與命令仍即時查詢；input／輔助 operation 的查詢保留到 C2。worker 在 12 秒察覺期限到達時，立即投影既有「正在確認 ComfyUI 連線…」狀態，不等待低頻 status 或它的 HTTP 期限；不因此把工作結案。獨立宿主的 status 頻率不變。

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

## 已核對的原生語意

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

共用協定由 `releases.SHARED_MODULES` 與建置流程產生，不能手動修改 shared 副本。C2 接續本機一致性、取消 run 殘留及 input／輔助 operation；D 才加入早到節點結果。manager、md、chat 的編輯保存不因此改走 ComfyUI 執行通道。

已完成任務的晚到參數收據仍可在同一接受交易保存到 job、parameter_state 與 channel_applied_events；原 prompt／owner 與 receipt revision 必須相符。這條路徑不重收圖片、不再推 frame，也不改終態。retired tombstone 仍直接忽略晚到寫入。
