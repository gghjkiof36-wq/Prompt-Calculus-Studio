# 快速測試與基準對照

從 checkout 根目錄執行。每個檔案使用獨立的 Python 程序，按清單順序執行；效能檔也不與其他測試程序並行。執行時不要另開一套測試或效能量測。

```powershell
& 'D:/Prompt Calculus Studio/tools/pcs-dev-environments/20261105/python/python.exe' -I tests/fast_suite.py --vendor 'D:/Prompt Calculus Studio/tools/pcs-dev-environments/20261105/vendor' --output 'D:/Prompt Calculus Studio/qa/03-prealpha3-batch1-20261109/s0/my-run'
```

`--output` 必須是尚不存在的目錄。預設清單來自 B1-S0 的 F-PROPOSAL：19 個 stage／queue 檔、B 的雙擊回歸、21 個 canvas 檔，最後執行 `test_084_stages` 的三個重開、暫停／繼續案例。清單與精簡案例完整 id 在 `fast_suite.py` 的 `STAGE`、`CANVAS`、`QUICK_STAGES`。單檔 120 秒，整套上限 360 秒。

另納入 `test_shared_core_boundary.py`：掃描共用模組任意深度的 Qt 匯入與同套件依賴封閉性，以合成反例自查，並在封鎖 Qt 的子程序呼叫共用圖片資料函式。共享清單由此契約驗證，不寫死模組數量。

B1-3B 納入 `test_stage_run_controls.py`（暫停後主按鈕、plan／工作區／服務隔離、關閉重開與選單取消）及 `test_canvas_card_menus.py`（13 種卡片尺寸對話框、選單入口與面板保留項目）。原有六分鐘與單檔上限不變。

B1-7 納入 `test_workspace_ambient.py`（`WORKSPACE` 清單）：固定 60 幀的 Canvas／文字頁 paint 計數、輸入與焦點、全域幾何、通知避讓、暫停與頂層視窗生命週期。Qt 6.11 offscreen 外掛顯示非 ToolTip 視窗時會忽略禁止焦點旗標而自動啟用；fixture 僅在 offscreen 對特效設定 `WA_DontShowOnScreen`，仍由真實 QWidget backing store 派送 paintEvent，不模擬 paint／焦點結果。原生桌面的輸入穿透、啟用、疊放、跨螢幕 DPI 與 OpenGL 合成仍須另驗；此屬性不進產品。

預設快速套件不能關閉看門狗或將單檔上限提高至 120 秒以上；只有明確使用 `--files`／`--file-list` 的診斷執行可調整。

執行器使用 unittest discovery（`-s tests -p <filename>` 等價方式），先加入明確指定的 vendor、checkout 與 tests 路徑。它設定 `QT_QPA_PLATFORM=offscreen`，並為每個程序設定獨立的 `PROMPT_STUDIO_DATA`、`TEMP`、`TMP`、`TMPDIR`。不使用使用者資料，也不啟動 ComfyUI 或 GPU。

其他驗證用法：

```powershell
# 指定檔案；預設仍為每檔 120 秒，沒有整套六分鐘限制。
& $python -I tests/fast_suite.py --vendor $vendor --output $newOutput --files test_modal_guard.py test_087_canvas_regression.py test_087_canvas_review.py
# 完整基準；084_stages 的全部案例超過 120 秒，只有快速套件使用精簡子集。
& $python -I tests/fast_suite.py --vendor $vendor --output $newOutput --file-list $fileList --timeout 240
# 同一份測試碼的看門狗 A/B 對照，各使用不同輸出目錄。
& $python -I tests/fast_suite.py --vendor $vendor --output $newOutput --file-list $singleShotList --timeout 240 --guard off
```

上述 `$python`、`$vendor`、`$newOutput`、清單路徑須先指定；清單是一行一個 `test_*.py`。沒有 `.git` 的唯讀內容匯出可用 `--source-commit <來源 SHA>` 記錄來源。

## 結果解讀

- `summary.json`：來源提交、工作樹、環境、總秒數及是否符合本次清單；未提交差異要另外保留 diff／內容雜湊，提交 SHA 本身不能代表改動內容。
- `results.json` 與各檔的 `result.json`：原始退出碼、耗時、Python／PySide 版本、通過數、失敗 id／traceback、WinError 32／3、暫存殘留數與路徑。
- 各檔 `tests.json`、`stdout.txt`、`stderr.txt`、`stacks.txt`：逐案例結果、完整輸出及程序逾時前的堆疊。
- `tmp` 的殘留在程序退出後、任何人工清理前計數；執行器不刪掉殘留來製造成功。`data` 是明確保留的隔離測試資料，不是 tempfile 殘留。
- `known_failures.json` 只分類符合 **完整 test id 與錯誤內容** 的失敗。它不改 unittest 結果、不 skip、不使用 expectedFailure；例如 B 仍是子程序 exit 1。未知失敗、零案例、skip、看門狗攔截、逾時、暫存殘留皆使整套失敗。
- 關閉後啟動縮圖的 S0-3 P2 與預覽 store 的 S0-4e 已從已知失敗清單移除。`test_close_barrier.py` 鎖住直接／延後關閉及已接受收藏的登記；`test_module_preview_store.py` 遍歷全部功能預覽，檢查記憶體 journal、資料檔案不變與連線釋放，可用上述 `--files` 用法執行。WinError 32 或暫存殘留仍直接使驗證失敗。

## 看門狗及 fixture

`_modal_guard.install()` 只包裝測試程序內的 Qt `exec`／`exec_`，產品檔案不改。原始模態迴圈照常執行。精確計時器在五秒時記錄測試 id、對話框類別、objectName、標題與呼叫堆疊，reject／close，返回後丟出 AssertionError。執行器也檢查事件記錄，以免 Qt 回呼吞掉 Python 例外後被當成成功。`PCS_TEST_MODAL_GUARD=0` 僅供 A/B 驗證。

`stage_fixture` 與 `_qt_cleanup` 載入時也安裝看門狗。`_dialog_driver` 等實際 NodeDialog 成為 activeModalWidget 才驅動，因為建構期間也可能處理事件；回呼中的斷言會回傳至主測試。

`_qt_cleanup.close_window` 在 close 後持續處理事件／DeferredDelete，直到 `_close_completed` 且沒有 `jobs.active`，最多兩秒；失敗顯示活動工作及 completion 狀態。ReceiptExecutor 持有的測試 Service 明確 close，並檢查所有 connection context 已結束。產品 Service 本來就逐次關閉 connection；測試封裝不吞掉檔案錯誤。所有 tempfile 改用環境的預設位置。

## 過時斷言的遷移

S0-4a 的舊 picker 在 setUp 就呼叫已刪除的 `toggle_lookup`／`query`，18 項都無法進入測試。現在 12 項驗證現行分類、別名搜尋、鍵盤與滑鼠插入、位置／owner、草稿、原素材、undo／redo、儲存重開、取消與工作區隔離、視窗邊界／字級／尺寸。下表逐項對應舊方法（皆在 `test_canvas_palette.PaletteTests`）：

| 舊方法 | 處理與保留的行為 |
| --- | --- |
| `test_text_only_categories_and_opt_in_lookup` | 改為分類／別名搜尋且不送遠端 completion；現行 picker 沒有 opt-in 查詢面板。 |
| `test_search_right_fifth_left_restores_search_cursor` | 改成 search → assets → categories 的現行焦點路徑；遠端第五星目已不存在。 |
| `test_local_second_to_fifth_back_then_restore_fifth` | 合併到鍵盤焦點測試，保留本機第二列選取；遠端選取索引已無意義。 |
| `test_tab_cycle_and_shift_tab_restore_selection` | 舊三欄查詢循環已移除；由現行方向鍵與 Enter 流程取代。 |
| `test_text_cursor_selection_and_ime_protect_editing` | 保留現行單行搜尋的游標／選取檢查；移除已刪除多行 query 的 composing 屬性契約。 |
| `test_empty_and_replaced_results_keep_valid_focus` | 改成空結果與清除搜尋還原分類；不再模擬遠端替換建議。 |
| `test_late_result_does_not_steal_focus_or_cover_local_list` | 刪除：此 picker 已無遠端建議及回呼；本機搜尋另斷言不 schedule completion。 |
| `test_single_typed_insert_closes_and_preserves_position_draft_and_undo` | 空白文字走「＋」的 NodeDialog；保留一次插入、位置、草稿、undo／redo；搜尋 Enter 只移動焦點。 |
| `test_single_suggestion_and_local_insert_use_invocation_point` | 保留滑鼠插入本機素材的位置與原素材不變；遠端 suggestion 已不存在。 |
| `test_backdrop_click_and_escape_close_without_canvas_changes` | 保留取消及狀態不變。 |
| `test_resize_center_bounds_large_font_and_no_close_buttons` | 保留現行兩欄的大小、邊界、字級與拖曳縮放；現行有關閉按鈕及「＋」選單，舊按鈕清單已作廢。 |
| `test_resize_and_column_widths_survive_reopening` | 合併到尺寸測試，保留兩欄尺寸與資料庫重開。 |
| `test_cache_clear_disable_reenable_reject_old_callbacks` | 刪除：picker 不再持有查詢／cache callback；不宣稱驗證 completion 服務。 |
| `test_stale_cache_is_shown_while_refresh_is_pending` | 刪除：現行 picker 無遠端快取展示。 |
| `test_pending_query_finishes_while_focus_in_local_column` | 刪除：現行 picker 無 pending query。 |
| `test_delayed_lookup_does_not_cross_workspace_before_request_starts` | 改成實際 picker 工作區改變後拒絕插入，保留隔離與狀態不變。 |
| `test_canvas_ownership_and_saved_reopen_after_palette_insert` | 保留 owner、卡片包含關係、位置、undo／redo、資料庫重開。 |
| `test_old_query_offline_close_workspace_and_store_reject_late_results` | 刪除：query／store 回呼機制不再是 picker 的功能；取消與工作區隔離有現行測試覆蓋。 |

其他遷移均保留相應行為斷言：

- canvas_ui 三項與 refinement 的新增子模組，使用目前分類 picker；保留副本獨立、巢狀順序、輸出、undo／redo／儲存。空白雙擊改驗證不新增；舊清單入口改成 prompt_editor 導航。現行版本資料不應產生 migration backup；真正舊資料備份仍由 refinement 測試。
- paragraphs 舊雙選取設定與 list_shell 已移除，改驗證導航前後的畫布素材、手動草稿、輸出／snapshot／排序／儲存不變；背景色讀 theme token。picker_folders 驗證現行 MaterialScope 順序與原有 folder 聚合、搜尋及唯讀性。
- regression／review／rev9／popup_layers 用真實模態 NodeDialog，保留儲存、取消、獨立副本、重開、undo／redo、遮罩與焦點。標題列拖曳改點不落在現行收合按鈕的位置。
- OutputPanel editor 改從 open_page().content 取得，保留文字編輯與事件歸屬。B1-3B 將預排程管理與 CLIP 工具移到卡片更多選單；管理回歸仍實際排序、移除、套用及 Undo／Redo。pause 圖示的語意由 accessibleName 驗證，5B 的「繼續」文字改驗證主執行按鈕及原 run 恢復。popup 仍透過正常主題切換驗證暫停與 compact output action 的樣式及既有 module_field 色彩，沒有手動刷新面板。
- CAPACITY 測試以 patch 將對應 runner 的上限設為 10，資料量大於上限並實際完成／補入；初始、後續序號、總數與參數繼承皆有斷言，不只把預期數字改成 100。
- work_queue 的 PNG 還原先 prepare_state，保留原工作區與空白草稿比較，再驗證輸入未變及新工作區根節點的 material_type 全為 text。
- Q8b 在現行圖片卡實際連接／斷開，驗證模式、profile、prompt、來源均保留且不送出。Q8a 的拖放失敗保留。
- visual_batch 用 id 找 clip；B1-3B 續經 01 核定，rev9 只將過時的父 padding 座標改為子卡底緣與父卡高度的中點，保留原期望並新增舊位置命中子卡的回歸；產品命中範圍不改。restart 舊完成尾端先繼續並結案，再由第二次點擊建立新批次。release_089 過時的 `==35` 已隨圖片繪製模組拆分移除，改由上述共用邊界測試驗證清單，對應的 known_failures 項目也已刪除。

B1-S0 沒有修改產品程式、資料格式或架構；當時程式導航圖無須變更。後續圖片模組拆分的責任入口見 [架構設計參考](../docs/development/architecture-reference.md)。已知失敗清單不是產品修復證據，完整驗收以任務輸出目錄的實測報告為準。
