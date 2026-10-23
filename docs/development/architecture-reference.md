# 架構設計參考

[開發指南](README.md) · 基準：0.8.9 Beta2，公開來源 `5aa00c146281d96dd9d12f45ae020cfbe6e9575e`，功能來源 `6f03c1658dbea4ee89bed4b91c21f0c57a9dc12a`。

後續功能優先沿用現有文件、交易、執行歸屬及投影邊界。已核範圍沒有支持整體重寫的證據；需要改善的是具體接點的責任與驗證。本頁用於決定新功能接在哪裡，並非全產品架構驗收或新功能排程。現有四項整理已在 Beta2 實作，細節見[擴充接點](extension-boundaries-20261023.md)。

## 先選正確的責任入口

| 需求 | 沿用入口 | 設計理由與必守邊界 |
| --- | --- | --- |
| 編輯 Canvas／素材文件內容 | [DocumentOperations.commit](../../prompt_calculus_studio/document_operations.py)；Canvas 保留相容入口，Manager 使用既有文件操作介面 | 候選 state 驗證後才發布，共用原文件 history。新工具不必切到 Canvas 畫面，也不另建 Undo stack；特殊 runtime 投影仍由原 adapter 處理。 |
| 編輯 Markdown | [MarkdownBuffer](../../prompt_calculus_studio/workspace_files.py) 的編輯／保存流程與 SourceLedger | 原文是保存來源，閱讀頁及搜尋結果是投影。Markdown 不在這次 Canvas 內容交易介面的遷移範圍；新工具沿用 buffer 的歷史與原文保護。 |
| 保存、另存、改名、搬移 | [WorkspaceCommands](../../prompt_calculus_studio/workspace_commands.py) → [WorkspaceFileOperations](../../prompt_calculus_studio/workspace_file_operations.py)／各文件 writer | 內容提交與實體保存有不同失敗點。不能把 commit 成功呈現成已落盤，也不能用重新做一次內容操作來重試保存。 |
| 本機背景檔案工作 | [Jobs](../../prompt_calculus_studio/jobs.py) 與 [JobOutcome](../../prompt_calculus_studio/job_outcomes.py) | 可丟棄的讀取與已產生持久副作用的回執分開處理；完成寫入仍回原 owner 登記。 |
| ComfyUI／Stage 執行 | [DocumentExecution](../../prompt_calculus_studio/document_execution.py) 與[既有執行歸屬](local-workspace/execution-owners.md) | 執行權、journal 及回執跟著 runtime owner，不能隨焦點分頁轉移；連線成功不代表工作流能力已相容。 |
| 新增外部服務 | [service_adapters](../../prompt_calculus_studio/service_adapters.py)；現有範例 [CivitAIBrowseAdapter](../../prompt_calculus_studio/civitai_adapter.py) | 封套凍結輸入並核對完整 request；各服務保留自己的傳輸、認證和回應限制。封套不承擔排程、重試或目的文件的新鮮度判斷。 |
| Stage 圖片內容推導 | [stage_context.project](../../prompt_calculus_studio/stage_context.py) → [image_source](../../prompt_calculus_studio/image_source.py)／[pnginfo](../../prompt_calculus_studio/pnginfo.py) | 一次操作共用驗證過的 bytes 與 metadata，Reader 的 choice、來源及可變輸出各自獨立。 |

例如新增「整理選中 Prompt」：先凍結目前文件、選取與版本，取得候選後再核對目的內容；使用者採用時，以該文件的一次 `commit(..., merge_key=None)` 寫入，再由原保存流程處理落盤。若功能目標是 Markdown，則接其 buffer 操作；不把閱讀頁 HTML 寫回原文。模型功能本身仍屬下述規劃。

## 保留的資料與依賴契約

- **同文件共用內容，不同文件各有 owner。** 視圖可以有各自的選取、縮放與呈現；素材原件與使用副本保持獨立。另存不把舊的在途工作轉給新文件，見[本機文件決策](local-workspace/ADR-001.md)及[執行歸屬](local-workspace/execution-owners.md)。
- **空稿、順序和歷史都是資料。** 保留手動空字串 `''` 與未編輯 `None` 的差別、綁定、輸出順序、Undo／Redo、原文與保存重開結果。新增介面不能只驗證畫面上的最後字串。
- **來源參照不等於持續同步。** 素材副本、Stage 來源、Markdown 相依各依既有明確操作更新；不能因相同名稱或 ID 就覆寫另一份內容。
- **共享核心保持無 Qt。** [releases.py 的 SHARED_MODULES](../../prompt_calculus_studio/releases.py) 決定組裝時複製到 ComfyUI 的原始模組。新增依賴要追到傳遞 import；修改來源，不手改生成的 `shared/` 副本。內容交易核心也保持無 Qt，但不是因此自動列入組裝清單。

## 背景結果：接受之前再核對目前狀態

請求 ID 只回答「這是誰的回覆」。接受時還須回答「這個目的地現在仍適用嗎」。不同入口保留自己的判定，不硬套一組通用 token：

| 工作類型 | 接回前核對 | 失效或關閉時 |
| --- | --- | --- |
| 閱讀、搜尋、預覽等投影 | owner／文件、來源快照、revision、mode、root／scope，以及 receiver 是否仍有效 | 拒收過期成功與錯誤；只收掉自己的 handle。隱藏頁依原機制失效，返回時刷新。 |
| 已接受且完成寫入的工作 | 原 writer、目的資料位置及完成／部分結果回執 | 收妥回執；目前 UI 退役不等於原 owner 的登記義務消失。 |
| 新服務回覆或未來候選採用 | 完整 request 對應，加上 caller 的 token epoch、版本和目的內容核對 | 視情境拒收顯示或拒絕採用；不得只用 request ID 允許寫入。 |

Beta2 的根範圍修復提供具體例子：背景閱讀已取得廣根下的內容，期間加入更深的根；即使文字版本未改，舊結果也可能失效。[reading_root](../../prompt_calculus_studio/markdown_relations.py) 統一有效根，`RichView.request_current` 及 `ReadingView.render_key` 將根納入身份；[WorkspaceShell](../../prompt_calculus_studio/workspace_shell.py) 在根設定成功保存後才通知受影響頁面。無關根、重複操作與失敗回復不刷新無關頁面。後續新增來源範圍也應檢查「工作已開始後範圍改變」的反例。

取消仍是合作式的。一般工作開始前取消不執行 worker；`finish_accepted=True` 目前用於已接受 Recent 收藏的登記義務，不能成為任意忽略取消的開關。收尾不能清除回呼新開的工作，也不提供跨程序 exactly-once 保證。關閉沿原有停止派送、保存／恢復與資源釋放流程，不以強制關 DB 或殺執行緒取代。

## 已完成、後續接入與重新評估條件

| 主題 | Beta2 已有／保留 | 何時才需要再改 |
| --- | --- | --- |
| 文件操作 | 共用內容交易、驗證及同一份歷史；Canvas runtime adapter 仍存在 | 新功能被迫借用畫面才能操作內容時，補最小能力介面；以無畫面操作、相同 Undo 及保存重試不重加內容驗證。 |
| 背景生命週期 | 回執與可作廢投影分流，保留現有 scheduler | 兩條實際流程需要相同排程改動時，才抽共用骨架；owner、有效性和接受義務仍由各入口決定。不能把保留 host 直接判為記憶體洩漏。 |
| 外部服務／AI | CivitAI 連線測試、搜尋、版本詳情已接封套；[AI／skill 內容契約](087-ai-skill-contracts.md)仍是規劃 | 有具體模型功能時，補來源原文 SHA、scope、版本、Unicode scalar spans、候選審閱及採用前重驗，再提交一次交易。模型、候選 UI、skill 執行尚未提供。 |
| Stage 來源 | 每次 `project` 按已解析 root／path／SHA 共用來源；每個 Reader 輸出獨立 | 固定案例量測仍顯示瓶頸時，分開量 hash、parse、state copy 與輸出成本再決定。25 MiB 是單來源捕獲上限，不是整體記憶體保證；新操作重新驗證，沒有跨操作快取。 |

優先順序是先確認新需求的資料歸屬與最小完整流程，再接既有介面，補必要驗證；只有出現上述具體限制才局部抽取。跨程序隔離、其他儲存格式或插件平台若成為真實需求，再另記取捨與遷移條件，現階段不預建框架。

## 沿用固定導航，不複製第二套地圖

既有導航的產品基準為 `f2fe2a67b3fa8c2251ebf2240b52e2f343530063`，文件提交為 `c8f9011c7244c6654e2b3cc73154ca5cc2a6e3a9`。它比 Beta2 的四項接點與有效根修復早；原模組／流程定位可作起點，不能推定已覆蓋後續改動。該文件提交獨立於發布樹，有這份 Git 歷史時可讀：

```powershell
git show 'c8f9011c7244c6654e2b3cc73154ca5cc2a6e3a9:docs/development/navigation-f2fe2a67/README.md'
git show 'c8f9011c7244c6654e2b3cc73154ca5cc2a6e3a9:docs/development/navigation-f2fe2a67/flows.md'
```

`unknowns.md` 保留該版本未覆蓋邊界。缺少此文件提交時，使用本頁、[擴充接點](extension-boundaries-20261023.md)及目標原碼，不將舊地圖冒稱為新版。後續地圖維護只補實際改動的模組、介面、資料流及依賴；原固定快照保留。

## 按風險選驗證

文件交易看 [test_document_operations.py](../../tests/test_document_operations.py) 與 [UI 呼叫端](../../tests/test_document_operations_ui.py)；回執看 [test_job_outcomes.py](../../tests/test_job_outcomes.py) 和 [呼叫端生命週期](../../tests/test_job_callers.py)；服務看 [test_service_adapters.py](../../tests/test_service_adapters.py)；Stage 看 [test_stage_image_snapshot.py](../../tests/test_stage_image_snapshot.py)；根變更看 [test_markdown_root_scope.py](../../tests/test_markdown_root_scope.py)。這些是選用入口，不代表新增功能已獲驗證。

每次依受影響契約選最小但完整的成功、取消／失敗、晚到回覆及保存重開控制，記來源和實際結果。既有的原生輸入、DPI、真服務／GPU及異常退出限制見 [Beta2 驗證](../releases/0.8.9/validation/beta.2.md)；本次整理文件沒有新增執行證據。維護分工與更新時機見[開發指南](README.md#隨功能維護)。
