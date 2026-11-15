# 預排程窗口、等待狀態與文件註冊

此契約適用於目前 Stage 預排程與 Workspace 文件 FIFO。C1 的事件接受格式與有界對帳見[通道協定](comfy-event-protocol.md)；先行窗口與 SCHED-A 的註冊順序不變。

## 通道接受與派送資格（C1）

原生／凍結生成不再由每次 desktop/status 引發 status／queue／history 輪詢。共享通道收到終局後，由原 operation 的收據、history 與最終收圖確認；接受 receipt、attempt、parameter_state、結果及 frame 同外層交易，成功後才喚醒 scheduler。取消 HTTP ACK 遺失時，cancel_confirmed 或定點收據仍可清 cancel_requested，保留手動暫停，釋放原在途權。

新 epoch、log.gap、不一致、心跳不符與啟動未結案，先查原 operation，再固定水位 snapshot，期間阻擋新派送。30 秒證據不足顯示「無法確認」，保留在途權；120 秒無進度只記診斷。fenced missing 可用既有「停止追蹤此筆」本地 retired，unavailable 不行。舊擴充缺少事件能力時顯示「需要更新擴充」，原任務仍可有界收尾。手動／restart pause、保留項目與全部繞過不因此自動解除。

無事 pump 不掃 inbox 或本機一致性；worker 每個 owner 有一頁交付上限，progress 不寫文件 receipt。input／輔助 operation 仍沿用原有路徑，通道接管另行實作。

## 本機一致性與已取消流程

`stage_invariants.InvariantChecker` 只在開文件、明確繼續前及原 operation 的對帳要求執行。開文件／繼續只查 journal 標頭及 frame.job，指定對帳以 PK 查一筆；不掃 retained queue payload。健康任務只核對 job 與 run 標頭。`pump` 不呼叫 checker。

| 不一致 | 接受規則與既有 Stage 狀態 |
|---|---|
| submitted 缺 job | 同 operation、workspace、server、run 的耐久 payload 與 prompt 證據才重建 unconfirmed 投影；仍只核對原 prompt。無證據顯示「資料不一致」，保留在途權。 |
| complete attempt 的 frame.job 未清 | 同一 accepting 交易檢查完整 result、來源檔與 continuation receipt，合併結果並前進一次。缺結果顯示「結果待確認」；已有 receipt 卻回退的 frame 顯示「資料不一致」。 |
| preparing 缺 job | 新 attempt 的 not_sent 意圖在準備前保存；進入 HTTP 前，issued 與 job 同交易提交。只有 not_sent 能轉 failed＋paused，等待明確 retry。issued 或舊紀錄缺意圖都顯示「無法確認」。 |

修復 receipt 使用既有 `channel_applied_events` 的 local namespace；不屬於伺服器 epoch，不推進 inbox 游標，不進 Canvas Undo。SQLite 回滾時不發布 job 記憶體、不通知、不推 frame。手動及 restart pause 保留；修復自身不呼叫 pump，也不重送。

Runtime 的未結案檢查包括 preparing／submitted／unconfirmed attempt，避免 job 遺失時釋放 lane。通道仍由 generation 的未結案索引收集 operation，不依 open_runs。已取消 run 的 operation 得到終態時，只清 frame.job／cancel_requested，保持 run cancelled 與 batch_cancel_resume 的原值；不復活流程、不合併取消後的下游 continuation。

## 先行窗口

`stage_batches.expand(runner, capacity)` 的容量（目前傳入 20）是每個 scheduler 依**目前組別優先順序**準備的可執行窗口，不是接受的點擊數、歷史 entry 數或保存上限。

- 先檢查 group 是否取消／繞過，以及 entry 或 batch head 的 run 是否為 `running`／`waiting`，再計入窗口。
- 可推進組的 `waiting`、`running` entry 計入；`retained`、paused／route run 和繞過組不占準備額度。
- deferred head 不預扣額度。遇到尚未就緒的 segment，僅扣除本次真正寫入的 entry 數，後方可準備組仍可使用餘額。
- 優先順序改變時，較低優先組已準備的 entry 仍保存；它們不搶占較高優先組的準備窗口。因此**磁碟上的已展開列總數可能超過 20**，不能用刪除、重寫或取消低優先列維持列數上限。
- 每次展開仍受原有短時間片／單項上限約束。snapshot、segment、expanded 游標及 batch_control 在原交易內更新；未推進組的快照、未展開數、供應游標與順序不變。
- 恢復原接線並明確繼續時，沿用 `restore_retained` 還原 waiting；不自動改接線、解除繞過或重送已接受的 prompt。

`stage_readiness` 只讀取既有狀態。它不決定已接受任務是否安全結束：`workspace_runtime.unfinished` 仍保護 submitted／unconfirmed、取消待確認、input mutation 及其他未結案 operation 的所有權。

熱路徑只保留單次呼叫內的查詢結果：expand 一次讀取所需 run 的狀態，預先依 scheduler／group 分組 heads 與 entry 數量；不逐列查狀態或對每組重掃全部 entries。文件資格判斷先檢查已有的 preparing／applying 等工作，再以 journal header 投影取得 run 狀態與最上層 frame，不載入 frozen scene。等待狀態以每次卡片刷新共用的 controls／run 狀態推導，文件排隊文字也只讀一次。沒有跨 Qt turn 或跨交易的快取，因此暫停、繞過、繼續或接線變更後，下次呼叫立即讀到新狀態；完整 journal 解碼及執行前驗證仍由原執行器負責。

## 文件 FIFO 與顯示

Workspace 的可推進判斷沿用 preparing、applying、apply_queue、直接輸入及既有非 Stage 工作，另檢查 Stage run 的實際進度：

- paused run 本身不提供新的派送資格；未結案任務仍可保有 FIFO owner。
- 已初始化、沒有 active entry 的 loop 若只剩繞過的 entry／snapshot／feed，不提供派送資格。沒有任何剩餘工作時，仍允許 interpreter 收尾。
- waiting run 有可推進的組且 scheduler 未手動暫停時，允許取得文件資格，讓原有 `promote_waiting` 在 pump 裡決定提升與順序。
- 不可推進且沒有在途任務的文件，不重新插入 FIFO ticket。這避免輪詢反覆新增／移除票據、持續重啟下一份文件的零毫秒計時器。

顯示只使用現有運行按鈕 tooltip 與預排程組別狀態，不新增控制或持久狀態：

| 條件 | 顯示 |
| --- | --- |
| 文件沒有可推進／在途工作，剩餘組全部繞過 | 已繞過 |
| 文件沒有可推進／在途工作，剩餘組有 retained 或 route 暫停（包含零已展開） | 等待：保留項目 |
| 文件已有 FIFO 等待票 | 按鈕 tooltip、無活動項目的組別：等待其他文件；原按鈕仍為已排隊 |
| 組內仍有提交、準備、結果收集、未確認、取消待確認或失敗 | 保留原活動／錯誤狀態的優先級 |

組別的取消、執行後繞過等既有顯示優先。運行按鈕的「繼續」及其動作保持原意義；保留項目的 tooltip 不被一般繼續提示覆蓋。全部繞過不等於取消或完成；解除繞過後沿原快照繼續。

## 文件註冊 gate

`WorkspaceComfyClient.register_document_library` 為每次註冊建立唯一的記憶體 token。建立 token、`persist()` 與送出 config 位於同一個 try 區塊；通用 `_Transactions` 不吞掉 after_commit 例外。

成功與失敗回覆共用有 token 身分檢查的終止入口：

1. 只有 token 仍屬於本次註冊時才處理；重複回覆、已失效回覆不得改動文件、通知、registered_client 或另一個 token。
2. 註冊開始時撤銷先前的 registered_client；僅 config 成功後才設為本文件。
3. 本地 persist／送出例外、非同步 config 拒絕與既有 transport timeout 都走相同失敗路徑。在原 journal 交易保存本文件 running／waiting run 的 `paused`、`pause_reason=error` 及錯誤訊息，保留 entry、attempt、job 與 prompt 身分。
4. 一次終止只顯示一次失敗通知；不逐 run 重複通知。
5. finally 釋放本次 token、發布狀態、喚醒 chain 與 Workspace scheduler。存檔／連線恢復後，使用者沿原「繼續」動作重試註冊；已接受的 prompt 仍收取原結果，不重送。

程式責任：`stage_batches` 管準備窗口，`stage_readiness` 管唯讀推導，`workspace_scheduler` 管文件資格與 FIFO，`workspace_connection` 管註冊終止，`run_controls`／`stage_widgets` 顯示狀態。沒有新的資料流、頁面或協定；程式地圖可補上 `stage_readiness` 的依賴關係。

回歸入口：`test_stage_sched_readiness.py`、`test_workspace_registration_gate.py`、`test_sched_cross_types.py`，以及既有批次快照、schedule groups、workspace execution 與取消測試。Qt offscreen 與合成 transport 證據不代表真實 ComfyUI／GPU 驗收。

## 原生 input／aux 的就緒與接受

C3 的 open／inspect／capture／apply 先保存原 operation intent，再送 HTTP。回覆只觸發有期限的定點讀回，不能直接清掉 lane；`comfy_aux_operations` 核對原 runtime owner 與完整收據後，在文件接受交易結案。已取消 run 的 input 仍由未結案索引追蹤，不依 open_runs。

run 使用 input_ids 關聯；原 input_operations 仍可讀，但不再雙寫副本。`comfy_aux_inputs` 的等待批次只在使用者當次操作、註冊完成、前一步提交後或明確繼續時喚醒；閒置 pump 不掃描歷史 input_batch。暫停、restart pause、取消與 S8 繞過都不提供新的派送資格。

foreign apply 完成後的 continuation、free_state 與原 receipt 在同一交易保存。重開不自動續送；只有 submission_intent=not_sent，且 input_batch 與各 input 的 owner／workspace／server／continuation 相符，才是合法的準備等待。這項證據也保護通道能力回覆之前的 legacy observe。已經 issued 的 attempt 即使保留 foreign_batch，仍須走 S5／S7 檢查，不能因按繼續再次提交。30 秒證據不足顯示「無法確認」，保留在途權；取消要求不是取消終態。

inspect 只保存讀取結果與參數描述。使用者按套用仍走原本的再次核對及 Undo 編輯交易；底稿 capture 不加入等待工作，也不在重開後恢復圖片 variant／upload。細節見 [通道協定](comfy-event-protocol.md#原生輔助操作與輸入c3)。
