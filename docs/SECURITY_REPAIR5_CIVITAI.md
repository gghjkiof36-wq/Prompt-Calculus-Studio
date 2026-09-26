# Repair 5：CivitAI 刪除後重下載 401 查證

2026-09-21，08；基準 `9cea7f4904a05213ecff51d9d661b3ce99ced508`。

本批修復下載錯誤原文直接進入使用者訊息及持久收據的問題。使用者回報的第二次下載 401 根因仍未確認，不能將此提交標為下載成功修復，也不能判定 Token 失效。未接觸真實憑證或資料，未連線 CivitAI、下載模型、建置或發布。

## 已確認的資料流

- `credentials.py`：Token 使用 Windows 帳號 DPAPI，獨立存入 credentials/civitai.bin；合成憑證保存、讀回、清除通過。這不代表跨電腦還原能力。
- `pages.py:ModelsPage.recycle`：檢查模型路徑、移至回收筒、標記 missing、保存資產；沒有清除憑證呼叫。離線測試以刪除合成模型並標記 missing 代替原生回收筒，不宣稱原生UI驗收。
- `civitai_ui.py:start_download`：開始時凍結目前 Token，傳到 perform_download → download_version。retry_download 使用原收據版本/檔案 URL，再由 start_download 取得目前 Token。
- `refresh_version/start_detail`：版本內容有 `_details_loaded` 標記；已載入版本可被重用，沒有每次下載重新查詢URL的保證。實際服務是否發出一次性/過期URL未知。
- `_SafeAuthRedirect`：同 HTTPS origin 保留 Authorization，跨主機或不同連接埠移除，HTTPS 降為 HTTP 拒絕。`.com` → `.red` 也屬跨來源；未觀察真實服務是否發生這條重導，不放寬安全政策。
- `network.py` 是候選查詢/翻譯入口，CivitAI 總開關在 `civitai_network.py`；本批未改任何聯網政策、Token 保存、URL 選擇或自動重試。

## 可重現風險與修復

基準 download_version 未將 HTTPError/URLError 轉成安全訊息，perform_download 把 str(exc) 寫入下載 JSON；Job 也將它傳給UI。離線服務把合成敏感字串放在 HTTP reason 或 URLError reason 時，原文直接冒出。收據又是現有備份內容之一，因此敏感 reason 可能隨私人備份傳遞。此為條件式風險：需要上游/代理/傳輸例外本身含敏感文字；沒有真實 Token 洩漏證據。

只修改 `prompt_studio/civitai.py` 的下載例外處理：HTTP 401/403/404/429 及其他狀態轉成固定訊息，保留 status/kind，不帶遠端 reason、URL 或例外鏈展示；URLError/TimeoutError 轉固定連線訊息。401 文案指出授權失敗並提供檢查方向，不宣稱 Key 一定失效。檔案系統一般 OSError、既有取消/Hash/同名拒絕仍保持原契約。

未改資料格式、依賴、下載目的地、授權邊界或 UI。原收據不清理、不覆寫。本修復只防止新的上述錯誤文字進入收據；其他來源欄位及舊資料不屬本次全面清理。

## 離線驗證

新增 `tests/test_civitai_reinstall_security.py`，攔截 HTTPSHandler 並禁止 socket.connect，無實際外連。基準執行5個測試方法：3通過、2錯誤（HTTP五種狀態及URLError，共6個錯誤案例）；錯誤原文出現在 baseline.log。候選5項全過。

1. 合成模型下载→登記→刪除模型並標記missing→再次下載/登記：DPAPI檔案不變，兩次初始請求均含 Bearer，收據無合成Token；最後明確清除Token可生效。
2. 同來源、跨域、跨埠重導及401後重新嘗試：跨來源無憑證，每個新的初始請求仍取得Token。
3. 伺服器模擬舊URL持續401：重試仍使用原快照。這證明重用行為，不能證明真實URL已過期。
4. HTTP 401/403/404/429/500：訊息與收據無服務端敏感reason，保留狀態碼，無殘留模型/partial。
5. URLError：訊息/收據無reason中的敏感URL，無殘留。

加上既有 test_api_redirect、test_civitai、test_civitai_repairs、test_v081，共 **54項通過**。含聯網關閉取消、Token修改後舊回呼、Hash、同名、登記失敗恢復與備份排除Token。既有Qt測試使用offscreen；不等同真實Windows回收筒或外部服務驗收。

執行環境：D槽Python3.12.14，vendor只讀，PYTHONDONTWRITEBYTECODE=1；TEMP/TMP在本worktree的qa/security-repair5/temp。證據：`qa/security-repair5/baseline.log`、`candidate.log`（本機保留，不把它們當公開附件）。

重現命令（先設定上述環境，PYTHONPATH含worktree、tests與D槽vendor）：

```powershell
& '<workspace>\tools\python312\python.exe' -m unittest test_civitai_reinstall_security test_api_redirect test_civitai test_civitai_repairs test_v081 -v
```

## 401 尚待驗證的假設及最小需求

Token有效性/帳號權限、跨來源授權重導、舊下載URL失效仍是分開的假設，不能用同一個401判定。離線環境不能替真實服務證實任何一項。

若另獲真實驗證授權，先由使用者在本機選定失敗模型的 model/version/file ID，確認是否同一帳號及有無Token即可；不要求交出Key、完整URL query、資料庫或原始日誌。最小探測方案為：

1. 以隔離診斷程序在記憶體載入使用者指定憑證，只記錄認證是否存在（boolean）；測一次帳號驗證與一次指定版本API，僅保存HTTP狀態與ID一致性。
2. 對原選定URL與剛查得同一file ID的URL，各做一次串流GET探測，只處理回應標頭後立即close，不保存/讀取模型body；服務端可能仍傳送少量資料，這仍需外連授權。限制次數，不循環重試、不繞過權限/付費。
3. 逐跳僅記錄scheme、hostname、port、HTTP狀態、Authorization存在boolean；不保存URL路徑/query、Location全文、header值、回應body或username。跨來源仍去除Token，禁止降級HTTP。
4. 比較認證API、原URL、最新URL的狀態：若API通過且最新URL成功而舊URL401，可支持URL過期假設；若在跨來源後失敗，先核對服務要求，不能直接傳遞Bearer；若API401，交使用者確認帳號設定，不自動清除/重置Token。

本批未執行上述真實探測。由獨立04審查本提交；08不自批，00另定401後續驗證及整輪結案。

## 文件差異

基準 DEVELOPMENT_STATUS 仍記Repair4待整批驗收，與00既有本機結案通知層次不同，本組未回寫歷史狀態。DATA_AND_PRIVACY 的聯網表仍缺CivitAI，屬既有文件缺口；本批報告補清楚實際資料流，未擴改公開文件或推定所有隱私缺口已關閉。
