# Canvas 外觀基準

此頁記錄 0.8.7 Candidate17 的第一批實作，供後續元件沿用。使用者確認的方向是精簡布局、深色霧面卡片與小型實心接點；這批實際外觀仍待使用者檢視，不代表整個 Canvas 已完成統一。

| 範圍 | 共用來源 | 本批決定 |
| --- | --- | --- |
| 畫布底色 | `theme.py`、`color_roles.py` 的 `canvas_background` | graphite 為中性 `#111111`，保留細點格；其他配色沿用自身底色 |
| Stage 卡面 | `module_chrome.matte_colors`、`FlowCard.surface_colors` | graphite 卡身 `#202625`、標頭 `#262c2b`、內層 `#191f1e`；邊線與文字由共用色彩規則派生，陰影較輕 |
| 連接點 | `flow_items.Port` | 直徑約 9 個場景單位，實心；滑過及可連接時顯示外圈。保留原本 22×26 命中範圍與連線規則 |
| 模組相關視窗 | `module_chrome.apply_module_dialog` | 新增功能模組與排列文字來源共用外框、標題、選取列；底部主要操作靠右 |
| 節點預覽 | `module_preview.ModulePreview` | 使用隔離資料建立真實元件，文字與控制保持即時繪製；按可用寬度縮放，最低 85%，過高內容可捲動 |

Stage 保留名稱、收合、更多、輸入／輸出與工作流入口。參考圖只決定外觀方向，不補回多餘的說明欄位。這批沒有改底部執行欄，也沒有將全部既有卡片自動換成 Stage 的新材質。

新增功能模組採滑過預覽、單擊加入，搜尋列 Enter 及右下新增按鈕也可加入；一次操作只建立一個模組。預覽用的尺寸與圖片批次控制簡化只存在於隔離元件，不改正式卡片尺寸、使用者工作區、歷史或來源資料。Qt 代理元件完成版面更新時才重新調整預覽範圍，避免留下多餘捲動區。

後續修改先沿用上表的色彩角色與元件，再比較同一批實際 Qt 畫面：未設定／已選工作流／工作流失效的 Stage、十種預覽、預排程管理入口、文字來源排序，以及淺色與較大字級。不能用示意 HTML、整卡縮圖或舊截圖代替程式目前的外觀。

回歸入口為 `tests/test_canvas_visual_batch.py`、`test_canvas_module_redesign.py`、`test_canvas_proxy_edges.py`、`test_canvas_raster.py` 與 `test_color_roles.py`。保存、Undo／Redo、連線命中與預覽不改資料是操作條件；離屏畫面與測試不替代原生桌面、真實服務或使用者視覺接受。各候選的實際結果見[驗證紀錄](../releases/0.8.7/validation.md)。
