# 高風險狀態入口（2026-09-14）

| 責任 | 入口與所有者 | 保留的行為 |
|---|---|---|
| 歸屬與連線 | `multi_output.assign/connect/disconnect`，UI 經 `TextCanvas.commit`／`MultiCanvas.commit` | 候選狀態驗證後才替換；同一份 undo／redo |
| 文字欄綁定／解除 | `multi_output.set_binding`，工作流管理和畫布對話框共用 | 驗證目標與唯一性；解除只影響指定工作流／輸出；失效工作流仍可解除 |
| 手動稿／自動稿 | `drafts.edit/clear`；`multi_output.capture_current/select_output` 處理目前輸出 | 空字串仍是手動稿；首次自動稿基準不被重寫；清單獨立草稿保留；鍵盤編輯仍用原編輯器 undo |
| 模型來源 | `Catalog.update_identified_models` | 核對目前路徑、size／mtime 與來源 SHA；只合併來源欄位，保留最新名稱、Notes、Trigger 等覆寫 |
| 生成 | `GenerationRunner.run/submit_next/observe` | 開始前凍結 profile、snapshot、圖片與次數；批次 token 阻止繼續舊批次；接收已提交任務的回應仍記入它自己的歷史 |

來源更新及生成原本已有集中入口，本階段經回歸確認後沿用，沒有增加平行狀態層。`ComfyClient.request` 以連線 epoch 排除舊伺服器回覆；`GenerationRunner.disconnected` 停止後續提交，**不聲稱已取消伺服器收到的任務**。提交狀態不確定時不自動重送。

CivitAI 工作由開始前的参数快照執行；網路／Token epoch 與選取序號讓舊回呼失效。模型來源回呼再讀取最新 Catalog，介面的未儲存 Notes 先保存。圖片預覽載入採獨立序號與取消；關閉頁面清除排隊圖片。

驗證使用 `test_maintenance_state`、`test_multi_output`、`test_multi_canvas`、`test_generation`、`test_civitai_repairs`、`test_v081`、`test_v081_alpha2` 和 `test_maintenance_changes`，共 92 項。新增四項保護不同編輯入口、清單／畫布草稿隔離、空手動稿、只解除指定綁定。其餘測試涵蓋保存、復原、過期來源、Notes／Token 修改、取消與冻结提交。
