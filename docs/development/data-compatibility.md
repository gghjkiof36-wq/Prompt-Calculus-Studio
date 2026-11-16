# 資料與相容性

歷史基準：0.8.8 Alpha 1，固定受驗功能來源 `0d4a5f5a9ef51a98137a18522c0d2bf110ca3b61`；發布整合／實際資產見[來源對照](../releases/0.8.8/source-provenance.md)。下列格式段落另記錄 Pre-alpha3 的 studio DB 契約。操作見[素材與批次](../guide/prompt-materials-088.md)。

後續開發維護：[舊 Stage 快照還原與視窗生命週期](legacy-stage-recovery.md)（0.8.9 第一階段來源）。

## 格式、遷移與使用位置

manifest 收取的候選 writer 使用 studio DB 的 WAL 模式；WAL／SHM 屬同一文件資料。只有候選表 intent／ready 由單一背景連線寫入，FULL synchronous 與原圖 fsync 保證不降低。job、文件及 final 接受仍在 GUI 原交易。這不新增欄位、不改 event_apply=2；搬遷仍要求停止舊程序，複製並恢復 SQLite 日誌後核驗原件不變。

目前 studio DB 使用 `event_apply=2`、`journal=1`。結果收取新增 `channel_result_candidates`，分開保存 display candidates 與 history final 的 intent／ready receipt。原圖、縮圖、metadata 位於文件資料目錄內，以相對路徑記錄，搬遷隨資料副本複製；文件／PNG 格式不變。正常開庫只驗證版本、表與索引形狀，缺表不得補建。

S5B registry 在複製完成的目標依序執行 `journal 0→1`、`event_apply 0→1→2`；已有 event_apply=1 的副本只跑 1→2。原庫與原偏好檔不變。舊程式不能開新 studio DB，pre-alpha3 不支援降版。下面兩段記錄先前 0→1 的歷史步驟，仍是完整搬遷鏈的一部分。

ready receipt 表示原圖已驗證及保存，不代表縮圖／metadata 已完成。`receipt.derived` 缺少或為 null 時背景補建；有值時保存兩個衍生檔的 hash。未發布的 event_apply=2 DDL 包含 `derived_pending INTEGER NOT NULL DEFAULT 0`、operation 索引、待補部分索引與 history asset hash 部分索引，以及單列 `channel_result_retention` 孤兒掃掠游標；registry、形狀驗證及搬遷清冊使用同一形狀，不在正常開庫補表、補欄或索引。游標與刪除同交易，兩次重開也從原進度續行。

未完成收取重開時在 worker 重核原圖，缺漏／hash 不符就重取；已完成的歷史不因開啟／bind 全量驗檔。只有 frame 已提交而衍生收據未補完的列，才由待補部分索引恢復。顯示失敗或使用時 hash 不符，按單張圖片交 worker 重建。衍生檔不逐檔 fsync，原圖新寫入仍先 flush／fsync 再原子替換；原圖修復只取原封存 final，不再次推 frame。final 接受同交易刪除候選列；其餘列跟隨既有 job／attempt 保留與有界孤兒清理，不新增檔案清理規則。詳見 [journal layout](journal-layout.md)。

本機一致性檢查沿用 event_apply=1、journal=1，沒有新增 SQL 表或欄位。attempt 的可選 submission_intent／operation_evidence／local_issue／local_origin 與 local continuation receipt 不需要新的 registry 步驟；operation_evidence 使用既有 generic content_paths 拆分，已知讀取器可按 pointer 還原。缺少意圖的舊資料視為傳輸不確定，不能推論未送出。document／PNG 版本不變，pre-alpha3 仍不支援降版；此處的 generic reader 相容性不等於批准整個應用程式降版。

Pre-alpha3 CHANNEL-C1 新增 studio DB 的 `storage_layout('event_apply',1)` 與 `channel_applied_events`；journal 仍為 1，document／PNG 格式不變。S5B registry 只在複製後的搬遷目標執行缺少的步驟：journal=0 先升 journal=1，再加 event_apply；既有 journal=1 只加 event_apply。正常開庫先核對標記與表結構，缺少標記時拒讀，不能順手補表。搬遷包含 comfy-channel.sqlite3 sidecar，原件與舊偏好檔保持不動；舊 PCS 不能開新 studio DB，pre-alpha3 不支援降版。

桌面與同版通用／legacy／modern 配套支援文件及 PNG 快照格式 1–7。格式 5／6／7 與 ComfyUI 後端、前端或 PCS 發布版號分開。新類型與游標需要格式 7，舊版程式不保證可讀；回退以相容程式及原資料副本為單位。

`material_type` 保存 text／combination／flow；文字 `variants`、組合 `combination`、流程 `sequence` 各有明確用途。載入、匯入、還原只在轉換邊界處理副本，保留 ID、名稱、正文、歷史、分類、綁定與空手動稿。未知格式拒絕，不替換已保存原件。

`selection_cursor` 按使用位置保存當前項、剩餘重複及隨機剩餘項；定義簽章排除游標。舊 `supply_cursor` 可讀。舊 5／6 升 7 時，只有可驗證為合法舊定義的游標轉換保留；任意過期簽章仍拒絕套用，避免把另一份來源的進度帶回。複製取得獨立身分，從素材庫加入依其起始設定建立副本。

## 批次、journal 與取消

供應計畫 version 2 保存凍結定義及逐筆值／進度差異。多筆來源取 max(N)，短者循環；Canvas 當批只解析一次。相同使用位置分線共用凍結前綴，較長分支延伸未保存部分；另一位置獨立。舊 version 1 已保存計畫沿原值讀取，不重算已完成 journal。

內容與游標在同一交易保存；成功保存即消耗，失敗整體回復。新的預排程組在點擊時保存一份 `batch_snapshot` 並預留整組，之後分段準備 entry；全部展開後刪除內容，`batch_control` 的流水號及游標收據獨立保留。尚未開始且沒有較新組使用相同游標時，取消可依收據撤回；其他情況及舊 journal 的取消不回退已保存進度。完成回執沿原有可見游標規則，不重複消耗已預留的 version 2 來源。補位、重試與重開沿原批快照，重開保持暫停，不重抽或以目前編輯改寫舊任務。ChangeCoordinator、Undo／Redo 及快照還原不派送生成。新 journal 欄位不提高文件或 PNG 格式版本，詳見[組別快照](stage-architecture.md#組別快照先行視窗與撤回)。

全部取消先保存完整本機狀態，再處理已確認歸屬的原生任務；合法空 feed 不造成取消中斷。未知／晚到回覆依原任務身分核對，不假標後端停止，也不清除外部或其他工作區任務。

## 生成投影、收納及完整多輸出順序

生成用 selected-only 快照只保留本次重建所需的素材與參照閉包，排除未用素材正文及未用分類／資料夾名稱。必要 Prompt、來源、所用分類／資料夾仍存在，不能稱匿名。完整備份不採這個縮減，保留完整私人素材庫。

必要 `material_navigation` folder／category 參照按實際引用保留；可選 `category_positions` 保存被保留分類的相對順序，缺欄仍相容。這是現行格式內的可選資料，不另升 schema。還原須處理 ID 衝突映射及退役引用，保留目標庫不相關收納，不能以生成端回傳路徑作安裝目標。

`output_order` 保存完整有效多輸出順序，不能僅取當前可見選中子集。切空輸出、刷新、物化、編輯、保存及還原不重排其他輸出；移除失效引用時保持有效 ID 的原相對次序。手動稿與 list／Canvas 草稿仍按各自 output 隔離。

PNG 恢復配置與必要文字，不匯入執行 journal 或自動建立新任務。特定舊候選 Stage PNG 本已有不一致，這次不修存量檔或放寬驗證；六份合法真舊完整快照的通過只代表該樣本範圍。

## 其他保留契約

本分支新增 Manager 文字變體專屬排除：節點 `excludes` 保持全部變體共用；專屬值保存在 `variants.groups[*].excludes`。首次使用非空專屬規則時，僅將這份 `variants.version` 標為 2，state 及可攜文件版本不因本功能另升級；未使用者保留 v1，已 v2 清空規則後不自動降版。v1 不接受帶有專屬規則的變體，v2 僅在文字變體路徑接受，普通 sequence／combination 不擴大語意。舊版巢狀驗證器拒讀 v2，避免忽略新欄位繼續執行。接收端仍會驗證完整快照；既有能力查詢不另新增變體版本欄位。

`exclusions.effective_excludes` 從已解析的內容樹合併節點共用與當輪變體專屬規則，供清單與 Canvas 原有排除範圍使用；不依編輯器當時的選取推定已凍結批次。原有深複製、signature、文件保存及快照保留整份 variants，不另建立平行規則表。主程式與配套使用同份 Qt-free 核心。

組合去重只作用自身當輪完整 Tag，舊文字去重值可讀，不擴為全域。排除 Tag 不刪模塊。手動 `""` 與自動 `None`、兩種草稿、原件／副本、歷史與綁定維持隔離。

目前文字變體將所選變體及外層 `children` 一起編譯，權重作用完整結果；有效子元素也參與原排除規則。組合／流程仍只編譯當輪有效 group，外層舊 `children` 是轉型保留稿，不能再次附加造成重複輸出。無顯式類型的舊變體優先辨識為文字，不因帶有子元素而誤轉組合。既有 legacy-format 重建保持原行為，已保存的批次文字不以新編譯結果取代。

文字變體取值也沿相同有效內容樹：format 3 起，父變體取值後繼續解析其子變體；停用項及被覆蓋的原內容不推進。各子項使用自己的模式與重複數，不增加笛卡兒積批次；舊格式快照不推進原本未參與內容的子項。

### 組合文字成員同步

組合／流程內的文字成員可保存 `member_link={item, document, base, ids}`：`document=null` 指向同文件的素材，跨文件則使用明確文件 ID；`base` 是最後對照的來源定義，`ids` 對應來源及成員自己的節點／變體身分。此欄位在載入時驗證，不能包含遞迴的 link；它不參與生成內容 signature。舊 `source_*` 仍為出處資訊，不改成全域同步參照。

`materials.save` 在同一內容交易內合併原件更新與組合成員；跨文件由 WorkspaceMaterials 對已開啟文件執行正常可復原交易。只對實際變動的來源同步，不因無關素材改動重套使用者剛復原的版本。正在編輯時延後，取消／套用後補處理。三方合併保留成員的局部修改、權重、開關、收合與有效選項；新增／刪除變體及隨機序列須重新核對有效進度，不復活舊的失效游標。來源的直接或間接循環不展開，保留現有副本。

舊成員只以唯一的文件／素材身分補接；找不到來源、來源刪除或有歧義時保留內容。沒有舊來源基準時不把現有局部文字當成可任意覆寫的舊稿。普通畫布根副本及 Reader 保持原有明確重讀語意；已排程的凍結內容、手動空稿與歷史不隨來源更新。同步範圍為文字與變體，縮圖附件仍由目的文件保存及明確複製流程管理。

分類另存／匯入時同步連結的本地素材 ID、基準與成員 ID 對照一起重映射；外部來源身分不變。舊程式可讀既存副本內容，但不提供此同步行為。驗證入口為 `test_material_links.py`、`test_workspace_material_transfer.py`、`test_variant_composition_queue.py`。

Tag 輸出在套權重前整理邊界逗號，例如 `test,` 產生 `(test:1.1)`；正文、跳脫逗號、內部逗號及自然語言內容保持原意。文件投影只複製待保存的內容與必要中介資料，維持來源／結果隔離及原驗證，不複製隨後會捨棄的舊 payload、本機歷史或字典。這些修正不新增保存欄位或變更文件版本。

Stage 最新狀態查詢／索引與未變輪詢優化不刪減歷史。桌面與配套包含相同 Qt-free 共用核心；實際包的逐檔及共用模組身份由發布來源對照確認。既有 `prompt_studio` 儲存／metadata／協定識別及 `PROMPT_STUDIO_DATA` 保留，不以顯示名稱改動推定格式已遷移。

[0.8.8 驗證範圍](../releases/0.8.8/validation/alpha.1.md)分開來源檢查、最終包及真實服務界線。

---

## 歷史格式說明

以下保留各版／前置當時的行為。前置 4／5 的有限來源取最短已由本版 max(N) 與短來源循環取代，較早 1–6 支援及資料夾不入 PNG 不能覆蓋上方現行規則。

## 歷史：0.8.8 本機前置 5

以下只適用 `--v0.8.8-preparation.5` 本機候選，尚未公開發布。沒有使用這兩項功能的文件保留原版本。

- 綁定後接預排程使用 `scheduler::bindings` 的 `control` 連線，文件／PNG 快照升為第 6 版。它保留 CLIP／圖片輸入的型別、原始來源與工作流節點欄位，不轉換成原始文字或圖片通道。原本的綁定前資料通道與 Stage 範圍排程繼續支援；同一張排程不能同時作綁定資料排程和 Stage 範圍排程。
- 排入時計畫保存綁定目標，每輪另存當輪內容。排入後改接線或綁定會暫停後續執行，不重寫舊項目；失敗重試沿原項目及快照。逐輪、明確批次、有限來源取最短、十項補位、批外圖片固定及取消規則沿用前置 4。
- 額外圖片預覽保存在 `canvas_functions.previews`，每筆有獨立 ID、收合狀態、位置、大小及輸入連線。舊 `__result_preview__` 和 `preview_display` 保留原樣。觀看圖片不消耗來源；圖片檔可共用唯讀引用。
- 複製預覽建立新 ID 並深複製可變顯示狀態。只複製兩端都選取的接線；單獨複製預覽需重新接來源。CLIP／圖片輸入副本仍須重新綁定，未解除工作流欄位的唯一性限制。複製圖片來源保留合法的來源 ID 格式。
- 桌面與配套擴充共同支援快照 1–6。舊擴充未宣告第 6 版時，不允許默默忽略新資料執行。既有手動空稿、原件與副本、Undo／Redo 及工作區保存規則不變。

## 歷史：0.8.8 本機前置 4

以下只適用 `--v0.8.8-preparation.4` 本機候選，尚未公開發布。其餘章節記錄既有公開版本。

- 使用文字組合、組合來源、去重或逐輪圖片供應時，文件升為 `version=5`，PNG 快照對應 `schema_version=5`。舊文件若未使用新功能，仍保留原版本。版本檢查阻止舊擴充默默忽略新功能。逐項 Tag 勾選介面已移除；先前候選資料的 `disabled_tags` 仍保留讀取及輸出相容，不在開啟或保存一般編輯時抹除。
- `sequence.version=1` 保存穩定組合 ID、模式、起始與目前組合，以及每組重複、直接文字和模組副本。切換輸入模式保留兩份草稿；重新排序不重建 ID，刪除起始組合需明確指定替代。
- `round_drafts` 與 `supply_cursor` 屬目前副本；當輪覆寫只用於對應邏輯輪次，原件及其他重複輪保持原內容。來源簽章改變時，不套用舊的供應位置。
- 「讀取組合」以 `source_id`、`source_module`、`source_revision` 記錄素材出處，內容是獨立副本。原件更名、修改或刪除不回寫已讀取副本；手動重新讀取會建立新內容 ID 並從原設定起點開始。舊供應計畫不得推進這份新副本。編輯內部文字模組、拖曳輪次或輪內模組時，保留起始與目前輪次 ID 及既有預留位置，同步更新副本的游標簽章；已排入內容仍凍結，舊計畫不能推進排序後的副本。`unselected=true` 表示新建但尚未選取素材，不能供應；缺值的既有來源保留原內容與行為。
- 供應計畫在排入時保存來源定義、隨機結果及邏輯輪 ID。排入位置與完成位置分開；重試不再次占用來源，取消單筆不再補回該筆，過期回呼不得推進新計畫或已手動改選的圖片。
- 多來源按展開重複後的每輪配對，以最先耗盡的有限來源為界。固定來源可重用，未參與該預排程的圖片在整批內固定。原有容量補位不改寫已排入的文字／圖片。
- 保存、匯入與還原仍驗證資料版本與完整內容；不拿新定義重算舊歷史文字，不匯入歷史執行 journal 作為新任務。Canvas 操作仍透過原交易保留 Undo／Redo。

第 5 版資料不可交給舊程式或舊擴充；回退使用保留的舊資料副本。程式碼回退不會回退資料。

## v7 生成模式與舊圖片連線（B3-GEN）

文件、PNG 與 journal 的格式及雜湊規則不變。v7 新建／首次匯入的 profile 依整張 API graph 推導相容標記 `mode`：有 LoadImage（包括不在採樣路徑上的節點）或有效圖片綁定為 `img2img`，其餘為 `txt2img`。更新既有 profile ID 保留原 mode，不因改名、參數或重讀而回寫。新 profile 保存及工作流交換寫入的 `generation.mode` 固定 `txt2img`，原 `chosen` 保留；一般載入、保存、快照及 Undo 不正規化舊 mode。PNG 繼續複製 profile 的 mode，擴充端不加新的推導規則。

新建 v7 圖片→Prompt 線被拒絕，已有的線在文件驗證、流程投影、PNG 還原與 Undo 時照舊接受。沒有載入遷移或自動改接；舊線仍參與依賴判斷，Stage 仍不藉此傳送圖片。v6 以下的接線與 direct mode 不變。來源參照驗證抽為 `generation.validate_source`，沿用原 hash、相對路徑、尺寸、名稱及 None 的接受集合；畫布圖層仍額外拒絕 None。

## 素材、文字草稿與內容歷史

- 候選26新增可選的庫級 `material_navigation` version 1，保存資料夾、分類ID歸屬及側欄順序；`settings.material_folder_collapsed` 保存收合偏好。缺欄位時全部分類在外層，不在refresh写入，不重排原`modules`或Canvas輸出。新增、改名、移動及移除沿既有Undo交易，僅收合不新增內容交易。已刪除分類的舊引用在顯示時忽略，下一次明確收納操作才清除；不支援的收納版本拒絕載入。此處原先的「資料夾不寫進 PNG」只記錄 0.8.7 初始行為；0.8.8 改為保留重建所需的資料夾／分類參照，依本頁現行規則。
- `items` 保存素材原件，`uses` 是每份畫布的獨立內容樹。新增 `content_kind`（tags/text/markdown）、`pinned`、`last_used`、`source_revision` 與 `collapsed` 為可選欄位；既有內容缺欄不強制改写收合狀態。未知內容格式拒絕，不回退成 tags 清理。
- `multi_output.outputs` 的 Canvas 與 list 草稿保留分隔，`None` 與 `""` 不可互換。Editor 切頁、切工作區及退出前同步目前副本，素材原件仍需明確保存；舊 list 的內容與次序可透過取回入口查看，不自動生成新流程。
- `content_history.version=1` 保存最多 200 筆／4 MiB 的內容及操作紀錄。素材、文字樹與 output 草稿的 before/after 分開記錄，output 帶 canvas/list scope。還原內容經 Canvas transaction 產生新的歷史；不改外部提交、圖片歷史或非目標輸出。
- Stage 未套用草稿放同資料庫的附加 `stage_editor_local` 表，按工作區、server、Stage、等待項目分隔。這不是 Stage 執行參數；提醒偏好與草稿保存不會套用或提交生成。
- Undo 為工作階段內 100 步／約 32 MB 的有界快照。未提交等待項目採紀錄身分與完整值比對，已嘗試／已提交／外部修改不復原成 waiting；成功復原保持原 run 暫停。
- 新 PNG 快照使用 `formatter_version=3`，包含變體的外層子元素，並在權重括號前清理 Tag 邊界逗號。主程式與 ComfyUI 配套須同時更新。無標記／第 1 版與第 2 版快照仍以各自的規則精確驗證；短期本機預覽曾以第 2 版輸出新規則，因此第 2 版只接受任一整套規則完全匹配的主輸出與所有分支，不混用結果、不放寬文字核對。新規則不同時，還原保留歷史 final 為手動稿，包含空字串；不拿新版輸出覆寫。portable 快照排除本機預覽路徑、釘選、使用時間及內容歷史。
- 舊 `workspace_scenes` 在載入副本內依完整 multi-output→CLIP→flow→Stage 次序升級，原件先備份；再次載入不得製造相同轉換備份。場景遷移不保留 runtime execution-input 或 Stage-result 暫存。

新增資料仍保留既有 `prompt_studio` 儲存與協定名稱。回退使用相容舊程式和原資料副本，不將新版編輯直接交回舊程式。具體已驗條件見[0.8.7 驗證範圍](../releases/0.8.7/validation/alpha.1.md)。

## 沿用 0.8.6 的導覽、起始流程與更新設定

修訂 12 不修改文件或 SQLite 格式。與本次修复直接相關的持久化界線如下；既有 Stage 相容表及較早格式說明保留。

| 資料 | 寫入者與用途 | 回復界線 |
|---|---|---|
| 資料目錄的 `extension-install.json` | Workspace 固定在資料根目錄；獨立宿主沿原目錄。擴充頁與自動綁定共用路徑及自動更新偏好 | 未綁定且回報路徑通過本機／實體位置驗證時可綁定；保留既有 root 與 automatic。固定檔不存在時一次搬遷舊 session 設定，衝突採保守值、原檔不改，規則見[相容指南](../guide/comfyui-compatibility.md#擴充資料夾自動綁定)。 |
| `.pcs-program-backups/install-state.json` | 安裝器保存本次替換位置、備份位置與交易狀態 | `completed` 是完成替換；`rolled_back` 僅在原樹核對成功且沒有競爭 target 時成立；`replacing`／`rollback_unconfirmed` 或未知狀態阻擋下一次安裝，不自動重試。 |
| `.pcs-program-backups/*-rollback.json` 與 `previous-*` | 安裝器保存原安裝相對路徑／大小／雜湊及完整原樹 | `local_library.json` 與其他原檔保留；競爭時不覆寫外來樹。復原未確認時保留所有仍存在的備份，供人工檢查。 |
| Canvas 配置與排程 journal | 原有保存／Undo／Redo與生成流程 | 卡片布局計時器沒有資料所有權；移除畫面卡片不由舊回呼改寫新卡片或生成紀錄。 |

0.8.6 沿用 Stage 明確覆寫與等待項保存規則，沒有把介面改版當成新生成命令。新資料與明確新增的工作區可建立六節點起始流程；既有布局、空手動稿、綁定及刪除結果不因載入而回填預設流程。

啟動切到 Canvas，不清除清單／畫布草稿。色彩、字級、側欄寬度及透明度等偏好留在目前資料設定，改布局不發送生成。配套擴充安裝偏好獨立保存於資料目錄；未確認安裝交易保留紀錄並停止自動重試。

提交與取消沿用任務身分核對；單筆移除只處理可移除的等待／已確認失敗項，底部取消保留其他項並暫停，全部取消由明確入口發起。舊 journal／PNG 不因導覽重建而重送任務。

操作見[入門指南](../guide/getting-started.md)與[Stage 參數](../guide/stage-parameters.md)，0.8.6 的證據見[0.8.6 驗證範圍](../releases/0.8.6/validation/alpha.1.md)。更新和回退仍以資料副本與相容主程式／擴充為單位。


## 0.8.7 Canvas 模組顯示與負權重

模組權重沿用十倍整數儲存，合法範圍擴為 `-1000` 至 `1000`（介面 `-100.0` 至 `100.0`）。父卡仍作用於整組，子元素、副本與原件的歸屬不變；修改經既有 Canvas 交易保存及復原。既有非負權重無需轉換。舊版本的驗證器可能拒絕負權重，因此含負值的新資料不能交回舊版；回退時使用保留的舊版程式與原資料副本。

功能模組新增可選的 `collapsed` 顯示欄位；缺值視為展開。預覽卡使用 `canvas_functions.preview_display.collapsed`，其餘放在對應模組資料內。收合不改原尺寸、文字內容、連線、綁定或生成提交，也不建立執行任務。新增功能頁的節點預覽使用隔離的預設資料和實際卡片元件，不能保存或讀取使用者的任務紀錄。

## Stage 參數與執行紀錄

這組保存規則於 0.8.5（2026-10-02）引入，0.8.6 沿用。

0.8.5 在既有 Stage 配置與 `stage_journal` 增加明確參數意圖，沒有把原生工作流全圖另做一份執行來源。操作見 [Stage 參數說明](../guide/stage-parameters.md)，0.8.5 的證據與未驗範圍見 [驗證記錄](../releases/0.8.5/validation/alpha.1.md)。

| 保存位置 | 格式與歸屬 | 行為邊界 |
|---|---|---|
| `multi_output.stages[stage_id].parameters` | 覆寫格式 `version: 1`；`identity` 保存 `workflow`、`frontend_id`、`origin`；`patches` 保存明確欄位 | Stage 實例 ID 為穩定擁有者，顯示名稱／編號不作鍵。工作區配置沿既有 `workspace_scenes` 保存；不同工作區即使保有相同 Stage ID，也不共用 journal 狀態。 |
| `patches[]` | 節點 `node`、完整實例 `path`、`class_type`、欄位 `field`、`schema`、`type`、外部基準 `base`、指定 `value`；視欄位附範圍、步進、選項 | 重新驗證定位、定義、來源連線與基準衝突；不靠同名節點猜搬，不以保存整張舊圖覆蓋外部新編輯。未知或無可靠回寫能力的欄位限制編輯，原資料保留。 |
| 種子政策 | patch 的 `seed_mode`、`seed_timing`、`intent_id`；準備時可另有 `resolved` | 模式是原生前端的執行政策。實際 seed 與下一值以所屬回執為依據；取回結果或未知提交對帳不再次抽取。核心整數驗證要求 JavaScript 安全整數；目前原生種子控制另以 `2**50` 為保真上限，並取實際欄位更嚴格的限制。超界原值保留且限制編輯，不宣稱 64 位全範圍可編輯。 |
| 排程 `saved.parameters[stage_id]` | 入列時深複製當時 Stage 的明確覆寫，可為 `None` | 只捕捉 PCS 已明確指定的欄位及模式，不把所有原生欄位提前凍結。後來修改 Stage 不暗改既有任務。 |
| 排程 `saved.parameter_overrides[stage_id]` | 單一等待任務的覆寫；必要工作流資料另放 `saved.parameter_profiles[stage_id]` | 優先於該任務捕捉的 Stage 覆寫；只改指定任務，不回寫 Stage、其他任務或原檔。保存時同時比較原 `saved` 與任務仍為 `waiting`，開始準備後拒絕套用並保留草稿。 |
| `stage_journal` 的 `attempt`／`parameter_state` | 已準備快照、工作區／Stage／parent 歸屬、參數回執與原生 `prompt_id` 關聯 | 執行紀錄獨立於 Canvas 編輯。只有符合所屬與實際 payload 的回執可推進種子狀態；重開保留 journal 並暫停未完成流程，不當成新生成指令。 |

有效覆寫依序取「單一任務覆寫 → 該任務入列時捕捉的 Stage 覆寫 → 沒有覆寫」。空 `patches` 表示不指定欄位；「重設為工作流值」移除 PCS 覆寫，而不是保存當下數字。未覆寫的原生欄位仍在提交準備時沿現行原生序列化取得；CLIP／圖片原有的保存／即時來源政策不變，未來圖片繼續引用所屬上游結果。

舊文件、舊 PNG 或舊等待項沒有 `parameters` 時，視為沒有 PCS 覆寫，不以目前畫布值補入。此新增欄位有自己的格式版本；不支援的參數版本在驗證時拒絕，不覆寫已存原文件。現有文件、Stage 與快照的外層版本不因新增可選欄位另行更名。新版資料不預設可以交回舊版，回退應使用保留的舊版程式與原資料副本。

一般保存／重開保留 Stage 覆寫、逐任務覆寫及工作區歸屬。一次面板套用沿 Canvas Undo／Redo 邊界恢復配置，不撤銷或重送已提交工作；等待任務編輯走 journal，不混入畫布 Undo。PNG 保存的是快照配置，恢復到目標工作區時可帶回 Stage 參數、工作流與原有文字；不匯入 `run`、`entry`、`attempt`、`parameter_state` 或生成歷史，不建立待送任務。配置快照不能當成執行 journal 備份。


早期載入與快照相容檢查見[2026-09-14 歷史紀錄](../archive/0.8.1/data-compatibility-2026-09-14.md)。
