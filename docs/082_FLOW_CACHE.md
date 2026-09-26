# 0.82 畫布流程、介面修正與搜尋快取

使用者於 2026-09-14 在 03 提供六張截圖，要求八項修正，隨後明確追加「快取方案順便做一下」。已核對 01 的對話與 HANDOFF rev 6；本輪授權取代首批對品牌、CLIP／執行列及 C 的待定狀態。尚未整合主倉庫或發布。

工作分支 `feature/03-082-panel-layout`，位置 `<workspace>/worktrees/03-082-panel-layout`；首批起點 9f1bef2，主倉庫基準 6d48f59。03 的 STATUS 是任務進度入口，00 單獨維護共同索引。

## 使用結果與验收範圍

| 要求 | 已實作結果 |
|---|---|
| 程式名稱與版本 | 視窗／應用名為 Prompt Calculus Studio，開發版本 v0.82 Alpha 1；增加 --v082-alpha 入口。 |
| 預設 Canvas | 本版首次開啟套用 Canvas，清單與畫布手動稿各自保留；使用者之後選擇清單會被記住。 |
| 獨立綁定與執行 | 畫布→Prompt 輸出→CLIP 輸入；綁定按鈕位於 CLIP 卡片。可新增、改接、移除 CLIP，工作流欄位不得重複綁定。底部執行列可拖移，固定在視窗座標，不隨畫布縮放；圖片預覽入口放在此列，開啟既有預覽。 |
| 素材面板尺寸 | 拖曳尺寸與三欄比例保存於設定；重新開啟恢復，在較小螢幕中限制於可用範圍。 |
| 刪除底部查詢提示 | 移除「輸入 Tag 後查詢」及底部候選統計；查詢狀態保留於候選區工具提示。 |
| 淡分隔線 | 素材面板加入三區分隔及條目細線；清單在分類與素材之間加入細線，保留既有卡片。 |
| 精簡文字 | 移除指定拖曳排序提示和自動組合说明；手動稿狀態仍可辨識。 |
| 設定位置 | 清單設定移到左側工作區三點選單旁。 |
| C 快取 | 既有搜尋入口增加持久快取的上限、淘汰、禁止及清除功能。 |

## 資料流與相容性

`multi_output` 內部版本由 2 升為 3，外層文件／快照版本仍為 4。新增 `clip_inputs` 與 `clip` 連線，bindings 由記錄 output 改為記錄 clip。實際提交時由 `clip_flow.bound_texts` 沿連線找出來源輸出，沿用 `compile_output` 取得文字，再交給原有 generation／GenerationRunner；每次按執行時凍結的 snapshot 仍是提交與歷史文字來源。

`state_loading.prepare_state` 在原驗證與遷移後呼叫 `clip_flow.upgrade`：每個舊輸出建立 CLIP 卡片，將原欄位綁定移入；只有原本接到執行的輸出會連到 CLIP，已斷線的輸出保持斷線。新舊 compiled_outputs 必須相等。Storage 在保存轉換後文件前保留 before-import 備份。手動空字串、未綁定欄位原值、畫布歸屬、順序與幾何保留；改接、刪除與復原都經原 Canvas.commit。

設定內工作流管理頁使用同一組 CLIP 綁定。舊 v1 工作流交換檔仍可匯入，先驗證整份候選資料再建立／重用 CLIP；錯誤時不更新原文件。歷史預覽依已保存快照判斷輸出歸屬，不讀目前工作區文字補寫。

共享純 Python 模組清單增加 clip_flow，沒有引入 Qt。ComfyUI 擴充宣告 `clip_inputs_v3`，桌面會核對支援再提交；實際使用新流程需同步此版共享模組與擴充。舊節點識別、prompt_studio 套件／協定名稱及既有資料路徑保持相容。此輪未產出新的安裝包，未以既有 0.81 擴充實機生成。

## 快取行為

- Danbooru 查詢與整體快取最多 5,000 筆、JSON 文字合計 16 MiB，先到上限先淘汰；每次最多保存 12 個候選，超額單筆不存。Google 查詢保留原 500 筆預算，不做背景預抓。
- 最後使用時間優先保留常用查詢。非空結果 24 小時內視為新鮮；舊結果先顯示，線上依既有節流更新。離線可讀舊非空結果；空結果 1 小時後失效。
- 設定→候選與翻譯加入「禁止搜尋快取」「清除搜尋快取」。禁止停止讀寫且保留資料；清除只刪搜尋快取與其使用時間，保留個人字典、已接受詞、素材、歷史及 Token。
- 沿用 SQLite cache 表的原三欄，另加 cache_access 保存使用時間。16 MiB 是 JSON 內容限制，不是整個 SQLite 檔案大小保證。
- 清除、禁止、重新啟用都使旧請求的快取世代失效；回呼同時核對資料庫／工作區、查詢、序號、連線與關閉狀態。禁止時的新查詢仍可聯網取得即時候選，但不保存。

## 驗證證據

- 77 項核心／介面相關測試通過，51.850 秒；`qa/082-final-core-ui.log`。涵蓋 CLIP 遷移、欄位唯一性、未綁定原文、手動空稿、生成快照、斷線／改接／undo/redo／重開、尺寸記憶、鍵盤與 IME、快取失效、工作流管理及原有設定邊緣。
- 生成、介面切換與基本資料的 53 項回歸通過，34.693 秒（test_generation、test_interface_modes、test_studio；工具執行結果）。
- 另以共享後端 Service 驗證 v3 提交與真實提交文字記錄，1 項通過，0.106 秒；完全本機 stub，不是 GPU 生成。
- 150% 縮放重跑 multi_canvas、canvas_palette、settings_edges 共 36 項通過，44.562 秒；`qa/082-final-scale150.log`。這是重跑，不另累計獨立測試數。
- 使用本機既有字型檢查離屏截圖：`qa/082-flow.png`、`082-palette.png`、`082-list.png`、`082-cache.png`。版本入口查詢結果為 Prompt Calculus Studio · v0.82 Alpha 1／--v082-alpha，差異空白檢查通過。
- 原生 Windows 多螢幕 DPI、實際 IME 候選窗、真實 API／Token、大型下載、ComfyUI GPU 生成、封裝及全新安裝未驗。本組驗證不代替獨立 Review／QA。

重現環境沿用 <workspace>/tools/python312/python.exe 與只讀 repository/vendor；PYTHONPATH 加 worktree 和 worktree/tests，QT_QPA_PLATFORM 設 offscreen:configfile=tests/offscreen_1920.json，TEMP/TMP 指向 worktree/qa。150% 另設 QT_SCALE_FACTOR=1.5。測試用獨立臨時資料，沒有新增依賴。

## 開啟與未納入項目

本機預覽入口 `qa/Open-PCS-082.vbs` 使用 D 槽工具、--v082-alpha 與此 worktree 的 qa/preview-data。使用者已操作的 preview-data 視為使用者資料；沒有重置、複製為測試輸入或關閉使用者程序。關閉舊預覽再雙擊入口即可載入新程式。

未納入：多選、最近使用／完整 Prompt 歷史、多網站，以及中期路線的本機辨識差額、LoRA／Trigger、收藏／個人評分、Recipe、Wildcard 與轉譯。以上依後續使用者核准再處理；首批 A/B 與本輪都尚未合併、公開發布或完成獨立 QA。
