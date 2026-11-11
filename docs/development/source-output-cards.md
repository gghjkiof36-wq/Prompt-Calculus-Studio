# 圖片來源與 Prompt 卡片（格式 7）

此實作僅適用 `multi_output.version >= 7`，不改格式、欄位名稱、資料值或 Stage 供應／排入語意。舊單畫布與較早格式繼續使用 `canvas_functions.SourcePanel`；新卡由 `source_panel.SourcePanelV7` 呈現。外觀使用既有 Canvas 色彩、圖示、選單與 `StudioDialog`。

## 圖片清單與匯入

`source_picker` 將四種選取方式接到 `source_import.SourceImport`。本機、媒體庫及最近生成支援多選，資料夾仍依自然檔名順序讀取。選取後不開清單頁，進度及取消在卡片加入列顯示。`image_bindings.import_source` 的驗證、讀檔、SHA-256 及內容定址複製在既有 `Jobs` 執行緒執行。完成後主執行緒透過一次 Canvas `commit` 加入，構成一筆 Undo。

`source_list.append_items` 保留非空清單的目前圖片、索引與提示詞選擇，換新 `batch_id`，兩張以上使用 `iterate=True`；空清單沿既有 `set_items`／`select` 初始化。來源資料不包含執行緒、進度或匯入旗標。匯入工作的擁有者為文件（舊宿主則為視窗），同卡重入與依賴該卡的執行入口被拒絕。取消／過期完成不提交；已複製的內容定址檔案保留。單檔錯誤累積成一則完成提示，其餘成功項目仍一起加入；工作整體失敗不提交。

工作捕捉 store、workspace、Canvas 編輯上下文與來源快照，完成前重新核對。`Window.is_closed()` 封鎖新工作與晚到回覆；既有 Jobs 關閉流程設定取消並等工作結束才釋放 store。`SourceListDialog` 的加入只改對話框候選，完成才提交；關閉會取消尚未完成的加入。執行中及完成時再次檢查 `source_active`，避免清單開啟期間開始執行後覆蓋固定清單。

匯入期間停用該卡的切圖、分段切換、空白預覽加入，以及選單的顯示圖片、提示詞來源和改用圖片清單；已有圖片的唯讀預覽仍可開啟。結束後恢復原有可用條件，已開啟選單也同步更新。Undo、其他 view 的來源修改、工作區切換或刪除等造成快照失效時，進度與完成回呼共用取消判定，只提示一次「已取消匯入圖片：圖片來源已變更。」；不採用匯入結果、不增加文件寫入。主動取消及文件／程式關閉不顯示取消提示，真正的匯入錯誤仍沿原路徑回報。

縮圖由 `source_thumbnails` 在工作執行緒產生 QImage，Qt 主執行緒接收後更新格子。格子與編號先出現；GUI 擁有的 LRU 最多保留 512 張 144×108 縮圖。對話框關閉、重載或銷毀會取消工作，序號拒絕過期回覆；工作執行緒不存取 QWidget、不建立 QPixmap，也不寫文件。宿主關閉屏障也會取消並等待縮圖 reader 釋放檔案後，才繼續原本的 store 關閉流程；沒有來源縮圖工作的舊格式路徑行為不變。

## 接線與相容入口

接線與舊 `stage_reference` 的優先順序及解析函式不變。卡片切圖仍呼叫既有 `step`，`全部` 清除 `input_index`，`只用這張` 呼叫既有 `ImageBindings.choose`。只有無圖片接線的舊 Stage 引用顯示「來自」及「改用圖片清單」。來源卡選單保留顯示圖片、原 `choose_prompt` 的提示詞欄位設定及通用卡片操作。

v7 移除的介面入口：更換圖片十項選單、建立 Stage 引用、圖片管理開關、接入圖片清單對話框、略過選取圖片及立即切到選取圖片。其對應操作為四項加入、圖片接線、直接清單入口、卡片切圖與輸入模式、縮圖移除及候選起點。舊格式的原始選單與手動清單仍留在原模組，未刪除；舊 `stage_reference`／`management_collapsed` 欄位仍可讀。

舊入口保留清單（若要移除，須另批處理舊格式相容性）：

| 位置 | 保留入口 |
| --- | --- |
| `canvas_functions.SourcePanel` | 原十項來源選單、圖片管理與提示詞設定控制項 |
| `source_controls.choose_many`／`choose_recent`／`edit_list` 的非 v7 分支 | 原替換式加入、最近生成與手動清單對話框 |
| `multi_canvas.OutputPanel` 的非 modern 分支 | 原摘要及「開啟 Prompt 輸出」按鈕 |
| `prompt_source_widgets` 的非 v7 顯示分支 | 原「逐輪」標籤與欄寬 |

## Prompt 預覽

### v7 圖片接線與結果重用（B3-GEN）

新圖片線只能接圖片輸入等接受圖片的模組，不能接 Prompt 輸出。`multi_output.valid_edge`／`flow_data.valid_edge` 預設驗證新建；文件驗證及已存流程投影明確使用 `stored=True`，保留舊圖片→Prompt 線。舊線顯示「舊連線 · 不送出圖片」與舊圖片接點，可以刪除或拖動目的端改接；拒絕的接點不當成空白處刪線。依賴判斷仍沿用 `stage_model`／`prompt_sources`，樣式不代表舊線對排程完全無影響。

最近生成、Canvas 生成結果及圖片預覽共用 `image_source_action.use_image`，名稱為「用這張圖當來源」。`image_input_routing` 只在目前 Prompt 有唯一直接 CLIP 工作流歸屬時提供同工作流候選；LoadImage 必須存在、image 欄可寫、綁定不重複，且輸入未接來源。多個可用卡讓使用者選擇，有來源的卡不可選；沒有可用目標或歸屬不明時，明確選工作流與未被綁定的 LoadImage，再建立來源、輸入及連線。不靠 Stage 組合推測工作流，不自動接下游 Stage，也不載入圖片的 Prompt。

目標確認前不匯入圖片；取消不改文件。確認後重新核對文件擁有者、profile、綁定及占用，以一次 `Canvas.commit → DocumentOperations.commit` 建立一筆 Undo。來源素材沿既有內容定址保存；提交失敗不改文件，但已匯入的素材檔依原清理規則保留。改線沿 S4 全量刷新，Undo／Redo 與其他視圖走既有通知。沒有舊線一鍵轉換功能；手動改接與結果重用共用綁定／歸屬／占用條件。

### 預覽文字

`OutputPanel` 及 `OutputEditor` 都取 `multi_output.compile_output(...).final_prompt`，同樣保留手動內容（含空字串）和來源錯誤時的既有顯示。`PromptPreview` 是自繪 QWidget，不含編輯器或捲動區，只排版至多六行，更新前比較完整文字。每個 Canvas 共用一個 `PromptCache` 輸入快照，以既有 `changes.layout_only` 契約忽略幾何變動；歷史、未啟用工作區快照及視角不參與目前工作區的編譯。輸入改變才清除文字快取，保留副本也能檢出執行游標原地變動。完整文字用於複製；拖曳（含放開提交）、平移、縮放不觸發編譯。`ModulePage`、來源排序樹與編輯器仍按需建立；「排序」聚焦來源樹，點文字聚焦編輯器。

「讀取流程」卡將 step 顯示為「每次一組」，batch 仍顯示「整個流程」，僅收窄顯示控制項，資料值不變。

驗證入口：`test_source_redesign.py`、`test_canvas_four_modules.py`、`test_output_source_tree.py`、`test_canvas_card_menus.py`、`test_088_canvas_entrypoints.py` 與 `test_native_file_dialogs.py`。以相同合成負載另量測 197 張匯入、清單首幀與大文件編輯；背景化本身不代表通過主執行緒停頓門檻。
