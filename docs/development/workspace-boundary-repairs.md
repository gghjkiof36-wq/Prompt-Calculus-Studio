# 工作區寫入與 CivitAI 回呼邊界

## Windows 第一次寫入前的目錄保護（B2-00-005）

`local_documents.directory_pin.pinned_target(root, relative, create_parents=False)` 在解析路徑前，先以既有 `pinned_parent` 保護 lexical root。需要建立附件父目錄時，每層都在上一層已固定的範圍內建立，再取得該層保護。禁止在未保護的父層一次建立所有後代。

可攜文件 create／save、可攜附件 prepare、Markdown 附件 prepare、工作區 Markdown／資料夾 create 使用此邊界。保護持續到候選寫入、發布、錯誤／恢復路徑診斷及暫存清理完成；保持原有 exclusive create、摘要核對、Windows displaced-file backup 與衝突結果。無法取得保護便回報失敗，不改用未保護的寫入。

Windows 保護由禁止 delete sharing 的目錄 handle 與 delete-on-close 空白 guard 提供。Guard 不含正文；離開 context 後釋放。非 Windows create 仍沿用原路徑檢查，不宣稱同等目錄競態保護；save 仍保留原 Windows-only 限制。

`test_workspace_write_boundaries.py` 覆蓋五個正文／附件／Markdown 寫入入口，在第一次寫入前換父目錄；另查取得保護失敗、既有巢狀 junction、空目錄原地改掛與附件發布失敗後的清理。全部假外部目標及資料都在單一測試根內。`test_publication_directory_boundary.py` 保留既有 native pin 的控制。正常保存、同名碰撞、原件／候選／外部版本保留與 schema 契約沿現有 local-document 檢查。

## CivitAI Token 版本（B2-00-006）

`token_changed` 同時前進 Token revision、搜尋 serial 與預覽 generation，取消舊 browse job／影像請求、清除尚未送出的舊搜尋／詳情／預覽與分頁 cursor。新請求仍由明確搜尋、選取或測試連線觸發；既有已顯示內容與已接受的下載不因此重做。

`start_job` 凍結 Token revision，成功與錯誤回呼都必須符合目前 revision 及聯網政策。搜尋 serial 檢查也先於清除 search_job，舊完成不能清掉新工作的追蹤。清除空白 Token 仍產生新的 revision。工作執行緒使用原本凍結的查詢／Token，不讀取晚改的 widget。

`test_civitai_token_epoch.py` 使用假 Token、受控 worker／影像回呼，涵蓋舊搜尋成功／失敗、舊排隊查詢、詳情／預覽、連線保存與相同 Token 的正控制。`test_civitai_repairs.py` 維持既有凍結參數、離線、取消與個人編輯控制。這些結果不表示已驗真服務、真帳戶權限或憑證外洩。

## 本機收藏偏好（R2-03-2-001）

`recent_destination` 與 `recent_external` 是可辨識且必須為字串的本機偏好；可攜文件排除它們，本機 runtime 保留。任意未知設定、非 JSON／非法核心資料與未知 required_features 的原拒絕規則維持。`test_recent_retarget.py` 包含正常保存／另存、實際選單／對話框回傳路徑、私有路徑不進 portable、未知設定與錯誤型別拒絕；另存後的晚到收藏仍使用原 store／Catalog。
