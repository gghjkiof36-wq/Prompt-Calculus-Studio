# 工作區恢復與關閉失敗

本頁描述從 `0ed6a317030d52aaad0f63856b2c367804f9cedf` 延伸的局部修正。提交身份與測試結果由對應修復交付記錄核對；本頁不代表發布或真實服務驗收。可攜文件格式、SQLite schema、生成協定與版本號均未變。

## 恢復副本與本機稿

`DesktopDocument.prepare()` 先向 `WorkspaceRuntime.acquire()` 取得已存在的本機狀態，再由 `CloseRecovery.restore_document()` 檢查關閉恢復副本。`runtime_restored` 記錄本次是否實際取得 runtime；資料夾存在本身不構成此證據。

`identity_maps` 的合法值是 `None` 或 dict。普通文件的 `None` 不轉成空 dict；複製映射仍保留原值。缺欄位、其他型別、未知 state 或無效 runtime_document 均不套用。原檔 SHA 改變時保留原檔、本機稿與恢復副本，並提示不同內容。

新寫入的文件恢復副本可帶 `runtime_base_sha256`：對已提交的 SQLite state 經 `prepare_state(..., multi=True)` 正規化後，以排序 JSON 計算 SHA-256。它記錄恢復稿的已保存前稿，不用檔案修改時間判定兩份內容先後。讀取前稿失敗不阻止保存編輯，但該副本沒有自動覆蓋不同 runtime 的依據。

| 情況 | 處理 |
|---|---|
| 沒有取得 runtime | 依原檔 SHA、state 與映射契約恢復副本 |
| runtime 與恢復 state 相同 | 沿用相同內容及副本的文件中繼資料 |
| runtime 不同，指紋符合副本的已保存前稿 | 套用承接此稿的恢復 state |
| runtime 不同，無指紋或指紋不符 | 保留目前 runtime；另保留副本及路徑，不猜測新舊 |

舊快取仍可由沒有 runtime 的新 reader 讀回；遇到兩份不同稿則提示人工判斷。快取不是執行 journal，也不直接寫回可攜原件。`execution-close-state` 是保存錯誤時的額外 state 記錄，不自動注入 runtime；人工恢復應先保留原件，核對 token、原檔及內容，不能將它當作可攜文件開啟。

## 獨立完成各個關閉階段

`DocumentExecution.close()` 依序嘗試 client shutdown、state 保存、store close，分開記錄成功階段。保存失敗時獨立嘗試 runtime 目錄中的 `DraftCache` state 記錄與工作區既有的文件恢復副本。任一個保存位置失效，不會取消另一個備份嘗試。

所有保存與備份都失敗時，保留該 owner 的 state 和 store，讓關閉重試；其他 owner 仍可清理。shutdown 失敗也保留它的 store 供重試，因為 client 收尾仍可能需要 journal。成功的階段不重做；尚未停止的 client 在下一次重試後會重新保存最新 state。

`WorkspaceRuntime.close(additional=())` 合併註冊 owner 與 Shell 提供的 active／retired owner，以物件身分去重。它收集各 owner 的錯誤後繼續其餘清理；全部 client 停止後才嘗試關閉 index。`closed` 只表示資源已完成收尾；是否曾有保存錯誤須另外讀 `close_errors`，不可用 `closed` 取代保存結果。

`close_errors.CloseError` 是 OSError 子類別，`failures` 保留階段與原例外。execution 和 runtime 都保存歷次錯誤並在本次有錯時拋出彙整例外，不能將沒有拋錯的重試誤當成從未失敗。

Shell 的 `close_execution_owners()` 接收彙整結果，顯示錯誤並寫入工作區 `DraftCache` 的 `execution-close-errors`，包含可用恢復路徑。錯誤紀錄也寫不出時拒絕完成關閉，下一次再嘗試。下一次啟動讀到此紀錄會提示；無新錯誤的正常關閉可清除歷史提示。

已完成的 host close 不在重試時重跑，避免 host 再次查詢已關閉的其他 owner 資料庫。所有 client 完成前保留 host／document 的 native 物件，之後才 `deleteLater()`。關閉已進入資源收尾時，分頁可能已卸載；失敗後保留的是待完成的關閉與稿件，不恢復為可繼續提交工作的正常工作區。

## 密集 Markdown 的同步工作範圍

原本不足 10,001 行且不足 128,001 字元的文件，仍可能一次格式化數千行。SourceView 現在對超過 1,000 行的文件沿用可見範圍語法標色；這是切換工作方式的界線，不是延遲保證。段落高度／縮排仍保留原來的 10,000 行界線（`lazy_paragraphs`），避免較短文件捲到末段時因未排版段落改變 scrollbar 範圍。捲動時補齊語法樣式，文字、BOM／換行、共享 buffer 及 Undo／Redo 仍由原資料流程管理。

普通閱讀的 renderer 選擇保留原來的 128,000 字元／10,000 行界線，避免中等純文字因格式化策略改變而提前啟動 WebEngine。包含表格等複合內容的 interactive RichView，同時依字元數與 SourceView 的密集行判定使用既有 `RichReadingProjection`。解析使用凍結的文字／路徑／開啟筆記內容；Qt 資源補齊與發布仍在介面執行，沿用最新 request、版本、snapshot 及存活檢查。

這次沒有改 WebEngine 首次建立、完整 DOM 呈現或 SourceLoading 的載入演算法。受限 offscreen 計時只能支持已量測的局部阻塞改善；一般桌面冷啟動、長文件全文耗時與視覺／操作驗收仍須分開確認。

## 相關驗證入口

- `test_workspace_recovery_contract.py`：普通 None／映射、完整 state 與空 draft、前稿指紋、較新 runtime、舊／非法快取及原件保護。
- `test_workspace_cleanup.py`、`test_workspace_cleanup_shell.py`：保存、備份、shutdown、store／index 失敗；其他 owner 收尾；重試與錯誤紀錄。
- 既有 `test_workspace_close_recovery.py`：完整關閉恢復流程。一次正常退出不能解除歷史原生 GC 崩潰的未確認狀態。
- `test_markdown_dense_rendering.py`、既有 `test_markdown_performance.py` 和 `test_markdown_insertion.py`：密集文件、末段格式、歷史、背景轉換與最新結果，以及既有共享文件生命週期。
