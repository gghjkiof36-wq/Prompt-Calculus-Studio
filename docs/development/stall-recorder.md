# GUI 停頓記錄與空通知契約

## 啟動與記錄

一般啟動使用原本的 `QApplication`，不安裝回呼包裝、不建立記錄檔或背景寫入執行緒。需要取證時使用：

```text
python run.py --record-stalls
python run_workspace.py --record-stalls --record-stalls-threshold 50
```

門檻單位是毫秒，預設 50，接受有限正數。單獨指定門檻不會開啟記錄；沒有 UI 或持久設定。現行工作區入口、選擇工作區入口及保留的歷史啟動入口共用 factory；`--check` 不建立 GUI 或記錄檔。

工作區選定後，記錄放在 `<data>/logs/stalls-<UTC 啟動時間>.jsonl`。每行是一個外層同步事件／回呼區間；巢狀事件與已登記回呼合併為同一筆，功能欄位選取區間內耗時最長的已登記回呼。例如：

```json
{"schema":1,"time":"2026-11-12T00:00:00+00:00","duration_ms":62.15,"event_type":"Timer","feature":"stage pump","object_class":"StageRunner"}
```

`time` 是完成時的 UTC 時間，`duration_ms` 是整個區間的 wall time，含巢狀工作，不是 CPU 用量。`event_type` 是 Qt 事件種類或 `Callback`；自訂事件只記 `User`。零延遲回呼可能由 `Timer`、`ZeroTimerEvent` 或 `MetaCall` 投遞；靜態回呼登記補上功能名稱。未登記功能使用 `unregistered`，不能推斷是哪份文件或哪個節點。

功能清單集中在 `performance_callbacks.CALLBACKS`：Stage pump／分段展開、ComfyUI 輪詢與同步、input bridge、工作區排程、Canvas 刷新／歷史投影、運行控制列、變更協調、文件視圖／存檔、自動存檔、外部檔案輪詢、環境動畫與監看器。只在診斷啟動時、建立 owner 前包裝這些入口；不使用通用方法 profiler、stack inspection 或 capture-all。

監看卡的 Lag 仍然是「最近一秒的最大 deadline 延遲」，與這裡的事件耗時不同。記錄器不依賴監看卡顯示，也不改變採樣頻率。長事件可包含同步 I/O、繪圖、OS 排程等待或巢狀事件迴圈；記錄本身不能區分這些原因，不能當作 GPU 呈現時間。

## 容量、關閉與隱私

GUI 只把慢事件送入 256 筆有界佇列；背景執行緒負責 JSON 編碼與檔案 I/O。單檔上限 10 MiB，另保留 `.1.jsonl`、`.2.jsonl` 兩份輪替檔。同次啟動最多約 30 MiB；不同啟動的檔案不自動刪除。佇列滿時捨棄新紀錄，下一次寫入機會以 `event_type: "Overflow"`、`feature: "recorder overflow"` 和 `dropped_count` 記錄數量。紀錄不進入 journal、SQLite 或文件交易。

正常 `aboutToQuit`、啟動入口的 `finally` 與程序退出保護會關閉記錄器。關閉可重複呼叫，通常會排空佇列；裝置失去回應時最多等兩秒，不阻止 PCS 退出。I/O 失敗會停止寫入，不把例外訊息加入紀錄，也不影響生成。異常終止或關閉逾時可能失去尚未寫出的紀錄。

只序列化固定欄位與靜態功能標籤；不讀取 widget 文字、`objectName`、事件 payload、文件／節點 ID、URL、檔案路徑、prompt、圖片、例外內容、locals 或 traceback。物件類別只接受 Qt 與產品類別名稱，其他類別記 `unregistered`。診斷程式未提供任意字串標籤入口。時間與功能使用情形仍是操作診斷資訊，由使用者自行決定是否交付記錄。

## P1 文件通知的過濾契約

`stage_context.execution_scope` 的原子提交與回復流程不變。成功 outer commit 後，只有 `ExecutionTransaction.changes` 非空才發出 `document_changed(..., reason='execution', kind='content')`。這個集合表示「已登記的可能寫入路徑」，不是深度內容比較：已登記但值相同也照常通知。journal-only 工作沒有 document 變更，因此不應啟動內容／歷史投影。

`CanvasRefreshScheduler.notified` 同時拒絕空欄位集合與既有 `derived` 通知。非空集合、`ALL`、未知欄位維持原路徑；多個 host 各自失效、各自呈現。Stage 的進度／結果訊號、ComfyUI 輪詢、暫停／繼續／取消／重連不靠空文件通知推進，仍照常發送與處理。`stage_batches.expand` 在解碼 batch 前先讀 run 的持久狀態，暫停時略過解碼；仍保留原有後置狀態檢查。

設定比較只排除 `comfy_count`，因為它是下一次提交數量，不影響現有卡片。font、theme、size 及任何未知設定仍參與比較，依原規則全量失效；內容差異的 S4 full fallback 完整保留。
