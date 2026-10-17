# P2：本機分頁檢視入口

這是單區、本機編輯的開發入口。頂層分頁接上真正的中央素材管理與 Canvas；選中的頁籤與內容使用相同底色、只有上方圓角。Home、前進／返回、媒體庫、探索、匯出與設定移到左側。原工作區下拉與 Canvas／清單模式橫列不出現在新入口。

## 啟動與資料

`run_workspace.py --root <文件根> --data <獨立工作資料>` 必須明確提供兩個位置，不採用正式使用者資料目錄。`--check` 只讀取文件與檢查 Qt 可載入性，不開 UI，也不寫入檢視資料。正常啟動使用 `QLockFile` 避免同一個 data 被兩個檢視程序同時開啟。

可在 Windows 以 `tools/build_local_workspace_preview.py --out <全新輸出目錄> --vendor <PySide6 依賴目錄>` 建立 BAT、合成分類／工作流／Markdown、獨立 data 與來源紀錄。輸出存在時拒絕重建，避免清掉檢視者的修改。BAT 使用目前 checkout 的 Python 原始碼，並非可獨立搬移的正式 EXE；不會替換原程式或加入永久的新舊模式按鈕。

固定提交後可使用 `--refresh-launcher` 更新同一 checkout 的啟動檔與來源紀錄；此操作保留所有檢視文件與 data。

## 文件與視圖的邊界

- `WorkspaceShell` 管理分頁、內容樹、視圖生命週期與側欄；其 state 僅含外觀，不裝載任一文件的全域 current state。
- `DesktopDocument` 各自持有文件 ID、讀取 receipt、dirty、保存結果及隔離工作目錄。每份已開文件只有一個可寫 host；同一實體檔案的別名聚焦原頁，另一位置的同 ID 文件拒絕形成第二個 writer。
- `EmbeddedHost` 重用既有 `Window` 的編輯、Canvas、原生 QTextDocument、Undo／Redo 與交易。在整個開頁期間不更換其文件 state 或 SQLite。分頁切換只顯示既有 widget；不重新建立文字 document。
- 可攜附件從已驗證 manifest 複製至工作目錄的邏輯位置及實體位置：舊 UI 依邏輯名讀圖，文件 writer 依 manifest 驗證。刪掉預覽引用只移除新文件的 manifest 項目，不刪原附件。
- 原生編輯對話框的「保存」提交素材交易，頁面「保存文件」才寫回 `.pcs-category`／`.pcs-workflow`。表單名稱、備註、預覽、變體等使用共同 `draftChanged` 訊號。關頁選保存時先完成尚開啟的素材對話框交易。
- 外改衝突或寫入失敗保持 dirty，提供目前檔案與候選回復檔的只讀檢視。關閉取消不移除頁面；關閉整個視窗先處理所有文件的決定，取消時保留全部頁籤。

## 操作

普通單擊重用一個乾淨的瀏覽頁；斜體表示瀏覽頁。雙擊、開始編輯、明確另開會保留頁面。一般點擊同一文件聚焦原頁。`＋`／Ctrl+T 開保留的新分頁，Ctrl+W 關頁，Ctrl+S 保存目前文件，Alt+Left／Right 返回或前進。頁籤可重排，空間不足時單列捲動與清單仍可操作。

分類頁保留原搜尋、三類素材篩選、釘選、排序、預覽、變體、新增、編輯與取消。內容樹只列分類文件、工作流文件、Markdown 與資料夾，不列每個素材。Markdown 在本階段為 UTF-8 純文字唯讀檢視，空檔有效。

## 本階段限制

這個入口只供本機編輯。`LocalEditingClient` 阻止實際連線與派送，沿用既有離線引擎狀態；它不是第二個執行引擎。媒體庫、探索、匯出、設定可從左側進入，但共用 AppServices、跨文件任務路由與全域設定整合仍待後續階段。

文件關閉／程式重啟後不還原整組頁籤或承諾完整原生 Undo。每次開啟的隔離 SQLite 留在 data/sessions；其長期 history／索引歸屬由後續儲存整合承接。本輪不提供檔案新建／搬移／回收、左右雙區、完整 Markdown 編輯或外部監看，也不匯入正式舊資料。

## 可重複的驗證

`tests/test_workspace_shell.py` 以真 Qt 視窗檢查預覽重用、保存目標、編輯取消、dirty 關頁、整窗取消、附件、衝突回復、同檔別名、原生文字物件、工作流 Undo 與窄視窗分頁。`tools/check_local_workspace_ui.py --root <合成根> --out <全新證據目錄>` 先複製合成文件，再實際操作素材／變體與 Canvas 對話框並截圖，結果不寫入供人檢視的來源樣本。

UI 自驗、獨立需求核對、真 ComfyUI／GPU 執行與正式發布是不同結果。測試紀錄必須標明來源提交、平台、比例、受影響範圍及原已存在的失敗，不以通過數量取代使用者檢視。
