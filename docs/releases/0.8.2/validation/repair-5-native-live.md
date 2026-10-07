# 0.8.2 Repair 5：原生執行首輪實機驗證

被測候選：產品基準 `f4ea054`，安裝端同基準另加唯讀 console 診斷。日期：2026-09-22；QA 提交 `d3021e6`。

## 環境

使用者本輪允許可見桌面與 GPU，指定只在既有 `illustrious v1.0` 測試。03 操作原 Chrome 分頁與啟動測試；05 以隔離 PCS 資料、正常 Window → GenerationRunner → ComfyClient 入口執行，並獨立核對結果。沒有建立第二個工作流、改模型或 LoRA、改日常資料庫、重新啟動 ComfyUI 或直接代送 `/prompt`。

原生圖仍為 1216×832、batch_size 2、28 steps。隔離 PCS 故意保留 832×1216、batch_size 1 的舊資料，用來檢查執行時是否錯用舊副本。原生視覺圖、API 圖及擴充設定均先保存私有備份。

## 項目與結果

### attempt-02 與首次失敗

產品基準 f4ea054；安裝端同基準加只讀 console 診斷。真 prompt_id 為 `36283702-e6f7-415c-9dfb-04a6a5be90b7`。

- 03 在 ComfyUI 看到 KSampler 藍框及 4% → 36% 的實際進度，完成後原生預覽顯示兩張。
- 05 獨立讀取同一筆真 history，確認 success、1216×832、batch_size 2、28 steps；checkpoint 及 LoRA 輸入與原圖一致。
- 兩張原圖均為 1216×832，與 PCS 收到的兩個本機副本逐張 SHA256 相同。PCS 圖片集合指向同一 prompt_id、節點 #8，最近生成也有兩筆。
- 正常產品輪詢取得結果後，QTest 分別選取兩張圖片，兩個縮圖及大預覽均核對成功，沒有向 UI 注入圖片。

05 固定報告為 `worktrees/05-real-f4ea054/qa/real-gate/ATTEMPT02_REPORT.md`，QA 提交 d3021e6。私有證據在 `<private-evidence-not-distributed>`，工作流、圖片、資料庫及完整回應不進 Git。KSampler 動態屬 03 GUI 觀測；沒有錄下原始 WebSocket 封包。此結果不能宣稱未加診斷的精確包已通過。

首個 attempt 在生成前因 readiness 拒絕，沒有 prompt_id 或 GPU 提交，失敗證據保留。重新整理後成功不足以唯一證明首次失敗的原因。

### 後續啟動修復

ComfyUI frontend 1.43.18 的 extension setup 使用 Promise.all；PCS 在自己的 setup 立即包裝 queuePrompt，可能早於其他擴充。若後完成的擴充再包一次，PCS 原有身分檢查會一直拒絕執行。這是已確認的啟動順序缺陷，但沒有倒推為首次實機失敗的唯一原因。

02 的獨立 bootstrap 只在冷啟動建立，等待原生 app.setup 完成後才公布的 window.app 標記，以及明確的空閒佇列，再安裝一次 PCS 入口。保留當時最外層的其他擴充處理鏈，不在輪詢中搶回入口、不試呼叫生成來探測忙碌、不跳過未知狀態。頁面卸載取消等待；已有標記的熱載入／重複啟動拒絕。

03 將輪詢宣告與命令接受改為同一套嚴格 readiness 檢查；缺失或錯型別的欄位不再因為是假值就被當成空閒。若安裝後其他擴充再次改寫入口，維持拒絕並提示原因。本次只改前端，不改後端協議或要求使用者重啟 ComfyUI。

## 尚未測試與已知限制

完整跨工作流自動切換、A→B 圖片傳遞、純圖片 B、多輪凍結、重連後 ownership／圖片新舊仲裁及無 Web 等價執行仍未完成。現在只驗證唯一原生分頁中已開啟的單一工作流執行一次；不接受 PCS 圖片輸入生成接線，也不會默默退回舊副本。後續不能以這份首關證據省略其餘驗收。

這是單工作流首關實測；不能據此聲稱未加診斷的精確包或 2026-09-27 公開 Repair 5 全部情境已通過。
