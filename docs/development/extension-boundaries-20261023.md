# 文件、背景工作與外部服務的擴充接點

本輪在 `9421165` 四項修復版本上整理既有責任邊界。沿用 07 的文件操作導航、Stage 資料流及 [AI／skill 契約](087-ai-skill-contracts.md)，不另建文件格式、Undo 系統、排程器或插件平台。各項程式與驗證固定版本見本輪交付紀錄。

## 文件內容操作

`document_operations.DocumentOperations.commit(operation, label, merge_key)` 是 Qt-free 的內容交易入口。輸入仍是修改候選 state 的既有 helper；通過驗證後才發布至原文件，記入同一份 `document.history`，清除 Redo，並通知原保存／顯示流程。

- `TextCanvas.commit` 是相容入口，委派給同一個 coordinator；素材 Manager 已直接使用 `host.document_operations.commit`。
- 複製候選、版本升級前 checkpoint、資料驗證、內容歷史、合併打字交易、100 筆／32 MB Undo 裁切，均保留原順序。
- MultiCanvas 的 Stage 投影在驗證前；route 通知與已移除連線的 detach 在發布後。最後的 Undo 投影包含既有 runtime 刷新結果。
- 同文件雙視圖使用原 history；手動空稿 `''` 與未編輯 `None`、綁定、排序及原件／副本語義不改。

這一步抽出共用提交核心。Canvas 的顯示同步、特殊 queue／snapshot Undo 恢復及焦點處理仍由原 adapter 負責；並未宣稱全部 Canvas 相依已消除。未來功能可直接提交一次內容操作，不需要切到 Canvas 畫面，也不能另造自己的 Undo stack。

實體保存、另存、改名及搬移仍走 `WorkspaceCommands → WorkspaceFileOperations`／原文件 writer。commit 的同步通知延續 autosave；「提交成功」不等於之後的磁碟保存必然成功，錯誤仍經現有文件狀態呈現。

## 背景工作完成與取消

背景工作分兩類：可以作廢的讀取／顯示結果，以及必須收妥的已完成寫入回執。沿用原 Jobs 的單工與取消 Event，未加入另一個 scheduler。

- 一般讀取回傳原值；取消後的晚到結果仍拒收，避免重新顯示舊搜尋、舊識別或預覽。
- 已有持久副作用的 worker 在原工作完成後明確回傳 `JobOutcome.completed`，錯誤可用 `failed/cancelled`。Jobs 不再用最後一刻取消訊號覆蓋此回執，讓原 owner 完成 catalog、報告或恢復資訊登記。
- 一般工作開始前已取消，不呼叫 worker。只有已由 desktop/collect 接受的 Recent 收藏，用 `finish_accepted=True` 完成原 owner 的剩餘登記；這是既有義務，不是任意忽略取消。執行途中仍由各 worker 在安全點處理取消。
- 成功／失敗回呼收尾均走 finally；即使回呼或錯誤呈現拋錯，也清楚結束該工作。若回呼已開啟下一份工作，不清掉新 owner；關閉中不發一般 idle 派送訊號，已接受收藏依原義務收尾。
- Gallery 等待其它工作釋放時保留原佇列，不先拿走一張再丟失；關閉時停止後續圖片並清除 importing。Gallery 錯誤及備份恢復路徑在關閉收尾時不另開 modal。

採用端：備份、模型複製、Gallery 匯入、乾淨圖片匯出、已接受的 Recent 收集及 CivitAI 下載。各自原本的回復資訊、部分結果、owner 及 receipt 仍保留。這不提供跨程序的通用 exactly-once 保證，也不強制結束工作執行緒。

## 外部服務

`service_adapters` 提供 Qt-free 的 `ServiceRequest / ServiceResult / ServiceError / Adapter`。request 凍結服務、操作、唯一請求 ID、scope 與 JSON 輸入；回覆須對應完整原 request，取得的結果為獨立副本。

第一個實際 adapter 是 `CivitAIBrowseAdapter`，承接現有測試連線、搜尋及版本詳情三項讀取。網路傳輸、認證、回應大小限制與內容正規化仍沿用 `civitai.py`。token 只由 client factory 持有，不放進 request、result 或 repr。安全錯誤保留 kind／status／message，未知例外不直接回顯。

UI 先凍結參數與 owner／token／搜尋版本，再交 worker；worker 內 `execute(...).value_for(request)` 讓失敗與錯配沿原 Jobs 失敗回呼。現有 token epoch、搜尋 serial、detail generation 仍須在回覆時核對。request ID 只證明回覆屬於哪一次請求，不能代替目標目前仍有效的檢查。

這是程序內的服務封套，**不是** [07 的 AI 內容 schema](087-ai-skill-contracts.md) 已實作。未來模型功能仍須依該契約處理來源原文 SHA、scope、snapshot/output revision、Unicode scalar spans、候選審閱，以及採用時再次驗證，再以一次 `document_operations.commit(..., merge_key=None)` 寫入。模型、候選 UI、skill bundle、自動工具執行均未在本輪新增。

## Stage 圖片來源

同次 Stage project 先依原 DFS 規則決定 reader 的圖片來源，再按已解析 root／path／預期 SHA 分組。每組只讀取一份受限 bytes，對該 bytes 驗 SHA，並以 `png_metadata_stream` 解析同一份內容。

- 維持 25 MiB 硬上限，實際最多讀取上限加一位元組；不只信任 stat，也不沿用圖片 capture 的 32 MiB fallback。
- 同來源不同 choice 各自產生結果，根節點及可變內容獨立，不能因共用 metadata 而互相改動。
- 每次 project 對本次接線、stage_reference 或 source_values 實際選中的來源重新驗證；未接線且沒有 override 的 reader 沿用原有內容，不讀檔。不以檔名、mtime 或大小替代 SHA，不使用跨操作快取。處理下一組前釋放上一組原始 bytes。
- 原 PNG CRC、壓縮文字與 metadata 上限、snapshot 驗證、路徑邊界、來源 override、循環及 materialize 順序均保留。

保證是同操作相同來源減少重讀與重解析；不將它描述為所有情境都有固定加速，或整個程序記憶體只用 25 MiB。metadata、獨立輸出與既有 state 副本仍各有成本。

## 驗證與限制

上述接點沿用既有文件、資料格式與執行歸屬；不提供跨程序 exactly-once 或整體記憶體上限的保證。選定來源的必要確認、未解的性能及異常退出情境，集中於 [Beta2 驗證與限制](../releases/0.8.9/validation/beta.2.md)。離屏與合成傳輸不代表真服務、GPU 或完整原生環境驗收。
