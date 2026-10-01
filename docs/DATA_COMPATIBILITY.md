# 0.8.5 Stage 參數：資料與相容邊界（2026-10-02）

本輪在既有 Stage 配置與 `stage_journal` 增加明確參數意圖，沒有把原生工作流全圖另做一份執行來源。操作見 [Stage 參數說明](STAGE_PARAMETERS.md)，本輪證據與未驗範圍見 [驗證記錄](validation/085_STAGE_PARAMETERS.md)。

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

離線相容檢查使用合成文件、獨立 SQLite 連線與 PNG 副本，涵蓋手動空稿、未知欄位、工作區隔離、舊資料缺欄及不支援版本；實際結果以本輪驗證記錄為準。這些檢查不等於使用者真實資料、舊版降級、ComfyUI 網頁或 GPU 驗收。

---

以下保留 2026-09-14 的歷史相容記錄，不代表本版未新增參數設定。

# 資料與相容邊界（2026-09-14）

桌面文件入口為 `Storage.load_current`，先由 `Storage.load` 驗證原資料，再用 `state_loading.prepare_state` 取得轉換副本。只有原文件確實存在且需要轉換時才先備份、再保存；新安裝不製造空文件備份。JSON 匯入在確認取代之前準備並驗證候選資料。歷史快照恢复也使用同一轉換入口，最後仍比對歷史生成文字。

| 相容資料 | 保留原因與處理 | 驗證 |
|---|---|---|
| 文件 version 1–4 | `core.validate_state` 先拒絕不支援版本；預設設定補齊不改既有值 | core／保存往返測試；未知版本不覆寫原文件 |
| 舊 Canvas 素材分類（0.7.0 時期） | `composition.migrate_canvas_library` 保留順序、權重、原型封存與工作區；比對轉換前後文字 | `test_canvas_refinement`、`test_maintenance_loading`，備份含原文件 |
| 多輸出早期連線與舊預設名稱（0.8.0 Alpha） | `multi_output.migrate` 升級一次；目前版本不重接已移除連線 | `test_multi_output`、`test_v08_alpha3` |
| PNG 快照 schema 1–4 | `snapshots.validate_snapshot/restore_snapshot` 驗證其自身文字；從未使用最新 Prompt 修補歷史 | `test_comfy_integration`、`test_snapshot_ui`、多畫布恢復 |
| SQLite／ZIP 副本 | 保留原資料目錄的相對圖片引用；ZIP 還原在副本目錄操作 | Alpha 2 備份驗證與維護載入往返；不新增 ZIP 匯入 UI |

`refresh_builder` 不再執行資料格式遷移。它仍整理目前組合的有效選項與順序，這是原操作的一部分，並非歷史資料版本轉換。`Storage.load` 保留為原始文件唯讀檢查入口；新增介面不能自行增加相同遷移判斷。

CivitAI 的 API 映射已集中於 `civitai.normalize_version`，詳細資料與下載計畫均使用它；缺失數據保留缺失，UI 顯示「未提供」。`civitai_assets` 將所選版本／實際檔案轉成下載計畫，Type 的目錄映射與 Base Model、用途分類各自保存。來源以 `asset_sources` 的 Hash 索引保存；`resources` 保存實際檔案與使用者資料，辨識更新只合併 `SOURCE_FIELDS/HASH_FIELDS`。未改動這些既有資料欄位，亦未引入新的資料格式。
