# Stage 模塊、接點與執行邊界

適用：0.8.6 Alpha 1。Stage 基本契約自 0.8.4 引入，參數保存與回執規則另見[資料相容性](data-compatibility.md)。

本頁保留上述版本的架構說明。2026-10-23 候選的單次圖片來源快照與服務接點，見[擴充接點補充](extension-boundaries-20261023.md)。

Stage 的接點規則與執行路徑集中定義；全應用的分類、設定頁及服務抽象仍屬後續整理，不能據此認為已有通用插件系統。

## 模塊與服務

Canvas 模塊按責任區分來源、組合、轉換、綁定、執行、排程、觀看。ComfyUI、CivitAI 是服務整合；ComfyUI Manager 應在 ComfyUI 設定內。未來 AI 自然語言／tags 轉譯可作轉換模塊，其服務設定另行管理。

`module_contracts.py` 是無 Qt 的模塊及接點目錄，統一名稱、顏色、型別及方向，供畫面及連線驗證共用。資料線（文字、圖片、PCS 結構）與輸入設定、流程引用、完成依賴分開；連線本身沒有執行副作用。歷史序列化名稱保持相容，沒有以顏色推測型別。

新增模塊先定義接點，再於 `flow_data` 補值的驗證／解析；涉及實際提交才擴充 Stage 原生協定。不得在預覽或資料解析中隱含生成。未來新資料型別需有自己的來源、版本、保存、取消及錯誤規則，不能把影音偽裝成圖片或讓 AI 呼叫阻塞 Canvas 刷新。

## 執行資料的責任

| 元件 | 責任 |
|---|---|
| `stage_model` | 編譯線性依賴及包含範圍；與 Qt、提交及等待項目無關 |
| `stage_context` | 在獨立資料上下文保存／讀取指定輸入，保留本輪結果引用及文字來源；不改可見畫布來預填排程 |
| `stage_store` | 同一桌面資料庫的流程、排程項、提交嘗試紀錄；每個工作區／預排程十項容量 |
| `stage_runner` | 逐層進入／退出範圍、派送、原生回執、完整完成、結果保存、恢復與去重 |
| `generation_runner`、`native_submission` | 沿用正式提交及完成追蹤；Stage 不另建生成服務 |
| 擴充 `native_queue` | 只套用與套用後提交分離；依任務識別取消及回報；種子控制只在提交時發生 |
| `stage_widgets` | 編輯與觀看介面，對 runner 發出明確操作；不直接改任務為完成 |

圖片結果以流程、Stage、提交嘗試、原生 `prompt_id`、節點及序號識別。預覽和下游共用已保存資產；指定任務結果不套用最近生成面板的筆數截斷。沒有結果時保留缺失，不取其他任務圖片。

外層只展開目前項目；內層完成後才離開範圍。一次手動操作只加入最外層負責者，無自動來源的內層只提供輸入。取消後排程容量釋放，但原生嘗試仍核對回覆並保存紀錄，不再推進流程。

## 排入與派送的快照邊界

目前工作樹的執行路徑使用同步唯讀投影：`stage_context.execution_state` 只複製投影會改寫的容器、圖片讀取設定及供應游標所屬根。編輯歷史、素材庫與其他工作區只供讀取，不隨每個手動輪次複製。隨機選擇與游標推進仍由原本的 `prompt_sources`／`prompt_materials` 按原順序處理。

凍結 scene 在內部上下文間唯讀共用；要編輯或取下一輪時，`workspace_scene.load` 仍建立獨立副本。保存邊界的 scene 驗證只讀取欄位，不為驗證另複製一份。這些投影不是可交給編輯介面的文件，也不是跨次執行的快取。

`StageStore.add_captured` 在同步捕捉結束時序列化完整紀錄，再從同一內容建立獨立的回傳物件。手動外層快照直接寫成 entry，同一交易內建立 run 的 owner 引用，省去先把全部 scene 寫入暫時的 `initial` 再刪除的重複工作；保存的 journal 欄位與內容不變。派送後若等待外部輸入套用或圖片上傳，使用 journal 已凍結的 `snapshot_state`，不再讀取借用的即時文件。

一次同步直譯的內部轉移合併成一筆堆疊保存；entry、供應游標與堆疊提交後，才開始下一個原生派送或完成後的輸入套用。失敗仍沿原交易回復文件與紀錄，沒有改變排程的暫停、取消與復原入口。

### 再次執行的排入判斷

新 run 的 `scope_progress` 與 run、初始 entry 在同一交易內保存。它記錄 plan 最外層目前或下一個範圍的 `index`、`key`、`auto`、entry `owner` 及 `state`（`pending`、`entered`、`left`）。待進入的手動範圍沿用初始 entry 的 owner；只登記進度，不提早建立 loop、feed 或凍結自動供應內容。外層手動範圍在內層執行期間繼續負責追加。

`start()` 只選同工作區、同服務、plan 簽章相符的 running／waiting run。`enqueue_scope()` 讀取其保存進度：尚未進入或已進入的手動範圍追加 entry；auto 範圍沿用「這批內容已在供應。」；沒有剩餘範圍則保存下一次完整流程。暫停時主按鈕仍走 `resume()`。新 run 的點擊判斷不讀 stack，因此第一次 pump 前連點與正常間隔相同。

`advance()` 進入外層 loop 時標記 `entered`；`pop()` 離開外層節點時選下一個範圍，沒有下一個則標記 `left`；新一輪重新登記首個範圍。進度與 stack 經 `save()` 一起保存，包含 80 次同步轉移後讓出給 `later()` 的邊界。沒有 `scope_progress` 的舊 run 整個生命週期仍用原先的 frame 判斷，不補欄位、不改 entry 或 StageStore schema。

排入接受後透過 `claim_dispatch()` 明確觸發 client 的派送權取得；其相容介面仍是帶有 claim／文件庫註冊副作用的 `dispatch_allowed` getter。這個呼叫留在 journal 與游標交易提交之後。

後續要讓 batch 再次執行排入下一組，接點是 `start()` 的 auto target 分支。`scope_progress` 只負責路由，不是批次清單或供應游標；目前自動內容仍在 `initialize_loop()` 建 feed、`fill_supply()` 預留。下一組的凍結、預留與撤回交易須另行實作，不可只移除擋下判斷。

### 同步擷取的資料所有權與查詢

`capture(..., borrow=True)` 與 `merge_saved(..., borrow=True)` 只在同步解譯與 journal 序列化之間借用已凍結的素材、場景及輸入葉節點；合併用的字典仍各自持有。派送在可讓出控制權之前，改用 journal 解碼後的 `snapshot_state`。`prompt_materials.resolve` 的遞迴重用父層已複製的子樹，不重複複製同一份定義；公開回傳的兩棵樹仍彼此獨立。

`stage_context.borrowed_base(live, scene=None, *, copy_top=True)` 是 `prepare_state` 與 runner 四處從即時文件建立基底的入口。預設只新建頂層 dict；有 scene 時沿用 `workspace_scene.load` 深複製場景欄位、建立新的 generation 字典，其中的既有 profiles 及其他非場景值仍共用。load 照舊移除 scene 缺少的欄位與 `_execution_inputs`。無 scene 且 `copy_top=False` 時直接回傳 live，保留原 refill 分支的語意。`execution_state` 等其他借用入口保持各自的責任，沒有合併到這個 helper。

呼叫端只能讀取共用部分。任何借用資料要放入跨過讓出點的容器（記憶體中的 run、`frame['ctx']`、合併／非同步存檔）前，必須先在主執行緒同步序列化；跨點保存的是序列化內容或由該內容解碼的獨立物件，不能把「稍後再序列化」當成已凍結。借用的 scene 也遵守這個時間點約束。

`copy_context` 的子上下文可以在自己的 saved 分類字典新增 key，但不得修改既有共用項目的內容（含其巢狀容器）。`prompt_materials.resolve` 必須保持輸入不變，否則 `prompt_sources.build` 共用的 plan 定義可能被改，且原簽章不一定能偵測。這兩個契約由 [借用固定測試](../../tests/test_stage_borrow_guard.py) 鎖住，沒有改變 `copy_context` 或供應解析的產品行為。

手動排入的交易備份只複製會被供應游標更新、結果投影與保存覆寫的分支。它保留失敗時復原文件與 journal 的界線，不複製無關素材與歷史。其他交易仍使用原有完整備份。只寫入而不讀回的 entry 不再解碼剛寫入的完整紀錄；journal 內容不變。

**已知限制**：`progress_backup` 是局部備份。`accepting(..., suppliers=...)` 能回復供應根、已複製的容器、output 與場景快取等列明分支；對無關 root、連線或其他共用深層物件的穿透寫入，交易失敗也無法回復。固定測試同時重現這個限制，並證明測試監看會在寫入當下阻止它；不能把這項監看當成正式執行的完整回復機制。交易的 `document=` 判斷、分支清單及回復後舊子物件引用仍須由後續交易整理處理。

借用監看只存在於 [測試工具](../../tests/_borrow_guard.py)，由 [快速套件](../../tests/FAST_SUITE.md) 安裝函式替換；產品沒有環境判斷、監看 import、指紋或凍結複製成本。它涵蓋 `borrowed_base`、`execution_state`、`merge_saved(borrow=True)`、`capture(borrow=True)`、`execution_scene`、`copy_context` 及 `workspace_scene.load(readonly=True)` 的共用 dict/list。寫入先拋出含資料路徑與產品呼叫位置的例外，執行器亦拒絕被回呼吞掉的違規。

監看只涵蓋測試實際走到的上述入口；它以唯讀副本取代共用容器，不能驗證原物件 identity、使用者稍後編輯造成的快照時間錯誤、其他自訂可變物件或刻意呼叫 dict/list 基底方法的繞過。直接寫即時文件（例如合法的 reserve、save）、`prompt_sources.build` 的內部別名、`workspace_scene.validate` 投影中的非場景頂層值，以及其他尚未接入的路徑不是全域攔截範圍。後續存檔去重／背景存檔必須另驗主執行緒序列化時點；後續執行交易整理須處理父子上下文所有權與完整回復，不能以監看通過代替這些工作。

供 UI 與輪詢使用的工作存在性、進度及 attempt 標頭，由既有連線即時投影必要欄位，不讀取完整場景。完成回報、參數回執及公開的完整紀錄查詢仍取回完整內容。這些查詢沒有跨呼叫快取，也沒有增加連線、資料表、欄位或索引。

## 共用連線的交易入口

目前工作樹由 `Storage.transaction()` 管理 `studio.sqlite3` 的同步交易。
Storage 建立的連線攜帶同一個交易管理物件；`StageStore(db).transaction()`
委派給該物件，所有綁定此連線的 StageStore 都看見同一個 `transaction_depth`。
獨立測試／預覽傳入普通 SQLite 連線時，StageStore 仍建立自己的交易管理物件，
不建立額外連線、資料表、欄位或索引。

契約如下：

- 每次進入增加深度，離開時必定減少。只有最外層正常離開才提交；例外穿出
  最外層時回滾，提交失敗也由 SQLite 回滾，原例外繼續傳遞。沿用 SQLite
  隱含開始寫入交易的時機，不為純讀取建立新的鎖定策略。
- 巢狀範圍不設 savepoint，也不設 rollback-only 標記。內層錯誤若被外層
  捕捉，內層已寫入的資料仍留在交易中，外層繼續成功時一起提交。這保留原本
  StageStore 的語意；需要整筆放棄時，讓例外穿出最外層。
- `Storage.after_commit(fn)` 在交易內按登記順序排隊，在最外層提交成功、
  深度回到 0 之後同步執行一次。回滾／提交失敗丟棄整個佇列；被捕捉的內層
  例外不丟棄回呼，因為其寫入也沒有撤銷。交易外登記則立即執行。
- 執行回呼前先移走佇列，所以回呼可另開獨立交易；沒有遺留回呼會被下一次
  交易重播。回呼若丟錯，錯誤傳給呼叫端，尚未執行的回呼丟棄；資料庫此時
  已提交，不能回滾。回呼只能作簡短的提交後收尾，不能承擔文件接受或驗證。
- 此入口只管資料庫及回呼，不還原記憶體、檔案或 UI。`accepting()` 繼續負責
  原有文件備份／回復；不要把一般資料庫交易當成完整的文件執行交易。

`Storage.save(commit=True)`、cache／usage 寫入、StageStore 的新增、更新、
排序、參數編輯、複製及保留項目恢復，以及 Catalog 的資料列寫入都使用此入口。
最近生成的裁切及相簿刪除也透過 Catalog 的 Storage 進入；它們不另行直接提交。
在交易外呼叫仍於本次呼叫內立即提交，沒有計時器或背景序列化。
開庫 schema／備份初始化及工作流版本核對的 `BEGIN IMMEDIATE` 保留其原有專用
流程；原始 `db.commit()`／`with db` 不是可在本入口內混用的新寫入介面。

`Window.persist()` 仍立刻呼叫 save，但用 `after_commit` 清除原本的
`close_cache['local-state']`；在外層交易內回傳 True 表示 save 已接受請求，
最終寫入及耐久性仍由同步範圍與外層決定。成功提交後取消同一個待執行的自動
存檔計時器；若後來的編輯重新啟動計時器，保留它。外層失敗保留恢復副本。
`resume()` 的保留 entry、
所有 scheduler 暫停旗標及 run 狀態在同一交易內恢復，成功後才通知與安排派送。
排序管理的多個 owner reorder 與移除亦在同一交易；移除呼叫鏈的
`stage_queue_actions.finish_entry` 加入同一深度，套用成功後才建立 queue Undo。

### 同步合併存檔與驗證去重

`StageRunner.accepting()`、派送與結果收集已透過 StageStore 共用此邊界。
`accepting()` 在交易內開啟 `Storage.coalesce_saves()`，巢狀範圍共用待存請求。
同一 state 的多次 save 合併；有待存文件時，結尾的 persist 也納入同一範圍，
保留視圖位置與恢復副本清除。合併範圍離開時先同步呼叫
`Storage.save(..., commit=False)`，驗證、序列化並寫入，然後才離開交易。
最後寫入丟錯會穿過 accepting 的文件／journal 回復；沒有讓出事件迴圈。
`commit=False` 本身只放棄自行提交，只有明確的合併範圍才延後寫入；合併範圍
必須在 managed transaction 內，不能跨過事件迴圈、計時器或背景工作。
`after_commit` 適合保存成功的清除／通知；它不是待存文件佇列。

`save()` 先呼叫 `synchronize_document()`，改寫 active output 的
`draft`／`draft_base` 或 `list_draft`、`list_state`，並深複製目前場景至
`workspace_scenes.items[workspace]`。即使合併，這一步仍立即執行，保留中間
讀取者看到的衍生資料；最終 flush 再同步一次。這些寫回可在驗證失敗前發生，
仍由 accepting 的既有備份還原。沒有擴大其回復範圍。

序列化寫入在 `_write_document()`。外層 `validate_state()` 驗證共用素材、設定、
工作流與 content_history；`workspace_scene.validate()` 只驗場景欄位及其參照。
共用尾端驗證仍在第一個場景的原位置執行一次，保留多項錯誤的回報優先順序。
save 剛產生的目前場景是已驗 live 欄位的深複本，省去重驗這些欄位，但仍檢查
場景容器、檢視位置及版本；一般 validate_state 不信任舊的目前場景快取。

content_history 每次都驗證：既有合併編輯可原地修改最後一筆，單靠 journal／
尾筆 identity 與筆數無法失效。容量檢查產生的 JSON 只在本次同步 save 重用，
以相同編碼器接回原插入順序與空白；沒有跨次快取或格式變更。場景深複製保留，
沒有假設所有讀取者都先 capture。借用資料的同步序列化時點持續有效。

[存檔去重回歸](../../tests/test_storage_save_dedup.py) 鎖住 body、衍生寫回、
驗證反例、巢狀提交、計時器與最終寫入失敗後再次點擊的行為。

[交易回歸](../../tests/test_storage_transactions.py) 用另一條連線確認提交可見性，
涵蓋內外層寫入、三層與捕捉例外、回呼／提交失敗、persist 恢復副本，以及
resume／排序管理中途失敗。它已納入一般及 borrow-guard 快速套件；離屏資料
驗證不代表真實服務、原生桌面或 GPU 驗收。

## 配置、歷史與相容

Canvas 配置屬工作區與 Undo；`stage_journal` 屬執行紀錄；生成回執沿用原有紀錄。三者不能互相替代。狀態恢復集中於載入邊界，PNG 不含工作佇列。舊 `chain_*` 與早期排程仍保留相容读取用途，v7 主路徑使用 Stage；無 Stage 不得回退到舊生成旁路。

共享模組可在無 Qt 環境匯入與處理資料。目前工作樹的圖片邊界分為 [composition_image](../../prompt_calculus_studio/composition_image.py)（純資料、驗證與來源路徑）及桌面專用的 [composition_render](../../prompt_calculus_studio/composition_render.py)（`PreviewCache`、`render_image`、`freeze_image_source`、`freeze_source`）。桌面模組不列入 `SHARED_MODULES`，共用模組不轉發桌面符號。[邊界測試](../../tests/test_shared_core_boundary.py) 檢查任意深度的 Qt 匯入與清單封閉性，並在封鎖 Qt 的子程序實際呼叫共用圖片函式；包匯入測試繼續核對組裝結果。

拆分保留繪製與凍結來源的回傳格式及 `originals/generation/` 路徑；`freeze_image_source` 每次經 `render_image` 新建預覽快取，編輯器與外層畫布各自持有的快取不變。原生瀏覽器頁面是目前 ComfyUI 介面的實作限制，不應變成所有未來模塊的前置條件。

操作見 [使用說明](../releases/0.8.4/development/stage.md)，遷移見 [資料相容](data-compatibility.md)。
