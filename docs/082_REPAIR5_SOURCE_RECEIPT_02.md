> 歷史開發紀錄：本文描述當時候選或實驗，不能當作2026-09-27公開Repair5的完成清單。現版仍需開啟ComfyUI網頁、單工作流一次；跨工作流、背景無Web、多圖下游逐張未完成。現行使用範圍見[使用指南](USER_GUIDE.md)及[來源對照](SOURCE_PROVENANCE.md)。

# 單一工作流的來源已套用收據

2026-09-22；02依03明確委派，基準4daaa5d。範圍只含workflow_state.py、web/workflow_sync.js及對應測試。03負責capture、route、core持久化與冷開接線；04獨立審查。

## 解決的問題

Web仍套用PCS來源A時，PCS可能先改成B。capture不能把當下B的來源摘要貼到A圖上。收據表示前端已完成的來源同步；route仍須用目前PCS來源及實際提交欄位核對，兩邊缺一不可。

live_state新增source_revision，直接使用native_queue.digest(bound_texts(state,profile))，含完整bound_texts清單，不能改成texts顯示DTO的hash。既有revision仍是顯示DTO摘要。編譯失敗或未綁定而bound_texts拒絕時，沿用texts=[]顯示，但source_revision=null，不認證成合法空來源。

## 前端介面

watchWorkflowRuns沿用原參數與refresh/changed/stop，新增三個同步方法：

- sourceReceipt()：回傳深拷貝的收據或null。未有合法live來源、初次文字不符、欄位／widget／node／workflow實例或identity變動、值變動、轉入子圖、缺root、重新configure或停止時不可回傳有效收據。
- markEdited(event)：傳原DOM input事件；呼叫端先完成widget值更新，排除PCS面板及同步寫入。只接受isTrusted===true、非isComposing、目前root中唯一widget.element/inputEl或其內部元素。合成事件、不明target、多widget共用target都回false。成功只標該欄實際值為明確手改，收據立即失效，待live來源確認後才重新產生；無參數不會推定所有欄位都由使用者改過。
- restoreSourceReceipt(receipt,key)：key須含library_id/workspace_id/workflow_id/frontend_id/path，來自03已核對的背景context。核owner組合、目前root與原生id/path、每欄class/widget值及mode，成功回true並恢復欄位歸屬，當下sourceReceipt仍為null。收到同owner/source_revision的live回覆後才確認；若離線期間source變了，保留文字且不確認，需要重新確認欄位歸屬。新的明確input可建立該欄歸屬，不能順帶確認其他未核欄位。

收據形狀：

```json
{
  "owner": "library/workspace/workflow",
  "source_revision": "64 lowercase SHA256 characters",
  "texts": [
    {"node": "9", "field": "text", "class_type": "CLIPTextEncode", "text": "actual widget text", "mode": "pcs"}
  ]
}
```

mode只能是pcs或manual。pcs表示來源已實際套用或當前值精確等於該來源；manual只來自明確input或已核對的持久收據。manual的text是目前手寫值，可以是空字串；其source_revision表示已處理的PCS來源版本，不宣稱手寫值等於PCS編譯文字。既有manual歸屬只在同owner、同binding、同node/widget且實際值未被不明修改時延續。

首次不符、無來源證據的程式改值、poll期間不明改值均保留原文，sourceReceipt為null。已證明PCS管理的欄位仍照既有同步規則接收後續來源，不擴大到其他文字欄或歷史任務；未綁定／斷線時清除舊同步歸屬。非同步回覆亦核graph、identity、activeWorkflow物件及載入世代。

## 03接線與驗收要求

capture的read fingerprint包含完整sourceReceipt；null不提交，graphToPrompt前後及close時比對同一收據與活圖。route核owner與key、目前source_revision、完整bound欄位集合及每欄實際API值；pcs值還要等於PCS目前編譯值，manual值依明確歸屬保留。不得讓route重新產生hash覆蓋前端已確認的來源版本。

若PCS在poll或capture期間由A改B，前端最多仍持A收據，route應拒絕／等待B實際同步。前端不能預知未收到的PCS變更，故保留A收據不代表已承認B。

core對source_receipt的同交易保存、context回傳、close-release與冷開lease由03負責，本提交不改core。旧資料沒有收據不猜manual ownership；符合目前PCS內容可重新確認，首次不符保持未知。

## 證據與限制

新增source_receipt.test.mjs涵蓋A→B race、明確手改/空字串、偽input/IME/其他欄位、unknown初值、欄位實例與identity替換、owner/binding變更、late response、source讀取失敗及重開手改收據。既有workflow_sync.test.mjs維持文字/歷史隔離與停止語義。

Python test_live_sync_repairs.py核對native來源hash完全一致、Unicode/空字串、來源更新不改舊回覆，以及失敗編譯不形成空來源ack。全部合成資料；沒有桌面、網路、服務或GPU操作。固定bundle上的真DOM input映射、IME末字、冷開與route整合仍需03/04/05驗證。
