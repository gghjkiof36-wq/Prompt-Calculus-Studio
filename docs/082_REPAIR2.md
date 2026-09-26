# 0.82 Alpha 1 Repair 2

本批修復連線後工作流清單縮減、Canvas 圖片取得不到生成結果，以及設定頁殘留的綁定入口。保留不開 ComfyUI 網頁也能生成；網頁開著時，對應工作流會呈現當次已綁定文字和結果圖片。

## 使用方式

- 綁定集中在 Canvas 的「CLIP 輸入」及「加載圖片」。設定頁保留工作流檔案管理，ComfyUI 側欄保留匯入／匯出與資料連接，刪除兩處綁定表單。0.82 匯入交換檔不會清除、重設或新建 Canvas 綁定。
- 點連線後會更新工作流清單；同一伺服器重連期間保留現有清單。切到不同伺服器才清掉另一來源的清單，舊回應不能覆寫新來源。
- 「加載圖片」依工作流 ID、節點和後端任務取得結果，再送入「預覽圖片」；不必先在網頁生成或顯示該圖片。來源檔名提示可看到實際任務編號與節點。
- 執行列新增「任務紀錄」入口，記錄伺服器、工作流來源、實際提交編號、等待／執行時間、最後節點、後端錯誤與結果圖片。顯示實際提交的各綁定文字及工作流，不從當前工作區補寫。
- 可取消時使用清楚的紅底白字，無任務時保留停用樣式。原有停止目前任務及右鍵清空佇列行為保留。

## 根因與資料流

圖片查詢原本只讀 `prompt_studio_request.generation`。`Service.prepare_prompt` 在入列前已消耗該欄位，改存正式 `prompt_studio.generation`；同時後端 Python 佇列使用 tuple，原查詢只接受 list。結果有保存，圖片查詢卻無法辨識所属工作流。修復同時支援正式封套、舊請求封套、Python tuple 與 JSON list。回歸經真實 `submission → Service.prepare_prompt → NodeImages.resolve → ImageBindings.poll → Canvas preview`，未建立假網頁作為結果來源。

清單問題來自 `WorkflowCatalog.connection_changed` 在 epoch 更新時清空 files／loaded，但可見中的設定頁沒有再次觸發 showEvent，也沒有續接讀取。現在重連只失效舊請求，連線成功後讀取一次；同源舊清單可繼續顯示。

桌面生成將按執行時凍結的 API 工作流送到 ComfyUI 後端。網頁有自己的編輯圖，原本沒有接收這份桌面提交。新增同步沿用現有擴充本機 API，從同一個 queue/history 讀出最小的文字與圖片資料，不新增資料庫、不經網頁重新提交。網頁用完整已儲存路徑／可用的原生 ID，或交換檔 PCS ID 對照；不以顯示名稱或相似圖猜測。

同步只套用已綁定文字與本次接收的圖片欄位、對應 PreviewImage／SaveImage 結果；保留其他文字、模型、參數、排版與連線。等待期間切頁、換圖或修改文字會拒絕過期回寫；同一任務不反覆覆蓋後續手寫內容。A→B 仍以完成的 A 任務編號取得來源，不會被網頁另一張縮圖替換。

前端依據 ComfyUI 的 [ExtensionManager 工作流介面](https://github.com/Comfy-Org/ComfyUI_frontend/blob/main/src/types/extensionTypes.ts)、[工作流路徑](https://github.com/Comfy-Org/ComfyUI_frontend/blob/main/src/platform/workflow/management/stores/workflowStore.ts)和 [nodeOutputs 相容入口](https://github.com/Comfy-Org/ComfyUI_frontend/blob/main/src/scripts/app.ts)。後端結果與事件原理見 [官方訊息文件](https://docs.comfy.org/development/comfyui-server/comms_messages)。舊前端若沒有已儲存工作流介面，仍可按交換檔 PCS ID 對照；無可靠身分時不修改別的工作流。

## 診斷與相容

任務紀錄沿用 `generation_jobs`，新增節點、時間、輸出和錯誤事件欄位；舊列仍可讀。舊工作流、手動空稿、欄位唯一綁定、工作流順序與 undo／redo 不改格式。新增原生崩潰堆疊記錄 `fault.log`，跟隨所用資料目錄；一般 Python 例外仍用既有 `error.log`。

本次未取得卡死當時的例外或記憶體证據，無法判定是記憶體、CKPT 或其他原因。等待時間只供對照，沒有自動重送、判死或重啟 GPU。操作系統直接終止程序時也可能來不及寫入堆疊。

## 更新與驗證

需要同時更新 Repair 2 桌面版及 `comfyui_prompt_calculus_studio` 擴充，重啟 ComfyUI 後端並重新整理網頁。只更新 EXE 無法修復已在執行的舊後端圖片查詢。安裝時保留原 `local_library.json` 與資料，兩個舊／新名稱的擴充不可同時載入。這次提供新包，不代為停止或改動實際安裝。

`qa/Open-PCS-082.vbs` 指向 Repair 2 EXE，沿用原資料。原日常資料僅唯讀核對工作流／節點／任務身分；未重置、寫入、測試或打包。所有回歸和 EXE 檢查使用獨立臨時資料。

本組離線證據：`qa/082-repair2-image-before.log` 記錄修復前正式封套無法找到圖片；`qa/082-repair2-final.log` 記錄修復後圖片完整路徑、清單、綁定所有權、快照、A→B、取消與任務診斷回歸；`qa/082-repair2-js.log` 記錄前端同步、編輯保護及既有橋接相容。介面離屏圖與封裝結果另列交付索引。

未驗證：使用者卡死精確觸發、原生 Windows 合成器／多 DPI、實際 ComfyUI 前端版本的自動預覽及真實 GPU 生圖。離線通過不代表這些實機項目完成；仍需獨立 Review／QA。
