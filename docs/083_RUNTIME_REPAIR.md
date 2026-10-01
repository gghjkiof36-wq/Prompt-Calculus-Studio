> 歷史候選文件：此處的行為、待驗與完成狀態僅適用該次候選。0.8.3 Alpha 1 的操作以 [直接執行說明](PCS_083_DIRECT.md)、[入門指南](GETTING_STARTED.md) 及 [QA 摘要](validation/083_DIRECT.md) 為準。

# 0.8.3 執行與預覽修正候選

2026-09-27，依使用者人工問題回報及「先修正，接下來 0.8.3」核准；基準 2b88fdb6ea18e921981bf87a1e22c61a0a02b675。本次延續工作佇列候選，先完成自驗，再交使用者驗證 Anima／ill，收到人工結果後才通知其他組。沒有替換安裝或公開發布。

## 本輪修改

- 畫布頂部加入「工作佇列」，生成期間仍可開啟「加入目前工作」。直接執行已有未結束工作時顯示忙碌原因，避免重複提交。
- 執行／保存工作快照會依綁定的原生 ID 與路徑，定位同一 ComfyUI 分頁內已載入的目標工作流。切換到其他可見工作流不會改綁定；同名副本、目標遺失、分頁重複或舊網頁擴充仍明示停止。
- 同一圖片工作流由路徑識別轉為 PCS 識別時不再殘留兩份來源。不同瀏覽器來源仍分開；已完成且原生身分相符的實際任務結果，提供綁定輸出節點的完整批次。不同工作流或不明來源不代填。
- 原生序列化比較排除已知顯示更新：節點位置／大小／排序、翻譯標籤、摺疊狀態、視角及前端版本。原生 ID、連線、模式、widgets、任意節點 properties 與 PCS 歸屬仍檢查；真正改稿或換圖時拒絕提交。PCS 既有手動稿紀錄在比較前同步完成。

## 原生切換保護

切換使用 ComfyUI 已載入的原生 workflow 物件及其最新 activeState，不讀取 PCS 的舊 API JSON 作替代。先由原生 tracker 保存目前草稿，再透過原生 loadGraphData 載入目標；保留來源及目標各自的 initialState、undo／redo。後端收到目標身分與新世代回執後才接受實際序列化。

載入開始至完成期間暫停編輯事件、tracker 的 checkState／updateState 及其他載入，防止原生 window 已排定回呼保存 clean 中的空畫布、undo 提早移動歷史。一般原生載入只觀察開始／結束，不阻擋一般 undo；已在載入的原件不可被另一份 PCS 切換插入。目標載入失敗時還原來源；還原也失敗時保留草稿／歷史並維持鎖定，明示重新整理，絕不發送生成。

參考版本為 ComfyUI frontend 1.43.18 的 [原生 loader](https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.43.18/src/scripts/app.ts)、[ChangeTracker](https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.43.18/src/scripts/changeTracker.ts)、[工作流服務](https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.43.18/src/platform/workflow/core/services/workflowService.ts)及[序列化流程](https://github.com/Comfy-Org/ComfyUI_frontend/blob/v1.43.18/src/utils/executionUtil.ts)。沒有將離線生命周期模型當成真實前端驗收。

## 驗證及交付

自驗入口 `tests/verify_v083.py`；測試範圍涵蓋連續三輪首次提交、真正修改仍停止、切換草稿／undo／空手動稿／失敗還原／載入競爭、綁定節點整批畫布預覽、生成中加入佇列、既有 PNG 還原及 Queue 保護、共享模組不載入 Qt、封裝資料邊界。結果限固定提交的 `qa/083/RESULTS.json`，打包後另核對來源雜湊、啟動入口及離屏畫布畫面。

人工驗證順序：先確認視窗為 0.8.3 與上方工作佇列可見；以 Anima 和 ill 各完成三輪，接著保留原綁定而切到另一原生工作流後執行，再核對兩邊草稿及 undo；確認綁定圖片節點直接顯示整批；最後在生成期間修改下一份文字、加入佇列並於上一份完成後啟動。需要更新配套擴充並重新整理 ComfyUI 網頁。

真實 ComfyUI、GPU、Anima／ill、關頁執行和原生切換／undo 的實機結果仍待人工回覆。候選包內附步驟與限制，舊候選及原資料保留。
