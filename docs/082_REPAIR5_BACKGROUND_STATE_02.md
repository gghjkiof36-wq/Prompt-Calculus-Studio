> 歷史開發紀錄：本文描述當時候選或實驗，不能當作2026-09-27公開Repair5的完成清單。現版仍需開啟ComfyUI網頁、單工作流一次；跨工作流、背景無Web、多圖下游逐張未完成。現行使用範圍見[使用指南](USER_GUIDE.md)及[來源對照](SOURCE_PROVENANCE.md)。

# 單一原生工作流：關頁交接與背景執行契約

2026-09-22；02限定核心交付，基準 `8b39e2f63648a5becc7f9af829095bbaddbfe063`。03已確認本文件的接受點與介面，並委派02實作 `comfyui_prompt_studio/background_state.py` 及專屬單元測試。03負責Service、routes、native queue、前端及整合驗證；04獨立審查。跨工作流與A→B暫擱。

## 用途與目前交付

使用者關閉Web前的未存尺寸、批量、文字和連線，經版本確認後成為背景執行來源。再次開啟同一原生工作流時，取回這個來源及合法seed推進後的編輯狀態，再接真正的執行事件。保存檔、PCS舊profile值與已完成圖片都不能取代這条證據鏈。

本提交只有交易／版本／節點驗證／seed核心，沒有發HTTP、WS、生成或操作原生頁面。原生恢復、真事件訂閱及產品入口由03整合，以下前端要求仍待獨立實機驗證。此交付不宣稱完整Web可關已通過。

## ADR：Service成為本範圍唯一接受點

- 背景：原 `bddd338` 的 `082_REPAIR5_SYNC_CONTRACT_02.md` S1建議由PCS Storage接受；本輪需要Web關閉及桌面不持續開啟時，Comfy服務仍能核對同一原生版本與提交日誌。
- 選擇：沿用Service的 `integration.sqlite3`，新增 `background_states`、`background_operations`、`background_receipts`；由BackgroundState單獨寫入可執行版本、租約與收據。Service.lock及同一SQLite交易保護它們。
- 影響：明確取代本範圍的舊Storage接受點提案。Storage繼續保存素材、手動稿、畫布及綁定，僅引用收據；不得再接受另一份可獨立演化的effective graph。現有資料不遷移、不覆寫歷史；缺背景紀錄一律尚未交接，不讀保存JSON當替代。
- 驗收：capture版本／內容／收據原子保存；bind真prompt後才原子推進seed後狀態；舊修訂、不同writer、未知提交及重啟不默許重送。舊版本可忽略新增表，回復仍使用各自相容資料副本，不聲稱舊程式理解新收據。

## 現機能力範圍

唯讀核對的圖有八個節點、七種標準class：KSampler、EmptyLatentImage、VAEDecode、CLIPTextEncode兩個、PreviewImage、CheckpointLoaderSimple、LoraLoader，均為mode0；不是八種自訂節點。

核心支援這七種class的平面圖、一個KSampler、至少一個PreviewImage；不寫死原有連線或數值，因此支援範圍內的結構改動可被確認。上限128節點／512連線／4MiB捕捉資料。visual widget與link重建的inputs必須逐欄等於output，ID與class也必須一致；交由原生 `/prompt` 最終檢查資源及執行合法性。

明確拒絕其他class、subgraph、bypass／mute、未知序列化、widget轉成尚未支援的輸入連線、缺必要連線及未知hook。CLIP支援一般字串、Unicode和空字串；本版拒絕含 `{`、`}`、`//`、`/*` 的動態／註解文字，避免把前端展開當成原文等價。兩份獲准讀取的現機匯出在此限定映射下相等，但不是對關頁之後新編輯的自動保證。

OpenPose已核包裝在queue前同步它自己的render style；已知圖不含OpenPose節點，不能僅因安裝此擴充就否定八節點全部能力，也不能由靜態節點名宣稱所有runtime hook都相同。03須核對實際掛載的hooks，才能給出 `hooks_verified:true`。

## Python介面與DTO

建構 `BackgroundState(service, *, multi_user=False, draw=None)`，只依賴Service的 `lock` 與 `connect()`。同一Service生命週期只建構一次；`server_epoch` 每次建構更新並把舊有效狀態標unknown。不得每個HTTP請求重建。unknown的lease也拒絕，回 `background_recovery_required`；不能從saved graph自動取得新租約重立effective。明確恢復／對帳入口留待有證據的後續補丁。`draw` 是可注入的[0,1)seed亂數來源，預設Python random。

`key` 必須且只包含 `library_id, workspace_id, workflow_id, frontend_id, path` 五個非空字串。本輪限default user；multi-user明示拒絕。03 route核對實際origin、profile綁定及default user，不信body自行宣告身分；不增加另一個authority table。

所有變更操作使用同一 `op_id` 重試，改內容必須換ID。capture/release/start的ID跨方法不能重用。除lease/snapshot外需傳 `server_epoch`，舊服務封包拒絕。

| 方法 | 最少輸入（均有key） | 回傳／保證 |
|---|---|---|
| `lease` | session, base_revision | lease_id, lease_epoch, server_epoch, revision, digest, phase, edit_seq；不同活writer拒絕，同session同revision可重取租約 |
| `capture` | lease_id, lease_epoch, op_id, base_revision, edit_seq, visual, output, seed, capability, source_revision | committed, revision, digest, edit_seq, op_id, server_epoch, source_revision；seq單調增加，revision遞增，收據與內容一次commit |
| `release` | lease_id, lease_epoch, op_id, revision, digest, edit_seq | released, revision, digest, op_id, server_epoch；必須與最新commit相等 |
| `start` | op_id, revision, digest, source_revision | prepared operation及submit_allowed；只有首次原子保留回true，重試同ID一律false，不重新抽seed |
| `bind_prompt` | op_id, prompt_id | queued operation及next_revision/next_digest；03提供真提交回覆／對帳證據後才呼叫，核心不自行發送或信任瀏覽器自報成功 |
| `submission_failed` | op_id, uncertain:boolean | 確定失敗標failed，不推進；不確定標uncertain並封鎖下一次提交，禁止自動重送 |
| `snapshot` | key即可 | revision/digest/phase/edit_seq/content/server_epoch；只供受信route讀取，unknown不等於可執行 |
| `attach_snapshot` | prompt_id, revision, digest, new_client_id | 原任務payload與current狀態；只讀核對，不建立WS、不改sid、不搶租約、不宣告正在執行 |

`content={visual,output,seed,capability,source_revision}`，digest對整份content作canonical JSON SHA256。捕捉JSON與執行JSON來自同一版本，output是其已驗投影；核心不維持另一份可任意改寫的profile.values。

`capability={version:'standard8-v1',frontend:'1.43.18',backend:'0.21.1',hooks_verified:true}`，尚未核對的前端或hooks不得偽填此值。

`source_revision` 是64位小寫SHA256：03的route按當時PCS真實bound_texts來源清單產生，覆蓋body值，沿用NativeQueue digest（JSON ensure_ascii=False、sort_keys=True、預設separators）。它不同於本核心content digest的compact JSON算法；核心只驗證格式和相等，不自行讀庫或重算來源。start再用本次真snapshot的同算法來源hash比對；若Canvas文字已改，回 `background_source_changed`，要求重開同步。不忽略新PCS來源，也不把新Canvas字串覆蓋Web已確認的個別手寫欄位。

operation含原revision/digest、固定payload、seed_transition、after內容及server_epoch。payload形狀為 `{prompt,extra_data:{extra_pnginfo:{workflow,pcs_background}}}`；pcs_background有version、operation_id、原revision/digest/source_revision。03接線加入真正client_id及既有PCS原稿／圖片來源metadata；不得重新覆蓋prompt inputs。

prepared之後尚不能標queued。真/prompt回覆確認後 `bind_prompt` 同交易寫prompt_id與下一個released版本。原operation的payload、revision及digest永久保留，after只推進當前編輯的合法seed狀態。若提交不確定，新的start及lease均拒絕；同epoch取得真回覆後可bind。不確定操作跨服務重啟保持封鎖，本版沒有猜測式恢復／清除入口，需03另做有真證據的對帳擴充。

attach的revision/digest指**原任務**版本；current必須仍等於該任務的next_revision/next_digest，否則 `background_attach_current_changed`。next最初由bind產生；只有下述已驗等價capture可延續其關聯。本版不把較晚編輯回退成執行圖。attach只返回上下文，03仍須核實queue/history實際running／complete狀態及事件序號。

### 重開後的等價版本關聯（2026-09-22 有界修正）

首次重開會重新取得writer並capture，即使使用者未改內容也會產生新revision；若任務仍只指向bind後的版本，第二次重開便找不到原prompt。capture現於同一SQLite交易內，僅在新舊已驗content的完整output、seed、capability、source_revision及visual.id具有相同canonical JSON時延續關聯。visual布局、節點序列及非執行序列化metadata可以不同；visual/API驗證仍先執行，不能以摘要相等取代驗證。

只更新同key、key_hash與server_epoch、狀態queued，且next_revision/next_digest同時精確等於capture前state的operation。更新的是接回目標版本；原prompt_id、revision、digest、payload、after、seed_transition與來源均不改。較舊任務不能只憑內容相同復活；一旦執行語義改變，即使之後改回原值，原關聯仍已中斷。prepared/uncertain仍依原規則封鎖。

operation新增可選 `equivalent_revisions` 證據陣列，每次保存capture_op_id、from_revision/from_digest、to_revision/to_digest與execution_digest。execution_digest是上述五個比較欄位（visual.id以visual_id命名）的canonical JSON SHA256，證明相鄰capture採用相同執行內容；完整視覺內容仍由原有content digest識別。證據、next目標、state與capture receipt一起commit；同op重試不追加第二筆，交易失敗全部rollback。舊operation沒有此欄位時視為尚無延續證據，無需遷移或增加資料表。

回退到不含此修正的核心不會建立新的等價關聯；其既有next版本檢查仍保留。服務重啟照常標unknown，不由新增證據自動恢復或放行。這項修正不提供瀏覽器BFcache恢復、事件訂閱、任務完成狀態或跨服務對帳；以上仍由03整合及04/05獨立驗收。

錯誤使用固定 `background_*` 代碼（ValueError），不攜帶文字、路徑、資產或原始網路錯誤；03映射固定中文提示。

## Seed等價與恢復

`seed={node_id,timing,hasExecuted,policy,seed,min,max,step2}`。timing是before/after；policy是fixed/increment/decrement/randomize；hasExecuted必須由已核原生widget的生命週期觀測取得，不能由JSON猜測。nodeCreated初始false，捕獲原beforeQueued正常返回後標true；hook被替換或錯過初始觀測時拒絕。

`prepare_seed(seed, draw)`回 `{execution_seed,after_seed,next_has_executed:true}`，不改輸入、不呼叫widget或queue。before模式首次不改seed，之後提交前改；after模式本次使用目前seed，成功後改。numeric upper clamp為2^50，按原生step2抽樣和夾限；seed須能由JavaScript精確表示。本版只接受整數seed bounds/step2，未知狀態拒絕，無須迫使用者改fixed。

randomize每次新operation最多抽一次，transition随prepared日誌保存；同ID重試不重抽。bind後才接受after內容；明確提交失敗不推進，符合本輪凍結要求。這不是Bitwise承諾Python與瀏覽器會抽中同一顆seed；驗收比對已凍結的實際seed與真payload。

前端1.43.18的私有 `HAS_EXECUTED` Symbol不在workflow JSON。重開已執行的控制器時，03可對**已核捕獲的原生seed control beforeQueued**呼叫一次 `{isPartialExecution:true}`：原碼會跳過applyWidgetControl，只把旗標置true。這是版本限定的生命週期恢復，不能遍歷呼叫任意custom hook，更不能呼叫app.queuePrompt當dry run。false保持新節點預設。小型離線probe已驗證這條原生分支，實機仍由03／05驗。

## 正常關頁與捕捉原子性（03接線責任）

1. 取得writer租約；提交活widget文字（包含空字串），等composition完成。捕捉前後核對activeWorkflow/path/id/rootGraph、edit_seq、完整graph／widgets fingerprint與settings/hook能力。
2. 呼叫原生graphToPrompt只作序列化；它會await serializeValue，因此前後一致性不可省。本版已知literal文字不應有動態展開副作用。不得probe queue或在捕捉中默跑before/afterQueued。
3. capture收到committed收據後，保存該次完整活圖fingerprint。任何新編輯、序列化尚未完成、未回ack或世代改變均使可release資格失效。
4. 依03指定正常pagehide：同步stopped，非BFcache/persisted，無await捕捉／pending，當下完整fingerprint等於最後commit，才用keepalive送小body release。正在IME composition也不得交接。
5. release未送達、dirty、頁面崩潰、未知writer均保持不可背景執行；poll silence和timeout不是release。pageshow/BFcache不得沿舊租約直接提交，須重取／確認租約及新capture。

核心只驗已提交的lease/seq/revision/digest，無法替瀏覽器證明最後按鍵有送達。前端release條件是必要驗收項，不能只用核心單元測試宣稱正常關頁已完成。

## 重開同原生圖與真進度（最小surface）

現機GraphCanvas用 `useWorkflowPersistenceV2`：512ms debounce，workspace-scoped草稿與session指標；`ComfyWorkflow.load()`仍引用V1 draftStore。只呼叫workflow.load或讀磁碟JSON均不足以保證V2未存版本。不要直接修改私有localStorage格式。

最小候選是在setup安裝版本受控的**冷啟動首個loadGraphData** adapter：只在尚無activeWorkflow／舊tracker時，在呼叫original前取得server snapshot、唯一定位原生完整path、載入該ComfyWorkflow物件取得磁碟baseline而不先open store，再把同一物件作第4參數交給原native loader一次。拒絕熱載入已有編輯圖，避免N03的清圖空窗。

`beforeConfigureGraph`之前已clean，單靠該hook無法修N03。原生afterLoad會reset tracker baseline；03須保留原saved baseline與dirty含義，核activeState/rootGraph/path/id及capture指紋。後面的restoreTabs可能再打開別圖，最終核對不可省。初始圖不能確認／已有另一圖時保留現場並拒絕，不用舊JSON作成功回應。

原生queueStore的history loadWorkflow只處理已完成任務，傳loadGraphData時無指定原path，可能建立temporary；不能拿它冒充相同原生工作流恢復。

新頁面用**自己的新sid**，按真prompt_id／epoch／revision訂閱。不得與PCS既有WS共用sid（後端會移除舊socket登記）。原生重連只補node，沒有完整prompt／步數快照。

03的真事件橋須在提交前就記錄原生start／progress_state／executed等完整payload和序號。attach在圖版本核對後回傳有來源的running快照，或重送已記錄的真start及最新真progress_state，保留原timestamp並標明replay／snapshot，再無縫接續較新序號；不得構造假的start、完成圖片或百分比。先訂閱／取得cut再補快照，避免關卡間漏事件。已完成則只呈現真history結果，不假装重新執行。

1.43.18的progress_state可更新節點進度，execution_start建立activeJobId；queue/history刷新只補job→workflowId。完整queuedJob.workflow／session path由內部executionStore.storeJob管理，公開extensionManager沒有此setter。限定同圖的真事件接回與完整原生job/path登記需分層驗證；04正在獨立核對受控surface。不能發明 `extensionManager.execution.storeJob` 或用假queue取回它。

原生executed handler直接定位當前rootGraph節點，因此03必須在身份或版本變動時解除訂閱／拒絕晚事件。對後端既有真事件作有身分轉送與用舊history反灌成新事件不同；兩者在紀錄和畫面上須可區別。

## 原碼證據索引

以下前端位置固定tag `v1.43.18`，行號按實際原檔（非網頁去空行版）：

- [app.ts](https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.43.18/src/scripts/app.ts)：1122 loadGraphData；1198後beforeConfigureGraph；1410 afterConfigureGraph；1424 afterLoadNewGraph；1548 graphToPrompt；1554 queuePrompt；1592 before hooks；1620 storeJob；1714 after hooks；729 executed handler。
- [widgets.ts](https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.43.18/src/scripts/widgets.ts)：69 timing設定；82私有旗標；235數值控制；278／288 before／after。
- [executionUtil.ts](https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.43.18/src/utils/executionUtil.ts)：27 graphToPrompt；44 visual serialize；102 await serializeValue。
- [dynamicPrompts.ts](https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.43.18/src/extensions/core/dynamicPrompts.ts)：nodeCreated安裝文字serializeValue，不可假定CLIP純讀widget即通用等價。
- [useWorkflowPersistenceV2.ts](https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.43.18/src/platform/workflow/persistence/composables/useWorkflowPersistenceV2.ts)：93 persist；132 debounce；173 initialize；257 restore tabs。
- [workflowDraftStoreV2.ts](https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.43.18/src/platform/workflow/persistence/stores/workflowDraftStoreV2.ts)：105 save；285 tryLoadGraph；318 restore。comfyWorkflow.ts:91 load仍讀V1；workflowService.ts:372 beforeLoad及432 afterLoad。
- [executionStore.ts](https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.43.18/src/stores/executionStore.ts)：248 handleExecutionStart；335 handleProgressState；536 storeJob；610附近active workflow path判定。queueStore.ts:418 loadWorkflow、543 job→workflowId；api.ts:550 socket、719 status/sid、731 executing縮減。
- backend0.21.1本機：server.py:258 websocket、917 post_prompt、1206 send_json、1217 send_sync；execution.py:661 add_message、720 client_id／真execution_start；comfy_execution/progress.py:162真progress_state、325每次reset registry。後端progress handler只附在目前registry不會自動跨run保留，不能註冊一次就假定永遠收得到。

已核現機bundle SHA256：GraphView-B1Csq2pm.js `FBB9FF549426E8817F950D64121C87B37270E0800547B2600B143CFCB918BCCE`；dialogService-DSBgqcNn.js `1D870DF50815A0FAAD3645F65A008E384337E67757364FB423DBB74C0B066FF7`；api-D9vMMk51.js `FE861923015625DE8DF356DC60A65920A9892DEE0E035A968337641073C86989`。這證明安裝檔，未讀本輪瀏覽器cache。

## 驗收與限制

- Python專屬測試：`python -m unittest discover -s tests -p test_background_state.py -v`。合成SQLite資料，涵蓋原子commit／rollback、並行start只給一次permit、收據重試、來源變更、舊writer、關頁未交接、重啟unknown、真ack後才推進及原payload不可變。
- 本組ignored qa/source_probe.mjs：抽取官方widgets.ts實際numeric before/after程式，去型別後用假widget執行。6項通過：before首回合、after、JSON丟失旗標、partial初始化、夾限與獲准私有八節點visual/API映射。widgets原檔SHA256 `43E1551EBCF9753E8A3EE47AFC0B647EACBECEBD2303BAB7F155DD3F311C820D`；probe SHA256 `928DEF23EA02C6B6D82DDE42D1DA111AD76B16D2B0B0BDAFDD7BD7F19F0EB7E0`。私有原圖／文字／資產名不入Git。
- 仍需03／05：關頁前未存新參數／手寫空字串／連線→release→Web真的關閉→真KSampler→兩圖；執行中從瀏覽器歷史冷啟動重開同ID與path→真進度／新圖；source_revision改變、IME末字、dirty、keepalive遺失、BFcache、未知hooks控制。
- 持久化未知提交的跨服務對帳、完整原生job/path註冊、其他節點與動態文字均未由本核心解決。沒有安裝／打包／发布，沒有A→B或一般通用工作流平台。
