# 快速測試與基準對照

從 checkout 根目錄執行。每個檔案使用獨立的 Python 程序，按清單順序執行；效能檔也不與其他測試程序並行。執行時不要另開一套測試或效能量測。

```powershell
& 'D:/Prompt Calculus Studio/tools/pcs-dev-environments/20261105/python/python.exe' -I tests/fast_suite.py --vendor 'D:/Prompt Calculus Studio/tools/pcs-dev-environments/20261105/vendor' --output 'D:/Prompt Calculus Studio/qa/03-prealpha3-batch1-20261109/s0/my-run'
```

`--output` 必須是尚不存在的目錄。預設清單來自 B1-S0 的 F-PROPOSAL：19 個 stage／queue 檔、B 的雙擊回歸、21 個 canvas 檔，最後執行 `test_084_stages` 的三個重開、暫停／繼續案例。清單與精簡案例完整 id 在 `fast_suite.py` 的 `STAGE`、`CANVAS`、`QUICK_STAGES`。單檔 120 秒，整套上限 360 秒。

另納入 `test_shared_core_boundary.py`：掃描共用模組任意深度的 Qt 匯入與同套件依賴封閉性，以合成反例自查，並在封鎖 Qt 的子程序呼叫共用圖片資料函式。共享清單由此契約驗證，不寫死模組數量。

B1-3B 納入 `test_stage_run_controls.py`（暫停後主按鈕、plan／工作區／服務隔離、關閉重開與選單取消）及 `test_canvas_card_menus.py`（13 種卡片尺寸對話框、選單入口與面板保留項目）。原有六分鐘與單檔上限不變。

B1-7 納入 `test_workspace_ambient.py`（`WORKSPACE` 清單）：固定 60 幀的 Canvas／文字頁 paint 計數、輸入與焦點、全域幾何、通知避讓、暫停與頂層視窗生命週期。Qt 6.11 offscreen 外掛顯示非 ToolTip 視窗時會忽略禁止焦點旗標而自動啟用；fixture 僅在 offscreen 對特效設定 `WA_DontShowOnScreen`，仍由真實 QWidget backing store 派送 paintEvent，不模擬 paint／焦點結果。原生桌面的輸入穿透、啟用、疊放、跨螢幕 DPI 與 OpenGL 合成仍須另驗；此屬性不進產品。

`test_request_lifetime.py` 納入 `WORKSPACE`：以實際 HTTP reply 及虛擬函式計數，驗證 GUI／worker 的成功、錯誤、逾時、取消與換代在 deferred deletion 前完成 Python sender／parent 清理，並驗證通道關閉的入站連線。這是確定性生命週期檢查，不靠壓力碰撞；長時間壓力程式不納入快速套件。

預設快速套件不能關閉看門狗或將單檔上限提高至 120 秒以上；只有明確使用 `--files`／`--file-list` 的診斷執行可調整。

## 借用資料監看

`test_storage_save_dedup.py` 驗證同步合併存檔、外層提交可見性、衍生草稿／場景
寫回、JSON body 位元組、驗證次數與原地歷史修改的反例；Qt 排入案例在最終
save 注入錯誤，檢查 0 筆 entry、游標／已存文件不變及下一次點擊恢復 N 筆。
它納入一般及 `--borrow-guard` 快速套件，時間上限不變。

`test_storage_transactions.py` 納入共用連線交易回歸：另一條 SQLite 連線確認
每個寫入點在外層失敗時不可見、交易外立即提交；保留三層巢狀中被捕捉例外的
既有語意，並驗證提交後回呼、persist 恢復副本、resume 與排序管理的整批回滾。
測試只替換 UI／worker 排程，實際寫入與交易方法照常執行；沒有真實服務或圖片
下載。完整入口契約見 [Stage 交易](../docs/development/stage-architecture.md#共用連線的交易入口)。

`test_stage_borrow_guard.py` 已納入快速套件：驗證 helper 的複製深度、`resolve` 不改輸入、子上下文既有 saved 項目禁止修改且新增 key 仍可用，以及 accepting 的登記回復、巢狀容器身分與未登記寫入對照。借用正向對照必須在改動來源前拋出 `BorrowViolation`；live 正向對照則在交易結束檢出未登記變動。預期事件和產品違規分開計數。

一般執行預設不開監看，六分鐘／單檔 120 秒預算不變。需要監看時加 `--borrow-guard`，或在啟動此執行器前設定 `PCS_BORROW_GUARD=1`。環境開關只供測試端讀取，產品不使用它。

```powershell
& $python -I tests/fast_suite.py --vendor $vendor --output $newOutput --borrow-guard
& $python -I tests/fast_suite.py --vendor $vendor --output $newOutput --borrow-guard --file-list $stageFiles --timeout 240
```

子程序在 discovery 前呼叫 `_borrow_guard.install_from_env()`，替換七個入口及已載入的產品函式別名：`borrowed_base`、`execution_state`、`merge_saved(borrow=True)`、`capture(borrow=True)`、`execution_scene`、`copy_context`、`workspace_scene.load(readonly=True)`。其他自訂測試入口須在載入測試前自行呼叫同一安裝函式；單設環境變數不會讓普通 `unittest` 自動載入工具。`install()` 可重複呼叫，`uninstall()` 可還原定義與別名。

每次違規先記錄路徑、產品呼叫位置及 test id，再立即拋錯，絕不繼續寫入。執行器檢查事件記錄，即使產品或 Qt 回呼捕捉了例外，該測試及整套結果仍失敗。`tests.json` 保存 `borrow_events`、`borrow_calls`；逐檔 `result.json` 保存 `borrow_violations`、`borrow_positive_controls` 及入口呼叫數。正向對照只有實際接住相符例外才會被標為 expected，未知事件不能套用 known failures 放行。

監看以唯讀副本保護共享 dict/list；原物件 identity 與時間點行為須另外驗證。執行交易另以完整 live 複本在提交／回復後檢查未登記變動。交易外的 UI handler 寫入另用 live dict/list 包裝及 state 綁定檢查，欄位為 MULTI_HISTORY_FIELDS 加 dictionary、generation；允許項目由 [live_write_allowlist.json](live_write_allowlist.json) 逐函式／呼叫點指定。純 helper 的測試 fixture 建立不視為 UI；合成 handler 用 `live_handler(owner)` 啟用。非 JSON 可變物件、未執行到的路徑或刻意使用基底類別方法的繞過仍不在保證內。完整契約見 [Stage 快照邊界](../docs/development/stage-architecture.md#同步擷取的資料所有權與查詢)。

文件寫入的三個新檔納入預設快速套件：`test_document_write_entries.py` 驗證三入口、通知、真正 journal 回復及兩個漏寫正向對照；`test_document_write_routes.py` 比較真實 handler 的入口 spy、Undo 全內容、刷新計數與 body；`test_document_write_compatibility.py` 固定遷移 UUID 對照缺 generation 的單／多畫布文件及 journal。基準 fixtures 由修改前來源的獨立 probe 產生，普通測試不更新它們。預算與逾時規則不變。

`test_execution_transactions.py` 提供 AST 鎖、巢狀登記、變更集合、Undo、純接線判斷、匯入替換與逐寫入點失敗注入。以 `--files test_execution_transactions.py --borrow-guard` 執行完整矩陣；快速套件保留借用檔的核心契約與正向對照，時間預算不變。

## 隔離執行與其他用法

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
- `known_failures.json` 只分類符合 **完整 test id 與錯誤內容** 的失敗。它不改 unittest 結果、不 skip、不使用 expectedFailure；目前只剩完整 Canvas 清單中的 Q8a，子程序仍回傳 exit 1。B 的雙擊回歸已修正並留在快速套件。未知失敗、零案例、skip、看門狗攔截、逾時、暫存殘留皆使整套失敗。
- 關閉後啟動縮圖的 S0-3 P2 與預覽 store 的 S0-4e 已從已知失敗清單移除。`test_close_barrier.py` 鎖住直接／延後關閉及已接受收藏的登記；`test_module_preview_store.py` 遍歷全部功能預覽，檢查記憶體 journal、資料檔案不變與連線釋放，可用上述 `--files` 用法執行。WinError 32 或暫存殘留仍直接使驗證失敗。

## 看門狗及 fixture

`test_stage_enqueue_progress.py` 是排入時機的獨立完整矩陣：auto／手動在 pump 前連點兩次及三次，對照 25ms；真實 `advance()` 的 80 次轉移讓出點；舊 run、waiting、交易回復及提交後 claim。用 `--files test_stage_enqueue_progress.py test_stage_click_regression.py --borrow-guard` 執行。快速套件維持原本清單與六分鐘上限，保留 B 的基本回歸。

`_modal_guard.install()` 只包裝測試程序內的 Qt `exec`／`exec_`，產品檔案不改。原始模態迴圈照常執行。精確計時器在五秒時記錄測試 id、對話框類別、objectName、標題與呼叫堆疊，reject／close，返回後丟出 AssertionError。執行器也檢查事件記錄，以免 Qt 回呼吞掉 Python 例外後被當成成功。`PCS_TEST_MODAL_GUARD=0` 僅供 A/B 驗證。

`stage_fixture` 與 `_qt_cleanup` 載入時也安裝看門狗。`_dialog_driver` 等實際 NodeDialog 成為 activeModalWidget 才驅動，因為建構期間也可能處理事件；回呼中的斷言會回傳至主測試。

圖片入口的單擊／雙擊 fixture 使用實際 viewport 事件與檢視器，只把 `image_preview.monotonic` 的兩次事件間隔固定為 25ms，避免顯示／關閉檢視器的耗時意外超過雙擊時限。它也驗證超過 `doubleClickInterval` 後會再次開啟，原圖片內容與所有權斷言保留；產品時鐘與事件處理不改。

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
