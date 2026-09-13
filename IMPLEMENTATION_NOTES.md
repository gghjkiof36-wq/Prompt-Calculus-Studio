# 0.6 實作記錄

- 新增 localhost QtNetwork 非同步連線，以及 ComfyUI 明確接管的單一分頁租約。後端長輪詢命令，桌面自動同步快照；run 不自動重送，重連丟棄舊請求回覆。前端使用原生 app.queuePrompt，提交前核對真正文字欄，保留 ComfyUI 的 seed／節點 callback 行為。
- 最近生成僅連結原 PNG，縮圖在背景建立；收藏複製原始位元組、驗證 metadata、以目的地去重。圖片庫收藏的內部原圖存於巢狀 albums 目錄，ZIP 備份已包含此目錄。
- 模組素材權重以十分之一整數保存；excludes 規則只在純核心組合時過濾完整 Tag，受影響項目由 compose_details 回傳。快照、JSON 匯入驗證、工作區固定組合與歷史恢復均包含權重及排除清單。
- 瀏覽器 HTML 圖片來源、data URL、原生剪貼簿與本機檔案共用拖入流程，網路讀取不阻塞 UI。資料量及圖片像素有上限，網路失敗不保存假圖片。
- CategoryDialog.move 更名為 move_category，避免覆寫 QWidget.move；對話框保持 application modal。固定模組清單處理 press/double-click，整列可快速切換。素材清單排序獨立於 selections 與 output_order。

## 0.4.3 與之前版本記錄

這一版是 Python + PySide6 + SQLite 原生桌面程式。以下記錄目前實作選擇，仍可依使用習慣調整。

- 0.4.3 以 output_order 保存右側有效群組順序。右側 reorder_output 不再重排 modules；新啟用群組依 modules 的零起算位置插入（超出則追加），已存在群組保留相對順序，清空群組即移出順序，重新加入再套用插入規則。舊資料尚無 output_order 時，依原 modules 及 temporary_before 遷移目前可見順序；不推測或還原使用者過去的模組排序。此規則取代下方歷史版本共用排序的描述。

- 圖示原稿在 prompt_studio/assets/studio.svg。build_windows.py 使用 Qt SVG 渲染成 16–256 px 共九個尺寸的 studio.ico，透過 PyInstaller --icon 嵌入 EXE；QApplication 與主視窗亦使用該 ICO，因此啟動鎖提示也有一致圖示。此變更不修改單一資料庫鎖或重複啟動行為。

- 0.4.2 新增 acrylic_transparency（整數 0–100，預設 61）與 confirm_clear_draft（布林，預設 true），沿用原設定的 SQLite 保存、備份驗證和舊資料預設值合併。透明度僅改 Acrylic 周邊的黑色覆蓋層 alpha，不使用整個視窗的 setWindowOpacity，不淡化文字，不改 Mica。
- 設定內提供只在 Acrylic 可見的滑桿／百分比欄位；預覽存在 Window.appearance_preview，不寫入已保存設定，取消或關閉時移除。顯示恢復事件亦使用同一預覽來源，避免操作途中被覆蓋。
- ClearDraftDialog 專門提供「不再提示」；只有確認清除時才保存偏好，取消或關閉不保存。略過提示也只清除手動稿，保留模組與片段；外觀設定提供恢復確認勾選。其他刪除提示不受影響。

- 0.4.1 撤回 0.4 的主視窗 QRegion 做法。使用原生 Qt Window + Title/SystemMenu/MinMax/Close flags，保留 WS_CAPTION、WS_THICKFRAME，沒有 FramelessWindowHint。雖保留 alpha backing store，Qt 的原生框架路徑不啟用 WS_EX_LAYERED；Windows 11 的 DWM 負責唯一外輪廓、陰影與原生標題列。Shell 不再繪製第二圈圓角或邊框，任何材質下都不設定主視窗遮罩。各彈窗和右鍵選單仍是獨立的 alpha 圓角表面，不啟用主視窗玻璃背景。
- 0.4.1 的玻璃底色 alpha 從 175 調為 100，DWM 桌布／背景著色可以較明顯透出；提示詞及素材內容區仍不透明。
- 標題列移除使用文字字元代替的最小化／最大化／關閉圖示，改交系統繪製與切換還原狀態。應用圖示使用 assets/studio.svg。
- 字型設定仍以 pt 保存與顯示，繪製時統一換成以 96 DPI 為基準的 Qt 邏輯像素，再由 Qt 處理各螢幕 DPR；卡片 delegate 同步採用此尺度。display.py 對 DPI／顯示變更／恢復啟用通知延遲重新整理字型、材質及清單，不重建資料、不改系統設定，也不輪詢。使用者已表示睡眠縮小不需優先解決，只有通知與狀態保留的模擬驗證，未驗證實際睡眠。
- `--frame-smoke-test` 僅允許新建測試資料目錄；檢查三種材質的正常、最大化、最小化、還原，驗證原生標題列／框架、分層旗標未啟用、遮罩為空及 Windows 原生狀態。不可再把單純 API 設定成功當成桌面視覺通過。

參考：[Qt 6.11.2 分層判斷與原生框架路徑](https://github.com/qt/qtbase/blob/v6.11.2/src/plugins/platforms/windows/qwindowswindow.cpp)、[DWM 不支援分層視窗或 region 的圓角限制](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/ui/apply-rounded-corners)。

以下保留先前版本實作歷史；外框部分已由上方 0.4.1 取代。

- 0.4 選單使用透明背景直接繪製圓角，移除 QRegion 選單裁切及原生矩形陰影。主視窗停用 DWM 額外邊框，材質模式以視窗 region 裁切整個背景；最大化時移除圓角及外框填滿畫面。依 Microsoft 文件，逐像素透明視窗無法套用自動 DWM 圓角。
- 手動版本以 draft is not None 判斷，空字串也視為手動版本。停用右側排序區，180 ms 淡出；清除需確認，取消保留內容，確認只清除 draft/draft_base。左側素材仍可瀏覽及選擇，新組合不覆寫手動內容。
- 模組及右側組合以 190 ms splitter 幾何過渡收合；樹狀群組使用 Qt 展開動畫。命名／設定視窗用 140 ms 入場淡入。沒有持續動畫計時器。
- 儲存與取消使用相同 padding，對話框同列按鈕設共同最小寬度。最終文字框上方操作會依寬度換行。複製成功回饋 1.4 秒，可重複按，修改文字時立即重置。
- tests/ui_review.py 使用獨立資料、合成色板檢視三種尺寸／字級、手動版本、選單透明四角及儲存／取消等高；不改動使用者資料。

參考：[Windows 圓角限制](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/ui/apply-rounded-corners)、[DWM 邊框控制](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute)、[Qt 透明頂層視窗](https://doc.qt.io/qt-6/qwidget.html)。

- 0.3.1 在 Windows / Qt 6.11.2 重現 RoundMenu 同步 exec 的原生 access violation；普通 QMenu.exec 與 RoundMenu.popup 正常。移除所有自訂選單同步呼叫，使用 open_at → popup，沿用 QAction 處理動作；關閉後 deferred delete。
- `run.py --menu-smoke-test` 僅允許全新的資料目錄，執行 7 類選單反覆開啟／關閉、複製、工作區設定／新增／刪除取消／刪除確認，以及圖片附註。資料僅為測試 fixture；結果寫入 menu-result.json。可對最終 EXE 執行相同測試，避免只驗證外觀而漏掉入口。

- 0.3 改用集中操作列、統一黑色內容區與有限寬度；自訂圓角命名／編輯視窗，保留系統檔案挑選器。
- `views.py` 使用 delegate 繪製可見素材卡片與組合項目，不為每張卡片建立完整 QWidget。
- 右側群組順序同步更新原有 modules 清單；新增可選的 `temporary_before` 錨點，讓臨時片段群組能置於任意模組之前。舊資料沒有此欄位時仍置於最後。
- 模組內以 selections 順序組合，臨時片段以清單順序組合。拖曳不跨模組改分類，亦不覆寫手動稿。
- 拖曳使用自身 QDrag 生命週期，避免 Qt 預設移動在 UI 重建後二次移除項目；只有拖曳到邊緣時才啟動捲動計時器。命名視窗與右鍵選單關閉後釋放。

- Prompt Item 屬於一個模組，沒有額外 Group 層；別名為可編輯文字清單。
- 模組、Prompt、工作區以版本化 JSON 文件存於 SQLite document 表；模型、相簿與圖片使用獨立 resources 表，避免每次輸入都重寫整個圖片庫。
- 圖片清單只取名稱、路徑與縮圖等少量欄位。完整 ComfyUI 圖資料只在選中圖片時讀取，圖示縮到顯示尺寸再保存於 UI。
- 縮圖最長邊 512 px、JPEG 品質 85；透明圖縮圖合成黑底，來源原圖不變。單圖解碼配置限制 128 MiB，並非整個程序記憶體上限。
- 模型以實際路徑識別，不讀 tensor、不計算大型模型雜湊。外部改名／搬動模型會成為新紀錄，舊說明標示原檔不存在；尚未自動合併身分。
- 模型刪除先檢查根目錄、檔案格式、junction／symlink、大小與修改時間，再交給 QFile.moveToTrash。檔案不可在其他程式同時搬動；檢查和操作間仍有作業系統競態時間。
- 模型匯入使用專用 partial 檔案，完成才改成正式檔名，不覆寫同名。每次讀寫 4 MiB，不把整個模型載入 RAM。
- PNG 只讀有用文字區塊，略過像素 IDAT。保留原始 metadata，另列可辨識節點原值，不宣稱還原整個計算圖。
- 工作區歷史保存建議參數。手動附到圖片時複製當時內容；後續工作區改動不會修改該圖片紀錄。
- 系統材質使用 Qt 透明視窗與 DWM 背景，主面板實色；無第三方逐卡片模糊套件。
- 普通閒置時不監看資料夾、不持續輪詢網路、不運行動畫。

## 0.7 乾淨匯出與介面修正

- 匯出分成純 Python 容器清理、Qt 編解碼及分頁預覽；沿用一條檔案背景工作，不新增影像套件或 GPU 工作。原圖受路徑、實體檔案身分、預覽前後時間／大小及應用資料夾限制保護。
- PNG 文字／EXIF、JPEG APP／COM（含 progressive 多掃描間的欄位）、WebP EXIF／XMP／未知區塊等透過允許清單移除；透明度、最小色彩解碼標頭及動畫結構保留。全清理會先處理支援的色彩／方向，再移除相應欄位。動畫需重新編碼時明確失敗。
- 暫存只放在指定輸出位置，驗證後才交付目標檔；結果、Preset 及選用雜湊存入 SQLite 的 clean_exports／clean_presets，不加入 resources 圖片索引，也不寫入圖片。
- 素材清單 InternalMove 寫回 visible IDs 在 items 中的既有位置，保留隱藏項目、模組歸屬及 output_order／selections。實際 EXE 拖曳與保存已驗證。
- Mica 與 Acrylic 的遮罩透明度分別保存，主內容區保持實色。未指定收藏而按運行才顯示紅色驚嘆號；曾 ready 後失去控制顯示黃色，重新 ready 或桌面主動 disconnect 清除警示。

## 未納入

0.7.1 將匯出清單繪製獨立到 export_views，資料掃描／匯出仍由既有背景工作執行。背景工作完成訊號可接續使用者的匯出請求，不增加常駐輪詢。最近生成選單先解析圖片 ID，再捕捉來源路徑，避免非同步更新清單後失效。WindowShell 只清除自己殘留的縮放游標，保留文字編輯器與分隔線自己的游標。

未來設定整頁、模型／節點／運行／資源管理的整合，等待 1.0 導覽規劃確認；目前保留既有頁面與資料結構，不先遷移模型管理。媒體庫目前支援圖片，改名不代表已支援影片。

圖片自動配對最近複製 Prompt、自動 Civitai 下載／辨識、任意長句可靠拆 Tag、全量 Danbooru 詞庫、跨路徑模型自動合併，以及清理未引用縮圖。ComfyUI 同步已在 0.6 透過明確綁定及桌面控制加入。

## 驗證

tests/test_studio.py 使用 qa 內的獨立暫存檔案測試，不操作使用者模型。測試覆蓋文字組合、SQLite 備份、PNG 來源、模型路徑與取消／失敗保護、中文輸入法預編輯、工作區歷史等。

tests/visual_smoke.py 渲染實際 Qt 元件並量測小型資料集；其中圖片明確標記為測試圖，不作生成效果示例。封裝後以 --smoke-test 及獨立資料目錄確認 Qt、SQLite 與視窗能正常啟動、保存及關閉。

封裝診斷曾發現 PATH 中 Poppler 的 icuuc.dll 被誤收，與 Qt 使用的 Windows ICU API 不相容。build_windows.py 現在隔離 PATH 並清除封裝快取，避免同名 DLL 汙染；release 的 _internal 只放產生的程式元件，更新時整份替換，data 則保留。
