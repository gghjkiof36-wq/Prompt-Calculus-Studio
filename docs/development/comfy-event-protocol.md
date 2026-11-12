# ComfyUI 持久收據與事件協定

適用於 pre-alpha3 的擴充通道第一段。此段提供事件來源、持久收據與唯讀同步端點；桌面只新增 Qt-free 協定解析，尚未啟用事件 reducer、游標儲存或取代輪詢。需要更新 ComfyUI 端擴充並重啟後端、重新整理網頁；此文件不是已安裝或 GPU 驗收證明。

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

`desktop/status` 同樣附加 `event_channel`、能力名稱與 `extension_info`，保留原 `extension` 與所有舊欄位。安裝位置來自已載入擴充的 `build_identity.LOADED_BUILD.root`，不是由 PCS 猜目錄；自動綁定在後續桌面通道段才啟用。

`server_instance` 存在擴充專用 `integration.sqlite3`，重建 Service 不變；每次 Service 建立產生新 `epoch` 並持久保存。舊 Service 的 writer 會被資料表 trigger 拒絕。每個 epoch 的全域 seq 從 1 遞增，wire 一律使用十進位字串，`"0"` 是初始游標。新 epoch 清除舊事件流、保留收據與 tombstone；必須重新 snapshot，不能接續舊游標。

單一使用者模式才提供此通道。舊 PCS 搭配新版擴充繼續使用原 HTTP 路徑與輪詢；本段及下一段不關閉輪詢。將來切換 reducer 的版本遇到舊擴充，更新前禁止新派送，不提供 legacy 輪詢相容模式。PCS 文件格式本段完全不變，也不建立 `event_apply` 表；後續資料格式搬遷另有版本邊界。

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

消費端（後續桌面通道段）收到 `log.gap`，必須安排**固定 as_of_seq 的 snapshot 加原生 queue/history 對帳**，完成後再接續其後事件；缺口本身不能直接把 generation job／attempt 判為失敗、完成或允許重送。`log_consistent=false` 表示仍等缺口持久化，先定點查證並沿既有 30 秒證據期限處理「無法確認」，不忙重試。看到 true 也仍要處理已收到的 gap；不能只看 heartbeat true 就跳過對帳。epoch 改變、游標過期也照常要求完整對帳。

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

native 與 frozen 都持久保存 cancel_requested，排隊中精確比對 prompt、marker／payload 後才移除，移除後保存 cancel_confirmed；沒有 interrupted 也能對帳。執行中只中斷已核對的工作，等待原生 interrupted 或精確 history 的取消證據。完成與取消競爭時保留兩者證據，後續 reducer 決定最終顯示，不能偽造 interrupted。

原生 recovery／abandon 以及 frozen 的 `/workflow/queue/recovery`、`/abandon`、`/cancel` 都沿用本機 token 的 POST 安全規則。frozen 請求使用 attempt，native 使用 id。missing fence 與 receipt 更新在同一 SQLite immediate transaction、queue mutex 內重新讀取；已接受但仍可能晚到 enqueue 的 submit_inflight 不准 fence。Service 重載保留同一程序的在途標記，真正程序重開才解除舊程序的標記。fence trigger 會拒絕晚到 prepare／submit 寫入。

只有已知收據、可核對 queue/history、排除在途提交後的 missing 才給 fenced 證據。找不到收據或 unavailable 不等同 missing。retired/abandon 留 durable tombstone，後來訊息不恢復派送資格。桌面既有「停止追蹤此筆」的 D1 行為由後續 reducer 段接入，本段不修改按鈕或 PCS 工作狀態。

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

共用協定由 `releases.SHARED_MODULES` 與建置流程產生，不能手動修改打包後的 shared 副本。後續 B 段接共享 transport／inbox，C 段才接 reducer／交易格式與禁止舊擴充派送，D 段接早到節點結果，效能整合在其後；本段不改 manager、md、chat，也不把它們路由到 ComfyUI。
