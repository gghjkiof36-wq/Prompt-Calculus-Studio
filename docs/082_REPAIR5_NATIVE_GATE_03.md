> 歷史開發紀錄：本文描述當時候選或實驗，不能當作2026-09-27公開Repair5的完成清單。現版仍需開啟ComfyUI網頁、單工作流一次；跨工作流、背景無Web、多圖下游逐張未完成。現行使用範圍見[使用指南](USER_GUIDE.md)及[來源對照](SOURCE_PROVENANCE.md)。

# Repair5 原生執行首關候選

> 2026-09-22 更新：使用者已授權並指定在既有 illustrious v1.0.0 做實機驗證，首輪結果與啟動修復見 [原生實機記錄](082_REPAIR5_NATIVE_LIVE_03.md)。下方保留歷史增量；其中「尚未 GPU」及早期自動切圖方案不代表目前結論。完整 A→B／無 Web 仍未完成。

這是開發驗證候選，不是 Repair5 完整交付或日常版本。原始基準 9cea7f4，工作分支 fix/03-082-repair5-20260921。使用獨立資料、擴充輸出及 ComfyUI 實例驗證，不能替換日常安裝。

## 目前採用範圍（優先於下方歷史增量）

04 對 fdbdbdc 的複審仍未通過 N03：原生 window capture 排入的鍵盤回呼在 document 攔截之前已成立，之後仍可能保存 clean 中的不完整畫布。02 另核對原生 undo 提早搬移歷史及載入失敗恢復的風險。03 因此撤回自動切換原生工作流功能，沒有繼續擴大對原生 changeTracker 的攔截或宣稱跨工作流已完成。

CLIP 卡入口改為「確認原生工作流」：只核對 ComfyUI 目前已選取的原件 ID／路徑／root，不呼叫 loadGraphData、checkState、undo 或生成。選到其他工作流時明示請先在 ComfyUI 切換。沿用內部 native/open 和 opened 回應欄位以維持傳輸相容，但 opened 現在僅表示確認了已選取的原件，不表示 PCS 執行了切圖。本次 native/open 與 queue 入口不再主動呼叫或替換原生 loader；既有使用者手動 JSON 匯入仍沿原本的 loadGraphData 路徑，未在本次修改或驗收。

原生生成仍呼叫 app.queuePrompt。PCS 不再攔截原生編輯事件或替換 loadGraphData，以免原生 undo 已搬移歷史後被 PCS 的 loader 拒絕。提交前發生身分／世代／序列化內容變更時，既有檢查拒絕 PCS 傳輸；已提交的真實 prompt_id 及凍結內容仍保留，結果未知不重送。方法包装與文字／圖片同步暫停保留到原生 queue 結束。這個調整沒有啟用未驗證的跨工作流或無 Web 執行。

自驗 qa/repair5-native-current-only.log：26 項 JavaScript 相關案例通過。重新以目前範圍驗唯讀確認、不同／重複／缺少／錯 ID 目標及子圖拒絕，確認完全不進入原生 load；另驗原生 undo 的 loader／歷史正常進行，PCS 於真正 HTTP 前拒絕已改變的來源。原先自動切換／逾時草稿測試保留在 Git 歷史及失敗日誌，不把功能撤回包裝成它們已通過。Python 桌面／原生傳輸相鄰檢查另列 qa/repair5-native-current-desktop.log。真 KSampler／兩張 GPU 輸出首關仍未驗。

380cad5、fdbdbdc 附件均保留為已知 N03 未過審，不能交日常使用。新固定候選須重新經04／05限定檢查及06驗包；跨工作流開啟、A→B、多輪及無 Web 仍在未完成清單。

## 本次改變

PCS v4 執行入口只下達有操作識別碼的原生命令，不從匯入 profile.graph/values 組裝另一份執行內容。已綁工作流必須已在唯一的 ComfyUI 分頁開啟；路徑、原生 ID、分頁、連線及載入世代須一致。ComfyUI frontend 1.43.18 的實際 app.queuePrompt(0,1) 負責序列化、seed hooks、HTTP、storeJob 和進度事件。ComfyUI backend 0.21.1 的真 queue/history 仍是執行證據。

前端 adapter 從 setup 起門控 app.queuePrompt，入場時確認 native processingQueue/queueItems 空閒；這兩個欄位是本版相容依賴，未知狀態會拒絕操作，不能試呼叫探忙。暫時封鎖提交期間的使用者編輯／切圖，序列化完成及傳輸前再檢查身分和內容。既有 api.queuePrompt 僅呼叫一次，保留 this/options/response，沒有自行 POST 或 private executionStore shim。

後端保存當次原生序列化及操作收據，只做驗證與紀錄，不回填參數或圖片。真 prompt_id 回覆後才標 queued；已送但回覆不確定不重送，可由真 queue/history 對帳。一次性傳輸標記在後端移除，避免保存 PNG 後再次打開時誤當重送。原始 PCS 組合快照與各原生節點實際文字分開記錄，含空白手動文字。

## 明確未完成

- 目前首關只開放單一已開啟工作流執行一次，不接受 PCS 圖片輸入接線；跨工作流原生開啟、圖片傳遞及多輪凍結尚未整合，會明確停止，不回退舊副本。
- CLIP 卡已有「在 ComfyUI 打開」入口，目前只切換原生 store 已載入且 ID 明確的工作流；尚未載入或未能確認 draft 的檔案，須先在 ComfyUI 打開原件。這不包含 A→B 自動執行串接，也沒有把 PCS graph 另開成副本。
- 尚未完成新的 PCS 文字意圖、原生手動文字及重連後 ownership 的完整版本協議。目前未知 ownership 保留網頁內容，不能宣稱即時雙向同步完成。
- 無網頁等價執行、跨工作區隔離、圖片新舊守門及完整 A→B→多輪仍待做；原生既有圖片輸入和 batch_size 都由同一原生圖決定。
- 不能用這個候選的限制改寫完整 Repair5 需求，也不能把合成 history 或本文件當真 KSampler 通過證據。

## 已有證據

- a8fa5b8：多圖縮圖列；QTest 點擊兩張不同合成 PNG 可選且刷新保留選擇。
- bac736a：不再以歷史文字、圖片或 synthetic executed 改動目前原生圖。
- tests/native_queue.test.mjs：9 項離線 adapter 測試，包括 1216×832/batch_size=2、方法回應傳遞、原生 hooks 的替身、忙碌、A-copy、A→B→A、序列化改值、遺失回覆、afterQueued 失敗、併發入場及逾時晚到序列化。與7項歷史回播保護合跑共16項，qa/repair5-native-adapter.log；不是實際瀏覽器 storeJob 證據。
- tests/test_native_queue.py：Service/SQLite/Qt transport 合成測試；驗當次參數及手動空文字不被舊 profile 覆蓋、兩圖全保留、同操作不重送、取消／篡改拒絕、重啟對帳和 PCS 只發原生命令。相關 Service/任務紀錄回歸另跑，日誌 qa/repair5-native-service.log。

下一關由 05 在固定提交上獨立檢查；真服務啟動、瀏覽器互動、GPU、模型及私有 workflow 均未操作。06 尚不打正式交付包。

## 04／05 首輪審查修復

b4ab6a4 獨立檢查未通過：04、05 都重現未儲存 A-copy 保留 PCS ID、原生 ID 不同卻仍能提交的錯綁；04 另重現 SQLite 日誌失敗後 ComfyUI 繼續執行。兩項均不可由其餘通過案例抵銷。

修復將原生 ID 比對套用到有／無路徑兩種情況；有原生 ID 時缺失或不符都拒絕。操作收據和 jobs 紀錄以同一 SQLite 交易保存，只有交易完成才允許原生請求继续；包含寫入、交易提交及未預期例外在內，失敗先清空可執行 prompt，未預期例外仍交後端日誌。拒絕時不留下成功 metadata，原生圖不被改動。

自驗 qa/repair5-native-review-fixes-final.log：21 項通過，新增未儲存圖的正反向／準備前換圖、兩張表各自寫入失敗及真 SQLite 延後約束在 commit 失敗的案例。初次測試的測試環境錯誤留存在 repair5-native-review-fixes.log，不當通過證據。ce44761 已經 04／05 固定增量重驗，兩項缺陷關閉；首關範圍及真實驗證限制不變。

## 原生開啟與單使用者邊界

CLIP 卡按鈕只發送 native/open，後端按唯一分頁、已載入原生目錄的 path／ID 找目標。前端與 queue 共用忙碌鎖，確認目前原生 changeTracker 已保留未儲存 graph，再以真 ComfyWorkflow 物件作 loadGraphData 第四參數。原生管理器負責 draft、undo 與輸出還原，不先切 activeWorkflow，也不保存／關閉／重新建立檔案。正在編輯 DOM 欄位、尚未完成變更、來源或載入內容不符均明示停止；成功只記 opened，不產生 prompt_id，也不會自動排隊。原生儲存失敗的重新啟動恢復、任意自訂節點及完整原生 undo 實機結果仍未驗。

切圖／序列化期間暫停 PCS 文字套用與圖片參照發布，晚到文字回應棄用；結束再讀目前圖，避免將 B 的圖片以 A 身分發布。原生產生的真正輸出事件保留。

依 02 現機 user_manager／api 原碼核對，目前原生入口只接受明確 single-user 模式。後端 multi_user=True 或未知時不宣告能力，所有原生操作拒絕，旧 PCS 傳輸標記也不允許執行；一般無 PCS 標記的 ComfyUI 排隊不受影響。scope 由後端固定 default，不從 body.user、client_id 或路徑猜使用者。多使用者支援仍未完成，不聲稱已隔離多租戶。

本增量自驗：qa/repair5-native-open-watchers.log 28 項 JavaScript 離線案例；qa/repair5-native-open-scope.log 25 項 Python／SQLite／Qt offscreen 案例。前者涵蓋 A→B→A 保存未存修改、相同圖不重新載入、錯 ID／未載入／重複目標、編輯或換頁競態、開啟結果不符／逾時不排隊及背景同步互斥。後者驗實際按鈕訊號只發 open、晚到不同工作區不刷新、開啟不能當 prepare 提交及 multi-user／未知模式拒絕。舊日誌不合計為本次總數。首次 UI 測試未還原合成工作區導致 teardown 停等，已停止該測試程序並修正測試還原，沒有操作日常 ComfyUI。尚待固定增量獨立審查，仍未有真瀏覽器/GPU 證據。

## 綁定圖片集合與下游選擇

「加載圖片 → 預覽圖片」現在讀取完整節點圖片集合，最多 64 張；超過上限明示停止，不截成前 64 張冒充完整結果。加載圖片的選单及預覽圖片的縮圖列都能選擇輸出，單張可直接使用，多張須在 PCS 明選，不繼承 ComfyUI 網頁的 imageIndex。未選時預覽保持空白，不把上一張當本次輸出。PCS 選擇綁定集合識別碼和圖片參照；結果任務、來源或檔案版本改變時舊選擇失效，選圖可復原／重做。

圖片回應同時核對資料庫、工作區、連線世代和工作流來源／原生 ID／節點。相同 PCS ID 改綁另一份原生圖時舊清單立即不可用，晚到回應不能填入新圖。只切換瀏覽器圖片選擇不刷新舊圖片集合的產生時間。擴充若只回傳舊單張格式，桌面提示更新擴充，不宣稱已取得全批次。

自驗 qa/repair5-image-collection-final.log：20 項 Python／Qt offscreen 相關回歸通過，含真 QTest 點擊兩張合成 PNG、紅／藍像素與下游來源一致、選圖復原／重做、新結果失效、換原生圖的晚到回應及舊擴充拒絕。qa/repair5-image-collection-js.log：4 項圖片發布離線案例通過。初版 test fixture 缺少 PreviewImage 節點，失敗日誌保留為 repair5-image-collection-first.log；補齊 fixture 後再驗，不隱藏原失敗。以上不是 GPU 產出的兩張圖片驗收。

本增量只補集合及選擇，未完成全部圖片來源新舊仲裁：重新開啟瀏覽器的舊縮圖、未帶 PCS 來源標記的原生歷史及斷線重連仍需後續閉環。PCS 圖片接入執行、A→B 和多輪限制仍照前節明示，不因選圖功能完成而放行舊副本執行。

## N03：逾時不提早解除編輯保護

04 在 69d3cc4 的限定審查重現：原生切圖超過等待時間後，載入仍可能停在 clean 清空 root 的階段，但編輯攔截已移除，mouseup 觸發原生 changeTracker 時可能把 A 未儲存草稿換成載入中的不完整畫布。這是合成原生生命週期證據，沒有宣稱已在使用者瀏覽器造成永久資料遺失。舊 Node EventTarget 的 boolean capture 移除差異曾掩蓋這項失敗；不能用舊測試通過否定反例。

修復把 open／queue 的編輯事件攔截與原生非同步操作一起清理：逾時只回報未確認，保護持續到原生 promise 真正結束。開始、移動、放開、鍵盤及輸入事件一起保護，避免只阻止按下卻仍讓放開動作保存半成品。提示明說載入結束前暫時鎖定編輯；晚到完成不另回成功、不自動生成，結束後恢復可編輯。

qa/repair5-native-open-n03.log：30 項 JavaScript 相關回歸通過，新增檢查逾時後全手勢仍受保護、來源草稿／undo 保留、原生載入真正結束後解除攔截。qa/repair5-native-open-n03-counterexample.log：03 原樣複用04修正版反例6項通過，不當作04獨立複審；新固定提交仍交04／05複驗。380cad5 舊附件保留為已知N03未過審，不交日常使用。
