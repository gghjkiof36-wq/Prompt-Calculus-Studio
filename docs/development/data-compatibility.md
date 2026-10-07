# 資料與相容性

適用：0.8.6 Alpha 1。Stage 參數沿用 0.8.5 的保存與覆寫規則。

## 導覽、起始流程與更新設定

0.8.6 沿用 Stage 明確覆寫與等待項保存規則，沒有把介面改版當成新生成命令。新資料與明確新增的工作區可建立六節點起始流程；既有布局、空手動稿、綁定及刪除結果不因載入而回填預設流程。

啟動切到 Canvas，不清除清單／畫布草稿。色彩、字級、側欄寬度及透明度等偏好留在目前資料設定，改布局不發送生成。配套擴充安裝偏好獨立保存於資料目錄；未確認安裝交易保留紀錄並停止自動重試。

提交與取消沿用任務身分核對；單筆移除只處理可移除的等待／已確認失敗項，底部取消保留其他項並暫停，全部取消由明確入口發起。舊 journal／PNG 不因導覽重建而重送任務。

操作見[入門指南](../guide/getting-started.md)與[Stage 參數](../guide/stage-parameters.md)，0.8.6 的證據見[0.8.6 驗證範圍](../releases/0.8.6/validation/alpha.1.md)。更新和回退仍以資料副本與相容主程式／擴充為單位。


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
