# Canvas 外觀基準

此頁記錄 0.8.7 Candidate19 的 Canvas 外觀基準，供後續元件沿用。使用者確認的方向是精簡布局、中性深灰卡片、小型實心接點，並優先統一彈出後的 UI。本批補上彈窗深度與操作細節，實際外觀待使用者檢視；其它主頁分批適配。

| 範圍 | 共用來源 | 本批決定 |
| --- | --- | --- |
| 畫布底色 | `theme.py`、`color_roles.py` 的 `canvas_background` | graphite 為中性 `#111111`，保留細點格；其他配色沿用自身底色 |
| Canvas 卡面 | `canvas_items.canvas_colors`、`module_chrome.matte_colors` | 各種卡片共用 graphite 卡身 `#232628`、標頭 `#282c2e`、內層 `#191c1e`；邊線與文字由共用色彩規則派生，陰影較輕 |
| 連接點 | `flow_items.Port` | 直徑約 9 個場景單位，實心；滑過及可連接時顯示外圈。保留原本 22×26 命中範圍與連線規則 |
| 模組相關視窗 | `module_chrome.apply_module_dialog`、`dialog_colors`、`canvas_dialog_context` | 深色外框、細邊線、統一標題與關閉圖示；多欄編輯使用稍亮的 `DialogEditor` 區域及深色欄位，小型表單的欄位比外框稍亮；主要操作用重點色 |
| 遮罩與進場 | `dialog_presentation.CanvasSheetDialog`、`DialogShade` | 功能選擇器擁有整窗遮罩與外側點擊，180ms 向上 10px 定位並加深遮罩；減少動態效果時直接呈現。巢狀共用彈窗壓暗可見父層，關閉或銷毀時移除遮罩 |
| 一般操作與綁定 | `module_chrome.style_action` | 暫停／繼續與存入素材庫同色、同圓角及邊框，隨主題更新；綁定入口保留深色內層 |
| 節點預覽 | `module_preview.ModulePreview`、`FunctionPicker` | 使用隔離資料建立真實元件，文字與控制保持即時繪製；固定可用區域，按寬與高等比例縮放，預覽不顯示捲動欄。對話框隨螢幕調整，內容切換不改視窗大小 |
| 文字模組選擇器 | `stacked_card_list.StackedCardListMixin`、`palette_cards.PickerCardDelegate` | 與 Prompt Editor 共用底部三層通知堆疊、捲動與命中範圍；捲到底展開，減少動態效果時使用一般清單 |

Stage 保留名稱、收合、更多、輸入／輸出與工作流入口。參考圖只決定外觀方向，不補回多餘的說明欄位。底部執行欄不在本批重設範圍。Candidate17 的偏綠 Stage 專用材質已由共用中性角色取代，原有 `#111111` 背景及實心接點保留。

新增功能模組採滑過預覽、單擊加入，搜尋列 Enter 及右下新增按鈕也可加入；一次操作只建立一個模組。預覽用的尺寸與圖片批次控制簡化只存在於隔離元件，不改正式卡片尺寸、使用者工作區、歷史或來源資料。Qt 代理元件完成版面更新時才重新調整縮放；預覽不再反向修改版面高度。正常字級保留可讀比例，小螢幕與大字級則縮小整張預覽，搜尋、清單與新增操作仍留在視窗內。

Canvas 主頁或其展開編輯頁開啟的 `StudioDialog` 自動採用 Canvas 彈窗角色，巢狀視窗沿用父層；涵蓋尺寸、命名、新增空白模組、完整圖片、排程詳情與確認等共用視窗。文字選擇器、Stage 參數頁及綁定頁也使用同一角色。媒體庫、設定、匯出主頁開啟的通用視窗保留原外觀，避免全域樣式造成半套遷移。原生檔案選擇器不重繪。

選單沿用共用 `RoundMenu`／`PopupSurface` 的較亮浮層、圓角與列高，沒有只為 Canvas 另設一種外觀。關閉時的外側滑鼠按下事件標記為不重送，避免同一事件觸發下方按鈕重開；之後獨立點擊及鍵盤仍可開啟。最近生成右側卡片改用 18px 關閉圖示、36×36 操作範圍，保留主視窗關閉與卡片關閉各自的作用。

圖片顯示只以新的完整圖片結果取代上一批；下一個等待、失敗或純文字任務不清掉目前畫面。同一輪分批結果仍合併，舊輪晚到的結果不蓋掉新輪完成結果。這個保留規則只修改 `StageRunner.preview_results` 顯示投影，不改任務日誌或下游 Stage 使用的 `results`；斷線、換來源及換工作區沿用原清空邊界。

## 後續適配順序

| 範圍 | 彈出後 UI | 主頁／未展開 UI |
| --- | --- | --- |
| Canvas | 選擇器、綁定、Stage 參數、排序及共用操作／巢狀對話框已接入；本批補遮罩、欄位分層及選單關閉，後續核對其它特殊 overlay 與錯誤狀態 | 共用卡面已接入；保持精簡布局與現有操作 |
| 最近生成、媒體庫 | 接續核對最近生成 overlay、資料夾與圖片操作對話框 | 使用者確認 Canvas 後再適配 |
| 連線與工作流 | 沿用 Canvas 綁定、欄位與操作色角色，逐項遷移專用對話框 | 保留目前資訊與工作流列表結構，另批處理 |
| 匯出、設定、探索 | 按預設、確認、篩選等視窗家族檢視 | 最後逐頁適配，避免只改背景而破壞既有統一性 |

舊版整體已有一致規則；遷移以完整元件家族為單位，不能全域覆寫舊色值後就宣稱完成。每批分開記錄已改入口、尚未適配入口、實際畫面及操作回歸。

後續修改先沿用上表的色彩角色與元件，再比較同一批實際 Qt 畫面：未設定／已選工作流／工作流失效的 Stage、十種預覽、預排程管理入口、文字來源排序，以及淺色與較大字級。不能用示意 HTML、整卡縮圖或舊截圖代替程式目前的外觀。

回歸入口包括 `tests/test_canvas_popup_layers.py`、`test_recent_overlay.py`、`test_popup_menu_ownership.py`、`test_canvas_cohesion.py`、`test_canvas_preview_continuity.py`、`test_canvas_visual_batch.py`、`test_canvas_module_redesign.py`、`test_stage_parameter_ui.py`、`test_editor_rev9.py`、`test_canvas_proxy_edges.py`、`test_canvas_raster.py` 與 `test_color_roles.py`。保存、Undo／Redo、連線命中與預覽不改資料是操作條件；離屏畫面與測試不替代原生桌面、真實服務或使用者視覺接受。各候選的實際結果見[驗證紀錄](../releases/0.8.7/validation.md)。
